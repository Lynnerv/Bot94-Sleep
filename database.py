import sqlite3
from pathlib import Path

DATABASE_FILE = Path("bot94_sleep.db")


def get_connection():
    connection = sqlite3.connect(DATABASE_FILE)
    connection.row_factory = sqlite3.Row
    return connection


def _column_names(connection, table_name):
    rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {row["name"] for row in rows}


def init_database():
    connection = get_connection()

    connection.execute("""
        CREATE TABLE IF NOT EXISTS schedules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            guild_id INTEGER NOT NULL,
            time TEXT NOT NULL,
            days TEXT NOT NULL,
            mute INTEGER NOT NULL DEFAULT 0,
            deafen INTEGER NOT NULL DEFAULT 0,
            disconnect INTEGER NOT NULL DEFAULT 0,
            confirmation INTEGER NOT NULL DEFAULT 1,
            enabled INTEGER NOT NULL DEFAULT 1
        )
    """)

    connection.execute("""
        CREATE TABLE IF NOT EXISTS schedule_executions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            schedule_id INTEGER NOT NULL,
            execution_date TEXT NOT NULL,
            original_time TEXT NOT NULL,
            override_time TEXT,
            override_datetime TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            reminder_sent INTEGER NOT NULL DEFAULT 0,
            confirmed INTEGER NOT NULL DEFAULT 0,
            executed INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(schedule_id, execution_date),
            FOREIGN KEY(schedule_id) REFERENCES schedules(id) ON DELETE CASCADE
        )
    """)

    # Compatibility with the previous v1.2 database.
    columns = _column_names(connection, "schedule_executions")
    if "override_datetime" not in columns:
        connection.execute(
            "ALTER TABLE schedule_executions ADD COLUMN override_datetime TEXT"
        )

    connection.commit()
    connection.close()


def create_schedule(
    user_id,
    guild_id,
    time,
    days,
    mute,
    deafen,
    disconnect,
    confirmation,
):
    connection = get_connection()
    cursor = connection.execute(
        """
        INSERT INTO schedules
        (user_id, guild_id, time, days, mute, deafen, disconnect, confirmation, enabled)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
        """,
        (
            user_id,
            guild_id,
            time,
            days,
            int(bool(mute)),
            int(bool(deafen)),
            int(bool(disconnect)),
            int(bool(confirmation)),
        ),
    )
    connection.commit()
    schedule_id = cursor.lastrowid
    connection.close()
    return schedule_id


def get_schedules():
    connection = get_connection()
    rows = connection.execute(
        "SELECT * FROM schedules WHERE enabled=1 ORDER BY id"
    ).fetchall()
    connection.close()
    return rows


def get_user_schedules(user_id):
    connection = get_connection()
    rows = connection.execute(
        "SELECT * FROM schedules WHERE user_id=? ORDER BY time, id",
        (user_id,),
    ).fetchall()
    connection.close()
    return rows


def update_schedule(
    schedule_id,
    user_id,
    time,
    days,
    mute,
    deafen,
    disconnect,
    confirmation,
):
    connection = get_connection()
    cursor = connection.execute(
        """
        UPDATE schedules
        SET time=?, days=?, mute=?, deafen=?, disconnect=?, confirmation=?
        WHERE id=? AND user_id=?
        """,
        (
            time,
            days,
            int(bool(mute)),
            int(bool(deafen)),
            int(bool(disconnect)),
            int(bool(confirmation)),
            schedule_id,
            user_id,
        ),
    )
    connection.commit()
    changed = cursor.rowcount > 0
    connection.close()
    return changed


def set_schedule_enabled(schedule_id, user_id, enabled):
    connection = get_connection()
    cursor = connection.execute(
        "UPDATE schedules SET enabled=? WHERE id=? AND user_id=?",
        (int(bool(enabled)), schedule_id, user_id),
    )
    connection.commit()
    changed = cursor.rowcount > 0
    connection.close()
    return changed


def delete_schedule(schedule_id, user_id):
    connection = get_connection()
    cursor = connection.execute(
        "DELETE FROM schedules WHERE id=? AND user_id=?",
        (schedule_id, user_id),
    )
    connection.commit()
    deleted = cursor.rowcount > 0
    connection.close()
    return deleted


def get_execution(schedule_id, execution_date):
    connection = get_connection()
    row = connection.execute(
        """
        SELECT * FROM schedule_executions
        WHERE schedule_id=? AND execution_date=?
        """,
        (schedule_id, execution_date),
    ).fetchone()
    connection.close()
    return row


def get_execution_by_id(execution_id):
    connection = get_connection()
    row = connection.execute(
        """
        SELECT e.*, s.user_id, s.guild_id, s.time, s.days,
               s.mute, s.deafen, s.disconnect, s.confirmation, s.enabled
        FROM schedule_executions e
        JOIN schedules s ON s.id=e.schedule_id
        WHERE e.id=?
        """,
        (execution_id,),
    ).fetchone()
    connection.close()
    return row


def create_execution(schedule_id, execution_date, original_time, created_at):
    connection = get_connection()
    connection.execute(
        """
        INSERT OR IGNORE INTO schedule_executions
        (schedule_id, execution_date, original_time, status,
         reminder_sent, confirmed, executed, created_at, updated_at)
        VALUES (?, ?, ?, 'pending', 0, 0, 0, ?, ?)
        """,
        (schedule_id, execution_date, original_time, created_at, created_at),
    )
    connection.commit()
    connection.close()
    return get_execution(schedule_id, execution_date)


def update_execution(execution_id, **fields):
    allowed = {
        "override_time",
        "override_datetime",
        "status",
        "reminder_sent",
        "confirmed",
        "executed",
        "updated_at",
    }
    fields = {
        key: value for key, value in fields.items() if key in allowed
    }
    if not fields:
        return False

    assignments = ", ".join(f"{key}=?" for key in fields)
    values = list(fields.values())
    values.append(execution_id)

    connection = get_connection()
    cursor = connection.execute(
        f"UPDATE schedule_executions SET {assignments} WHERE id=?",
        values,
    )
    connection.commit()
    changed = cursor.rowcount > 0
    connection.close()
    return changed


def get_pending_executions():
    connection = get_connection()
    rows = connection.execute(
        """
        SELECT e.*, s.user_id, s.guild_id, s.time, s.days,
               s.mute, s.deafen, s.disconnect, s.confirmation, s.enabled
        FROM schedule_executions e
        JOIN schedules s ON s.id=e.schedule_id
        WHERE e.status='pending' AND e.executed=0 AND s.enabled=1
        ORDER BY e.execution_date, e.id
        """
    ).fetchall()
    connection.close()
    return rows
