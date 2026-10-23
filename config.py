
import os
from pathlib import Path
from dotenv import load_dotenv

# Project root folder
BASE_DIR = Path(__file__).resolve().parent

# Load environment variables from .env
load_dotenv(BASE_DIR / ".env")

# MySQL configuration
DB_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "port": int(os.getenv("DB_PORT", "3306")),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
    "database": os.getenv("DB_NAME"),
    "charset": "utf8mb4",
    "cursorclass": __import__(
        "pymysql"
    ).cursors.Cursor,
    "autocommit": False,
}
