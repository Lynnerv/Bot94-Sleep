import asyncio
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import discord

from database import get_schedules


logger = logging.getLogger("Bot94Sleep")

TIMEZONE = ZoneInfo("America/Lima")


DAY_NAMES = {
    0: "lunes",
    1: "martes",
    2: "miercoles",
    3: "jueves",
    4: "viernes",
    5: "sabado",
    6: "domingo",
}


class Scheduler:

    def __init__(self, bot):
        self.bot = bot
        self.running = True
        self.executed = set()

    async def start(self):

        logger.info("Scheduler iniciado.")

        while self.running:

            try:
                await self.check_schedules()
            except Exception:
                logger.exception("Error revisando horarios.")

            await asyncio.sleep(15)

    async def check_schedules(self):

        now = datetime.now(TIMEZONE)

        current_day = DAY_NAMES[now.weekday()]
        current_time = now.strftime("%H:%M")

        schedules = get_schedules()

        for schedule in schedules:

            days = [
                day.strip().lower()
                for day in schedule["days"].split(",")
            ]

            if current_day not in days:
                continue

            if schedule["time"] != current_time:
                continue

            execution_key = (
                schedule["id"],
                now.strftime("%Y-%m-%d"),
                current_time
            )

            if execution_key in self.executed:
                continue

            self.executed.add(execution_key)

            await self.process_schedule(schedule)

            # Evitar que el conjunto crezca indefinidamente
            if len(self.executed) > 1000:
                self.executed.clear()

    async def process_schedule(self, schedule):

        guild = self.bot.get_guild(schedule["guild_id"])

        if guild is None:
            logger.warning(
                f"Servidor {schedule['guild_id']} no encontrado."
            )
            return

        member = guild.get_member(schedule["user_id"])

        if member is None:
            logger.warning(
                f"Usuario {schedule['user_id']} no encontrado."
            )
            return

        if member.voice is None or member.voice.channel is None:

            logger.info(
                f"[Horario {schedule['id']}] "
                f"{member} no está conectado a voz. "
                f"No se ejecuta ninguna acción."
            )

            return

        logger.info(
            f"[Horario {schedule['id']}] "
            f"{member} está en "
            f"{member.voice.channel.name}."
        )

        if schedule["confirmation"]:

            from bot import ConfirmationView

            view = ConfirmationView(
                self.bot,
                member,
                schedule
            )

            try:
                await member.send(
                    content=(
                        "🌙 **Bot94 Sleep**\n\n"
                        f"Tu horario está listo para ejecutarse.\n\n"
                        f"Servidor: **{guild.name}**\n"
                        f"Canal: **{member.voice.channel.name}**\n\n"
                        f"{view.action_text()}"
                    ),
                    view=view
                )

            except discord.Forbidden:

                logger.warning(
                    f"No se pudo enviar DM a {member}."
                )

                # Si los DMs están cerrados, no ejecutamos
                # automáticamente cuando se requiere confirmación.

            return

        await execute_actions(
            member,
            schedule["mute"],
            schedule["deafen"],
            schedule["disconnect"]
        )


async def execute_actions(
    member,
    mute=False,
    deafen=False,
    disconnect=False
):

    if member.voice is None or member.voice.channel is None:

        logger.info(
            f"{member} ya no está conectado a voz."
        )

        return False

    try:

        if mute or deafen:

            await member.edit(
                mute=True if mute else None,
                deafen=True if deafen else None
            )

            logger.info(
                f"Acciones aplicadas a {member}: "
                f"mute={mute}, deafen={deafen}"
            )

        if disconnect:

            await member.move_to(None)

            logger.info(
                f"{member} fue desconectado."
            )

        return True

    except discord.Forbidden:

        logger.exception(
            "El bot no tiene permisos suficientes."
        )

        return False

    except discord.HTTPException:

        logger.exception(
            "Discord rechazó la acción."
        )

        return False