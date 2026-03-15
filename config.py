import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

TOKEN = os.getenv("TOKEN", "")
DB_URL = os.getenv(
    "DB_URL",
    "postgresql+asyncpg://bombino:bombino_secret@localhost:5432/bombino",
)

assert TOKEN, "TOKEN must be set in .env"
