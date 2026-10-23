"""A hard cap on OpenAI calls for the eval harness.

`Budget.install()` wraps `chat.completions.create` and `.parse`, counts every request and refuses the one that would
pass the limit, and refuses any model other than gpt-4o-mini. The count survives restarts in evals/.budget.json, so
several runs share one cap. SDK-internal HTTP retries are not visible here, so keep some headroom.
"""

import json
from pathlib import Path

from paperbound import config

LEDGER = config.ROOT / "evals" / ".budget.json"
DEFAULT_LIMIT = 160
ALLOWED_MODEL = "gpt-4o-mini"


class BudgetExceeded(RuntimeError):
    pass


class Budget:
    def __init__(self, limit: int = DEFAULT_LIMIT, path: Path = LEDGER):
        self.limit, self.path = limit, path
        saved = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        self.calls = int(saved.get("calls", 0))
        self.session_calls = 0

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.calls)

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"calls": self.calls, "limit": self.limit}), encoding="utf-8")

    def charge(self, model: str | None) -> None:
        if model != ALLOWED_MODEL:
            raise BudgetExceeded(f"model {model!r} is not allowed in evals (only {ALLOWED_MODEL})")
        if self.calls >= self.limit:
            raise BudgetExceeded(f"OpenAI call budget of {self.limit} is used up")
        self.calls += 1
        self.session_calls += 1
        self._save()

    def install(self) -> None:
        from openai.resources.chat.completions import Completions

        budget = self
        for name in ("create", "parse"):
            original = getattr(Completions, name)
            if getattr(original, "_budgeted", False):
                continue

            def wrapper(self_, *args, __orig=original, **kwargs):
                budget.charge(kwargs.get("model"))
                return __orig(self_, *args, **kwargs)

            wrapper._budgeted = True
            setattr(Completions, name, wrapper)
