import json
import sqlite3

from contextlib import contextmanager
from datetime import datetime

from config import DATABASE_FILE, TIMEZONE


def now_iso():
    return datetime.now(
        TIMEZONE
    ).isoformat(timespec="seconds")


@contextmanager
def db():

    connection = sqlite3.connect(
        DATABASE_FILE
    )

    connection.row_factory = sqlite3.Row

    try:
        yield connection
        connection.commit()

    finally:
        connection.close()


def init_db():

    with db() as connection:

        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS schedules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,

                user_id INTEGER NOT NULL,
                guild_id INTEGER NOT NULL,

                time TEXT NOT NULL,
                days TEXT NOT NULL,

                mute INTEGER NOT NULL DEFAULT 0,
                deafen INTEGER NOT NULL DEFAULT 0,
                disconnect INTEGER NOT NULL DEFAULT 1,

                confirmation INTEGER NOT NULL DEFAULT 1,
                enabled INTEGER NOT NULL DEFAULT 1,

                created_at TEXT NOT NULL
            );


            CREATE TABLE IF NOT EXISTS schedule_executions (

                id INTEGER PRIMARY KEY AUTOINCREMENT,

                schedule_id INTEGER NOT NULL,

                execution_date TEXT NOT NULL,

                original_datetime TEXT NOT NULL,

                override_datetime TEXT,

                status TEXT NOT NULL DEFAULT 'pending',

                reminder_sent INTEGER NOT NULL DEFAULT 0,

                confirmed INTEGER NOT NULL DEFAULT 0,

                created_at TEXT NOT NULL,

                updated_at TEXT NOT NULL,

                UNIQUE(
                    schedule_id,
                    execution_date
                )
            );


            CREATE TABLE IF NOT EXISTS sleep_checks (

                user_id INTEGER NOT NULL,

                guild_id INTEGER NOT NULL,

                enabled INTEGER NOT NULL DEFAULT 0,

                interval_minutes INTEGER NOT NULL DEFAULT 15,

                response_minutes INTEGER NOT NULL DEFAULT 2,

                next_check_at TEXT,

                waiting_until TEXT,

                last_prompt_at TEXT,

                muted_by_sleep_check INTEGER NOT NULL DEFAULT 0,

                PRIMARY KEY (
                    user_id,
                    guild_id
                )
            );


            CREATE TABLE IF NOT EXISTS voice_effects (

                user_id INTEGER NOT NULL,

                guild_id INTEGER NOT NULL,

                mute_by_bot INTEGER NOT NULL DEFAULT 0,

                deafen_by_bot INTEGER NOT NULL DEFAULT 0,

                updated_at TEXT NOT NULL,

                PRIMARY KEY (
                    user_id,
                    guild_id
                )
            );
            """
        )


init_db()


def create_schedule(
    user_id,
    guild_id,
    time,
    days,
    mute,
    deafen,
    disconnect,
    confirmation
):

    mute = bool(
        mute or deafen
    )

    with db() as connection:

        cursor = connection.execute(
            """
            INSERT INTO schedules
            (
                user_id,
                guild_id,
                time,
                days,
                mute,
                deafen,
                disconnect,
                confirmation,
                enabled,
                created_at
            )

            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
            """,

            (
                int(user_id),
                int(guild_id),
                time,
                json.dumps(
                    days,
                    ensure_ascii=False
                ),
                int(mute),
                int(deafen),
                int(disconnect),
                int(confirmation),
                now_iso()
            )
        )

        return cursor.lastrowid


def get_schedules(
    user_id=None,
    guild_id=None,
    enabled_only=False
):

    query = """
        SELECT *
        FROM schedules
        WHERE 1=1
    """

    parameters = []

    if user_id is not None:

        query += """
            AND user_id = ?
        """

        parameters.append(
            int(user_id)
        )

    if guild_id is not None:

        query += """
            AND guild_id = ?
        """

        parameters.append(
            int(guild_id)
        )

    if enabled_only:

        query += """
            AND enabled = 1
        """

    query += """
        ORDER BY time, id
    """

    with db() as connection:

        return list(
            connection.execute(
                query,
                parameters
            ).fetchall()
        )


def get_schedule(schedule_id):

    with db() as connection:

        return connection.execute(
            """
            SELECT *
            FROM schedules
            WHERE id = ?
            """,
            (int(schedule_id),)
        ).fetchone()


def update_schedule(
    schedule_id,
    **fields
):

    allowed = {
        "time",
        "days",
        "mute",
        "deafen",
        "disconnect",
        "confirmation",
        "enabled"
    }

    fields = {
        key: value
        for key, value in fields.items()
        if key in allowed
    }

    if "deafen" in fields and fields["deafen"]:

        fields["mute"] = 1

    if (
        "days" in fields
        and isinstance(fields["days"], list)
    ):

        fields["days"] = json.dumps(
            fields["days"],
            ensure_ascii=False
        )

    for key in (
        "mute",
        "deafen",
        "disconnect",
        "confirmation",
        "enabled"
    ):

        if key in fields:

            fields[key] = int(
                bool(fields[key])
            )

    if not fields:

        return False

    with db() as connection:

        cursor = connection.execute(
            "UPDATE schedules SET "
            +
            ",".join(
                f"{key} = ?"
                for key in fields
            )
            +
            " WHERE id = ?",

            list(fields.values())
            +
            [int(schedule_id)]
        )

        return cursor.rowcount > 0


def delete_schedule(schedule_id):

    with db() as connection:

        connection.execute(
            """
            DELETE FROM schedule_executions
            WHERE schedule_id = ?
            """,
            (int(schedule_id),)
        )

        cursor = connection.execute(
            """
            DELETE FROM schedules
            WHERE id = ?
            """,
            (int(schedule_id),)
        )

        return cursor.rowcount > 0


def get_execution(
    schedule_id,
    execution_date
):

    with db() as connection:

        return connection.execute(
            """
            SELECT *
            FROM schedule_executions

            WHERE schedule_id = ?
            AND execution_date = ?
            """,

            (
                int(schedule_id),
                execution_date
            )
        ).fetchone()


def create_execution(
    schedule_id,
    execution_date,
    original_datetime
):

    current = now_iso()

    with db() as connection:

        connection.execute(
            """
            INSERT OR IGNORE INTO schedule_executions
            (
                schedule_id,
                execution_date,
                original_datetime,
                created_at,
                updated_at
            )

            VALUES (?, ?, ?, ?, ?)
            """,

            (
                int(schedule_id),
                execution_date,
                original_datetime,
                current,
                current
            )
        )

        return get_execution(
            schedule_id,
            execution_date
        )


def update_execution(
    execution_id,
    **fields
):

    allowed = {
        "override_datetime",
        "status",
        "reminder_sent",
        "confirmed"
    }

    fields = {
        key: value
        for key, value in fields.items()
        if key in allowed
    }

    if not fields:

        return False

    fields["updated_at"] = now_iso()

    with db() as connection:

        cursor = connection.execute(
            "UPDATE schedule_executions SET "
            +
            ",".join(
                f"{key} = ?"
                for key in fields
            )
            +
            " WHERE id = ?",

            list(fields.values())
            +
            [int(execution_id)]
        )

        return cursor.rowcount > 0


def get_enabled_sleep_checks():

    with db() as connection:

        return list(
            connection.execute(
                """
                SELECT *
                FROM sleep_checks
                WHERE enabled = 1
                """
            ).fetchall()
        )


def get_sleep_check(
    user_id,
    guild_id
):

    with db() as connection:

        return connection.execute(
            """
            SELECT *
            FROM sleep_checks

            WHERE user_id = ?
            AND guild_id = ?
            """,

            (
                int(user_id),
                int(guild_id)
            )
        ).fetchone()


def set_sleep_check(
    user_id,
    guild_id,
    enabled,
    interval_minutes,
    response_minutes,
    next_check_at
):

    with db() as connection:

        connection.execute(
            """
            INSERT INTO sleep_checks
            (
                user_id,
                guild_id,
                enabled,
                interval_minutes,
                response_minutes,
                next_check_at,
                waiting_until,
                last_prompt_at,
                muted_by_sleep_check
            )

            VALUES (
                ?, ?, ?, ?, ?, ?, NULL, NULL, 0
            )

            ON CONFLICT(
                user_id,
                guild_id
            )

            DO UPDATE SET

                enabled =
                    excluded.enabled,

                interval_minutes =
                    excluded.interval_minutes,

                response_minutes =
                    excluded.response_minutes,

                next_check_at =
                    excluded.next_check_at,

                waiting_until =
                    NULL,

                last_prompt_at =
                    NULL
            """,

            (
                int(user_id),
                int(guild_id),
                int(enabled),
                int(interval_minutes),
                int(response_minutes),
                next_check_at
            )
        )


def update_sleep_check(
    user_id,
    guild_id,
    **fields
):

    allowed = {
        "enabled",
        "interval_minutes",
        "response_minutes",
        "next_check_at",
        "waiting_until",
        "last_prompt_at",
        "muted_by_sleep_check"
    }

    fields = {
        key: value
        for key, value in fields.items()
        if key in allowed
    }

    if not fields:

        return False

    with db() as connection:

        cursor = connection.execute(
            "UPDATE sleep_checks SET "
            +
            ",".join(
                f"{key} = ?"
                for key in fields
            )
            +
            """
            WHERE user_id = ?
            AND guild_id = ?
            """,

            list(fields.values())
            +
            [
                int(user_id),
                int(guild_id)
            ]
        )

        return cursor.rowcount > 0


def upsert_voice_effect(
    user_id,
    guild_id,
    mute_by_bot=None,
    deafen_by_bot=None
):

    old = get_voice_effect(
        user_id,
        guild_id
    )

    mute = (
        old["mute_by_bot"]
        if old and mute_by_bot is None
        else int(bool(mute_by_bot))
    )

    deafen = (
        old["deafen_by_bot"]
        if old and deafen_by_bot is None
        else int(bool(deafen_by_bot))
    )

    with db() as connection:

        connection.execute(
            """
            INSERT INTO voice_effects
            (
                user_id,
                guild_id,
                mute_by_bot,
                deafen_by_bot,
                updated_at
            )

            VALUES (?, ?, ?, ?, ?)

            ON CONFLICT(
                user_id,
                guild_id
            )

            DO UPDATE SET

                mute_by_bot =
                    excluded.mute_by_bot,

                deafen_by_bot =
                    excluded.deafen_by_bot,

                updated_at =
                    excluded.updated_at
            """,

            (
                int(user_id),
                int(guild_id),
                mute,
                deafen,
                now_iso()
            )
        )


def get_voice_effect(
    user_id,
    guild_id
):

    with db() as connection:

        return connection.execute(
            """
            SELECT *
            FROM voice_effects

            WHERE user_id = ?
            AND guild_id = ?
            """,

            (
                int(user_id),
                int(guild_id)
            )
        ).fetchone()


def clear_voice_effect(
    user_id,
    guild_id
):

    with db() as connection:

        connection.execute(
            """
            DELETE FROM voice_effects

            WHERE user_id = ?
            AND guild_id = ?
            """,

            (
                int(user_id),
                int(guild_id)
            )
        )