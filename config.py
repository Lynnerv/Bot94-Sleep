from pathlib import Path
import os
from zoneinfo import ZoneInfo

BASE_DIR = Path(__file__).resolve().parent

DATABASE_FILE = BASE_DIR / "xz94.db"

TIMEZONE = ZoneInfo(os.getenv("XZ94_TIMEZONE", "America/Lima"))

API_HOST = os.getenv("XZ94_API_HOST", "127.0.0.1")
API_PORT = int(os.getenv("XZ94_API_PORT", "8765"))

CHECK_SECONDS = 15
SCHEDULE_REMINDER_HOURS = 2
SCHEDULE_REMINDER_WINDOW_MINUTES = 2
