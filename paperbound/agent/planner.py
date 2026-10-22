import asyncio
import json
import re
import time
from dataclasses import dataclass, field
from datetime import date, timedelta

from openai import OpenAI

from paperbound import clock, config
from paperbound.agent import policy
from paperbound.agent.tools import TOOLS
from paperbound.agent.tools.base import RunContext
from paperbound.pipeline.extract import MODEL, PRICE_IN_PER_M, PRICE_OUT_PER_M
from paperbound.agent.profiles import LEASE_PROMPT, PROFILES, RUN_ID_PLACEHOLDER, Token
from paperbound.doctypes import SUPPORTED
from paperbound.pipeline.impact import Brief
from paperbound.pipeline.types import DerivedObligation, ValidatedField
from paperbound.pipeline.validate import BLOCKING_FLAGS

MAX_TOOL_CALLS = 12
MAX_ROUNDS = 4
TIMEOUT_SECONDS = 60
REMINDER_LEAD_DAYS = (60, 30, 14)
SYSTEM_PROMPT = LEASE_PROMPT  # kept for callers that import the lease prompt from here


@dataclass
class PlannedAction:
    tool: str
    args: dict
    source_field_ids: list[int]  # local ids (the ids shown to the model); mapped to DB ids on persist
    source_fields: list[str]  # the same fields, by name
    rationale: str
    policy_status: str
    policy_reason: str
    group_key: str | None = None
    group_mode: str | None = None  # bundle | exclusive


@dataclass
class PlanResult:
    actions: list[PlannedAction] = field(default_factory=list)
    rejected: list[dict] = field(default_factory=list)
    model: str = MODEL
    tokens: int = 0
    cost_usd: float = 0.0
    api_calls: int = 0
    tool_calls: int = 0
    notes: list[str] = field(default_factory=list)


def plannable_fields(fields: list[ValidatedField]) -> dict[int, ValidatedField]:
    """Fields the planner may see: present, grounded and with no blocking flag. Ids start at 1."""
    usable = [f for f in fields if f.value is not None and not (set(f.flags) & BLOCKING_FLAGS)]
    return {i: f for i, f in enumerate(usable, start=1)}


# ---- tokens, reminder schedule and number checks: everything the model may not decide ----

def reminder_dates(brief: Brief, today: date) -> dict[date, int]:
    """Staged reminders: 60, 30 and 14 days before the deadline, only those still in the future."""
    if brief.due_date is None:
        return {}
    staged = {brief.due_date - timedelta(days=d): d for d in REMINDER_LEAD_DAYS}
    return {when: d for when, d in sorted(staged.items()) if when > today}


_NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")
_ISO_DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_LONG_DATE_RE = re.compile(r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\s+\d{4}\b")


def _numbers(text: str) -> list[float]:
    out = []
    for raw in _NUMBER_RE.findall(text):
        try:
            out.append(float(raw.replace(",", "")))
        except ValueError:
            pass
    return out


def check_numbers(text: str, allowed_numbers: set[float], allowed_dates: set[str]) -> str | None:
    """Every date and number the model typed must appear in the brief. Returns the problem, if any."""
    remaining = text
    for match in _ISO_DATE_RE.findall(text):
        if match not in allowed_dates:
            return f"date {match} does not match the brief"
        remaining = remaining.replace(match, " ")
    for match in _LONG_DATE_RE.findall(text):
        try:
            month, day, year = re.match(r"(\w+)\s+(\d{1,2}),?\s+(\d{4})", match).groups()
            iso = date(int(year), list(_MONTH_NAMES).index(month) + 1, int(day)).isoformat()
        except (ValueError, AttributeError):
            return f"date {match} is not valid"
        if iso not in allowed_dates:
            return f"date {match} does not match the brief"
        remaining = remaining.replace(match, " ")
    for number in _numbers(remaining):
        if not any(abs(number - a) < 0.005 for a in allowed_numbers):
            return f"number {number:g} does not match the brief"
    return None


_MONTH_NAMES = (
    "January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December",
)
_TOKEN_RE = re.compile(r"\{(\w+)\}")


def fill_tokens(text: str, tokens: dict[str, Token]) -> tuple[str | None, set[str], str | None]:
    """Replace {token}s. Returns (filled text, token names used, problem)."""
    used: set[str] = set()
    problem: list[str] = []

    def sub(match):
        name = match.group(1)
        if name not in tokens:
            problem.append(f"unknown or unavailable token {{{name}}} (available: {', '.join(sorted(tokens))})")
            return match.group(0)
        used.add(name)
        return tokens[name].text

    out = _TOKEN_RE.sub(sub, text)
    return (None, used, problem[0]) if problem else (out, used, None)


def _allowed(tokens: dict[str, Token], brief: Brief, dates: dict[date, int], today: date):
    numbers = {float(d) for d in REMINDER_LEAD_DAYS}
    for t in tokens.values():
        numbers.update(_numbers(t.text))
    for line in brief.lines:
        if isinstance(line.value, (int, float)):
            numbers.add(float(line.value))
            numbers.add(abs(float(line.value)))
    allowed_dates = {today.isoformat(), *(d.isoformat() for d in dates)}
    if brief.due_date:
        allowed_dates.add(brief.due_date.isoformat())
    for token in tokens.values():
        allowed_dates.update(_long_to_iso(token.text))
    return numbers, allowed_dates


def _long_to_iso(text: str) -> list[str]:
    out = []
    for m in re.finditer(r"(\w+)\s+(\d{1,2}),\s+(\d{4})", text):
        if m.group(1) in _MONTH_NAMES:
            out.append(date(int(m.group(3)), _MONTH_NAMES.index(m.group(1)) + 1, int(m.group(2))).isoformat())
    return out


def resolve_placeholders(args: dict, run_id: int) -> dict:
    """Replace the run-id placeholder in links once the run exists."""
    return json.loads(json.dumps(args).replace(RUN_ID_PLACEHOLDER, str(run_id)))


class Planner:
    """State for one planning run: what the model may propose and what has been proposed so far."""

    def __init__(self, fields, obligations, brief: Brief | None, today: date, doc_type: str = "lease"):
        self.today = today
        self.profile = PROFILES[doc_type]
        self.refs = plannable_fields(fields)
        self.obligations = obligations
        self.brief = brief if brief is not None else SUPPORTED[doc_type].brief(fields, today)
        self.by_name = {f.name: i for i, f in self.refs.items()}
        self.tokens = self.profile.build_tokens(self.refs, self.brief)
        self.dates = reminder_dates(self.brief, today)
        self.numbers, self.allowed_dates = _allowed(self.tokens, self.brief, self.dates, today)
        self.proposed_dates: set[date] = set()
        self.proposed_variants: set[str] = set()

    def local_ids(self, names) -> list[int]:
        return sorted({self.by_name[n] for n in names if n in self.by_name})

    def missing(self) -> list[str]:
        out = [f"create_calendar_event for {d.isoformat()}" for d in self.dates if d not in self.proposed_dates]
        if self.brief.due_date:
            out += [f"draft_email variant {v}" for v in self.profile.email_variants if v not in self.proposed_variants]
        return out

    def user_message(self, summary: str) -> str:
        by_name = self.by_name
        return json.dumps(
            {
                "today": self.today.isoformat(),
                "document_summary": summary,
                "fields": [
                    {"id": i, "name": f.name, "value": f.value, "page": f.page_no, "confidence": f.confidence}
                    for i, f in self.refs.items()
                ],
                "obligations": [
                    {
                        "kind": o.kind,
                        "due_date": o.due_date.isoformat(),
                        "description": o.description,
                        "source_field_ids": [by_name[n] for n in o.source_fields if n in by_name],
                    }
                    for o in self.obligations
                ],
                "decision_brief": [
                    {"label": ln.label, "value": ln.text, "source_field_ids": self.local_ids(ln.source_fields)}
                    for ln in self.brief.lines
                ],
                "reminder_dates": [d.isoformat() for d in self.dates],
                "tokens": {name: t.text for name, t in self.tokens.items()},
            },
            indent=1,
        )

    # ---- shaping model proposals ----

    def shape_calendar(self, args: dict) -> tuple[dict | None, set[str], str | None]:
        try:
            when = date.fromisoformat(str(args.get("date")))
        except ValueError:
            return None, set(), "date must be an ISO date (YYYY-MM-DD)"
        if when not in self.dates:
            allowed = ", ".join(d.isoformat() for d in self.dates) or "none (no reminder is still in the future)"
            return None, set(), f"date must be one of the staged reminder dates: {allowed}"
        if when in self.proposed_dates:
            return None, set(), "duplicate of an action already proposed"
        title, used_t, problem = self._prose(args.get("title", ""))
        if problem:
            return None, set(), problem
        intro, used_d, problem = self._prose(args.get("description", ""))
        if problem:
            return None, set(), problem
        used = used_t | used_d
        block, cited = self.profile.facts_block(self, self.dates[when])
        names = {n for t in used for n in self.tokens[t].sources} | cited
        self.proposed_dates.add(when)
        return {"title": title, "date": when.isoformat(), "description": f"{intro}\n\n{block}".strip()}, names, None

    def shape_email(self, args: dict, variant) -> tuple[dict | None, set[str], str | None]:
        if variant not in self.profile.email_variants:
            return None, set(), f"variant must be one of {', '.join(self.profile.email_variants)}"
        if variant in self.proposed_variants:
            return None, set(), "duplicate of an action already proposed"
        subject, used_s, problem = self._prose(args.get("subject", ""))
        if problem:
            return None, set(), problem
        body, used_b, problem = self._prose(args.get("body", ""))
        if problem:
            return None, set(), problem
        used = used_s | used_b
        need = self.profile.email_required(variant, self.tokens)
        absent = [t for t in need if self.tokens[t].text not in f"{subject}\n{body}"]
        if absent:
            return None, set(), "the email must cite " + ", ".join("{" + t + "}" for t in absent)
        problem = self.profile.email_problem(variant, body)
        if problem:
            return None, set(), problem
        self.proposed_variants.add(variant)
        names = {n for t in used for n in self.tokens[t].sources}
        return {"subject": subject, "body": body}, names, None

    def _prose(self, text) -> tuple[str | None, set[str], str | None]:
        text = str(text or "")
        problem = check_numbers(text, self.numbers, self.allowed_dates)
        if problem:
            return None, set(), problem
        filled, used, problem = fill_tokens(text, self.tokens)
        return filled, used, problem

def plan_actions(
    fields: list[ValidatedField],
    obligations: list[DerivedObligation],
    existing_obligations: list[dict] | None = None,
    client: OpenAI | None = None,
    today: date | None = None,
    brief: Brief | None = None,
    doc_type: str = "lease",
) -> PlanResult:
    today = clock.today(today)
    result = PlanResult()
    state = Planner(fields, obligations, brief, today, doc_type)
    if not state.refs or not obligations:
        result.notes.append("Nothing to plan: no usable fields or no obligations.")
        return result

    client = client or OpenAI(api_key=config.OPENAI_API_KEY, max_retries=1)
    ctx = RunContext(mode="sandbox", existing_obligations=existing_obligations or [])
    deadline = time.monotonic() + TIMEOUT_SECONDS

    # Dedupe check against get_obligations happens before the model proposes anything; it counts as a tool call.
    existing = asyncio.run(TOOLS["get_obligations"].run({}, ctx))
    result.tool_calls = 1
    messages = [
        {"role": "system", "content": state.profile.system_prompt},
        {"role": "user", "content": state.user_message(state.profile.summary(state.refs))},
        {"role": "user", "content": "get_obligations result: " + json.dumps(existing.data)},
    ]
    tool_schemas = [t.schema() for t in TOOLS.values()]

    for _ in range(MAX_ROUNDS):
        if result.tool_calls >= MAX_TOOL_CALLS:
            break
        remaining = deadline - time.monotonic()
        if remaining <= 1:
            result.notes.append("Planner timeout reached.")
            break
        completion = client.chat.completions.create(
            model=MODEL, messages=messages, tools=tool_schemas, temperature=0, timeout=remaining
        )
        result.api_calls += 1
        usage = completion.usage
        result.tokens += usage.total_tokens
        result.cost_usd += usage.prompt_tokens * PRICE_IN_PER_M / 1e6 + usage.completion_tokens * PRICE_OUT_PER_M / 1e6

        message = completion.choices[0].message
        calls = message.tool_calls or []
        if not calls:
            break
        messages.append(
            {
                "role": "assistant",
                "content": message.content,
                "tool_calls": [
                    {"id": c.id, "type": "function", "function": {"name": c.function.name, "arguments": c.function.arguments}}
                    for c in calls
                ],
            }
        )

        needs_reply = False
        for call in calls:
            if result.tool_calls >= MAX_TOOL_CALLS:
                reply = {"status": "rejected", "reason": f"tool call limit ({MAX_TOOL_CALLS}) reached"}
                result.notes.append("Tool call limit reached.")
            else:
                result.tool_calls += 1
                reply, was_read_only, rejected = _handle_call(call, state, ctx, result)
                needs_reply = needs_reply or was_read_only or rejected
            messages.append({"role": "tool", "tool_call_id": call.id, "content": json.dumps(reply)})
        if not needs_reply:
            todo = state.missing()
            if not todo:
                break  # everything expected was proposed and accepted
            messages.append({"role": "user", "content": "Still missing: " + "; ".join(todo)})

    _add_tracker_record(state, result)
    todo = state.missing()
    if todo:
        result.notes.append("Not proposed: " + "; ".join(todo) + ".")
    for item in state.brief.skipped:
        result.notes.append(f"Brief line skipped ({item['key']}): {item['reason']}.")
    result.cost_usd = round(result.cost_usd, 6)
    return result


def _handle_call(call, state: Planner, ctx, result: PlanResult):
    """Returns (tool reply, was_read_only, was_rejected)."""
    name = call.function.name
    tool = TOOLS.get(name)
    if tool is None:
        return _reject(result, name, "unknown tool")
    try:
        args = json.loads(call.function.arguments or "{}")
        if not isinstance(args, dict):
            raise ValueError
    except ValueError:
        return _reject(result, name, "arguments are not a JSON object")

    if not tool.side_effect:
        out = asyncio.run(tool.run(args, ctx))
        return {"status": "ok", **out.data}, True, False

    ids = args.pop("source_field_ids", None)
    rationale = str(args.pop("rationale", "") or "")
    variant = args.pop("variant", None)
    if not isinstance(ids, list) or not ids or not all(isinstance(i, int) and i in state.refs for i in ids):
        return _reject(result, name, "source_field_ids must be a non-empty list of ids from the provided fields")

    clean = tool.finalize_args(args)
    group_key = group_mode = None
    cited: set[str] = set()
    if name == "create_calendar_event":
        shaped, cited, problem = state.shape_calendar(clean)
        group_key, group_mode = "reminders", "bundle"
    elif name == "draft_email":
        shaped, cited, problem = state.shape_email(clean, variant)
        group_key, group_mode = state.profile.email_group
    else:
        return _reject(result, name, "not proposed by the model: code files this action")
    if problem:
        return _reject(result, name, problem)
    clean = {**clean, **shaped}
    problem = tool.validate_args(clean)
    if problem:
        _undo(state, name, clean, variant)
        return _reject(result, name, problem)

    source_ids = sorted(set(ids) | set(state.local_ids(cited)))
    decision = policy.decide(tool, [state.refs[i] for i in source_ids])
    result.actions.append(
        PlannedAction(
            tool=name,
            args=clean,
            source_field_ids=source_ids,
            source_fields=[state.refs[i].name for i in source_ids],
            rationale=rationale,
            policy_status=decision.status,
            policy_reason=decision.reason,
            group_key=group_key,
            group_mode=group_mode,
        )
    )
    return {"status": "proposed", "note": "pending human approval; not executed"}, False, False


def _undo(state: Planner, tool: str, args: dict, variant) -> None:
    """A proposal failed the tool's own validation after being recorded as seen; allow a corrected retry."""
    if tool == "create_calendar_event":
        state.proposed_dates.discard(date.fromisoformat(args["date"]))
    elif tool == "draft_email":
        state.proposed_variants.discard(variant)


def _add_tracker_record(state: Planner, result: PlanResult) -> None:
    built = state.profile.tracker_record(state)
    if built is None:
        return
    args, names = built
    tool = TOOLS["upsert_record"]
    problem = tool.validate_args(args)
    if problem:
        result.rejected.append({"tool": tool.name, "reason": problem})
        return
    ids = state.local_ids(names)
    decision = policy.decide(tool, [state.refs[i] for i in ids])
    result.actions.append(
        PlannedAction(
            tool=tool.name,
            args=args,
            source_field_ids=ids,
            source_fields=[state.refs[i].name for i in ids],
            rationale="Track the notice deadline as a decision that is still needed. Built by code from the decision brief.",
            policy_status=decision.status,
            policy_reason=decision.reason,
        )
    )


def _reject(result: PlanResult, tool: str, reason: str):
    result.rejected.append({"tool": tool, "reason": reason})
    return {"status": "rejected", "reason": reason}, False, True
