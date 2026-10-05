import asyncio
import logging
from datetime import date, datetime, time as dt_time, timedelta
from zoneinfo import ZoneInfo

import discord

from database import (
    create_execution,
    get_execution,
    get_pending_executions,
    get_schedules,
    update_execution,
)

logger = logging.getLogger("Bot94Sleep.Scheduler")

TIMEZONE = ZoneInfo("America/Lima")
REMINDER_HOURS = 2
REMINDER_MINUTES = 2
CHECK_SECONDS = 15

DAY_NAMES = {
    0: "lunes",
    1: "martes",
    2: "miercoles",
    3: "jueves",
    4: "viernes",
    5: "sabado",
    6: "domingo",
}


def normalize_actions(mute=False, deafen=False, disconnect=False):
    if deafen:
        mute = True
    return bool(mute), bool(deafen), bool(disconnect)


async def execute_actions(member, mute=False, deafen=False, disconnect=False):
    if member is None or member.voice is None or member.voice.channel is None:
        return False

    mute, deafen, disconnect = normalize_actions(
        mute, deafen, disconnect
    )

    try:
        if mute or deafen:
            await member.edit(mute=mute, deafen=deafen)

        if disconnect:
            await member.move_to(None)

        return True

    except (discord.Forbidden, discord.HTTPException) as exc:
        logger.error(
            "No se pudieron ejecutar acciones sobre %s: %s",
            member,
            exc,
        )
        return False
    except Exception:
        logger.exception(
            "Error inesperado ejecutando acciones sobre %s",
            member,
        )
        return False


def parse_schedule_days(value):
    return {
        part.strip().lower()
        for part in value.split(",")
        if part.strip()
    }


def build_candidate(now, day_name, time_text):
    hour, minute = map(int, time_text.split(":"))
    weekday = list(DAY_NAMES.values()).index(day_name)
    delta = (weekday - now.weekday()) % 7
    target_date = now.date() + timedelta(days=delta)
    candidate = datetime.combine(
        target_date,
        dt_time(hour, minute),
        tzinfo=TIMEZONE,
    )

    if candidate <= now:
        candidate += timedelta(days=7)

    return candidate


def next_occurrence(now, schedule):
    candidates = []

    for day_name in parse_schedule_days(schedule["days"]):
        if day_name not in DAY_NAMES.values():
            continue
        candidates.append(
            build_candidate(now, day_name, schedule["time"])
        )

    return min(candidates) if candidates else None


def execution_datetime(execution):
    if execution["override_datetime"]:
        return datetime.fromisoformat(execution["override_datetime"])

    return datetime.combine(
        date.fromisoformat(execution["execution_date"]),
        dt_time(*map(int, execution["original_time"].split(":"))),
        tzinfo=TIMEZONE,
    )


class Scheduler:
    def __init__(self, bot):
        self.bot = bot
        self.running = True

    async def start(self):
        logger.info("Scheduler iniciado.")

        while self.running:
            try:
                await self.check_schedules()
            except Exception:
                logger.exception("Error revisando horarios.")

            await asyncio.sleep(CHECK_SECONDS)

    async def check_schedules(self):
        now = datetime.now(TIMEZONE)
        schedules = get_schedules()

        # Creamos la ejecución cuando entra en la ventana de 2 horas.
        # Así, si el bot arranca después del recordatorio pero antes de
        # la hora de ejecución, todavía puede ejecutar correctamente.
        for schedule in schedules:
            occurrence = next_occurrence(now, schedule)

            if occurrence is None:
                continue

            if occurrence - now > timedelta(hours=REMINDER_HOURS):
                continue

            if now > occurrence:
                continue

            execution_date = occurrence.date().isoformat()
            execution = get_execution(
                schedule["id"],
                execution_date,
            )

            if execution is None:
                execution = create_execution(
                    schedule["id"],
                    execution_date,
                    schedule["time"],
                    now.isoformat(),
                )

            if (
                schedule["confirmation"]
                and not execution["reminder_sent"]
            ):
                reminder_at = (
                    occurrence - timedelta(hours=REMINDER_HOURS)
                )

                if (
                    reminder_at
                    <= now
                    < reminder_at + timedelta(minutes=REMINDER_MINUTES)
                ):
                    await self.send_reminder(
                        schedule,
                        execution,
                        occurrence,
                    )

                    update_execution(
                        execution["id"],
                        reminder_sent=1,
                        updated_at=now.isoformat(),
                    )

        # Ejecutar las ejecuciones pendientes cuya hora efectiva ya llegó.
        for execution in get_pending_executions():
            effective_dt = execution_datetime(execution)

            if effective_dt <= now:
                await self.execute_scheduled(execution)

    async def send_reminder(
        self,
        schedule,
        execution,
        occurrence,
    ):
        guild = self.bot.get_guild(schedule["guild_id"])

        if guild is None:
            return

        member = guild.get_member(schedule["user_id"])

        if member is None:
            return

        from bot import ScheduleReminderView

        text = (
            "🌙 **Bot94 Sleep — Recordatorio**\n\n"
            f"⏰ Tienes un Sleep programado para "
            f"**{occurrence.strftime('%H:%M')}**.\n\n"
            f"{format_actions(schedule)}\n\n"
            "Este aviso dura 2 minutos. Si no respondes, "
            "el horario seguirá normalmente."
        )

        try:
            view = ScheduleReminderView(
                self.bot,
                schedule,
                execution,
            )
            message = await member.send(text, view=view)
            view.message = message

        except discord.Forbidden:
            logger.warning(
                "No se pudo enviar DM de recordatorio a %s.",
                member,
            )
        except Exception:
            logger.exception(
                "Error enviando recordatorio del schedule %s.",
                schedule["id"],
            )

    async def execute_scheduled(self, execution):
        guild = self.bot.get_guild(execution["guild_id"])

        if guild is None:
            return

        member = guild.get_member(execution["user_id"])

        if (
            member is None
            or member.voice is None
            or member.voice.channel is None
        ):
            logger.info(
                "Schedule %s: el usuario no está en voz; "
                "no se ejecuta.",
                execution["schedule_id"],
            )
            update_execution(
                execution["id"],
                status="skipped",
                executed=1,
                updated_at=datetime.now(TIMEZONE).isoformat(),
            )
            return

        result = await execute_actions(
            member,
            mute=execution["mute"],
            deafen=execution["deafen"],
            disconnect=execution["disconnect"],
        )

        update_execution(
            execution["id"],
            status="executed" if result else "failed",
            executed=1,
            updated_at=datetime.now(TIMEZONE).isoformat(),
        )


def format_actions(row):
    mute, deafen, disconnect = normalize_actions(
        row["mute"],
        row["deafen"],
        row["disconnect"],
    )

    actions = []

    if mute:
        actions.append("🔇 Mute")
    if deafen:
        actions.append("🎧 Deafen")
    if disconnect:
        actions.append("🚪 Disconnect")

    return "\n".join(actions) or "⚠️ Sin acciones"
