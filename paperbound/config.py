import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

DATABASE_URL = os.environ.get("DATABASE_URL", "")
STORAGE_DIR = Path(os.environ.get("STORAGE_DIR") or "storage")
if not STORAGE_DIR.is_absolute():
    STORAGE_DIR = ROOT / STORAGE_DIR
WEB_DIR = ROOT / "web"
