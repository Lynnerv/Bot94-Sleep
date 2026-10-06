import asyncio
import logging
import os

from datetime import (
    datetime,
    timedelta
)

import discord

from discord import app_commands

from discord.ext import commands

from dotenv import load_dotenv

from api import start_control_api

from config import TIMEZONE

from database import (
    get_sleep_check,
    update_sleep_check,
    get_voice_effect,
    clear_voice_effect,
    update_execution
)

from scheduler import (
    Scheduler,
    execute_actions
)


load_dotenv()


TOKEN = os.getenv(
    "DISCORD_TOKEN"
)

if not TOKEN:

    raise RuntimeError(
        "No se encontró "
        "DISCORD_TOKEN en .env"
    )


logging.basicConfig(
    level=logging.INFO,
    format=(
        "[%(asctime)s] "
        "[%(levelname)s] "
        "%(name)s: %(message)s"
    )
)


log = logging.getLogger(
    "Bot94Sleep"
)


intents = discord.Intents.default()

intents.guilds = True
intents.members = True
intents.voice_states = True


bot = commands.Bot(
    command_prefix="!",
    intents=intents
)


scheduler = None
api_runner = None


class SleepCheckView(
    discord.ui.View
):

    def __init__(
        self,
        bot_instance,
        user_id,
        guild_id
    ):

        super().__init__(
            timeout=120
        )

        self.bot = bot_instance
        self.user_id = user_id
        self.guild_id = guild_id

    async def check_user(
        self,
        interaction
    ):

        if (
            interaction.user.id
            != self.user_id
        ):

            await interaction.response.send_message(
                "Este aviso no es para ti.",
                ephemeral=True
            )

            return False

        return True

    @discord.ui.button(
        label="Sí, sigo aquí",
        style=discord.ButtonStyle.success
    )
    async def awake(
        self,
        interaction,
        button
    ):

        if not await self.check_user(
            interaction
        ):

            return

        row = get_sleep_check(
            self.user_id,
            self.guild_id
        )

        now = datetime.now(
            TIMEZONE
        )

        if row:

            update_sleep_check(
                self.user_id,
                self.guild_id,

                waiting_until=None,

                last_prompt_at=
                    now.isoformat(
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

        await interaction.response.edit_message(
            content=(
                "🟢 Sigues aquí. "
                "Bot94 Sleep continuará esperando."
            ),
            view=None
        )

    @discord.ui.button(
        label="Mutearme",
        style=discord.ButtonStyle.secondary
    )
    async def mute(
        self,
        interaction,
        button
    ):

        if not await self.check_user(
            interaction
        ):

            return

        guild = self.bot.get_guild(
            self.guild_id
        )

        member = (
            guild.get_member(
                self.user_id
            )
            if guild
            else None
        )

        if not member:

            await interaction.response.edit_message(
                content=(
                    "⚠️ No encontré al usuario."
                ),
                view=None
            )

            return

        success = await execute_actions(
            member,
            mute=True
        )

        if success:

            update_sleep_check(
                self.user_id,
                self.guild_id,
                waiting_until=None,
                muted_by_sleep_check=1
            )

        await interaction.response.edit_message(
            content=(
                "🔇 Te he muteado."
                if success
                else
                "⚠️ No pude mutearte."
            ),
            view=None
        )


class ScheduleReminderView(
    discord.ui.View
):

    def __init__(
        self,
        bot_instance,
        row,
        execution_id,
        target
    ):

        super().__init__(
            timeout=120
        )

        self.bot = bot_instance
        self.row = row
        self.execution_id = execution_id
        self.target = target

    async def adjust(
        self,
        interaction,
        delta
    ):

        if (
            interaction.user.id
            != int(
                self.row["user_id"]
            )
        ):

            await interaction.response.send_message(
                "Este aviso no es para ti.",
                ephemeral=True
            )

            return

        new_target = (
            self.target
            +
            timedelta(
                minutes=delta
            )
        )

        update_execution(
            self.execution_id,

            override_datetime=
                new_target.isoformat(),

            confirmed=1,

            status="pending"
        )

        await interaction.response.edit_message(
            content=(
                "✅ Solo esta ejecución "
                f"se movió a **{new_target:%H:%M}**.\n\n"
                "El horario recurrente no cambió."
            ),
            view=None
        )

    @discord.ui.button(
        label="-15",
        style=discord.ButtonStyle.secondary
    )
    async def minus15(
        self,
        interaction,
        button
    ):

        await self.adjust(
            interaction,
            -15
        )

    @discord.ui.button(
        label="-5",
        style=discord.ButtonStyle.secondary
    )
    async def minus5(
        self,
        interaction,
        button
    ):

        await self.adjust(
            interaction,
            -5
        )

    @discord.ui.button(
        label="+5",
        style=discord.ButtonStyle.primary
    )
    async def plus5(
        self,
        interaction,
        button
    ):

        await self.adjust(
            interaction,
            5
        )

    @discord.ui.button(
        label="+15",
        style=discord.ButtonStyle.primary
    )
    async def plus15(
        self,
        interaction,
        button
    ):

        await self.adjust(
            interaction,
            15
        )

    @discord.ui.button(
        label="+30",
        style=discord.ButtonStyle.primary,
        row=2
    )
    async def plus30(
        self,
        interaction,
        button
    ):

        await self.adjust(
            interaction,
            30
        )

    @discord.ui.button(
        label="Mantener",
        style=discord.ButtonStyle.success,
        row=2
    )
    async def keep(
        self,
        interaction,
        button
    ):

        if (
            interaction.user.id
            != int(
                self.row["user_id"]
            )
        ):

            await interaction.response.send_message(
                "Este aviso no es para ti.",
                ephemeral=True
            )

            return

        update_execution(
            self.execution_id,
            confirmed=1,
            status="pending"
        )

        await interaction.response.edit_message(
            content=(
                f"🟢 Se mantiene **{self.target:%H:%M}**."
            ),
            view=None
        )


@bot.event
async def on_ready():

    global scheduler
    global api_runner

    log.info(
        "Bot94 Sleep conectado como %s",
        bot.user
    )

    log.info(
        "Bot ID: %s",
        bot.user.id
    )

    try:

        synced = await bot.tree.sync()

        log.info(
            "Comandos sincronizados: %s",
            len(synced)
        )

    except Exception:

        log.exception(
            "Error sincronizando comandos"
        )

    if scheduler is None:

        scheduler = Scheduler(
            bot
        )

        asyncio.create_task(
            scheduler.start()
        )

    if api_runner is None:

        api_runner = await start_control_api(
            bot
        )

    log.info(
        "API local lista en "
        "http://127.0.0.1:8765"
    )


@bot.event
async def on_voice_state_update(
    member,
    before,
    after
):

    # Si Bot94 Sleep fue quien aplicó
    # mute/deafen, al volver a entrar
    # a voz restauramos el estado normal.

    if (
        before.channel is None
        and after.channel is not None
    ):

        effect = get_voice_effect(
            member.id,
            member.guild.id
        )

        if (
            effect
            and (
                effect["mute_by_bot"]
                or
                effect["deafen_by_bot"]
            )
        ):

            try:

                await member.edit(
                    mute=False,
                    deafen=False
                )

                clear_voice_effect(
                    member.id,
                    member.guild.id
                )

                log.info(
                    "Restauré voz de %s "
                    "al volver a entrar.",
                    member.id
                )

            except (
                discord.Forbidden,
                discord.HTTPException
            ):

                log.exception(
                    "No pude restaurar voz."
                )


@bot.tree.command(
    name="ping",
    description="Comprueba Bot94 Sleep."
)
async def ping(interaction):

    await interaction.response.send_message(
        "🏓 Pong!"
    )


@bot.tree.command(
    name="voice",
    description="Muestra tu estado de voz."
)
async def voice(interaction):

    member = (
        interaction.guild.get_member(
            interaction.user.id
        )
        if interaction.guild
        else None
    )

    if (
        member
        and member.voice
    ):

        await interaction.response.send_message(
            "🎧 Estás en "
            f"**{member.voice.channel.name}**."
        )

    else:

        await interaction.response.send_message(
            "🔇 No estás en voz."
        )


@bot.tree.command(
    name="testaction",
    description="Prueba mute/deafen/disconnect."
)
@app_commands.describe(
    mute="Mute",
    deafen="Deafen (también mute)",
    disconnect="Disconnect"
)
async def testaction(
    interaction,
    mute: bool = False,
    deafen: bool = False,
    disconnect: bool = False
):

    if not interaction.guild:

        await interaction.response.send_message(
            "Solo funciona en un servidor.",
            ephemeral=True
        )

        return

    member = interaction.guild.get_member(
        interaction.user.id
    )

    if (
        not member
        or not member.voice
    ):

        await interaction.response.send_message(
            "Entra a un canal de voz primero.",
            ephemeral=True
        )

        return

    if not (
        mute
        or deafen
        or disconnect
    ):

        await interaction.response.send_message(
            "Selecciona una acción.",
            ephemeral=True
        )

        return

    success = await execute_actions(
        member,
        mute,
        deafen,
        disconnect
    )

    await interaction.response.send_message(
        (
            "✅ Acción ejecutada."
            if success
            else
            "❌ No se pudo ejecutar."
        ),
        ephemeral=True
    )


@bot.tree.command(
    name="sleep",
    description="Sleep rápido."
)
@app_commands.describe(
    minutes="Minutos"
)
async def sleep(
    interaction,
    minutes: int
):

    await interaction.response.send_message(
        (
            "💤 Sleep recibido.\n"
            f"Tiempo: **{minutes} minutos**.\n\n"
            "El control completo está disponible "
            "desde Bot94 Sleep."
        ),
        ephemeral=True
    )


bot.run(
    TOKEN
)