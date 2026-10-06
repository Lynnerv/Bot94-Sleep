import asyncio

from datetime import (
    datetime,
    timedelta
)

from aiohttp import web

from config import (
    API_HOST,
    API_PORT,
    TIMEZONE
)

from database import (
    create_schedule,
    get_schedules,
    update_schedule,
    delete_schedule,
    get_sleep_check,
    set_sleep_check
)

from scheduler import execute_actions


active_sleeps = {}


def key(user_id, guild_id):

    return (
        f"{int(guild_id)}:"
        f"{int(user_id)}"
    )


def error(message, status=400):

    return web.json_response(
        {
            "ok": False,
            "error": message
        },
        status=status
    )


async def body(request):

    try:

        return await request.json()

    except Exception:

        return None


async def get_member(
    bot,
    user_id,
    guild_id
):

    try:

        user_id = int(user_id)
        guild_id = int(guild_id)

    except (
        TypeError,
        ValueError
    ):

        return None, "IDs inválidos."

    guild = bot.get_guild(
        guild_id
    )

    if not guild:

        return None, (
            "El bot no está conectado "
            "a ese servidor."
        )

    member = guild.get_member(
        user_id
    )

    if not member:

        try:

            member = await guild.fetch_member(
                user_id
            )

        except Exception:

            return None, (
                "No pude encontrar "
                "al usuario."
            )

    if (
        not member.voice
        or not member.voice.channel
    ):

        return None, (
            "Primero entra a un canal "
            "de voz."
        )

    return member, None


def state(record):

    if not record:

        return {
            "ok": True,
            "active": False,
            "status": "idle"
        }

    remaining = 0

    if (
        record["status"] == "active"
        and record["ends_at"]
    ):

        remaining = max(
            0,
            int(
                (
                    record["ends_at"]
                    -
                    datetime.now(TIMEZONE)
                ).total_seconds()
            )
        )

    return {
        "ok": True,
        "active":
            record["status"] == "active",

        "status":
            record["status"],

        "remaining_seconds":
            remaining,

        "remaining_minutes":
            (
                (remaining + 59) // 60
                if remaining
                else 0
            ),

        "ends_at":
            (
                record["ends_at"].isoformat()
                if record["ends_at"]
                else None
            ),

        "mute":
            record["mute"],

        "deafen":
            record["deafen"],

        "disconnect":
            record["disconnect"],

        "error":
            record.get("error")
    }


async def health(request):

    bot = request.app["bot"]

    return web.json_response(
        {
            "ok": True,
            "api": True,
            "bot_ready": bot.is_ready()
        }
    )


async def status(request):

    user_id = request.query.get(
        "user_id"
    )

    guild_id = request.query.get(
        "guild_id"
    )

    if not user_id or not guild_id:

        return error(
            "Faltan user_id y guild_id."
        )

    return web.json_response(
        state(
            active_sleeps.get(
                key(
                    user_id,
                    guild_id
                )
            )
        )
    )


async def start_sleep(request):

    data = await body(
        request
    )

    if not isinstance(
        data,
        dict
    ):

        return error(
            "JSON inválido."
        )

    try:

        user_id = int(
            data["user_id"]
        )

        guild_id = int(
            data["guild_id"]
        )

        minutes = int(
            data["minutes"]
        )

    except Exception:

        return error(
            "user_id, guild_id y minutes deben ser válidos."
        )

    if not 1 <= minutes <= 1440:

        return error(
            "El tiempo debe estar entre "
            "1 y 1440 minutos."
        )

    mute = bool(
        data.get("mute")
    )

    deafen = bool(
        data.get("deafen")
    )

    disconnect = bool(
        data.get("disconnect")
    )

    if deafen:

        mute = True

    if not (
        mute
        or deafen
        or disconnect
    ):

        return error(
            "Selecciona al menos una acción."
        )

    member, problem = await get_member(
        request.app["bot"],
        user_id,
        guild_id
    )

    if problem:

        return error(
            problem,
            409
        )

    sleep_key = key(
        user_id,
        guild_id
    )

    old = active_sleeps.get(
        sleep_key
    )

    if (
        old
        and old["status"] == "active"
    ):

        return error(
            "Ya tienes un Sleep activo.",
            409
        )

    record = {

        "user_id":
            user_id,

        "guild_id":
            guild_id,

        "minutes":
            minutes,

        "mute":
            mute,

        "deafen":
            deafen,

        "disconnect":
            disconnect,

        "status":
            "active",

        "ends_at":
            (
                datetime.now(TIMEZONE)
                +
                timedelta(
                    minutes=minutes
                )
            ),

        "task":
            None,

        "error":
            None,

        "member_id":
            member.id
    }

    active_sleeps[
        sleep_key
    ] = record

    record["task"] = asyncio.create_task(
        run_sleep(
            request.app["bot"],
            record
        )
    )

    return web.json_response(
        state(record)
    )


async def run_sleep(
    bot,
    record
):

    try:

        while (
            record["status"] == "active"
        ):

            seconds = (
                record["ends_at"]
                -
                datetime.now(TIMEZONE)
            ).total_seconds()

            if seconds <= 0:

                break

            await asyncio.sleep(
                min(seconds, 1)
            )

        if (
            record["status"]
            != "active"
        ):

            return

        guild = bot.get_guild(
            record["guild_id"]
        )

        member = (
            guild.get_member(
                record["user_id"]
            )
            if guild
            else None
        )

        if (
            not member
            or not member.voice
        ):

            record["status"] = (
                "completed"
            )

            record["error"] = (
                "Ya no estaba en voz."
            )

            return

        success = await execute_actions(
            member,
            record["mute"],
            record["deafen"],
            record["disconnect"]
        )

        record["status"] = (
            "completed"
            if success
            else "failed"
        )

        if not success:

            record["error"] = (
                "No se pudieron ejecutar "
                "las acciones."
            )

    except asyncio.CancelledError:

        pass

    except Exception as exc:

        record["status"] = "failed"

        record["error"] = str(exc)


async def adjust(request):

    data = await body(
        request
    )

    try:

        sleep_key = key(
            data["user_id"],
            data["guild_id"]
        )

        delta = int(
            data.get(
                "delta",
                0
            )
        )

    except Exception:

        return error(
            "Datos inválidos."
        )

    record = active_sleeps.get(
        sleep_key
    )

    if (
        not record
        or record["status"]
        != "active"
    ):

        return error(
            "No hay un Sleep activo.",
            409
        )

    now = datetime.now(
        TIMEZONE
    )

    new_end = (
        record["ends_at"]
        +
        timedelta(
            minutes=delta
        )
    )

    minimum = (
        now
        +
        timedelta(
            minutes=1
        )
    )

    maximum = (
        now
        +
        timedelta(
            hours=24
        )
    )

    new_end = max(
        new_end,
        minimum
    )

    new_end = min(
        new_end,
        maximum
    )

    record["ends_at"] = new_end

    return web.json_response(
        state(record)
    )


async def set_time(request):

    data = await body(
        request
    )

    try:

        sleep_key = key(
            data["user_id"],
            data["guild_id"]
        )

        minutes = int(
            data["minutes"]
        )

    except Exception:

        return error(
            "Datos inválidos."
        )

    if not 1 <= minutes <= 1440:

        return error(
            "Tiempo inválido."
        )

    record = active_sleeps.get(
        sleep_key
    )

    if (
        not record
        or record["status"]
        != "active"
    ):

        return error(
            "No hay un Sleep activo.",
            409
        )

    record["ends_at"] = (
        datetime.now(TIMEZONE)
        +
        timedelta(
            minutes=minutes
        )
    )

    record["minutes"] = minutes

    return web.json_response(
        state(record)
    )


async def cancel(request):

    data = await body(
        request
    )

    try:

        sleep_key = key(
            data["user_id"],
            data["guild_id"]
        )

    except Exception:

        return error(
            "Datos inválidos."
        )

    record = active_sleeps.get(
        sleep_key
    )

    if (
        not record
        or record["status"]
        != "active"
    ):

        return error(
            "No hay un Sleep activo.",
            409
        )

    record["status"] = (
        "cancelled"
    )

    task = record.get(
        "task"
    )

    if (
        task
        and not task.done()
    ):

        task.cancel()

    return web.json_response(
        state(record)
    )


async def schedules(request):

    try:

        user_id = int(
            request.query["user_id"]
        )

    except Exception:

        return error(
            "Falta user_id."
        )

    guild_value = request.query.get(
        "guild_id"
    )

    guild_id = (
        int(guild_value)
        if guild_value
        else None
    )

    rows = get_schedules(
        user_id,
        guild_id
    )

    import json

    return web.json_response(
        {
            "ok": True,

            "schedules": [

                {
                    "id":
                        row["id"],

                    "user_id":
                        row["user_id"],

                    "guild_id":
                        row["guild_id"],

                    "time":
                        row["time"],

                    "days":
                        json.loads(
                            row["days"]
                        ),

                    "mute":
                        bool(
                            row["mute"]
                        ),

                    "deafen":
                        bool(
                            row["deafen"]
                        ),

                    "disconnect":
                        bool(
                            row["disconnect"]
                        ),

                    "confirmation":
                        bool(
                            row["confirmation"]
                        ),

                    "enabled":
                        bool(
                            row["enabled"]
                        )
                }

                for row in rows
            ]
        }
    )


async def schedule_create(request):

    data = await body(
        request
    )

    try:

        user_id = int(
            data["user_id"]
        )

        guild_id = int(
            data["guild_id"]
        )

        schedule_time = str(
            data["time"]
        )

        days = list(
            data["days"]
        )

    except Exception:

        return error(
            "Faltan datos."
        )

    mute = bool(
        data.get("mute")
    )

    deafen = bool(
        data.get("deafen")
    )

    disconnect = bool(
        data.get("disconnect")
    )

    confirmation = bool(
        data.get(
            "confirmation",
            True
        )
    )

    if deafen:

        mute = True

    if not (
        mute
        or deafen
        or disconnect
    ):

        return error(
            "Selecciona al menos una acción."
        )

    schedule_id = create_schedule(
        user_id,
        guild_id,
        schedule_time,
        days,
        mute,
        deafen,
        disconnect,
        confirmation
    )

    return web.json_response(
        {
            "ok": True,
            "schedule_id":
                schedule_id
        },
        status=201
    )


async def schedule_update(request):

    data = await body(
        request
    )

    try:

        schedule_id = int(
            data["schedule_id"]
        )

    except Exception:

        return error(
            "schedule_id inválido."
        )

    fields = {
        key: data[key]
        for key in (
            "time",
            "days",
            "mute",
            "deafen",
            "disconnect",
            "confirmation",
            "enabled"
        )
        if key in data
    }

    if fields.get("deafen"):

        fields["mute"] = True

    return web.json_response(
        {
            "ok":
                update_schedule(
                    schedule_id,
                    **fields
                )
        }
    )


async def schedule_delete(request):

    data = await body(
        request
    )

    try:

        schedule_id = int(
            data["schedule_id"]
        )

    except Exception:

        return error(
            "schedule_id inválido."
        )

    return web.json_response(
        {
            "ok":
                delete_schedule(
                    schedule_id
                )
        }
    )


async def sleep_check_get(request):

    try:

        user_id = int(
            request.query["user_id"]
        )

        guild_id = int(
            request.query["guild_id"]
        )

    except Exception:

        return error(
            "Faltan IDs."
        )

    row = get_sleep_check(
        user_id,
        guild_id
    )

    return web.json_response(
        {
            "ok": True,

            "enabled":
                bool(row["enabled"])
                if row
                else False,

            "interval_minutes":
                row["interval_minutes"]
                if row
                else 15,

            "response_minutes":
                row["response_minutes"]
                if row
                else 2
        }
    )


async def sleep_check_set(request):

    data = await body(
        request
    )

    try:

        user_id = int(
            data["user_id"]
        )

        guild_id = int(
            data["guild_id"]
        )

        enabled = bool(
            data.get("enabled")
        )

        interval = int(
            data.get(
                "interval_minutes",
                15
            )
        )

        response = int(
            data.get(
                "response_minutes",
                2
            )
        )

    except Exception:

        return error(
            "Datos inválidos."
        )

    if not (
        1 <= interval <= 1440
    ):

        return error(
            "Intervalo inválido."
        )

    if not (
        1 <= response <= 30
    ):

        return error(
            "Tiempo de respuesta inválido."
        )

    next_check = None

    if enabled:

        next_check = (
            datetime.now(TIMEZONE)
            +
            timedelta(
                minutes=interval
            )
        ).isoformat(
            timespec="seconds"
        )

    set_sleep_check(
        user_id,
        guild_id,
        enabled,
        interval,
        response,
        next_check
    )

    return web.json_response(
        {
            "ok": True
        }
    )


def create_app(bot):

    application = web.Application()

    application["bot"] = bot

    application.router.add_get(
        "/api/health",
        health
    )

    application.router.add_get(
        "/api/sleep/status",
        status
    )

    application.router.add_post(
        "/api/sleep/start",
        start_sleep
    )

    application.router.add_post(
        "/api/sleep/adjust",
        adjust
    )

    application.router.add_post(
        "/api/sleep/set",
        set_time
    )

    application.router.add_post(
        "/api/sleep/cancel",
        cancel
    )

    application.router.add_get(
        "/api/schedules",
        schedules
    )

    application.router.add_post(
        "/api/schedules",
        schedule_create
    )

    application.router.add_put(
        "/api/schedules",
        schedule_update
    )

    application.router.add_delete(
        "/api/schedules",
        schedule_delete
    )

    application.router.add_get(
        "/api/sleep-check",
        sleep_check_get
    )

    application.router.add_post(
        "/api/sleep-check",
        sleep_check_set
    )

    return application


async def start_control_api(bot):

    runner = web.AppRunner(
        create_app(bot)
    )

    await runner.setup()

    try:

        site = web.TCPSite(
            runner,
            API_HOST,
            API_PORT
        )

        await site.start()

    except OSError:

        await runner.cleanup()

        raise

    return runner