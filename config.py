import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

TOKEN = os.getenv("TOKEN", "")
DB_URL = os.getenv(
    "DB_URL",
    "postgresql+asyncpg://bombino:bombino_secret@localhost:5432/bombino",
)
OWNER_IDS: set[int] = set()
for _id in os.getenv("OWNER_IDS", "").split(","):
    _id = _id.strip()
    if _id.isdigit():
        OWNER_IDS.add(int(_id))

assert TOKEN, "TOKEN must be set in .env"
