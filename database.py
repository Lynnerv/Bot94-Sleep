import sqlite3
from pathlib import Path


DATABASE_FILE = Path("bot94_sleep.db")


def get_connection():
    connection = sqlite3.connect(DATABASE_FILE)
    connection.row_factory = sqlite3.Row
    return connection


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
    confirmation
):
    connection = get_connection()

    cursor = connection.execute("""
        INSERT INTO schedules (
            user_id,
            guild_id,
            time,
            days,
            mute,
            deafen,
            disconnect,
            confirmation,
            enabled
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
    """, (
        user_id,
        guild_id,
        time,
        days,
        int(mute),
        int(deafen),
        int(disconnect),
        int(confirmation)
    ))

    connection.commit()

    schedule_id = cursor.lastrowid

    connection.close()

    return schedule_id


def get_schedules():
    connection = get_connection()

    rows = connection.execute("""
        SELECT *
        FROM schedules
        WHERE enabled = 1
    """).fetchall()

    connection.close()

    return rows


def get_user_schedules(user_id):
    connection = get_connection()

    rows = connection.execute("""
        SELECT *
        FROM schedules
        WHERE user_id = ?
        ORDER BY time
    """, (user_id,)).fetchall()

    connection.close()

    return rows


def delete_schedule(schedule_id, user_id):
    connection = get_connection()

    cursor = connection.execute("""
        DELETE FROM schedules
        WHERE id = ?
        AND user_id = ?
    """, (schedule_id, user_id))

    connection.commit()

    deleted = cursor.rowcount > 0

    connection.close()

    return deleted


# ============================================================
# NUEVAS FUNCIONES PARA EL GUI
# ============================================================

def update_schedule(
    schedule_id,
    user_id,
    time,
    days,
    mute,
    deafen,
    disconnect,
    confirmation
):
    """
    Actualiza un horario existente.
    Solo permite modificar horarios pertenecientes al usuario.
    """

    connection = get_connection()

    cursor = connection.execute("""
        UPDATE schedules
        SET
            time = ?,
            days = ?,
            mute = ?,
            deafen = ?,
            disconnect = ?,
            confirmation = ?
        WHERE id = ?
        AND user_id = ?
    """, (
        time,
        days,
        int(mute),
        int(deafen),
        int(disconnect),
        int(confirmation),
        schedule_id,
        user_id
    ))

    connection.commit()

    updated = cursor.rowcount > 0

    connection.close()

    return updated


def set_schedule_enabled(schedule_id, user_id, enabled):
    """
    Activa o desactiva un horario.
    """

    connection = get_connection()

    cursor = connection.execute("""
        UPDATE schedules
        SET enabled = ?
        WHERE id = ?
        AND user_id = ?
    """, (
        int(enabled),
        schedule_id,
        user_id
    ))

    connection.commit()

    updated = cursor.rowcount > 0

    connection.close()

    return updated