"""Tests never read the developer's .env (real keys, real database) and never inherit deployment settings."""

import os

os.environ["PAPERBOUND_SKIP_DOTENV"] = "1"
for name in ("DATABASE_URL", "OPENAI_API_KEY", "AS_OF", "COI_MIN_EACH_OCCURRENCE", "DAILY_LLM_BUDGET_USD", "MAX_UPLOAD_MB",
             "MAX_PAGES", "UPLOADS_PER_IP_PER_HOUR", "UPLOAD_TTL_HOURS", "RUN_COST_ESTIMATE_USD"):
    os.environ.pop(name, None)
os.environ["SESSION_SECRET"] = "test-session-secret"  # a fixed signing key, so no test depends on a per-process random one
