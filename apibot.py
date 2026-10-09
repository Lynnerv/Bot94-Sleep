import asyncio
import logging
import os
from datetime import datetime, timedelta
import discord
from discord import app_commands
from dotenv import load_dotenv
from config import TIMEZONE
from database import create_execution, create_schedule, delete_schedule, get_schedules, update_execution, update_schedule
from scheduler import Scheduler
from api import start_control_api, active_sleeps

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    raise RuntimeError("No se encontró DISCORD_TOKEN en el archivo .env")

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("XZ94")
intents = discord.Intents.default()
intents.voice_states = True
intents.members = True
bot = discord.Client(intents=intents)
bot.tree = app_commands.CommandTree(bot)
scheduler = None
api_runner = None
def sleep_key(user_id, guild_id):
    return f"{int(guild_id)}:{int(user_id)}"

class SleepConfigView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=900)
    @discord.ui.select(placeholder="🌙 Elige una duración", options=[
        discord.SelectOption(label="15 minutos", value="15"),
        discord.SelectOption(label="30 minutos", value="30"),
        discord.SelectOption(label="45 minutos", value="45"),
        discord.SelectOption(label="1 hora", value="60"),
        discord.SelectOption(label="1 hora 15 minutos", value="75"),
        discord.SelectOption(label="1 hora 30 minutos", value="90"),
        discord.SelectOption(label="Personalizado", value="custom")])
    async def preset(self, interaction, select):
        if select.values[0] == "custom":
            await interaction.response.send_modal(CustomSleepModal())
        else:
            await start_sleep_message(interaction, int(select.values[0]))

class CustomSleepModal(discord.ui.Modal, title="Temporizador personalizado"):
    minutes = discord.ui.TextInput(label="Minutos (1–1440)", placeholder="30", max_length=4)
    async def on_submit(self, interaction):
        try: minutes = int(self.minutes.value.strip())
        except ValueError:
            await interaction.response.send_message("Introduce minutos como número.", ephemeral=True); return
        if not 1 <= minutes <= 1440:
            await interaction.response.send_message("Usa entre 1 y 1440 minutos.", ephemeral=True); return
        await start_sleep_message(interaction, minutes)

class SleepActiveView(discord.ui.View):
    def __init__(self, user_id, guild_id):
        super().__init__(timeout=None); self.user_id=user_id; self.guild_id=guild_id
    def embed(self, record):
        stamp = int(record["ends_at"].timestamp())
        return discord.Embed(title="🌙 Temporizador de Sueño",
            description=f"⏱️ **Estado:** 🟢 Activo\nTermina <t:{stamp}:R> · **{record['ends_at']:%H:%M}**",
            color=discord.Color.from_rgb(229,211,173))
    async def change(self, interaction, delta):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Este temporizador no es tuyo.", ephemeral=True); return
        record = active_sleeps.get(sleep_key(self.user_id, self.guild_id))
        if not record or record["status"] != "active":
            await interaction.response.send_message("No hay un temporizador activo.", ephemeral=True); return
        now = datetime.now(TIMEZONE)
        # Reducir descuenta 5 minutos por pulsación, pero nunca permite
        # bajar de 5 minutos restantes. Desde 15 min se puede reducir dos veces.
        if delta < 0:
            record["ends_at"] = max(
                now + timedelta(minutes=5),
                record["ends_at"] + timedelta(minutes=delta),
            )
        else:
            record["ends_at"] = min(
                record["ends_at"] + timedelta(minutes=delta),
                now + timedelta(hours=24),
            )
        await interaction.response.edit_message(embed=self.embed(record), view=self)
    @discord.ui.button(label="➖ Reducir", style=discord.ButtonStyle.secondary)
    async def reduce(self, interaction, button): await self.change(interaction, -5)
    @discord.ui.button(label="➕ Extender", style=discord.ButtonStyle.secondary)
    async def extend(self, interaction, button): await self.change(interaction, 15)
    @discord.ui.button(label="❌ Cancelar", style=discord.ButtonStyle.danger)
    async def cancel(self, interaction, button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Este temporizador no es tuyo.", ephemeral=True); return
        record = active_sleeps.get(sleep_key(self.user_id, self.guild_id))
        if record: record["status"] = "cancelled"
        await interaction.response.edit_message(embed=discord.Embed(title="🌙 Temporizador de Sueño",
            description="⏱️ **Estado:** 🟡 Inactivo · Cancelado", color=discord.Color.from_rgb(229,211,173)), view=None)

async def start_sleep_message(interaction, minutes):
    if not interaction.guild:
        await interaction.response.send_message("Este comando solo funciona en un servidor.", ephemeral=True); return
    member = interaction.guild.get_member(interaction.user.id)
    if not member or not member.voice:
        await interaction.response.send_message("Primero entra a un canal de voz.", ephemeral=True); return
    k = sleep_key(member.id, interaction.guild.id)
    if active_sleeps.get(k, {}).get("status") == "active":
        await interaction.response.send_message("Ya tienes un temporizador activo.", ephemeral=True); return
    ends = datetime.now(TIMEZONE)+timedelta(minutes=minutes)
    record = {"user_id":member.id, "guild_id":interaction.guild.id, "status":"active", "ends_at":ends, "task":None}
    active_sleeps[k] = record
    view = SleepActiveView(member.id, interaction.guild.id)
    # Si el temporizador se eligió desde el selector inicial, sustituimos ese
    # mismo mensaje por el estado activo, en vez de dejar dos mensajes separados.
    if getattr(interaction, "message", None) is not None:
        await interaction.response.edit_message(embed=view.embed(record), view=view, content=None)
        record["message"] = interaction.message
    else:
        await interaction.response.send_message(embed=view.embed(record), view=view, ephemeral=True)
        record["message"] = await interaction.original_response()
    record["task"] = asyncio.create_task(run_sleep(record))

async def run_sleep(record):
    try:
        while record["status"] == "active":
            seconds = (record["ends_at"]-datetime.now(TIMEZONE)).total_seconds()
            if seconds <= 0: break
            await asyncio.sleep(min(seconds, 1))
        if record["status"] != "active": return
        guild = bot.get_guild(record["guild_id"])
        member = guild.get_member(record["user_id"]) if guild else None
        if member and member.voice:
            await member.move_to(None)
            record["status"] = "completed"
        else:
            record["status"] = "completed"
    except asyncio.CancelledError: raise
    except (discord.Forbidden, discord.HTTPException) as exc:
        record["status"], record["error"] = "failed", str(exc)
    finally:
        # El mensaje permanece visible; no borramos la evidencia del estado final.
        msg = record.get("message")
        if msg and record["status"] in {"completed", "failed"}:
            try:
                await msg.edit(embed=discord.Embed(title="🌙 Temporizador de Sueño",
                    description=f"⏱️ **Estado:** {'✅ Completado' if record['status']=='completed' else '❌ Error'}",
                    color=discord.Color.from_rgb(229,211,173)), view=None)
            except discord.HTTPException: pass

MONTHS = ["enero","febrero","marzo","abril","mayo","junio","julio","agosto","septiembre","octubre","noviembre","diciembre"]
def parse_time(value):
    try:
        h,m = map(int, value.strip().split(":"))
        if 0 <= h <= 23 and 0 <= m <= 59: return f"{h:02d}:{m:02d}"
    except (AttributeError, ValueError): pass
    return None

class ScheduleTimeModal(discord.ui.Modal, title="Elegir hora"):
    time = discord.ui.TextInput(label="Hora (HH:MM)", placeholder="23:59", max_length=5)
    def __init__(self, view):
        super().__init__(); self.parent_view=view
    async def on_submit(self, interaction):
        value=parse_time(self.time.value)
        if not value:
            await interaction.response.send_message("Hora inválida. Usa HH:MM.", ephemeral=True); return
        self.parent_view.selected_time=value
        await interaction.response.edit_message(embed=self.parent_view.embed(), view=self.parent_view)

class ScheduleCreateView(discord.ui.View):
    def __init__(self, user_id, guild_id):
        super().__init__(timeout=900)
        from datetime import date
        self.user_id=user_id; self.guild_id=guild_id
        self.selected_date=datetime.now(TIMEZONE).date()
        self.selected_time=None
        self.offset=0
        self.day_page=0
        self.build()
    def build(self):
        self.clear_items()
        today=datetime.now(TIMEZONE).date()
        month_index=(today.year*12+today.month-1)+self.offset
        year,month=divmod(month_index,12); month+=1
        import calendar
        days=calendar.monthrange(year,month)[1]

        # Discord permite hasta 25 componentes por View. Mostramos los días
        # 1–20 y, con un botón de página, los días restantes del mes.
        start_day=1 if self.day_page==0 else 21
        end_day=min(20,days) if self.day_page==0 else days
        for day in range(start_day, end_day+1):
            d=datetime(year,month,day).date()
            b=discord.ui.Button(label=f"{day} {['L','M','X','J','V','S','D'][d.weekday()]}",
                style=discord.ButtonStyle.primary if d==self.selected_date else discord.ButtonStyle.secondary,
                row=(day-start_day)//5, disabled=d<today)
            async def callback(interaction, date_value=d):
                if interaction.user.id != self.user_id:
                    await interaction.response.send_message("Este calendario no es para ti.", ephemeral=True); return
                self.selected_date=date_value
                self.day_page=0 if date_value.day<=20 else 1
                self.build()
                await interaction.response.edit_message(embed=self.embed(), view=self)
            b.callback=callback; self.add_item(b)

        self.add_item(discord.ui.Button(label="← Mes", row=4, disabled=self.offset<=0))
        self.children[-1].callback=self.prev_month
        self.add_item(discord.ui.Button(label="Mes →", row=4))
        self.children[-1].callback=self.next_month
        if days>20:
            label="Días 21–31 ▶" if self.day_page==0 else "◀ Días 1–20"
            b=discord.ui.Button(label=label, row=4)
            b.callback=self.toggle_day_page
            self.add_item(b)
        b=discord.ui.Button(label=f"🕐 {self.selected_time or 'Elegir hora'}", row=4)
        b.callback=self.choose_time; self.add_item(b)
        b=discord.ui.Button(label="Guardar horario", style=discord.ButtonStyle.success, row=4)
        b.callback=self.save; self.add_item(b)

    async def toggle_day_page(self, i):
        if i.user.id != self.user_id:
            await i.response.send_message("Este calendario no es para ti.", ephemeral=True); return
        self.day_page=1-self.day_page
        self.build()
        await i.response.edit_message(embed=self.embed(),view=self)
    async def prev_month(self, i):
        if i.user.id != self.user_id: await i.response.send_message("Este calendario no es para ti.", ephemeral=True); return
        self.offset=max(0,self.offset-1); self.day_page=0; self.build(); await i.response.edit_message(embed=self.embed(),view=self)
    async def next_month(self, i):
        if i.user.id != self.user_id: await i.response.send_message("Este calendario no es para ti.", ephemeral=True); return
        self.offset+=1; self.day_page=0; self.build(); await i.response.edit_message(embed=self.embed(),view=self)
    async def choose_time(self, i):
        if i.user.id != self.user_id: await i.response.send_message("Este calendario no es para ti.", ephemeral=True); return
        await i.response.send_modal(ScheduleTimeModal(self))
    async def save(self, i):
        if i.user.id != self.user_id: await i.response.send_message("Este calendario no es para ti.", ephemeral=True); return
        if not self.selected_time:
            await i.response.send_message("Primero selecciona una hora.",ephemeral=True); return
        target=datetime.fromisoformat(f"{self.selected_date.isoformat()}T{self.selected_time}:00").replace(tzinfo=TIMEZONE)
        if target<=datetime.now(TIMEZONE):
            await i.response.send_message("La fecha y hora deben ser futuras.",ephemeral=True); return
        sid=create_schedule(self.user_id,self.guild_id,self.selected_date.isoformat(),self.selected_time)
        create_execution(sid,self.selected_date.isoformat(),target.isoformat())
        await i.response.edit_message(embed=discord.Embed(title="🌙 Horario guardado",
            description=f"📅 **{self.selected_date:%d/%m/%Y}**\n🕐 **{self.selected_time}**\n🟢 Activo",
            color=discord.Color.from_rgb(229,211,173)),view=None)
        try:
            msg=await i.original_response()
            update_schedule(sid,channel_id=msg.channel.id,message_id=msg.id)
        except discord.HTTPException: pass
    def embed(self):
        return discord.Embed(title="🌙 Configurar horario",
            description=f"Mes: **{self.selected_date:%m/%Y}**\nFecha: **{self.selected_date:%d/%m/%Y}**\nHora: **{self.selected_time or 'Sin elegir'}**\n\nEl horario se ejecuta una sola vez.",
            color=discord.Color.from_rgb(229,211,173))

class SchedulePanelView(discord.ui.View):
    def __init__(self,user_id,guild_id):
        super().__init__(timeout=900); self.user_id=user_id; self.guild_id=guild_id
    @discord.ui.button(label="🌙 Crear horario",style=discord.ButtonStyle.primary)
    async def create(self,i,b):
        if i.user.id!=self.user_id: await i.response.send_message("Este panel no es para ti.",ephemeral=True); return
        v=ScheduleCreateView(self.user_id,self.guild_id)
        await i.response.edit_message(embed=v.embed(),view=v)
    @discord.ui.button(label="❌ Eliminar horario por ID",style=discord.ButtonStyle.danger)
    async def delete(self,i,b):
        if i.user.id!=self.user_id: await i.response.send_message("Este panel no es para ti.",ephemeral=True); return
        await i.response.send_modal(DeleteScheduleModal(self.user_id,self.guild_id))

class DeleteScheduleModal(discord.ui.Modal,title="Eliminar horario"):
    schedule_id=discord.ui.TextInput(label="ID del horario",placeholder="1",max_length=10)
    def __init__(self,user_id,guild_id): super().__init__(); self.user_id=user_id; self.guild_id=guild_id
    async def on_submit(self,i):
        try: sid=int(self.schedule_id.value)
        except ValueError: await i.response.send_message("ID inválido.",ephemeral=True); return
        if delete_schedule(sid,user_id=self.user_id,guild_id=self.guild_id):
            await i.response.send_message("Horario eliminado.",ephemeral=True)
        else: await i.response.send_message("No se encontró ese horario.",ephemeral=True)

class ScheduleReminderView(discord.ui.View):
    def __init__(self,bot_instance,row,execution_id,target):
        super().__init__(timeout=120); self.bot=bot_instance; self.row=row; self.execution_id=execution_id; self.target=target; self.message=None; self.refresh_task=None
    async def adjust(self,i,delta):
        if i.user.id!=int(self.row["user_id"]):
            await i.response.send_message("Este aviso no es para ti.",ephemeral=True); return
        new=self.target+timedelta(minutes=delta)
        if new<=datetime.now(TIMEZONE):
            await i.response.send_message("Esa hora ya pasó.",ephemeral=True); return
        self.target=new
        update_execution(self.execution_id,override_datetime=new.isoformat(),confirmed=1,status="pending",reminder_sent=1)
        await i.response.edit_message(content=f"🌙 XZ94 · Nueva hora: **{new:%d/%m/%Y %H:%M}**",view=None)
        self.stop()
    @discord.ui.button(label="-15 min",style=discord.ButtonStyle.secondary)
    async def minus(self,i,b): await self.adjust(i,-15)
    @discord.ui.button(label="+15 min",style=discord.ButtonStyle.primary)
    async def plus(self,i,b): await self.adjust(i,15)
    @discord.ui.button(label="Mantener",style=discord.ButtonStyle.success)
    async def keep(self,i,b):
        if i.user.id!=int(self.row["user_id"]):
            await i.response.send_message("Este aviso no es para ti.",ephemeral=True); return
        update_execution(self.execution_id,confirmed=1,status="pending",reminder_sent=1)
        await i.response.edit_message(content=f"🌙 Se mantiene la desconexión para **{self.target:%d/%m/%Y %H:%M}**.",view=None)
        self.stop()

@bot.event
async def on_ready():
    global scheduler, api_runner
    log.info("Conectado como %s",bot.user)
    try: await bot.tree.sync()
    except Exception: log.exception("No se pudieron sincronizar los comandos")
    if scheduler is None:
        scheduler=Scheduler(bot); asyncio.create_task(scheduler.start())
    if api_runner is None: api_runner=await start_control_api(bot)
    log.info("API disponible en http://127.0.0.1:8765")

@bot.tree.command(name="ping",description="Comprueba que XZ94 funciona.")
async def ping(i): await i.response.send_message("🏓 XZ94 está funcionando.")

@bot.tree.command(name="sleep",description="Abre el temporizador de sueño.")
async def sleep(i):
    if not i.guild or not i.guild.get_member(i.user.id) or not i.guild.get_member(i.user.id).voice:
        await i.response.send_message("Primero entra a un canal de voz.",ephemeral=True); return
    await i.response.send_message(embed=discord.Embed(title="🌙 Temporizador de Sueño",
        description="Elige una duración. Podrás ajustar o cancelar el temporizador.",
        color=discord.Color.from_rgb(229,211,173)),view=SleepConfigView(),ephemeral=True)

@bot.tree.command(name="schedule",description="Programa una desconexión de voz.")
async def schedule(i):
    if not i.guild:
        await i.response.send_message("Este comando solo funciona en un servidor.",ephemeral=True); return
    rows=get_schedules(i.user.id,i.guild.id,enabled_only=True)
    summary="\n".join(f"• ID `{r['id']}` · {r['date']} a las {r['time']}" for r in rows) or "No tienes horarios activos."
    await i.response.send_message(embed=discord.Embed(title="🌙 Horarios XZ94",
        description=summary,color=discord.Color.from_rgb(229,211,173)),
        view=SchedulePanelView(i.user.id,i.guild.id),ephemeral=True)

bot.run(TOKEN)
