import asyncio
import json
import logging

from datetime import (
    datetime,
    timedelta,
    time as dtime
)

import discord

from config import (
    TIMEZONE,
    CHECK_SECONDS,
    SCHEDULE_REMINDER_HOURS
)

from database import (
    get_schedules,
    get_execution,
    create_execution,
    update_execution,
    get_enabled_sleep_checks,
    update_sleep_check,
    upsert_voice_effect
)


log = logging.getLogger(
    "Bot94Sleep.Scheduler"
)


DAY_NAMES = {
    0: "lunes",
    1: "martes",
    2: "miercoles",
    3: "jueves",
    4: "viernes",
    5: "sabado",
    6: "domingo"
}


def normalize_actions(
    mute,
    deafen,
    disconnect
):

    if deafen:

        mute = True

    return (
        bool(mute),
        bool(deafen),
        bool(disconnect)
    )


async def execute_actions(
    member,
    mute=False,
    deafen=False,
    disconnect=False
):

    mute, deafen, disconnect = normalize_actions(
        mute,
        deafen,
        disconnect
    )

    if (
        not member.voice
        or not member.voice.channel
    ):

        return False

    try:

        if mute or deafen:

            await member.edit(
                mute=mute,
                deafen=deafen
            )

            upsert_voice_effect(
                member.id,
                member.guild.id,
                mute_by_bot=mute,
                deafen_by_bot=deafen
            )

        if disconnect:

            await member.move_to(None)

        return True

    except (
        discord.Forbidden,
        discord.HTTPException
    ):

        log.exception(
            "Discord rechazó una acción sobre %s",
            member.id
        )

        return False


def _days(value):

    try:

        return [
            str(x).lower().strip()
            for x in json.loads(value)
        ]

    except Exception:

        return []


def _time(value):

    try:

        hours, minutes = map(
            int,
            value.split(":")
        )

        return dtime(
            hours,
            minutes
        )

    except Exception:

        return None


def next_occurrence(
    row,
    now
):

    schedule_time = _time(
        row["time"]
    )

    wanted_days = set(
        _days(row["days"])
    )

    if (
        not schedule_time
        or not wanted_days
    ):

        return None

    for offset in range(8):

        date = (
            now
            +
            timedelta(days=offset)
        ).date()

        if (
            DAY_NAMES[date.weekday()]
            not in wanted_days
        ):

            continue

        occurrence = datetime.combine(
            date,
            schedule_time,
            tzinfo=TIMEZONE
        )

        if occurrence > now:

            return occurrence

    return None


async def find_member(
    bot,
    user_id,
    guild_id
):

    guild = bot.get_guild(
        int(guild_id)
    )

    if not guild:

        return None

    member = guild.get_member(
        int(user_id)
    )

    if member:

        return member

    try:

        return await guild.fetch_member(
            int(user_id)
        )

    except Exception:

        return None


class Scheduler:

    def __init__(self, bot):

        self.bot = bot
        self.running = True

    async def start(self):

        log.info(
            "Scheduler iniciado"
        )

        while self.running:

            try:

                await self.check_schedules()

                await self.check_sleep_checks()

            except Exception:

                log.exception(
                    "Error del scheduler"
                )

            await asyncio.sleep(
                CHECK_SECONDS
            )

    async def check_schedules(self):

        now = datetime.now(
            TIMEZONE
        )

        for row in get_schedules(
            enabled_only=True
        ):

            occurrence = next_occurrence(
                row,
                now
            )

            if not occurrence:

                continue

            execution_date = (
                occurrence
                .date()
                .isoformat()
            )

            execution = get_execution(
                row["id"],
                execution_date
            )

            if execution is None:

                if (
                    occurrence - now
                    <= timedelta(
                        hours=
                        SCHEDULE_REMINDER_HOURS
                    )
                ):

                    execution = create_execution(
                        row["id"],
                        execution_date,
                        occurrence.isoformat()
                    )

                else:

                    continue

            if execution["status"] in {
                "executed",
                "skipped",
                "failed",
                "cancelled"
            }:

                continue

            target = datetime.fromisoformat(
                execution[
                    "override_datetime"
                ]
                or execution[
                    "original_datetime"
                ]
            )

            reminder_time = (
                target
                -
                timedelta(
                    hours=
                    SCHEDULE_REMINDER_HOURS
                )
            )

            if (
                bool(row["confirmation"])
                and not execution["reminder_sent"]
                and reminder_time <= now < target
            ):

                await self.send_reminder(
                    row,
                    execution,
                    target
                )

                update_execution(
                    execution["id"],
                    reminder_sent=1,
                    status="waiting"
                )

                continue

            if now >= target:

                member = await find_member(
                    self.bot,
                    row["user_id"],
                    row["guild_id"]
                )

                if (
                    not member
                    or not member.voice
                ):

                    result = "skipped"

                else:

                    success = await execute_actions(
                        member,
                        bool(row["mute"]),
                        bool(row["deafen"]),
                        bool(row["disconnect"])
                    )

                    result = (
                        "executed"
                        if success
                        else "failed"
                    )

                update_execution(
                    execution["id"],
                    status=result,
                    confirmed=int(
                        result == "executed"
                    )
                )

    async def send_reminder(
        self,
        row,
        execution,
        target
    ):

        member = await find_member(
            self.bot,
            row["user_id"],
            row["guild_id"]
        )

        if not member:

            return

        from apibot import ScheduleReminderView

        try:

            await member.send(
                (
                    "⏰ **Bot94 Sleep**\n\n"
                    f"Tu desconexión está programada "
                    f"para las **{target:%H:%M}**.\n\n"
                    "Puedes modificar solamente esta "
                    "ejecución."
                ),
                view=ScheduleReminderView(
                    self.bot,
                    row,
                    execution["id"],
                    target
                )
            )

        except discord.Forbidden:

            log.warning(
                "No pude enviar DM a %s",
                member.id
            )

    async def check_sleep_checks(self):

        now = datetime.now(
            TIMEZONE
        )

        for row in get_enabled_sleep_checks():

            member = await find_member(
                self.bot,
                row["user_id"],
                row["guild_id"]
            )

            if (
                not member
                or not member.voice
            ):

                continue

            waiting = None

            if row["waiting_until"]:

                waiting = datetime.fromisoformat(
                    row["waiting_until"]
                )

            if (
                waiting
                and now >= waiting
            ):

                success = await execute_actions(
                    member,
                    mute=True
                )

                if success:

                    update_sleep_check(
                        member.id,
                        member.guild.id,
                        waiting_until=None,
                        muted_by_sleep_check=1,
                        next_check_at=(
                            now
                            +
                            timedelta(
                                minutes=
                                row[
                                    "interval_minutes"
                                ]
                            )
                        ).isoformat(
                            timespec="seconds"
                        )
                    )

                continue

            if waiting:

                continue

            next_check = None

            if row["next_check_at"]:

                next_check = datetime.fromisoformat(
                    row["next_check_at"]
                )

            if (
                not next_check
                or now >= next_check
            ):

                await self.prompt_sleep_check(
                    row,
                    member,
                    now
                )

    async def prompt_sleep_check(
        self,
        row,
        member,
        now
    ):

        from apibot import SleepCheckView

        waiting = (
            now
            +
            timedelta(
                minutes=
                row[
                    "response_minutes"
                ]
            )
        )

        try:

            await member.send(
                (
                    "😴 **¿Sigues despierto?**\n\n"
                    "Si sigues aquí, confirma.\n"
                    "Si no respondes, Bot94 Sleep "
                    "te silenciará."
                ),
                view=SleepCheckView(
                    self.bot,
                    member.id,
                    member.guild.id
                )
            )

            update_sleep_check(
                member.id,
                member.guild.id,
                waiting_until=waiting.isoformat(
                    timespec="seconds"
                ),
                last_prompt_at=now.isoformat(
                    timespec="seconds"
                ),
                next_check_at=(
                    now
                    +
                    timedelta(
                        minutes=
                        row[
                            "interval_minutes"
                        ]
                    )
                ).isoformat(
                    timespec="seconds"
                )
            )

        except discord.Forbidden:

            log.warning(
                "No pude enviar Sleep Check a %s",
                member.id
            )