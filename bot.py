import os

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv


# =========================
# CONFIGURACIÓN
# =========================

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.default()

bot = commands.Bot(
    command_prefix="!",
    intents=intents
)


# =========================
# EVENTO: BOT CONECTADO
# =========================

@bot.event
async def on_ready():
    print(f"Bot94 Sleep conectado como {bot.user}")
    print(f"ID del bot: {bot.user.id}")

    try:
        synced = await bot.tree.sync()
        print(f"Comandos sincronizados: {len(synced)}")
    except Exception as e:
        print(f"Error sincronizando comandos: {e}")


# =========================
# COMANDO /PING
# =========================

@bot.tree.command(
    name="ping",
    description="Comprueba si Bot94 Sleep está funcionando."
)
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message(
        "🏓 Pong! Bot94 Sleep está funcionando."
    )


# =========================
# COMANDO /VOICE
# =========================

@bot.tree.command(
    name="voice",
    description="Muestra tu estado actual en un canal de voz."
)
async def voice(interaction: discord.Interaction):

    # Verificar que estamos dentro de un servidor
    if interaction.guild is None:
        await interaction.response.send_message(
            "❌ Este comando solo funciona dentro de un servidor.",
            ephemeral=True
        )
        return

    # Buscar al usuario dentro del servidor
    member = interaction.guild.get_member(interaction.user.id)

    if member is None:
        await interaction.response.send_message(
            "❌ No pude encontrar tu usuario en este servidor.",
            ephemeral=True
        )
        return

    # Comprobar si está conectado a voz
    if member.voice is None or member.voice.channel is None:
        await interaction.response.send_message(
            "🎧 No estás conectado a ningún canal de voz.",
            ephemeral=True
        )
        return

    # Obtener el canal de voz
    channel = member.voice.channel

    await interaction.response.send_message(
        f"🎧 Estás conectado a **{channel.name}**."
    )


# =========================
# INICIAR BOT
# =========================

if not TOKEN:
    raise RuntimeError(
        "No se encontró DISCORD_TOKEN en el archivo .env"
    )

bot.run(TOKEN)