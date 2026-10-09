import asyncio
import logging
from datetime import datetime, timedelta
import discord
from config import TIMEZONE, CHECK_SECONDS, SCHEDULE_REMINDER_HOURS
from database import get_schedules, get_execution, create_execution, update_execution, update_schedule

log = logging.getLogger("XZ94.Scheduler")
FINAL = {"executed", "skipped", "failed", "cancelled"}

async def disconnect_member(member):
    if not member or not member.voice or not member.voice.channel:
        return False
    try:
        await member.move_to(None)
        return True
    except (discord.Forbidden, discord.HTTPException):
        log.exception("Discord no pudo desconectar al usuario %s", member.id)
        return False

async def find_member(bot, user_id, guild_id):
    guild = bot.get_guild(int(guild_id))
    if not guild: return None
    member = guild.get_member(int(user_id))
    if member: return member
    try: return await guild.fetch_member(int(user_id))
    except (discord.NotFound, discord.Forbidden, discord.HTTPException): return None

def schedule_target(row):
    try:
        return datetime.fromisoformat(f"{row['date']}T{row['time']}:00").replace(tzinfo=TIMEZONE)
    except (TypeError, ValueError):
        return None

async def safe_delete(bot, channel_id, message_id):
    if not channel_id or not message_id: return
    try:
        channel = bot.get_channel(int(channel_id)) or await bot.fetch_channel(int(channel_id))
        message = await channel.fetch_message(int(message_id))
        await message.delete()
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        pass

class Scheduler:
    def __init__(self, bot):
        self.bot = bot
        self.running = True
        self.reminder_tasks = set()

    async def start(self):
        log.info("Scheduler iniciado.")
        while self.running:
            try: await self.check_schedules()
            except asyncio.CancelledError: raise
            except Exception: log.exception("Error revisando horarios")
            await asyncio.sleep(CHECK_SECONDS)

    async def check_schedules(self):
        now = datetime.now(TIMEZONE)
        for row in get_schedules(enabled_only=True):
            target = schedule_target(row)
            if target is None:
                log.error("Fecha/hora inválida en horario %s", row["id"])
                continue
            execution = get_execution(row["id"], row["date"])
            if execution is None:
                execution = create_execution(row["id"], row["date"], target.isoformat())
            if execution["status"] in FINAL: continue
            current = datetime.fromisoformat(execution["override_datetime"] or execution["original_datetime"])
            remaining = (current - now).total_seconds()
            if remaining > 0:
                reminder_at = current - timedelta(hours=SCHEDULE_REMINDER_HOURS)
                if not execution["reminder_sent"] and now >= reminder_at:
                    sent = await self.send_reminder(row, execution, current)
                    if sent:
                        update_execution(execution["id"], reminder_sent=1, status="waiting")
                continue
            if remaining < -max(120, CHECK_SECONDS * 4):
                result = "skipped"
            else:
                member = await find_member(self.bot, row["user_id"], row["guild_id"])
                result = "executed" if member and await disconnect_member(member) else "skipped"
            update_execution(execution["id"], status=result, confirmed=int(result == "executed"))
            update_schedule(row["id"], enabled=False)
            await safe_delete(self.bot, row["channel_id"], row["message_id"])
            await safe_delete(self.bot, execution["reminder_channel_id"], execution["reminder_message_id"])

    async def send_reminder(self, row, execution, target):
        member = await find_member(self.bot, row["user_id"], row["guild_id"])
        if not member: return False
        try:
            from apibot import ScheduleReminderView
            view = ScheduleReminderView(self.bot, row, execution["id"], target)
            message = await member.send(
                f"🌙 **XZ94**\nTu desconexión está programada para **{target:%d/%m/%Y a las %H:%M}**.\nPuedes ajustar solo este horario.",
                view=view)
            view.message = message
            view.refresh_task = asyncio.create_task(view.refresh_loop())
            update_execution(execution["id"], reminder_channel_id=message.channel.id,
                             reminder_message_id=message.id)
            return True
        except discord.Forbidden:
            log.warning("No se pudo enviar DM al usuario %s", member.id)
            return False
