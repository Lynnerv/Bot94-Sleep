import asyncio
import calendar
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path

import discord
from discord import app_commands
from dotenv import load_dotenv

from config import TIMEZONE
from database import (
    create_execution,
    create_schedule,
    delete_schedule,
    get_schedules,
    update_execution,
    update_schedule,
)
from scheduler import Scheduler, disconnect_member
from api import start_control_api

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    raise RuntimeError("No se encontró DISCORD_TOKEN en el archivo .env")

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("XZ94")

intents = discord.Intents.default()
intents.voice_states = True
intents.members = True

bot = discord.Client(intents=intents)
bot.tree = app_commands.CommandTree(bot)

scheduler = None
api_runner = None
active_sleeps = {}


# -------------------------
# Sleep
# -------------------------

def sleep_key(user_id, guild_id):
    return f"{int(guild_id)}:{int(user_id)}"


class SleepConfigView(discord.ui.View):
    def __init__(self, bot_instance):
        super().__init__(timeout=900)
        self.bot = bot_instance

    @discord.ui.select(
        placeholder="🌙 Configurar un temporizador de sueño",
        options=[
            discord.SelectOption(label="15 minutos", value="15"),
            discord.SelectOption(label="30 minutos", value="30"),
            discord.SelectOption(label="45 minutos", value="45"),
            discord.SelectOption(label="1 hora", value="60"),
            discord.SelectOption(label="1 hora 15 minutos", value="75"),
            discord.SelectOption(label="1 hora 30 minutos", value="90"),
            discord.SelectOption(label="Ingresar duración", value="custom"),
        ],
    )
    async def preset(self, interaction: discord.Interaction, select: discord.ui.Select):
        value = select.values[0]
        if value == "custom":
            await interaction.response.send_modal(CustomSleepModal(self.bot, interaction.message))
            return
        await start_sleep_message(interaction, int(value), interaction.message)


class CustomSleepModal(discord.ui.Modal, title="🌙 Temporizador personalizado"):
    minutes = discord.ui.TextInput(
        label="Minutos",
        placeholder="Ej. 20",
        min_length=1,
        max_length=4,
    )

    def __init__(self, bot_instance, parent_message=None):
        super().__init__()
        self.bot = bot_instance
        self.parent_message = parent_message

    async def on_submit(self, interaction):
        try:
            value = int(self.minutes.value.strip())
        except ValueError:
            await interaction.response.send_message("❌ Introduce un número de minutos.", ephemeral=True)
            return
        if not 1 <= value <= 1440:
            await interaction.response.send_message("❌ Usa entre 1 y 1440 minutos.", ephemeral=True)
            return
        await start_sleep_message(interaction, value, self.parent_message)


class SleepActiveView(discord.ui.View):
    def __init__(self, bot_instance, user_id, guild_id):
        super().__init__(timeout=None)
        self.bot = bot_instance
        self.user_id = user_id
        self.guild_id = guild_id
        self.message = None
        self.refresh_task = None

    def build_embed(self, record):
        remaining = max(0, int((record["ends_at"] - datetime.now(TIMEZONE)).total_seconds()))
        unix_time = int(record["ends_at"].timestamp())
        ends = record["ends_at"].strftime("%H:%M")

        # Discord actualiza <t:...:R> en el cliente de forma automática,
        # incluso segundo a segundo durante el último minuto.
        if remaining <= 60:
            status_text = f"Termina en <t:{unix_time}:R> ({ends})"
        else:
            status_text = f"Termina en <t:{unix_time}:R> ({ends})"

        embed = discord.Embed(
            title="🌙 Temporizador de Sueño",
            color=discord.Color.from_rgb(229, 211, 173),
            description=(
                "El temporizador te desconecta del canal de voz cuando termina la cuenta regresiva, "
                "para que puedas descansar sin preocuparte por tu batería.\n\n"
                f"⏱️ **Estado del Temporizador:** 🟢 {status_text}"
            ),
        )
        return embed

    async def update_message(self, record):
        if not self.message:
            return
        remaining = max(0, int((record["ends_at"] - datetime.now(TIMEZONE)).total_seconds()))
        for item in self.children:
            if getattr(item, "custom_id", None) == "xz94_sleep_reduce":
                item.disabled = remaining <= 5 * 60
        try:
            await self.message.edit(embed=self.build_embed(record), content=None, view=self)
        except discord.NotFound:
            self.stop()
        except discord.HTTPException:
            pass

    @discord.ui.button(label="➖ Reducir", style=discord.ButtonStyle.secondary, custom_id="xz94_sleep_reduce")
    async def reduce(self, interaction, button):
        record = active_sleeps.get(sleep_key(self.user_id, self.guild_id))
        if not record or record["status"] != "active":
            await interaction.response.send_message("No hay un temporizador activo.", ephemeral=True)
            return
        now = datetime.now(TIMEZONE)
        # Nunca deja el temporizador en menos de 1 minuto.
        record["ends_at"] = max(now + timedelta(minutes=5), record["ends_at"] - timedelta(minutes=5))
        await interaction.response.defer()
        await self.update_message(record)

    @discord.ui.button(label="➕ Extender", style=discord.ButtonStyle.secondary, custom_id="xz94_sleep_extend")
    async def extend(self, interaction, button):
        record = active_sleeps.get(sleep_key(self.user_id, self.guild_id))
        if not record or record["status"] != "active":
            await interaction.response.send_message("No hay un temporizador activo.", ephemeral=True)
            return
        record["ends_at"] = min(
            record["ends_at"] + timedelta(minutes=5),
            datetime.now(TIMEZONE) + timedelta(hours=24),
        )
        await interaction.response.defer()
        await self.update_message(record)

    @discord.ui.button(label="❌ Cancelar temporizador", style=discord.ButtonStyle.secondary, custom_id="xz94_sleep_cancel")
    async def cancel(self, interaction, button):
        record = active_sleeps.get(sleep_key(self.user_id, self.guild_id))
        if record:
            record["status"] = "cancelled"
            if record.get("task") and not record["task"].done():
                record["task"].cancel()
        await interaction.response.edit_message(
            embed=discord.Embed(
                title="🌙 Temporizador de Sueño",
                color=discord.Color.from_rgb(229, 211, 173),
                description=(
                    "El temporizador te desconecta del canal de voz cuando termina la cuenta regresiva, "
                    "para que puedas descansar sin preocuparte por tu batería.\n\n"
                    "⏱️ **Estado del Temporizador:** 🟡 **Inactivo**"
                ),
            ),
            view=SleepConfigView(self.bot),
        )

    async def refresh_loop(self):
        # No editamos cada segundo: el timestamp relativo de Discord se actualiza
        # automáticamente en la interfaz del usuario. Solo reeditamos al entrar
        # en el último minuto para asegurar que el cliente reciba el estado actual.
        while True:
            record = active_sleeps.get(sleep_key(self.user_id, self.guild_id))
            if not record or record["status"] != "active":
                return
            remaining = (record["ends_at"] - datetime.now(TIMEZONE)).total_seconds()
            if remaining <= 60:
                await self.update_message(record)
                return
            await asyncio.sleep(max(0.5, remaining - 60))


async def start_sleep_message(interaction, minutes, parent_message=None):
    if not interaction.guild:
        await interaction.response.send_message("❌ Este comando solo funciona dentro de un servidor.", ephemeral=True)
        return
    member = interaction.guild.get_member(interaction.user.id)
    if not member or not member.voice:
        await interaction.response.send_message("❌ Primero entra a un canal de voz.", ephemeral=True)
        return

    existing = active_sleeps.get(sleep_key(member.id, interaction.guild.id))
    if existing and existing["status"] == "active":
        await interaction.response.send_message("Ya tienes un temporizador activo.", ephemeral=True)
        return

    # Reutilizamos el mismo mensaje de interacción. La respuesta inicial de /sleep
    # es efímera, por lo que Discord muestra automáticamente “Solo tú puedes verlo”.
    message = parent_message or getattr(interaction, "message", None)
    if message is None:
        await interaction.response.send_message(
            embed=discord.Embed(
                title="🌙 Temporizador de Sueño",
                color=discord.Color.from_rgb(229, 211, 173),
                description=(
                    "El temporizador te desconecta del canal de voz cuando termina la cuenta regresiva, "
                    "para que puedas descansar sin preocuparte por tu batería.\n\n"
                    "⏱️ **Estado del Temporizador:** 🟡 **Inactivo**"
                ),
            ),
            view=SleepConfigView(bot),
            ephemeral=True,
        )
        message = await interaction.original_response()
    else:
        # La interacción del selector/modal debe quedar respondida antes de editar
        # el mensaje efímero. Usamos defer aquí y luego editamos explícitamente
        # el mismo mensaje con la vista activa.
        if not interaction.response.is_done():
            await interaction.response.defer()

    record = {
        "user_id": member.id,
        "guild_id": interaction.guild.id,
        "status": "active",
        "ends_at": datetime.now(TIMEZONE) + timedelta(minutes=minutes),
        "task": None,
        "error": None,
        "channel_id": getattr(message.channel, "id", None),
        "message_id": getattr(message, "id", None),
        "message": message,
        "member_id": member.id,
        "view": None,
    }
    active_sleeps[sleep_key(member.id, interaction.guild.id)] = record
    view = SleepActiveView(bot, member.id, interaction.guild.id)
    view.message = message
    record["view"] = view
    # Adjuntar explícitamente la vista activa al mismo mensaje.
    # Esto evita que Discord conserve la vista anterior del selector.
    try:
        edited = await message.edit(embed=view.build_embed(record), content=None, view=view)
        view.message = edited
        record["message"] = edited
    except discord.NotFound:
        return
    except discord.HTTPException as exc:
        log.exception("No se pudo actualizar el temporizador con sus botones: %s", exc)
        return

    from api import run_sleep
    record["task"] = asyncio.create_task(run_sleep(bot, record))
    view.refresh_task = asyncio.create_task(view.refresh_loop())


# -------------------------
# Schedule / calendar
# -------------------------
MONTHS = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]
WEEKDAYS = ["L", "M", "X", "J", "V", "S", "D"]


def fmt_date(date_value):
    return f"{date_value.day} de {MONTHS[date_value.month - 1]} de {date_value.year}"


def parse_time(value):
    try:
        h, m = map(int, value.strip().split(":"))
        if 0 <= h <= 23 and 0 <= m <= 59:
            return f"{h:02d}:{m:02d}"
    except (TypeError, ValueError):
        pass
    return None


def schedule_datetime(date_value, time_value):
    return datetime.fromisoformat(f"{date_value.isoformat()}T{time_value}:00").replace(tzinfo=TIMEZONE)


class ScheduleCreateView(discord.ui.View):
    def __init__(self, user_id, guild_id, selected_date=None, month_date=None, selected_time=None, day_page=0):
        super().__init__(timeout=900)
        now = datetime.now(TIMEZONE)
        self.user_id = int(user_id)
        self.guild_id = int(guild_id)
        self.selected_date = selected_date
        self.month_date = (month_date or now).replace(day=1)
        self.selected_time = selected_time
        self.day_page = day_page
        self.build()

    def build(self):
        self.clear_items()
        year, month = self.month_date.year, self.month_date.month
        days_in_month = calendar.monthrange(year, month)[1]
        start_day = 1 if self.day_page == 0 else 21
        end_day = min(20, days_in_month) if self.day_page == 0 else days_in_month

        for index, day in enumerate(range(start_day, end_day + 1)):
            date_value = self.month_date.replace(day=day).date()
            label = f"{day} {WEEKDAYS[date_value.weekday()]}"
            today = datetime.now(TIMEZONE).date()
            is_past = date_value < today
            button = discord.ui.Button(
                label=label,
                style=discord.ButtonStyle.secondary,
                row=index // 5,
                disabled=is_past,
            )
            button.callback = self.make_day_callback(date_value)
            self.add_item(button)

        # En Discord una View tiene como máximo 25 componentes. Mantener el
        # calendario en botones permite conservar el estilo anterior; los días
        # 21-31 se muestran en la misma interfaz mediante el botón de página.
        nav_row = 4
        now_month = datetime.now(TIMEZONE).replace(day=1).date()
        current_month = self.month_date.date()
        prev_button = discord.ui.Button(
            label="◀ Mes anterior",
            style=discord.ButtonStyle.secondary,
            row=nav_row,
            disabled=current_month <= now_month,
        )
        next_button = discord.ui.Button(label="Mes siguiente ▶", style=discord.ButtonStyle.secondary, row=nav_row)
        prev_button.callback = self.previous_month
        next_button.callback = self.next_month
        self.add_item(prev_button)
        self.add_item(next_button)

        if days_in_month > 20:
            more_label = "Días 21–31 ▶" if self.day_page == 0 else "◀ Días 1–20"
            more_button = discord.ui.Button(label=more_label, style=discord.ButtonStyle.secondary, row=nav_row)
            more_button.callback = self.toggle_day_page
            self.add_item(more_button)

        time_button = discord.ui.Button(label=f"🕐 {self.selected_time or 'Elegir hora'}", style=discord.ButtonStyle.secondary, row=4)
        time_button.callback = self.choose_time
        self.add_item(time_button)

        save_button = discord.ui.Button(label="🌙 Guardar horario", style=discord.ButtonStyle.secondary, row=4)
        save_button.callback = self.save
        self.add_item(save_button)

    def make_day_callback(self, date_value):
        async def callback(interaction):
            if interaction.user.id != self.user_id:
                await interaction.response.send_message("Este calendario no es para ti.", ephemeral=True)
                return
            if date_value < datetime.now(TIMEZONE).date():
                await interaction.response.send_message("❌ No puedes seleccionar una fecha anterior a hoy.", ephemeral=True)
                return
            self.selected_date = date_value
            self.build()
            await interaction.response.edit_message(embed=self.embed(), view=self)
        return callback

    async def toggle_day_page(self, interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Este calendario no es para ti.", ephemeral=True)
            return
        self.day_page = 1 - self.day_page
        self.build()
        await interaction.response.edit_message(embed=self.embed(), view=self)

    async def previous_month(self, interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Este calendario no es para ti.", ephemeral=True)
            return
        now_month = datetime.now(TIMEZONE).replace(day=1).date()
        if self.month_date.date() <= now_month:
            await interaction.response.send_message("❌ No puedes navegar a meses anteriores al actual.", ephemeral=True)
            return
        year, month = self.month_date.year, self.month_date.month - 1
        if month == 0:
            year, month = year - 1, 12
        self.month_date = self.month_date.replace(year=year, month=month, day=1)
        self.day_page = 0
        if self.selected_date and self.selected_date < datetime.now(TIMEZONE).date():
            self.selected_date = None
        self.build()
        await interaction.response.edit_message(embed=self.embed(), view=self)

    async def next_month(self, interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Este calendario no es para ti.", ephemeral=True)
            return
        year, month = self.month_date.year, self.month_date.month + 1
        if month == 13:
            year, month = year + 1, 1
        self.month_date = self.month_date.replace(year=year, month=month, day=1)
        self.day_page = 0
        self.build()
        await interaction.response.edit_message(embed=self.embed(), view=self)

    async def choose_time(self, interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Este calendario no es para ti.", ephemeral=True)
            return
        await interaction.response.send_modal(ScheduleTimeModal(self))

    async def save(self, interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Este calendario no es para ti.", ephemeral=True)
            return
        if not self.selected_date:
            await interaction.response.send_message("❌ Primero selecciona una fecha.", ephemeral=True)
            return
        if not self.selected_time:
            await interaction.response.send_message("❌ Primero selecciona una hora.", ephemeral=True)
            return
        target = schedule_datetime(self.selected_date, self.selected_time)
        if target <= datetime.now(TIMEZONE):
            await interaction.response.send_message("❌ La fecha y hora deben ser futuras.", ephemeral=True)
            return

        sid = create_schedule(self.user_id, self.guild_id, self.selected_date.isoformat(), self.selected_time)
        create_execution(sid, self.selected_date.isoformat(), target.isoformat())
        await interaction.response.edit_message(embed=discord.Embed(
            title="🌙 Horario de Sueño",
            description=(
                f"📅 **{fmt_date(self.selected_date)}**\n"
                f"⏰ **{self.selected_time}**\n\n"
                "⏱️ **Estado del Horario:** 🟢 **Activo**"
            ),
            color=discord.Color.from_rgb(229, 211, 173),
        ), view=None)
        try:
            message = await interaction.original_response()
            update_schedule(sid, channel_id=message.channel.id, message_id=message.id)
        except Exception:
            pass
        self.stop()

    def embed(self):
        selected = fmt_date(self.selected_date) if self.selected_date else "Sin fecha seleccionada"
        selected_time = self.selected_time or "Sin hora seleccionada"
        page = "1–20" if self.day_page == 0 else "21–31"
        return discord.Embed(
            title="🌙 Configurar horario",
            description=(
                f"📅 **{MONTHS[self.month_date.month - 1].capitalize()} {self.month_date.year}** · días {page}\n"
                "`L  M  X  J  V  S  D`\n\n"
                f"📅 **Fecha:** {selected}\n"
                f"⏰ **Hora:** {selected_time}\n\n"
                "El horario solo se ejecutará en la fecha que elijas."
            ),
            color=discord.Color.from_rgb(229, 211, 173),
        )


class ScheduleTimeModal(discord.ui.Modal, title="🕐 Elegir hora"):
    time = discord.ui.TextInput(label="Hora (HH:MM)", placeholder="23:59", max_length=5)

    def __init__(self, calendar_view):
        super().__init__()
        self.calendar_view = calendar_view
        if calendar_view.selected_time:
            self.time.default = calendar_view.selected_time

    async def on_submit(self, interaction):
        value = parse_time(self.time.value)
        if not value:
            await interaction.response.send_message("❌ Hora inválida. Usa HH:MM.", ephemeral=True)
            return
        self.calendar_view.selected_time = value
        self.calendar_view.build()
        await interaction.response.edit_message(embed=self.calendar_view.embed(), view=self.calendar_view)


class SchedulePanelView(discord.ui.View):
    def __init__(self, user_id, guild_id):
        super().__init__(timeout=900)
        self.user_id = int(user_id)
        self.guild_id = int(guild_id)

    @discord.ui.button(label="🌙 Configurar un horario", style=discord.ButtonStyle.primary)
    async def create(self, interaction, button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Este panel no es para ti.", ephemeral=True)
            return
        view = ScheduleCreateView(self.user_id, self.guild_id)
        await interaction.response.edit_message(embed=view.embed(), view=view)

    @discord.ui.button(label="❌ Eliminar horario", style=discord.ButtonStyle.danger)
    async def delete(self, interaction, button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Este panel no es para ti.", ephemeral=True)
            return
        await interaction.response.send_modal(DeleteScheduleModal(self.user_id))


class DeleteScheduleModal(discord.ui.Modal, title="❌ Eliminar horario"):
    schedule_id = discord.ui.TextInput(label="ID del horario", placeholder="2", max_length=10)

    def __init__(self, user_id):
        super().__init__()
        self.user_id = int(user_id)

    async def on_submit(self, interaction):
        try:
            sid = int(self.schedule_id.value)
        except ValueError:
            await interaction.response.send_message("❌ ID inválido.", ephemeral=True)
            return
        if delete_schedule(sid, self.user_id):
            await interaction.response.send_message("❌ Horario eliminado.", ephemeral=True)
        else:
            await interaction.response.send_message("❌ No encontré ese horario.", ephemeral=True)


class ScheduleReminderView(discord.ui.View):
    def __init__(self, bot_instance, row, execution_id, target):
        super().__init__(timeout=120)
        self.bot = bot_instance
        self.row = row
        self.execution_id = execution_id
        self.target = target
        self.message = None
        self.refresh_task = None

    def countdown_text(self):
        remaining = max(0, int((self.target - datetime.now(TIMEZONE)).total_seconds()))
        if remaining <= 60:
            return f"⏱️ **Faltan {remaining} segundos** para la desconexión."
        return f"⏰ Desconexión programada para **{self.target:%d/%m/%Y a las %H:%M}**."

    async def refresh_loop(self):
        try:
            while True:
                remaining = (self.target - datetime.now(TIMEZONE)).total_seconds()
                if remaining <= 0:
                    return
                if remaining <= 60 and self.message:
                    unix_time = int(self.target.timestamp())
                    try:
                        await self.message.edit(
                            content=(
                                "🌙 **XZ94**\n\n"
                                f"⏱️ **Termina en <t:{unix_time}:R> ({self.target:%H:%M})**\n\n"
                                "Puedes cambiar solamente esta fecha/hora."
                            ),
                            view=self,
                        )
                    except discord.NotFound:
                        return
                    return
                await asyncio.sleep(min(15, max(1, remaining - 60)))
        except asyncio.CancelledError:
            return

    async def check(self, interaction):
        if interaction.user.id != int(self.row["user_id"]):
            await interaction.response.send_message("Este aviso no es para ti.", ephemeral=True)
            return False
        return True

    async def adjust(self, interaction, delta):
        if not await self.check(interaction):
            return
        new = self.target + timedelta(minutes=delta)
        if new <= datetime.now(TIMEZONE):
            await interaction.response.send_message("❌ Esa hora ya pasó.", ephemeral=True)
            return
        self.target = new
        update_execution(self.execution_id, override_datetime=new.isoformat(), confirmed=1, status="pending")
        await interaction.response.edit_message(
            content=(
                "🌙 **XZ94**\n\n"
                f"La desconexión quedó para **{new:%d/%m/%Y a las %H:%M}**.\n\n"
                "Este cambio solo afecta este horario."
            ),
            view=None,
        )
        if self.refresh_task and not self.refresh_task.done():
            self.refresh_task.cancel()
        self.stop()

    @discord.ui.button(label="-5", style=discord.ButtonStyle.secondary)
    async def m5(self, i, b): await self.adjust(i, -5)

    @discord.ui.button(label="-15", style=discord.ButtonStyle.secondary)
    async def m15(self, i, b): await self.adjust(i, -15)

    @discord.ui.button(label="-30", style=discord.ButtonStyle.secondary)
    async def m30(self, i, b): await self.adjust(i, -30)

    @discord.ui.button(label="-60", style=discord.ButtonStyle.secondary)
    async def m60(self, i, b): await self.adjust(i, -60)

    @discord.ui.button(label="+5", style=discord.ButtonStyle.primary, row=1)
    async def p5(self, i, b): await self.adjust(i, 5)

    @discord.ui.button(label="+15", style=discord.ButtonStyle.primary, row=1)
    async def p15(self, i, b): await self.adjust(i, 15)

    @discord.ui.button(label="+30", style=discord.ButtonStyle.primary, row=1)
    async def p30(self, i, b): await self.adjust(i, 30)

    @discord.ui.button(label="+60", style=discord.ButtonStyle.primary, row=1)
    async def p60(self, i, b): await self.adjust(i, 60)

    @discord.ui.button(label="Hora específica", style=discord.ButtonStyle.secondary, row=2)
    async def specific(self, i, b):
        if await self.check(i):
            await i.response.send_modal(SpecificTimeModal(self))

    @discord.ui.button(label="Mantener", style=discord.ButtonStyle.success, row=2)
    async def keep(self, i, b):
        if await self.check(i):
            update_execution(self.execution_id, confirmed=1, status="pending")
            if self.refresh_task and not self.refresh_task.done():
                self.refresh_task.cancel()
            await i.response.edit_message(
                content=f"🌙 Se mantiene la desconexión para **{self.target:%d/%m/%Y a las %H:%M}**.",
                view=None,
            )
            self.stop()


class SpecificTimeModal(discord.ui.Modal, title="🕐 Hora específica"):
    time = discord.ui.TextInput(label="Nueva hora (HH:MM)", placeholder="23:59", max_length=5)

    def __init__(self, reminder_view):
        super().__init__()
        self.reminder_view = reminder_view

    async def on_submit(self, interaction):
        value = parse_time(self.time.value)
        if not value:
            await interaction.response.send_message("❌ Hora inválida.", ephemeral=True)
            return
        base = self.reminder_view.target
        new = base.replace(hour=int(value[:2]), minute=int(value[3:]), second=0, microsecond=0)
        if new <= datetime.now(TIMEZONE):
            await interaction.response.send_message("❌ Esa hora ya pasó.", ephemeral=True)
            return
        self.reminder_view.target = new
        update_execution(self.reminder_view.execution_id, override_datetime=new.isoformat(), confirmed=1, status="pending")
        await interaction.response.edit_message(
            content=f"🌙 La desconexión quedó para **{new:%d/%m/%Y a las %H:%M}**.",
            view=None,
        )
        self.reminder_view.stop()


# -------------------------
# Discord events / commands
# -------------------------
@bot.event
async def on_ready():
    global scheduler, api_runner
    log.info("XZ94 conectado como %s", bot.user)
    log.info("Bot ID: %s", bot.user.id)
    try:
        synced = await bot.tree.sync()
        log.info("Comandos sincronizados: %s", len(synced))
    except Exception:
        log.exception("Error sincronizando comandos")
    if scheduler is None:
        scheduler = Scheduler(bot)
        asyncio.create_task(scheduler.start())
    if api_runner is None:
        api_runner = await start_control_api(bot)
    log.info("API local lista en http://127.0.0.1:8765")


@bot.tree.command(name="ping", description="Comprueba XZ94.")
async def ping(interaction):
    await interaction.response.send_message("🏓 Pong! XZ94 está funcionando.")


@bot.tree.command(name="sleep", description="Abre el temporizador de sueño.")
async def sleep(interaction):
    member = interaction.guild.get_member(interaction.user.id) if interaction.guild else None
    if not member or not member.voice:
        await interaction.response.send_message("❌ Primero entra a un canal de voz.", ephemeral=True)
        return
    await interaction.response.send_message(
        embed=discord.Embed(
            title="🌙 Temporizador de Sueño",
            description=(
                "El temporizador te desconecta del canal de voz cuando termina la cuenta regresiva, "
                "para que puedas descansar sin preocuparte por tu batería.\n\n"
                "⏱️ **Estado del Temporizador:** 🟡 **Inactivo**"
            ),
        ),
        view=SleepConfigView(bot),
        ephemeral=True,
    )


@bot.tree.command(name="schedule", description="Configura una fecha para tu desconexión.")
async def schedule(interaction):
    if not interaction.guild:
        await interaction.response.send_message("❌ Este comando solo funciona dentro de un servidor.", ephemeral=True)
        return
    rows = get_schedules(interaction.user.id, interaction.guild.id, enabled_only=True)
    lines = [
        "🌙 **Horarios de Sueño**",
        "",
        "Configura una fecha y hora para que XZ94 te desconecte automáticamente.",
        "",
    ]
    if rows:
        for row in rows:
            date_value = datetime.fromisoformat(row["date"]).date()
            lines += [
                f"📅 **{fmt_date(date_value)}**",
                f"⏰ **{row['time']}**",
                "🟢 **Activo**",
                f"ID: `{row['id']}`",
                "",
            ]
    else:
        lines += ["No tienes horarios configurados.", ""]
    schedule_text = "\n".join(lines[4:]) if rows else "No tienes horarios configurados."
    embed = discord.Embed(
        title="🌙 Horario de Sueño",
        description=(
            "El horario te desconecta del canal de voz en la fecha y hora que elijas.\n\n"
            "⏱️ **Estado del Horario:** 🟡 **Inactivo**\n\n"
            + schedule_text
        ),
        color=discord.Color.from_rgb(229, 211, 173),
    )
    await interaction.response.send_message(
        embed=embed,
        view=SchedulePanelView(interaction.user.id, interaction.guild.id),
    )


bot.run(TOKEN)
