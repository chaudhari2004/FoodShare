
import os
from pathlib import Path
from dotenv import load_dotenv
import pymysql

# Project root directory
BASE_DIR = Path(__file__).resolve().parent

# Load local .env file (Render uses Environment Variables)
load_dotenv(BASE_DIR / ".env")

# MySQL SSL CA certificate
ca_value = os.getenv("DB_SSL_CA")

if not ca_value:
    raise RuntimeError(
        "DB_SSL_CA is missing. Configure the Aiven CA certificate path."
    )

ca_path = Path(ca_value)

# Resolve relative paths from the project root
if not ca_path.is_absolute():
    ca_path = BASE_DIR / ca_path

# Never connect without the configured CA certificate
if not ca_path.is_file():
    raise FileNotFoundError(
        f"Database CA certificate not found: {ca_path}"
    )

# MySQL connection configuration
DB_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "port": int(os.getenv("DB_PORT", "3306")),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
    "database": os.getenv("DB_NAME"),
    "charset": "utf8mb4",
    "cursorclass": pymysql.cursors.Cursor,
    "autocommit": False,
    "ssl": {
        "ca": str(ca_path)
    },
}
