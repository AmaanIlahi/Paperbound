"""A hard cap on OpenAI calls for the eval harness.

`Budget.install()` wraps `chat.completions.create` and `.parse`, counts every request, refuses any call that is not temperature 0, and refuses the one that would
pass the limit, and refuses any model other than gpt-4o-mini. The limit is **per run** (each process starts at zero).
evals/.budget.json is only a cumulative report of calls made across runs; nothing is refused because of it. SDK-internal
HTTP retries are not visible here, so keep some headroom.
"""

import json
from pathlib import Path

from paperbound import config

LEDGER = config.ROOT / "evals" / ".budget.json"
DEFAULT_LIMIT = 500
ALLOWED_MODEL = "gpt-4o-mini"


class BudgetExceeded(RuntimeError):
    pass


class Budget:
    def __init__(self, limit: int = DEFAULT_LIMIT, path: Path = LEDGER):
        self.limit, self.path = limit, path
        saved = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        self.calls = int(saved.get("calls", 0))  # cumulative, for the report only
        self.session_calls = 0  # this run's count: the one the limit applies to

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.session_calls)

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"calls": self.calls, "last_run_calls": self.session_calls, "last_run_limit": self.limit}), encoding="utf-8")

    def charge(self, model: str | None, temperature: float | None = 0) -> None:
        if model != ALLOWED_MODEL:
            raise BudgetExceeded(f"model {model!r} is not allowed in evals (only {ALLOWED_MODEL})")
        if temperature != 0:
            raise BudgetExceeded(f"temperature {temperature!r} is not allowed in evals (only 0)")
        if self.session_calls >= self.limit:
            raise BudgetExceeded(f"OpenAI call budget of {self.limit} for this run is used up")
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
                budget.charge(kwargs.get("model"), kwargs.get("temperature"))
                return __orig(self_, *args, **kwargs)

            wrapper._budgeted = True
            setattr(Completions, name, wrapper)
