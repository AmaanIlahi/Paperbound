import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
if not os.environ.get("PAPERBOUND_SKIP_DOTENV"):  # set by tests and containers that must ignore a local .env
    load_dotenv(ROOT / ".env")

DATABASE_URL = os.environ.get("DATABASE_URL", "")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
STORAGE_DIR = Path(os.environ.get("STORAGE_DIR") or "storage")
if not STORAGE_DIR.is_absolute():
    STORAGE_DIR = ROOT / STORAGE_DIR
WEB_DIR = ROOT / "web"
DEMO_DIR = ROOT / "fixtures" / "demo"  # recorded demo runs: fixtures/demo/<fixture>/snapshot.json


def coi_min_each_occurrence() -> float:
    """Minimum each-occurrence limit a certificate must show (COI_MIN_EACH_OCCURRENCE, default 1,000,000)."""
    return float(os.environ.get("COI_MIN_EACH_OCCURRENCE") or 1_000_000)


def generic_confidence_cap() -> float:
    """Highest confidence a generic-fallback field can show (GENERIC_CONFIDENCE_CAP, default 0.7), so it never looks as
    trusted as a typed field."""
    return float(os.environ.get("GENERIC_CONFIDENCE_CAP") or 0.7)


def generic_action_min_confidence() -> float:
    """Generic fields below this confidence never feed an obligation or action (GENERIC_ACTION_MIN_CONFIDENCE, default 0.6)."""
    return float(os.environ.get("GENERIC_ACTION_MIN_CONFIDENCE") or 0.6)


def crosscheck_rent_tolerance() -> float:
    """How far the T-12's base rent may be from the rent roll's total annual rent, as a share (CROSSCHECK_RENT_TOLERANCE, default 0.05)."""
    return float(os.environ.get("CROSSCHECK_RENT_TOLERANCE") or 0.05)
