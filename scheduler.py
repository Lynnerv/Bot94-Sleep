import asyncio
import logging
from datetime import datetime, timedelta

import discord

from config import TIMEZONE, CHECK_SECONDS, SCHEDULE_REMINDER_HOURS
from database import get_schedules, get_execution, create_execution, update_execution, update_schedule

log = logging.getLogger("XZ94.Scheduler")


async def disconnect_member(member):
    if not member or not member.voice or not member.voice.channel:
        return False
    try:
        await member.move_to(None)
        return True
    except (discord.Forbidden, discord.HTTPException):
        log.exception("Discord rechazó la desconexión de %s", member.id)
        return False


async def find_member(bot, user_id, guild_id):
    guild = bot.get_guild(int(guild_id))
    if not guild:
        return None
    member = guild.get_member(int(user_id))
    if member:
        return member
    try:
        return await guild.fetch_member(int(user_id))
    except Exception:
        return None


def schedule_target(row):
    try:
        return datetime.fromisoformat(f"{row['date']}T{row['time']}:00").replace(tzinfo=TIMEZONE)
    except (TypeError, ValueError):
        return None


async def delete_message_by_ids(bot, channel_id, message_id):
    if not channel_id or not message_id:
        return
    try:
        channel = bot.get_channel(int(channel_id)) or await bot.fetch_channel(int(channel_id))
        message = await channel.fetch_message(int(message_id))
        await message.delete()
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        pass
    except Exception:
        log.exception("No se pudo eliminar el mensaje %s", message_id)


async def edit_message_by_ids(bot, channel_id, message_id, content=None, embed=None):
    if not channel_id or not message_id:
        return False
    try:
        channel = bot.get_channel(int(channel_id)) or await bot.fetch_channel(int(channel_id))
        message = await channel.fetch_message(int(message_id))
        await message.edit(content=content, embed=embed)
        return True
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        return False
    except Exception:
        log.exception("No se pudo actualizar el mensaje %s", message_id)
        return False


class Scheduler:
    def __init__(self, bot):
        self.bot = bot
        self.running = True
        self.countdown_tasks = {}

    async def start(self):
        log.info("Scheduler iniciado")
        while self.running:
            try:
                await self.check_schedules()
            except Exception:
                log.exception("Error revisando horarios")
            await asyncio.sleep(CHECK_SECONDS)

    def start_countdown(self, row, execution):
        execution_id = int(execution["id"])
        task = self.countdown_tasks.get(execution_id)
        if task and not task.done():
            return
        self.countdown_tasks[execution_id] = asyncio.create_task(
            self._countdown_message(row["id"], execution_id)
        )

    async def _countdown_message(self, schedule_id, execution_id):
        try:
            while True:
                row = next((r for r in get_schedules(enabled_only=True) if int(r["id"]) == int(schedule_id)), None)
                execution = None
                if row:
                    execution = get_execution(schedule_id, row["date"])
                if not row or not execution:
                    return
                if execution["status"] in {"executed", "skipped", "failed", "cancelled"}:
                    return

                current_target = datetime.fromisoformat(
                    execution["override_datetime"] or execution["original_datetime"]
                )
                remaining = (current_target - datetime.now(TIMEZONE)).total_seconds()
                if remaining <= 0:
                    return
                if remaining > 60:
                    await asyncio.sleep(min(15, max(1, remaining - 60)))
                    continue

                unix_time = int(current_target.timestamp())
                embed = discord.Embed(
                    title="🌙 Horario de Sueño",
                    description=(
                        f"📅 **{current_target:%d/%m/%Y}**\n"
                        f"⏰ **{current_target:%H:%M}**\n\n"
                        f"⏱️ **Estado del Horario:** 🟢 Termina en <t:{unix_time}:R> ({current_target:%H:%M})"
                    ),
                    color=discord.Color.from_rgb(229, 211, 173),
                )
                await edit_message_by_ids(self.bot, row["channel_id"], row["message_id"], embed=embed)
                # Discord actualiza el timestamp relativo cada segundo en el cliente.
                return
        except asyncio.CancelledError:
            return
        except Exception:
            log.exception("Error en cuenta regresiva del horario %s", schedule_id)
        finally:
            self.countdown_tasks.pop(int(execution_id), None)

    async def check_schedules(self):
        now = datetime.now(TIMEZONE)
        for row in get_schedules(enabled_only=True):
            target = schedule_target(row)
            if target is None:
                log.warning("Horario %s tiene fecha/hora inválida", row["id"])
                continue

            execution_date = row["date"]
            execution = get_execution(row["id"], execution_date)
            if execution is None:
                execution = create_execution(row["id"], execution_date, target.isoformat())

            if execution["status"] in {"executed", "skipped", "failed", "cancelled"}:
                continue

            current_target = datetime.fromisoformat(
                execution["override_datetime"] or execution["original_datetime"]
            )
            remaining = (current_target - now).total_seconds()

            # Nunca ejecutamos un horario que quedó atrasado por más de la ventana
            # de tolerancia. Si acaba de vencer (por ejemplo, el bot revisó cada
            # 15 segundos), sí se ejecuta normalmente.
            if remaining <= 0:
                late_seconds = abs(remaining)
                if late_seconds > max(120, CHECK_SECONDS * 4):
                    await delete_message_by_ids(self.bot, row["channel_id"], row["message_id"])
                    await delete_message_by_ids(
                        self.bot,
                        execution["reminder_channel_id"],
                        execution["reminder_message_id"],
                    )
                    update_execution(execution["id"], status="skipped", confirmed=0)
                    update_schedule(row["id"], enabled=False)
                    task = self.countdown_tasks.pop(int(execution["id"]), None)
                    if task and not task.done():
                        task.cancel()
                    continue

                member = await find_member(self.bot, row["user_id"], row["guild_id"])
                success = await disconnect_member(member) if member and member.voice else False
                result = "executed" if success else "skipped"
                await delete_message_by_ids(self.bot, row["channel_id"], row["message_id"])
                await delete_message_by_ids(
                    self.bot,
                    execution["reminder_channel_id"],
                    execution["reminder_message_id"],
                )
                update_execution(execution["id"], status=result, confirmed=int(success))
                update_schedule(row["id"], enabled=False)
                task = self.countdown_tasks.pop(int(execution["id"]), None)
                if task and not task.done():
                    task.cancel()
                continue

            # Durante el último minuto, el mensaje del horario muestra segundos restantes.
            if 0 < remaining <= 60:
                self.start_countdown(row, execution)

            reminder_time = current_target - timedelta(hours=SCHEDULE_REMINDER_HOURS)
            if not execution["reminder_sent"] and reminder_time <= now < current_target:
                sent = await self.send_reminder(row, execution, current_target)
                if sent:
                    update_execution(execution["id"], reminder_sent=1, status="waiting")
                continue

            if now >= current_target:
                member = await find_member(self.bot, row["user_id"], row["guild_id"])
                success = await disconnect_member(member) if member and member.voice else False
                result = "executed" if success else "skipped"

                # El mensaje principal del horario y el recordatorio DM se eliminan al ejecutar.
                await delete_message_by_ids(self.bot, row["channel_id"], row["message_id"])
                await delete_message_by_ids(
                    self.bot,
                    execution["reminder_channel_id"],
                    execution["reminder_message_id"],
                )
                update_execution(execution["id"], status=result, confirmed=int(success))
                update_schedule(row["id"], enabled=False)

                task = self.countdown_tasks.pop(int(execution["id"]), None)
                if task and not task.done():
                    task.cancel()

    async def send_reminder(self, row, execution, target):
        member = await find_member(self.bot, row["user_id"], row["guild_id"])
        if not member:
            return False
        from apibot import ScheduleReminderView
        try:
            view = ScheduleReminderView(self.bot, row, execution["id"], target)
            message = await member.send(
                "🌙 **XZ94**\n\n"
                f"Tu desconexión está programada para el **{target:%d/%m/%Y a las %H:%M}**.\n\n"
                "Puedes cambiar solamente esta fecha/hora.",
                view=view,
            )
            view.message = message
            view.refresh_task = asyncio.create_task(view.refresh_loop())
            update_execution(
                execution["id"],
                reminder_channel_id=message.channel.id,
                reminder_message_id=message.id,
            )
            return True
        except discord.Forbidden:
            log.warning("No pude enviar DM a %s", member.id)
            return False
