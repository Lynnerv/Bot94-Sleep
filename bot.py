import asyncio
import logging
import os
from datetime import datetime, timedelta

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

from database import (
    create_schedule,
    delete_schedule,
    get_pending_executions,
    get_user_schedules,
    init_database,
    update_execution,
)
from scheduler import Scheduler, TIMEZONE, execute_actions


load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    raise RuntimeError("No se encontró DISCORD_TOKEN en el archivo .env")


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

logger = logging.getLogger("Bot94Sleep")

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

scheduler = None
registered_control_views = set()


def normalize_actions(mute=False, deafen=False, disconnect=False):
    if deafen:
        mute = True
    return bool(mute), bool(deafen), bool(disconnect)


def action_text(mute, deafen, disconnect):
    mute, deafen, disconnect = normalize_actions(
        mute, deafen, disconnect
    )

    actions = []

    if mute:
        actions.append("🔇 Mute")
    if deafen:
        actions.append("🎧 Deafen")
    if disconnect:
        actions.append("🚪 Disconnect")

    return "\n".join(actions) or "⚠️ Ninguna acción seleccionada."


def parse_days(days):
    aliases = {
        "lunes": "lunes",
        "martes": "martes",
        "miercoles": "miercoles",
        "miércoles": "miercoles",
        "jueves": "jueves",
        "viernes": "viernes",
        "sabado": "sabado",
        "sábado": "sabado",
        "domingo": "domingo",
    }

    result = []

    for day in days.lower().split(","):
        normalized = aliases.get(day.strip())

        if normalized is None:
            return None

        if normalized not in result:
            result.append(normalized)

    return result


def valid_time(value):
    try:
        hour, minute = map(int, value.split(":"))
        return 0 <= hour <= 23 and 0 <= minute <= 59
    except (ValueError, AttributeError):
        return False


def format_minutes(minutes):
    minutes = max(1, int(minutes))

    if minutes < 60:
        return f"{minutes} min"

    hours, mins = divmod(minutes, 60)

    if mins == 0:
        return f"{hours} h"

    return f"{hours} h {mins} min"


def remaining_minutes(end_at):
    seconds = max(1, int((end_at - datetime.now(TIMEZONE)).total_seconds()))
    return max(1, (seconds + 59) // 60)


class SleepTimeModal(discord.ui.Modal, title="Tiempo personalizado"):
    minutes = discord.ui.TextInput(
        label="Minutos",
        placeholder="Ejemplo: 75",
        min_length=1,
        max_length=4,
        required=True,
    )

    def __init__(self, parent_view):
        super().__init__()
        self.parent_view = parent_view

    async def on_submit(self, interaction):
        try:
            value = int(str(self.minutes.value).strip())
        except ValueError:
            await interaction.response.send_message(
                "❌ Introduce un número entero de minutos.",
                ephemeral=True,
            )
            return

        if not 1 <= value <= 1440:
            await interaction.response.send_message(
                "❌ El tiempo debe estar entre 1 y 1440 minutos.",
                ephemeral=True,
            )
            return

        await self.parent_view.set_minutes_from_modal(
            interaction,
            value,
        )


class SleepView(discord.ui.View):
    def __init__(
        self,
        bot_instance,
        member,
        minutes,
        mute=True,
        deafen=False,
        disconnect=False,
    ):
        # El usuario puede editar el Sleep mientras está activo,
        # incluso si tarda más de unos minutos.
        super().__init__(timeout=None)

        self.bot_instance = bot_instance
        self.member = member
        self.minutes = int(minutes)

        self.mute, self.deafen, self.disconnect = normalize_actions(
            mute,
            deafen,
            disconnect,
        )

        self.started = False
        self.completed = False
        self.cancelled = False
        self.ends_at = None
        self.task = None
        self.message = None

    def render(self):
        if self.completed:
            return (
                "✅ **Bot94 Sleep completado**\n\n"
                f"{action_text(self.mute, self.deafen, self.disconnect)}"
            )

        if self.cancelled:
            return "❌ **Bot94 Sleep cancelado.**"

        if self.started and self.ends_at:
            timestamp = int(self.ends_at.timestamp())
            return (
                "🌙 **Bot94 Sleep activo**\n\n"
                f"⏳ **Termina:** <t:{timestamp}:R>\n"
                f"🕐 **Hora:** <t:{timestamp}:t>\n\n"
                f"{action_text(self.mute, self.deafen, self.disconnect)}\n\n"
                "Puedes seguir ajustando el tiempo mientras está activo."
            )

        return (
            "🌙 **Bot94 Sleep configurado**\n\n"
            f"⏰ **Tiempo: {format_minutes(self.minutes)}**\n\n"
            f"{action_text(self.mute, self.deafen, self.disconnect)}\n\n"
            "Ajusta el tiempo y pulsa **Confirmar** para iniciar."
        )

    async def interaction_check(self, interaction):
        if interaction.user.id != self.member.id:
            await interaction.response.send_message(
                "❌ Este Sleep pertenece a otro usuario.",
                ephemeral=True,
            )
            return False

        return True

    async def refresh_message(self, interaction):
        await interaction.response.edit_message(
            content=self.render(),
            view=self,
        )

    async def set_minutes(self, interaction, value):
        value = max(1, min(1440, int(value)))

        if self.completed or self.cancelled:
            await interaction.response.send_message(
                "❌ Este Sleep ya terminó.",
                ephemeral=True,
            )
            return

        if self.started:
            self.ends_at = datetime.now(TIMEZONE) + timedelta(
                minutes=value
            )
            self.minutes = value
            await self.restart_task()
        else:
            self.minutes = value

        await self.refresh_message(interaction)

    async def set_minutes_from_modal(self, interaction, value):
        value = max(1, min(1440, int(value)))

        if self.completed or self.cancelled:
            await interaction.response.send_message(
                "❌ Este Sleep ya terminó.",
                ephemeral=True,
            )
            return

        if self.started:
            self.ends_at = datetime.now(TIMEZONE) + timedelta(
                minutes=value
            )
            self.minutes = value
            await self.restart_task()
        else:
            self.minutes = value

        if self.message:
            await interaction.response.defer()
            await self.message.edit(
                content=self.render(),
                view=self,
            )
        else:
            await interaction.response.send_message(
                self.render(),
                ephemeral=True,
            )

    async def adjust(self, interaction, delta):
        if self.completed or self.cancelled:
            await interaction.response.send_message(
                "❌ Este Sleep ya terminó.",
                ephemeral=True,
            )
            return

        if self.started and self.ends_at:
            minimum_end = datetime.now(TIMEZONE) + timedelta(minutes=1)
            new_end = self.ends_at + timedelta(minutes=delta)

            if new_end < minimum_end:
                new_end = minimum_end

            if new_end > datetime.now(TIMEZONE) + timedelta(hours=24):
                new_end = datetime.now(TIMEZONE) + timedelta(hours=24)

            self.ends_at = new_end
            self.minutes = remaining_minutes(self.ends_at)

            await self.restart_task()
        else:
            self.minutes = max(
                1,
                min(1440, self.minutes + delta),
            )

        await self.refresh_message(interaction)

    async def restart_task(self):
        if self.task and not self.task.done():
            self.task.cancel()

        self.task = asyncio.create_task(
            self.wait_for_completion()
        )

    async def start_timer(self, interaction):
        if self.started:
            return

        if self.completed or self.cancelled:
            await interaction.response.send_message(
                "❌ Este Sleep ya terminó.",
                ephemeral=True,
            )
            return

        self.started = True
        self.ends_at = datetime.now(TIMEZONE) + timedelta(
            minutes=self.minutes
        )

        self.message = interaction.message

        await interaction.response.edit_message(
            content=self.render(),
            view=self,
        )

        self.task = asyncio.create_task(
            self.wait_for_completion()
        )

    async def wait_for_completion(self):
        try:
            while self.ends_at:
                seconds = (
                    self.ends_at - datetime.now(TIMEZONE)
                ).total_seconds()

                if seconds <= 0:
                    break

                await asyncio.sleep(seconds)

            if self.cancelled or self.completed:
                return

            member = self.member.guild.get_member(self.member.id)

            if (
                member is None
                or member.voice is None
                or member.voice.channel is None
            ):
                self.completed = True
                await self.update_finished_message(
                    "⚠️ **Sleep terminado**, pero no estabas en un canal de voz."
                )
                return

            result = await execute_actions(
                member,
                mute=self.mute,
                deafen=self.deafen,
                disconnect=self.disconnect,
            )

            self.completed = True

            if result:
                text = (
                    "✅ **Bot94 Sleep completado**\n\n"
                    f"{action_text(self.mute, self.deafen, self.disconnect)}"
                )
            else:
                text = (
                    "⚠️ **Bot94 Sleep terminó**, pero no se "
                    "pudieron ejecutar las acciones."
                )

            await self.update_finished_message(text)

        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception("Error en el temporizador de Sleep.")
            self.completed = True
            await self.update_finished_message(
                "⚠️ **Sleep terminó con un error inesperado.**"
            )

    async def update_finished_message(self, content):
        if not self.message:
            return

        for child in self.children:
            child.disabled = True

        try:
            await self.message.edit(
                content=content,
                view=self,
            )
        except discord.HTTPException:
            pass

    @discord.ui.button(
        label="15 min",
        style=discord.ButtonStyle.secondary,
        row=0,
    )
    async def preset_15(self, interaction, button):
        await self.set_minutes(interaction, 15)

    @discord.ui.button(
        label="30 min",
        style=discord.ButtonStyle.secondary,
        row=0,
    )
    async def preset_30(self, interaction, button):
        await self.set_minutes(interaction, 30)

    @discord.ui.button(
        label="45 min",
        style=discord.ButtonStyle.secondary,
        row=0,
    )
    async def preset_45(self, interaction, button):
        await self.set_minutes(interaction, 45)

    @discord.ui.button(
        label="1 hora",
        style=discord.ButtonStyle.secondary,
        row=0,
    )
    async def preset_60(self, interaction, button):
        await self.set_minutes(interaction, 60)

    @discord.ui.button(
        label="1h 15m",
        style=discord.ButtonStyle.secondary,
        row=1,
    )
    async def preset_75(self, interaction, button):
        await self.set_minutes(interaction, 75)

    @discord.ui.button(
        label="1h 30m",
        style=discord.ButtonStyle.secondary,
        row=1,
    )
    async def preset_90(self, interaction, button):
        await self.set_minutes(interaction, 90)

    @discord.ui.button(
        label="➖ 15",
        style=discord.ButtonStyle.secondary,
        row=2,
    )
    async def minus_15(self, interaction, button):
        await self.adjust(interaction, -15)

    @discord.ui.button(
        label="✏️ Personalizado",
        style=discord.ButtonStyle.primary,
        row=2,
    )
    async def custom(self, interaction, button):
        self.message = interaction.message
        await interaction.response.send_modal(
            SleepTimeModal(self)
        )

    @discord.ui.button(
        label="➕ 15",
        style=discord.ButtonStyle.secondary,
        row=2,
    )
    async def plus_15(self, interaction, button):
        await self.adjust(interaction, 15)

    @discord.ui.button(
        label="❌ Cancelar",
        style=discord.ButtonStyle.danger,
        row=3,
    )
    async def cancel(self, interaction, button):
        if self.completed:
            await interaction.response.send_message(
                "❌ Este Sleep ya terminó.",
                ephemeral=True,
            )
            return

        self.cancelled = True

        if self.task and not self.task.done():
            self.task.cancel()

        for child in self.children:
            child.disabled = True

        await interaction.response.edit_message(
            content="❌ **Bot94 Sleep cancelado.**",
            view=self,
        )

    @discord.ui.button(
        label="✅ Confirmar",
        style=discord.ButtonStyle.success,
        row=3,
    )
    async def confirm(self, interaction, button):
        await self.start_timer(interaction)


class ScheduleTimeModal(discord.ui.Modal, title="Cambiar hora"):
    time = discord.ui.TextInput(
        label="Nueva hora",
        placeholder="Ejemplo: 00:14",
        min_length=5,
        max_length=5,
        required=True,
    )

    def __init__(self, parent_view):
        super().__init__()
        self.parent_view = parent_view

    async def on_submit(self, interaction):
        value = str(self.time.value).strip()

        if not valid_time(value):
            await interaction.response.send_message(
                "❌ Usa una hora válida en formato HH:MM.",
                ephemeral=True,
            )
            return

        await self.parent_view.set_custom_time_from_modal(
            interaction,
            value,
        )


class ScheduleReminderView(discord.ui.View):
    """Aviso de 2 horas antes. Solo confirmar/cancelar."""

    def __init__(self, bot_instance, schedule, execution):
        super().__init__(timeout=120)

        self.bot_instance = bot_instance
        self.schedule = schedule
        self.execution = execution
        self.message = None

        confirm = discord.ui.Button(
            label="✅ Confirmar",
            style=discord.ButtonStyle.success,
            custom_id=f"schedule_reminder_confirm_{execution['id']}",
        )
        confirm.callback = self.confirm
        self.add_item(confirm)

        cancel = discord.ui.Button(
            label="❌ Cancelar",
            style=discord.ButtonStyle.danger,
            custom_id=f"schedule_reminder_cancel_{execution['id']}",
        )
        cancel.callback = self.cancel
        self.add_item(cancel)

    async def interaction_check(self, interaction):
        if interaction.user.id != self.schedule["user_id"]:
            await interaction.response.send_message(
                "❌ Este horario pertenece a otro usuario.",
                ephemeral=True,
            )
            return False

        return True

    async def confirm(self, interaction):
        update_execution(
            self.execution["id"],
            confirmed=1,
            updated_at=datetime.now(TIMEZONE).isoformat(),
        )

        self.execution = dict(self.execution)
        self.execution["confirmed"] = 1

        control = ScheduleControlView(
            self.bot_instance,
            self.schedule,
            self.execution,
        )
        control.message = interaction.message

        await interaction.response.edit_message(
            content=(
                control.render()
                + "\n\n"
                "✅ **Confirmado. Puedes seguir ajustando esta ejecución.**"
            ),
            view=control,
        )

        self.stop()

    async def cancel(self, interaction):
        update_execution(
            self.execution["id"],
            status="cancelled",
            updated_at=datetime.now(TIMEZONE).isoformat(),
        )

        await interaction.response.edit_message(
            content=(
                "❌ **Ejecución cancelada.**\n\n"
                "El horario recurrente sigue intacto."
            ),
            view=None,
        )

        self.stop()

    async def on_timeout(self):
        if not self.message:
            return

        try:
            await self.message.edit(
                content=(
                    self.message.content
                    + "\n\n⌛ **Recordatorio expirado.** "
                    "El horario sigue programado normalmente."
                ),
                view=None,
            )
        except discord.HTTPException:
            pass


class ScheduleControlView(discord.ui.View):
    """Controles que quedan disponibles después de confirmar."""

    def __init__(self, bot_instance, schedule, execution):
        super().__init__(timeout=None)

        self.bot_instance = bot_instance
        self.schedule = schedule
        self.execution = execution
        self.message = None

        prefix = f"schedule_control_{execution['id']}_"

        for label, delta, custom_id in [
            ("➖ 5 min", -5, prefix + "m5"),
            ("➖ 15 min", -15, prefix + "m15"),
            ("➖ 30 min", -30, prefix + "m30"),
            ("➖ 60 min", -60, prefix + "m60"),
            ("➕ 5 min", 5, prefix + "p5"),
            ("➕ 15 min", 15, prefix + "p15"),
            ("➕ 30 min", 30, prefix + "p30"),
            ("➕ 60 min", 60, prefix + "p60"),
        ]:
            button = discord.ui.Button(
                label=label,
                style=discord.ButtonStyle.secondary,
                custom_id=custom_id,
            )
            button.callback = self.make_adjust_callback(delta)
            self.add_item(button)

        edit = discord.ui.Button(
            label="✏️ Cambiar hora",
            style=discord.ButtonStyle.primary,
            custom_id=prefix + "time",
        )
        edit.callback = self.change_time
        self.add_item(edit)

        cancel = discord.ui.Button(
            label="❌ Cancelar ejecución",
            style=discord.ButtonStyle.danger,
            custom_id=prefix + "cancel",
        )
        cancel.callback = self.cancel
        self.add_item(cancel)

    def render(self):
        from scheduler import execution_datetime

        effective = execution_datetime(self.execution)
        timestamp = int(effective.timestamp())

        return (
            "🌙 **Bot94 Sleep — Ejecución confirmada**\n\n"
            f"⏰ **Ejecuta:** <t:{timestamp}:F>\n"
            f"⏳ **Faltan:** <t:{timestamp}:R>\n\n"
            f"{action_text(self.schedule['mute'], self.schedule['deafen'], self.schedule['disconnect'])}\n\n"
            "Los cambios son solo para esta ejecución. "
            "El horario recurrente no se modifica."
        )

    async def set_effective_datetime(self, interaction, effective):
        minimum = datetime.now(TIMEZONE) + timedelta(minutes=1)

        if effective < minimum:
            effective = minimum

        update_execution(
            self.execution["id"],
            override_datetime=effective.isoformat(),
            override_time=effective.strftime("%H:%M"),
            updated_at=datetime.now(TIMEZONE).isoformat(),
        )

        self.execution = dict(self.execution)
        self.execution["override_datetime"] = effective.isoformat()
        self.execution["override_time"] = effective.strftime("%H:%M")

        await interaction.response.edit_message(
            content=self.render(),
            view=self,
        )

    def make_adjust_callback(self, delta):
        async def callback(interaction):
            if interaction.user.id != self.schedule["user_id"]:
                await interaction.response.send_message(
                    "❌ Este horario pertenece a otro usuario.",
                    ephemeral=True,
                )
                return

            from scheduler import execution_datetime

            current = execution_datetime(self.execution)
            new_time = current + timedelta(minutes=delta)

            await self.set_effective_datetime(
                interaction,
                new_time,
            )

        return callback

    async def set_custom_time(self, interaction, value):
        hour, minute = map(int, value.split(":"))
        now = datetime.now(TIMEZONE)

        candidate = now.replace(
            hour=hour,
            minute=minute,
            second=0,
            microsecond=0,
        )

        if candidate <= now:
            candidate += timedelta(days=1)

        await self.set_effective_datetime(
            interaction,
            candidate,
        )

    async def set_custom_time_from_modal(self, interaction, value):
        hour, minute = map(int, value.split(":"))
        now = datetime.now(TIMEZONE)

        candidate = now.replace(
            hour=hour,
            minute=minute,
            second=0,
            microsecond=0,
        )

        if candidate <= now:
            candidate += timedelta(days=1)

        minimum = now + timedelta(minutes=1)
        if candidate < minimum:
            candidate = minimum

        update_execution(
            self.execution["id"],
            override_datetime=candidate.isoformat(),
            override_time=candidate.strftime("%H:%M"),
            updated_at=now.isoformat(),
        )

        self.execution = dict(self.execution)
        self.execution["override_datetime"] = candidate.isoformat()
        self.execution["override_time"] = candidate.strftime("%H:%M")

        if self.message:
            await interaction.response.defer()
            await self.message.edit(
                content=self.render(),
                view=self,
            )
        else:
            await interaction.response.send_message(
                self.render(),
                ephemeral=True,
            )

    async def change_time(self, interaction):
        if interaction.user.id != self.schedule["user_id"]:
            await interaction.response.send_message(
                "❌ Este horario pertenece a otro usuario.",
                ephemeral=True,
            )
            return

        self.message = interaction.message
        await interaction.response.send_modal(
            ScheduleTimeModal(self)
        )

    async def cancel(self, interaction):
        if interaction.user.id != self.schedule["user_id"]:
            await interaction.response.send_message(
                "❌ Este horario pertenece a otro usuario.",
                ephemeral=True,
            )
            return

        update_execution(
            self.execution["id"],
            status="cancelled",
            updated_at=datetime.now(TIMEZONE).isoformat(),
        )

        await interaction.response.edit_message(
            content=(
                "❌ **Ejecución cancelada.**\n\n"
                "El horario recurrente sigue intacto."
            ),
            view=None,
        )


@bot.event
async def on_ready():
    global scheduler

    logger.info(
        "Bot94 Sleep conectado como %s",
        bot.user,
    )

    try:
        synced = await bot.tree.sync()
        logger.info(
            "Comandos sincronizados: %s",
            len(synced),
        )
    except Exception:
        logger.exception(
            "Error sincronizando comandos",
        )

    if scheduler is None:
        scheduler = Scheduler(bot)
        asyncio.create_task(scheduler.start())
        logger.info("Scheduler iniciado correctamente.")

    # Recuperar controles de ejecuciones confirmadas que siguen pendientes.
    for execution in get_pending_executions():
        if not execution["confirmed"]:
            continue

        if execution["id"] in registered_control_views:
            continue

        bot.add_view(
            ScheduleControlView(
                bot,
                execution,
                execution,
            )
        )

        registered_control_views.add(execution["id"])


@bot.tree.command(
    name="ping",
    description="Comprueba si Bot94 Sleep está funcionando.",
)
async def ping(interaction):
    await interaction.response.send_message(
        "🏓 Pong! Bot94 Sleep está funcionando."
    )


@bot.tree.command(
    name="voice",
    description="Muestra tu estado actual en un canal de voz.",
)
async def voice(interaction):
    if interaction.guild is None:
        await interaction.response.send_message(
            "❌ Este comando solo funciona dentro de un servidor.",
            ephemeral=True,
        )
        return

    member = interaction.guild.get_member(
        interaction.user.id
    )

    if (
        member is None
        or member.voice is None
        or member.voice.channel is None
    ):
        await interaction.response.send_message(
            "🎧 No estás conectado a ningún canal de voz.",
            ephemeral=True,
        )
        return

    await interaction.response.send_message(
        f"🎧 Estás conectado a **{member.voice.channel.name}**."
    )


@bot.tree.command(
    name="testaction",
    description="Prueba una acción de Bot94 Sleep.",
)
@app_commands.choices(
    action=[
        app_commands.Choice(
            name="🔇 Mute",
            value="mute",
        ),
        app_commands.Choice(
            name="🎧 Deafen",
            value="deafen",
        ),
        app_commands.Choice(
            name="🚪 Disconnect",
            value="disconnect",
        ),
    ]
)
async def testaction(
    interaction,
    action: app_commands.Choice[str],
):
    if interaction.guild is None:
        await interaction.response.send_message(
            "❌ Este comando solo funciona en un servidor.",
            ephemeral=True,
        )
        return

    member = interaction.guild.get_member(
        interaction.user.id
    )

    if member is None or member.voice is None:
        await interaction.response.send_message(
            "🎧 Primero entra a un canal de voz.",
            ephemeral=True,
        )
        return

    mute, deafen, disconnect = normalize_actions(
        action.value == "mute",
        action.value == "deafen",
        action.value == "disconnect",
    )

    result = await execute_actions(
        member,
        mute,
        deafen,
        disconnect,
    )

    await interaction.response.send_message(
        f"{'✅' if result else '❌'} "
        f"Acción ejecutada: **{action.name}**"
    )


@bot.tree.command(
    name="sleep",
    description="Prepara un temporizador de Sleep interactivo.",
)
@app_commands.describe(
    minutes="Minutos iniciales.",
    mute="Aplicar mute.",
    deafen="Aplicar deafen (incluye mute).",
    disconnect="Desconectar al terminar.",
)
async def sleep(
    interaction,
    minutes: int,
    mute: bool = True,
    deafen: bool = False,
    disconnect: bool = False,
):
    if interaction.guild is None:
        await interaction.response.send_message(
            "❌ Este comando solo funciona dentro de un servidor.",
            ephemeral=True,
        )
        return

    if not 1 <= minutes <= 1440:
        await interaction.response.send_message(
            "❌ Los minutos deben estar entre 1 y 1440.",
            ephemeral=True,
        )
        return

    mute, deafen, disconnect = normalize_actions(
        mute,
        deafen,
        disconnect,
    )

    if not (mute or deafen or disconnect):
        await interaction.response.send_message(
            "❌ Selecciona al menos una acción.",
            ephemeral=True,
        )
        return

    member = interaction.guild.get_member(
        interaction.user.id
    )

    if (
        member is None
        or member.voice is None
        or member.voice.channel is None
    ):
        await interaction.response.send_message(
            "🎧 Primero entra a un canal de voz.",
            ephemeral=True,
        )
        return

    view = SleepView(
        bot,
        member,
        minutes,
        mute,
        deafen,
        disconnect,
    )

    await interaction.response.send_message(
        view.render(),
        view=view,
        ephemeral=True,
    )


@bot.tree.command(
    name="schedule",
    description="Crea un horario recurrente.",
)
@app_commands.describe(
    time="Hora HH:MM. Ejemplo: 23:59",
    days="Días separados por coma. Ejemplo: martes,jueves,viernes",
    mute="Aplicar mute.",
    deafen="Aplicar deafen (incluye mute).",
    disconnect="Desconectar.",
    confirmation="Pedir confirmación 2 horas antes.",
)
async def schedule(
    interaction,
    time: str,
    days: str,
    mute: bool = True,
    deafen: bool = False,
    disconnect: bool = False,
    confirmation: bool = True,
):
    if interaction.guild is None:
        await interaction.response.send_message(
            "❌ Este comando solo funciona dentro de un servidor.",
            ephemeral=True,
        )
        return

    if not valid_time(time):
        await interaction.response.send_message(
            "❌ Hora inválida. Usa HH:MM, por ejemplo **23:59**.",
            ephemeral=True,
        )
        return

    parsed_days = parse_days(days)

    if not parsed_days:
        await interaction.response.send_message(
            "❌ Días inválidos. Ejemplo: `martes,jueves,viernes`.",
            ephemeral=True,
        )
        return

    mute, deafen, disconnect = normalize_actions(
        mute,
        deafen,
        disconnect,
    )

    if not (mute or deafen or disconnect):
        await interaction.response.send_message(
            "❌ Debes seleccionar al menos una acción.",
            ephemeral=True,
        )
        return

    schedule_id = create_schedule(
        interaction.user.id,
        interaction.guild.id,
        time,
        ",".join(parsed_days),
        mute,
        deafen,
        disconnect,
        confirmation,
    )

    await interaction.response.send_message(
        f"✅ **Horario creado**\n\n"
        f"🆔 ID: `{schedule_id}`\n"
        f"⏰ Hora: **{time}**\n"
        f"📅 Días: **{', '.join(parsed_days)}**\n\n"
        f"{action_text(mute, deafen, disconnect)}\n\n"
        f"Confirmación: {'Sí' if confirmation else 'No'}"
    )


@bot.tree.command(
    name="schedules",
    description="Muestra tus horarios guardados.",
)
async def schedules(interaction):
    rows = get_user_schedules(
        interaction.user.id
    )

    if not rows:
        await interaction.response.send_message(
            "📭 No tienes horarios guardados.",
            ephemeral=True,
        )
        return

    lines = [
        "🌙 **Tus horarios de Bot94 Sleep**\n"
    ]

    for row in rows:
        lines.append(
            f"**ID {row['id']}**\n"
            f"⏰ {row['time']}\n"
            f"📅 {row['days']}\n"
            f"{action_text(row['mute'], row['deafen'], row['disconnect'])}\n"
            f"Confirmación: "
            f"{'Sí' if row['confirmation'] else 'No'}\n"
            f"Estado: "
            f"{'Activo' if row['enabled'] else 'Desactivado'}\n"
        )

    await interaction.response.send_message(
        "\n".join(lines),
        ephemeral=True,
    )


@bot.tree.command(
    name="deleteschedule",
    description="Elimina uno de tus horarios.",
)
@app_commands.describe(
    schedule_id="ID del horario.",
)
async def deleteschedule(
    interaction,
    schedule_id: int,
):
    deleted = delete_schedule(
        schedule_id,
        interaction.user.id,
    )

    await interaction.response.send_message(
        (
            "🗑️ Horario eliminado."
            if deleted
            else "❌ No encontré un horario con ese ID."
        ),
        ephemeral=True,
    )


init_database()
bot.run(TOKEN)
