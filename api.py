import asyncio
from datetime import datetime, timedelta
from aiohttp import web
import discord
from config import API_HOST, API_PORT, TIMEZONE
from database import create_execution, create_schedule, delete_schedule, get_schedule, get_schedules, update_schedule
from scheduler import disconnect_member

active_sleeps = {}

def key(user_id, guild_id):
    return f"{int(guild_id)}:{int(user_id)}"

def err(message, status=400):
    return web.json_response({"ok": False, "error": message}, status=status)

async def read_json(request):
    try:
        data = await request.json()
        return data if isinstance(data, dict) else None
    except (ValueError, TypeError):
        return None

async def member_for(bot, user_id, guild_id, require_voice=True):
    try:
        user_id, guild_id = int(user_id), int(guild_id)
    except (TypeError, ValueError):
        return None, "Los IDs deben ser números."
    guild = bot.get_guild(guild_id)
    if guild is None:
        return None, "El bot no está conectado a ese servidor."
    member = guild.get_member(user_id)
    if member is None:
        try:
            member = await guild.fetch_member(user_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return None, "No se encontró al usuario en el servidor."
    if require_voice and (not member.voice or not member.voice.channel):
        return None, "Primero entra a un canal de voz."
    return member, None

def sleep_state(record):
    if not record:
        return {"ok": True, "active": False, "status": "idle", "remaining_seconds": 0,
                "remaining_minutes": 0, "ends_at": None, "error": None}
    seconds = 0
    if record.get("status") == "active":
        seconds = max(0, int((record["ends_at"] - datetime.now(TIMEZONE)).total_seconds()))
    return {"ok": True, "active": record.get("status") == "active", "status": record.get("status"),
            "remaining_seconds": seconds, "remaining_minutes": (seconds + 59)//60,
            "ends_at": record["ends_at"].isoformat() if record.get("ends_at") else None,
            "error": record.get("error")}

async def health(request):
    bot = request.app["bot"]
    return web.json_response({"ok": True, "api": True, "bot_ready": bot.is_ready()})

async def status(request):
    u, g = request.query.get("user_id"), request.query.get("guild_id")
    if not u or not g: return err("Faltan user_id y guild_id.")
    try: k = key(u, g)
    except (TypeError, ValueError): return err("IDs inválidos.")
    return web.json_response(sleep_state(active_sleeps.get(k)))

async def run_sleep(bot, record):
    try:
        while record["status"] == "active":
            remaining = (record["ends_at"] - datetime.now(TIMEZONE)).total_seconds()
            if remaining <= 0: break
            await asyncio.sleep(min(remaining, 1))
        if record["status"] != "active": return
        member, problem = await member_for(bot, record["user_id"], record["guild_id"], False)
        if member and member.voice:
            ok = await disconnect_member(member)
            record["status"] = "completed" if ok else "failed"
            if not ok: record["error"] = "Discord no pudo desconectar al usuario."
        else:
            record["status"] = "completed"
            record["error"] = "El usuario ya no estaba en un canal de voz."
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        record["status"], record["error"] = "failed", str(exc)

async def start_sleep(request):
    data = await read_json(request)
    if not data: return err("JSON inválido.")
    try:
        u, g, minutes = int(data["user_id"]), int(data["guild_id"]), int(data["minutes"])
    except (KeyError, TypeError, ValueError): return err("user_id, guild_id y minutes son obligatorios.")
    if not 1 <= minutes <= 1440: return err("El tiempo debe estar entre 1 y 1440 minutos.")
    member, problem = await member_for(request.app["bot"], u, g)
    if problem: return err(problem, 409)
    k = key(u, g); old = active_sleeps.get(k)
    if old and old["status"] == "active": return err("Ya tienes un Sleep activo.", 409)
    record = {"user_id":u, "guild_id":g, "status":"active",
              "ends_at":datetime.now(TIMEZONE)+timedelta(minutes=minutes),
              "task":None, "error":None, "member_id":member.id}
    active_sleeps[k] = record
    record["task"] = asyncio.create_task(run_sleep(request.app["bot"], record))
    return web.json_response(sleep_state(record))

async def adjust(request):
    data = await read_json(request)
    if not data: return err("JSON inválido.")
    try:
        k = key(data["user_id"], data["guild_id"]); delta = int(data.get("delta", 0))
    except (KeyError, TypeError, ValueError): return err("Datos inválidos.")
    record = active_sleeps.get(k)
    if not record or record["status"] != "active": return err("No hay un Sleep activo.", 409)
    now = datetime.now(TIMEZONE)
    record["ends_at"] = max(now + timedelta(minutes=1),
                            min(record["ends_at"] + timedelta(minutes=delta), now + timedelta(hours=24)))
    return web.json_response(sleep_state(record))

async def set_time(request):
    data = await read_json(request)
    if not data: return err("JSON inválido.")
    try: k, minutes = key(data["user_id"], data["guild_id"]), int(data["minutes"])
    except (KeyError, TypeError, ValueError): return err("Datos inválidos.")
    if not 1 <= minutes <= 1440: return err("Usa entre 1 y 1440 minutos.")
    record = active_sleeps.get(k)
    if not record or record["status"] != "active": return err("No hay un Sleep activo.", 409)
    record["ends_at"] = datetime.now(TIMEZONE) + timedelta(minutes=minutes)
    return web.json_response(sleep_state(record))

async def cancel(request):
    data = await read_json(request)
    if not data: return err("JSON inválido.")
    try: k = key(data["user_id"], data["guild_id"])
    except (KeyError, TypeError, ValueError): return err("IDs inválidos.")
    record = active_sleeps.get(k)
    if not record or record["status"] != "active": return err("No hay un Sleep activo.", 409)
    record["status"] = "cancelled"
    task = record.get("task")
    if task and not task.done(): task.cancel()
    return web.json_response(sleep_state(record))

def schedule_dict(row):
    return {"id":row["id"], "user_id":row["user_id"], "guild_id":row["guild_id"],
            "date":row["date"], "time":row["time"], "enabled":bool(row["enabled"])}

async def schedules(request):
    try:
        u = int(request.query["user_id"])
        g = int(request.query["guild_id"]) if request.query.get("guild_id") else None
    except (KeyError, TypeError, ValueError): return err("user_id es obligatorio y debe ser numérico.")
    rows = get_schedules(u, g)
    return web.json_response({"ok":True, "schedules":[schedule_dict(r) for r in rows]})

async def schedule_create(request):
    data = await read_json(request)
    if not data: return err("JSON inválido.")
    try:
        u, g = int(data["user_id"]), int(data["guild_id"])
        date, time = str(data["date"]), str(data["time"])
        target = datetime.fromisoformat(f"{date}T{time}:00").replace(tzinfo=TIMEZONE)
    except (KeyError, TypeError, ValueError): return err("Fecha, hora o IDs inválidos.")
    if target <= datetime.now(TIMEZONE): return err("La fecha y hora deben ser futuras.")
    try:
        sid = create_schedule(u, g, date, time)
        create_execution(sid, date, target.isoformat())
    except Exception as exc: return err(f"No se pudo guardar el horario: {exc}", 500)
    return web.json_response({"ok":True, "schedule_id":sid})

async def schedule_update(request):
    data = await read_json(request)
    if not data: return err("JSON inválido.")
    try: sid, uid = int(data["schedule_id"]), int(data["user_id"])
    except (KeyError, TypeError, ValueError): return err("schedule_id y user_id son obligatorios.")
    row = get_schedule(sid)
    if not row or int(row["user_id"]) != uid: return err("Horario no encontrado.", 404)
    fields = {}
    if "enabled" in data: fields["enabled"] = int(bool(data["enabled"]))
    if "date" in data or "time" in data:
        date, time = data.get("date", row["date"]), data.get("time", row["time"])
        try: target = datetime.fromisoformat(f"{date}T{time}:00").replace(tzinfo=TIMEZONE)
        except (TypeError, ValueError): return err("Fecha u hora inválida.")
        if target <= datetime.now(TIMEZONE): return err("La fecha y hora deben ser futuras.")
        fields.update(date=date, time=time)
    return web.json_response({"ok":update_schedule(sid, **fields)})

async def schedule_delete(request):
    data = await read_json(request)
    if not data: return err("JSON inválido.")
    try: sid, uid = int(data["schedule_id"]), int(data["user_id"])
    except (KeyError, TypeError, ValueError): return err("schedule_id y user_id son obligatorios.")
    return web.json_response({"ok":delete_schedule(sid, user_id=uid)})

def create_app(bot):
    app = web.Application(client_max_size=64*1024)
    app["bot"] = bot
    app.router.add_get("/api/health", health)
    app.router.add_get("/api/sleep/status", status)
    app.router.add_post("/api/sleep/start", start_sleep)
    app.router.add_post("/api/sleep/adjust", adjust)
    app.router.add_post("/api/sleep/set", set_time)
    app.router.add_post("/api/sleep/cancel", cancel)
    app.router.add_get("/api/schedules", schedules)
    app.router.add_post("/api/schedules", schedule_create)
    app.router.add_patch("/api/schedules", schedule_update)
    app.router.add_delete("/api/schedules", schedule_delete)
    return app

async def start_control_api(bot):
    runner = web.AppRunner(create_app(bot))
    await runner.setup()
    try:
        await web.TCPSite(runner, API_HOST, API_PORT).start()
    except Exception:
        await runner.cleanup()
        raise
    return runner
