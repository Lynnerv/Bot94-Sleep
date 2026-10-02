import os
import logging
import asyncio

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

from database import (
    init_database,
    create_schedule,
    get_user_schedules,
    delete_schedule
)

from scheduler import Scheduler, execute_actions


# ============================================================
# CONFIGURACIÓN
# ============================================================

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")

if not TOKEN:
    raise RuntimeError(
        "No se encontró DISCORD_TOKEN en el archivo .env"
    )


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger("Bot94Sleep")


# ============================================================
# BOT
# ============================================================

intents = discord.Intents.default()

bot = commands.Bot(
    command_prefix="!",
    intents=intents
)

scheduler = None


# ============================================================
# FUNCIONES AUXILIARES
# ============================================================

def normalize_actions(mute, deafen, disconnect):
    """
    En Bot94 Sleep, ensordecer siempre implica también mutear.
    """
    if deafen:
        mute = True

    return mute, deafen, disconnect


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

    if not actions:
        return "⚠️ Ninguna acción seleccionada."

    return "\n".join(actions)


def parse_days(days):

    valid_days = {
        "lunes",
        "martes",
        "miercoles",
        "miércoles",
        "jueves",
        "viernes",
        "sabado",
        "sábado",
        "domingo"
    }

    result = []

    for day in days.lower().split(","):

        day = day.strip()

        if day == "miércoles":
            day = "miercoles"

        if day == "sábado":
            day = "sabado"

        if day not in valid_days:
            return None

        if day not in result:
            result.append(day)

    return result


def valid_time(time):

    try:

        parts = time.split(":")

        if len(parts) != 2:
            return False

        hour = int(parts[0])
        minute = int(parts[1])

        return (
            0 <= hour <= 23
            and 0 <= minute <= 59
        )

    except ValueError:

        return False


# ============================================================
# CONFIRMATION VIEW
# ============================================================

class ConfirmationView(discord.ui.View):

    def __init__(
        self,
        bot,
        member,
        schedule
    ):

        super().__init__(
            timeout=900
        )

        self.bot = bot
        self.member = member
        self.schedule = schedule

        self.completed = False

    def action_text(self):

        return action_text(
            self.schedule["mute"],
            self.schedule["deafen"],
            self.schedule["disconnect"]
        )

    async def interaction_check(
        self,
        interaction: discord.Interaction
    ):

        if interaction.user.id != self.member.id:

            await interaction.response.send_message(
                "❌ Este horario pertenece a otro usuario.",
                ephemeral=True
            )

            return False

        return True

    @discord.ui.button(
        label="Ejecutar",
        style=discord.ButtonStyle.success,
        emoji="▶️"
    )
    async def execute(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        if self.completed:
            return

        self.completed = True

        result = await execute_actions(
            self.member,
            self.schedule["mute"],
            self.schedule["deafen"],
            self.schedule["disconnect"]
        )

        if result:

            await interaction.response.edit_message(
                content=(
                    "✅ **Bot94 Sleep**\n\n"
                    "Las acciones fueron ejecutadas."
                ),
                view=None
            )

        else:

            await interaction.response.edit_message(
                content=(
                    "⚠️ **Bot94 Sleep**\n\n"
                    "No se pudieron ejecutar las acciones."
                ),
                view=None
            )

        self.stop()

    @discord.ui.button(
        label="Cancelar",
        style=discord.ButtonStyle.danger,
        emoji="❌"
    )
    async def cancel(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        if self.completed:
            return

        self.completed = True

        await interaction.response.edit_message(
            content=(
                "❌ **Bot94 Sleep**\n\n"
                "Horario cancelado."
            ),
            view=None
        )

        self.stop()

    @discord.ui.button(
        label="Snooze 15 min",
        style=discord.ButtonStyle.secondary,
        emoji="⏰"
    )
    async def snooze(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        await interaction.response.send_message(
            "⏰ Se pospuso 15 minutos.",
            ephemeral=True
        )

        await asyncio.sleep(15 * 60)

        if self.member.voice is None:
            return

        await execute_actions(
            self.member,
            self.schedule["mute"],
            self.schedule["deafen"],
            self.schedule["disconnect"]
        )

        self.stop()


# ============================================================
# EVENTO READY
# ============================================================

@bot.event
async def on_ready():

    global scheduler

    logger.info(
        f"Bot94 Sleep conectado como {bot.user}"
    )

    logger.info(
        f"ID del bot: {bot.user.id}"
    )

    try:

        synced = await bot.tree.sync()

        logger.info(
            f"Comandos sincronizados: {len(synced)}"
        )

    except Exception as e:

        logger.exception(
            f"Error sincronizando comandos: {e}"
        )

    if scheduler is None:

        scheduler = Scheduler(bot)

        bot.loop.create_task(
            scheduler.start()
        )

        logger.info(
            "Scheduler iniciado correctamente."
        )


# ============================================================
# /PING
# ============================================================

@bot.tree.command(
    name="ping",
    description="Comprueba si Bot94 Sleep está funcionando."
)
async def ping(interaction: discord.Interaction):

    await interaction.response.send_message(
        "🏓 Pong! Bot94 Sleep está funcionando."
    )


# ============================================================
# /VOICE
# ============================================================

@bot.tree.command(
    name="voice",
    description="Muestra tu estado actual en un canal de voz."
)
async def voice(interaction: discord.Interaction):

    if interaction.guild is None:

        await interaction.response.send_message(
            "❌ Este comando solo funciona dentro de un servidor.",
            ephemeral=True
        )

        return

    member = interaction.guild.get_member(
        interaction.user.id
    )

    if member is None:

        await interaction.response.send_message(
            "❌ No pude encontrar tu usuario.",
            ephemeral=True
        )

        return

    if member.voice is None or member.voice.channel is None:

        await interaction.response.send_message(
            "🎧 No estás conectado a ningún canal de voz.",
            ephemeral=True
        )

        return

    await interaction.response.send_message(
        f"🎧 Estás conectado a "
        f"**{member.voice.channel.name}**."
    )


# ============================================================
# /TESTACTION
# ============================================================

@bot.tree.command(
    name="testaction",
    description="Prueba una acción de Bot94 Sleep."
)
@app_commands.describe(
    action="Acción que quieres probar."
)
@app_commands.choices(
    action=[
        app_commands.Choice(
            name="🔇 Mute",
            value="mute"
        ),
        app_commands.Choice(
            name="🎧 Deafen",
            value="deafen"
        ),
        app_commands.Choice(
            name="🚪 Disconnect",
            value="disconnect"
        )
    ]
)
async def testaction(
    interaction: discord.Interaction,
    action: app_commands.Choice[str]
):

    if interaction.guild is None:

        await interaction.response.send_message(
            "❌ Este comando solo funciona en un servidor.",
            ephemeral=True
        )

        return

    member = interaction.guild.get_member(
        interaction.user.id
    )

    if member is None:

        await interaction.response.send_message(
            "❌ No encontré tu usuario.",
            ephemeral=True
        )

        return

    if member.voice is None:

        await interaction.response.send_message(
            "🎧 Primero entra a un canal de voz.",
            ephemeral=True
        )

        return

    mute = action.value == "mute"
    deafen = action.value == "deafen"
    disconnect = action.value == "disconnect"

    # 🎧 Deafen también aplica 🔇 Mute
    mute, deafen, disconnect = normalize_actions(
        mute, deafen, disconnect
    )

    result = await execute_actions(
        member,
        mute=mute,
        deafen=deafen,
        disconnect=disconnect
    )

    if result:

        await interaction.response.send_message(
            f"✅ Acción ejecutada: **{action.name}**"
        )

    else:

        await interaction.response.send_message(
            "❌ No pude ejecutar la acción. "
            "Revisa los permisos del bot.",
            ephemeral=True
        )


# ============================================================
# /SLEEP
# ============================================================

@bot.tree.command(
    name="sleep",
    description="Programa una acción después de X minutos."
)
@app_commands.describe(
    minutes="Minutos hasta ejecutar.",
    mute="Aplicar mute.",
    deafen="Aplicar deafen.",
    disconnect="Desconectarte del canal.",
    confirmation="Pedir confirmación antes de ejecutar."
)
async def sleep(
    interaction: discord.Interaction,
    minutes: int,
    mute: bool = True,
    deafen: bool = True,
    disconnect: bool = False,
    confirmation: bool = True
):

    if interaction.guild is None:

        await interaction.response.send_message(
            "❌ Este comando solo funciona dentro de un servidor.",
            ephemeral=True
        )

        return

    if minutes < 1 or minutes > 1440:

        await interaction.response.send_message(
            "❌ Los minutos deben estar entre 1 y 1440.",
            ephemeral=True
        )

        return

    # 🎧 Deafen también aplica 🔇 Mute
    mute, deafen, disconnect = normalize_actions(
        mute, deafen, disconnect
    )

    if not (mute or deafen or disconnect):

        await interaction.response.send_message(
            "❌ Debes seleccionar al menos una acción.",
            ephemeral=True
        )

        return

    member = interaction.guild.get_member(
        interaction.user.id
    )

    if member is None:

        await interaction.response.send_message(
            "❌ No encontré tu usuario.",
            ephemeral=True
        )

        return

    if member.voice is None:

        await interaction.response.send_message(
            "🎧 Primero entra a un canal de voz.",
            ephemeral=True
        )

        return

    await interaction.response.send_message(
        f"🌙 **Bot94 Sleep configurado**\n\n"
        f"⏰ Tiempo: **{minutes} minutos**\n\n"
        f"{action_text(mute, deafen, disconnect)}\n\n"
        f"Confirmación: "
        f"{'Sí' if confirmation else 'No'}"
    )

    await asyncio.sleep(minutes * 60)

    # Comprobar nuevamente el estado actual
    member = interaction.guild.get_member(
        interaction.user.id
    )

    if member is None:
        return

    if member.voice is None or member.voice.channel is None:

        logger.info(
            f"{member} ya no está en voz. "
            f"Sleep cancelado."
        )

        return

    if confirmation:

        view = ConfirmationView(
            bot,
            member,
            {
                "mute": int(mute),
                "deafen": int(deafen),
                "disconnect": int(disconnect)
            }
        )

        try:

            await member.send(
                content=(
                    "🌙 **Bot94 Sleep**\n\n"
                    "Tu temporizador terminó.\n\n"
                    f"{action_text(mute, deafen, disconnect)}"
                ),
                view=view
            )

        except discord.Forbidden:

            logger.warning(
                f"No se pudo enviar DM a {member}."
            )

    else:

        await execute_actions(
            member,
            mute,
            deafen,
            disconnect
        )


# ============================================================
# /SCHEDULE
# ============================================================

@bot.tree.command(
    name="schedule",
    description="Crea un horario recurrente."
)
@app_commands.describe(
    time="Hora en formato HH:MM. Ejemplo: 23:59",
    days="Días separados por coma. Ejemplo: martes,jueves,viernes",
    mute="Aplicar mute.",
    deafen="Aplicar deafen.",
    disconnect="Desconectar.",
    confirmation="Pedir confirmación."
)
async def schedule(
    interaction: discord.Interaction,
    time: str,
    days: str,
    mute: bool = True,
    deafen: bool = True,
    disconnect: bool = False,
    confirmation: bool = True
):

    if interaction.guild is None:

        await interaction.response.send_message(
            "❌ Este comando solo funciona dentro de un servidor.",
            ephemeral=True
        )

        return

    if not valid_time(time):

        await interaction.response.send_message(
            "❌ Hora inválida. Usa HH:MM, por ejemplo **23:59**.",
            ephemeral=True
        )

        return

    # 🎧 Deafen también aplica 🔇 Mute
    mute, deafen, disconnect = normalize_actions(
        mute, deafen, disconnect
    )

    parsed_days = parse_days(days)

    if not parsed_days:

        await interaction.response.send_message(
            "❌ Días inválidos.\n\n"
            "Ejemplo:\n"
            "`martes,jueves,viernes`",
            ephemeral=True
        )

        return

    if not (mute or deafen or disconnect):

        await interaction.response.send_message(
            "❌ Debes seleccionar al menos una acción.",
            ephemeral=True
        )

        return

    schedule_id = create_schedule(
        user_id=interaction.user.id,
        guild_id=interaction.guild.id,
        time=time,
        days=",".join(parsed_days),
        mute=mute,
        deafen=deafen,
        disconnect=disconnect,
        confirmation=confirmation
    )

    await interaction.response.send_message(
        f"✅ **Horario creado**\n\n"
        f"🆔 ID: `{schedule_id}`\n"
        f"⏰ Hora: **{time}**\n"
        f"📅 Días: **{', '.join(parsed_days)}**\n\n"
        f"{action_text(mute, deafen, disconnect)}\n\n"
        f"Confirmación: "
        f"{'Sí' if confirmation else 'No'}"
    )


# ============================================================
# /SCHEDULES
# ============================================================

@bot.tree.command(
    name="schedules",
    description="Muestra tus horarios guardados."
)
async def schedules(
    interaction: discord.Interaction
):

    rows = get_user_schedules(
        interaction.user.id
    )

    if not rows:

        await interaction.response.send_message(
            "📭 No tienes horarios guardados.",
            ephemeral=True
        )

        return

    lines = [
        "🌙 **Tus horarios de Bot94 Sleep**\n"
    ]

    for row in rows:

        actions = action_text(
            row["mute"],
            row["deafen"],
            row["disconnect"]
        )

        lines.append(
            f"**ID {row['id']}**\n"
            f"⏰ {row['time']}\n"
            f"📅 {row['days']}\n"
            f"{actions}\n"
            f"Confirmación: "
            f"{'Sí' if row['confirmation'] else 'No'}\n"
        )

    await interaction.response.send_message(
        "\n".join(lines),
        ephemeral=True
    )


# ============================================================
# /DELETESCHEDULE
# ============================================================

@bot.tree.command(
    name="deleteschedule",
    description="Elimina uno de tus horarios."
)
@app_commands.describe(
    schedule_id="ID del horario."
)
async def deleteschedule(
    interaction: discord.Interaction,
    schedule_id: int
):

    deleted = delete_schedule(
        schedule_id,
        interaction.user.id
    )

    if deleted:

        await interaction.response.send_message(
            f"🗑️ Horario `{schedule_id}` eliminado.",
            ephemeral=True
        )

    else:

        await interaction.response.send_message(
            "❌ No encontré un horario con ese ID.",
            ephemeral=True
        )


# ============================================================
# INICIAR
# ============================================================

init_database()

bot.run(TOKEN)