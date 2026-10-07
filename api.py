import asyncio
import json
from datetime import datetime, timedelta

from aiohttp import web
import discord

from config import API_HOST, API_PORT, TIMEZONE
from database import (
    create_execution,
    create_schedule,
    delete_schedule,
    get_schedule,
    get_schedules,
    update_schedule,
)
from scheduler import disconnect_member

active_sleeps = {}


def key(user_id, guild_id):
    return f"{int(guild_id)}:{int(user_id)}"


def error(message, status=400):
    return web.json_response({"ok": False, "error": message}, status=status)


async def body(request):
    try:
        return await request.json()
    except Exception:
        return None


async def get_member(bot, user_id, guild_id, require_voice=True):
    try:
        user_id, guild_id = int(user_id), int(guild_id)
    except (TypeError, ValueError):
        return None, "IDs inválidos."
    guild = bot.get_guild(guild_id)
    if not guild:
        return None, "El bot no está conectado a ese servidor."
    member = guild.get_member(user_id)
    if not member:
        try:
            member = await guild.fetch_member(user_id)
        except Exception:
            return None, "No pude encontrar al usuario."
    if require_voice and (not member.voice or not member.voice.channel):
        return None, "Primero entra a un canal de voz."
    return member, None


def state(record):
    if not record:
        return {"ok": True, "active": False, "status": "idle", "remaining_seconds": 0, "remaining_minutes": 0, "ends_at": None}
    remaining = 0
    if record["status"] == "active" and record["ends_at"]:
        remaining = max(0, int((record["ends_at"] - datetime.now(TIMEZONE)).total_seconds()))
    return {
        "ok": True,
        "active": record["status"] == "active",
        "status": record["status"],
        "remaining_seconds": remaining,
        "remaining_minutes": (remaining + 59) // 60 if remaining else 0,
        "ends_at": record["ends_at"].isoformat() if record["ends_at"] else None,
    }


async def health(request):
    return web.json_response({"ok": True, "api": True, "bot_ready": request.app["bot"].is_ready()})


async def status(request):
    user_id, guild_id = request.query.get("user_id"), request.query.get("guild_id")
    if not user_id or not guild_id:
        return error("Faltan user_id y guild_id.")
    return web.json_response(state(active_sleeps.get(key(user_id, guild_id))))


async def start_sleep(request):
    data = await body(request)
    if not isinstance(data, dict):
        return error("JSON inválido.")
    try:
        user_id, guild_id, minutes = int(data["user_id"]), int(data["guild_id"]), int(data["minutes"])
    except Exception:
        return error("user_id, guild_id y minutes deben ser válidos.")
    if not 1 <= minutes <= 1440:
        return error("El tiempo debe estar entre 1 y 1440 minutos.")
    member, problem = await get_member(request.app["bot"], user_id, guild_id)
    if problem:
        return error(problem, 409)
    sleep_key = key(user_id, guild_id)
    old = active_sleeps.get(sleep_key)
    if old and old["status"] == "active":
        return error("Ya tienes un Sleep activo.", 409)
    record = {
        "user_id": user_id,
        "guild_id": guild_id,
        "status": "active",
        "ends_at": datetime.now(TIMEZONE) + timedelta(minutes=minutes),
        "task": None,
        "error": None,
        "channel_id": data.get("channel_id"),
        "message_id": data.get("message_id"),
        "member_id": member.id,
    }
    active_sleeps[sleep_key] = record
    record["task"] = asyncio.create_task(run_sleep(request.app["bot"], record))
    return web.json_response(state(record))


async def run_sleep(bot, record):
    try:
        while record["status"] == "active":
            seconds = (record["ends_at"] - datetime.now(TIMEZONE)).total_seconds()
            if seconds <= 0:
                break
            await asyncio.sleep(min(seconds, 1))
        if record["status"] != "active":
            return

        guild = bot.get_guild(record["guild_id"])
        member = guild.get_member(record["user_id"]) if guild else None
        if member and member.voice:
            success = await disconnect_member(member)
            record["status"] = "completed" if success else "failed"
            if not success:
                record["error"] = "No se pudo desconectar al usuario."
        else:
            record["status"] = "completed"
            record["error"] = "El usuario ya no estaba en voz."
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        record["status"] = "failed"
        record["error"] = str(exc)
    finally:
        # El mensaje del Sleep se elimina siempre al terminar la cuenta regresiva,
        # incluso si el usuario ya no estaba en voz o Discord devuelve un error.
        view = record.get("view")
        if view and view.refresh_task and not view.refresh_task.done():
            view.refresh_task.cancel()
        # Las respuestas efímeras no se pueden recuperar con fetch_message()
        # porque no existen como mensajes normales del canal. Si conservamos
        # el objeto InteractionMessage, Discord permite eliminarlo directamente.
        message_obj = record.get("message")
        if message_obj is not None:
            try:
                await message_obj.delete()
                return
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass
            except Exception:
                pass

        channel_id = record.get("channel_id")
        message_id = record.get("message_id")
        if channel_id and message_id:
            try:
                channel = bot.get_channel(int(channel_id)) or await bot.fetch_channel(int(channel_id))
                message = await channel.fetch_message(int(message_id))
                await message.delete()
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass
            except Exception:
                pass


async def adjust(request):
    data = await body(request)
    try:
        sleep_key = key(data["user_id"], data["guild_id"])
        delta = int(data.get("delta", 0))
    except Exception:
        return error("Datos inválidos.")
    record = active_sleeps.get(sleep_key)
    if not record or record["status"] != "active":
        return error("No hay un Sleep activo.", 409)
    now = datetime.now(TIMEZONE)
    record["ends_at"] = max(now + timedelta(minutes=1), min(record["ends_at"] + timedelta(minutes=delta), now + timedelta(hours=24)))
    return web.json_response(state(record))


async def set_time(request):
    data = await body(request)
    try:
        sleep_key = key(data["user_id"], data["guild_id"])
        minutes = int(data["minutes"])
    except Exception:
        return error("Datos inválidos.")
    if not 1 <= minutes <= 1440:
        return error("Tiempo inválido.")
    record = active_sleeps.get(sleep_key)
    if not record or record["status"] != "active":
        return error("No hay un Sleep activo.", 409)
    record["ends_at"] = datetime.now(TIMEZONE) + timedelta(minutes=minutes)
    return web.json_response(state(record))


async def cancel(request):
    data = await body(request)
    try:
        sleep_key = key(data["user_id"], data["guild_id"])
    except Exception:
        return error("Datos inválidos.")
    record = active_sleeps.get(sleep_key)
    if not record or record["status"] != "active":
        return error("No hay un Sleep activo.", 409)
    record["status"] = "cancelled"
    if record.get("task") and not record["task"].done():
        record["task"].cancel()
    return web.json_response(state(record))


def schedule_json(row):
    return {
        "id": row["id"],
        "user_id": row["user_id"],
        "guild_id": row["guild_id"],
        "date": row["date"],
        "time": row["time"],
        "enabled": bool(row["enabled"]),
    }


async def schedules(request):
    try:
        user_id = int(request.query["user_id"])
        guild_id = request.query.get("guild_id")
        guild_id = int(guild_id) if guild_id else None
    except Exception:
        return error("Falta user_id.")
    rows = get_schedules(user_id, guild_id)
    return web.json_response({"ok": True, "schedules": [schedule_json(r) for r in rows]})


async def schedule_create(request):
    data = await body(request)
    try:
        target = datetime.fromisoformat(f"{data['date']}T{data['time']}:00").replace(tzinfo=TIMEZONE)
    except Exception:
        return error("Fecha u hora inválida.")
    if target <= datetime.now(TIMEZONE):
        return error("La fecha y hora deben ser futuras.")
    try:
        sid = create_schedule(data["user_id"], data["guild_id"], data["date"], data["time"])
        create_execution(sid, data["date"], target.isoformat())
    except Exception as exc:
        return error(f"No se pudo crear el horario: {exc}")
    return web.json_response({"ok": True, "schedule_id": sid})


async def schedule_update(request):
    data = await body(request)
    try:
        sid = int(data["schedule_id"])
    except Exception:
        return error("schedule_id inválido.")
    if "user_id" in data:
        row = get_schedule(sid)
        if not row or int(row["user_id"]) != int(data["user_id"]):
            return error("Horario no encontrado.", 404)
    fields = {k: data[k] for k in ("date", "time", "enabled") if k in data}
    return web.json_response({"ok": update_schedule(sid, **fields)})


async def schedule_delete(request):
    data = await body(request)
    try:
        deleted = delete_schedule(int(data["schedule_id"]), int(data["user_id"]) if data.get("user_id") else None)
    except Exception:
        return error("Datos inválidos.")
    return web.json_response({"ok": deleted})


def create_app(bot):
    app = web.Application()
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
    app = create_app(bot)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, API_HOST, API_PORT)
    await site.start()
    return runner
