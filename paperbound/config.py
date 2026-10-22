import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

DATABASE_URL = os.environ.get("DATABASE_URL", "")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
STORAGE_DIR = Path(os.environ.get("STORAGE_DIR") or "storage")
if not STORAGE_DIR.is_absolute():
    STORAGE_DIR = ROOT / STORAGE_DIR
WEB_DIR = ROOT / "web"


def coi_min_each_occurrence() -> float:
    """Minimum each-occurrence limit a certificate must show (COI_MIN_EACH_OCCURRENCE, default 1,000,000)."""
    return float(os.environ.get("COI_MIN_EACH_OCCURRENCE") or 1_000_000)
