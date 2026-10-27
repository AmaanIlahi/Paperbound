"""Narration templates: one plain-language paragraph per pipeline step, written by code from what the step produced.

Used twice, so a live run reads exactly like a recorded one:
  - scripts/record_demo.py drafts narration.json for a recorded sample (then a human edits it);
  - the job worker narrates a live run step by step as each step finishes.
There is no LLM here. Inputs are plain dicts shaped like the snapshot (fields, steps, brief lines, actions).
"""

from dataclasses import dataclass

DEFAULT_ACTION_TEXT = {
    "reminders": "Staged reminders before the deadline. Code chose the dates and filled in every number; the model only wrote the intro line. Approve them together or one by one.",
    "notice_choice": "Two drafts of one decision: approving one rejects the other. Drafts only: nothing is sent, and the recipient stays a placeholder.",
    "tool:draft_email": "A draft email. Approving it only saves a draft in the sandbox inbox: it is never sent.",
    "tool:upsert_record": "A tracker record built entirely by code from the brief: no model wrote any of it.",
    "default": "A proposed action. It stays pending until you approve or reject it.",
}

# Action cards of the generic fallback (kept apart so the recorded samples' narration keys stay what they were).
GENERIC_ACTION_TEXT = {
    "deadline_reminders": "One reminder on the date of each grounded future deadline. Code built every title, date and quote: no model wrote any of it. Approve them together or one by one.",
    "tool:flag_for_review": "Paperbound has no schema for this document type, so a person should check what was read. Approving only adds it to the sandbox review queue.",
}


# Action cards of the tabular types (rent roll, T-12).
TABLE_ACTION_TEXT = {
    "lease_expirations_12m": "One reminder on the end date of each lease that expires within 12 months. Code built every title, date and fact from parsed cells: no model wrote any of it. Approve them together or one by one.",
    "lease_expirations_24m": "One reminder on the end date of each lease that expires in the 13 to 24 months from now. Code built every title, date and fact from parsed cells: no model wrote any of it.",
    "tool:flag_for_review": "Something in this table did not check out, so a person should look before anyone relies on it. Approving only adds it to the sandbox review queue.",
}


@dataclass
class Narrator:
    kind: str  # lower-case document kind, e.g. "lease"
    filename: str
    pages: int
    as_of: str
    table: str | None = None  # "rent_roll" or "t12": the tabular types, whose narration differs

    # ---- framing ----

    def intro(self, live: bool = False) -> str:
        if live:
            return (
                f"This is a live run of the real pipeline on your {self.kind} ({self.filename}, {self.pages} page(s)), as of "
                f"{self.as_of}. Each step is narrated as it finishes: which parts are plain code and which use a model."
            )
        return (
            f"This is a recorded run of the real pipeline on a {self.kind} ({self.filename}, {self.pages} page(s)), replayed as of "
            f"{self.as_of}. No model is called while you watch, and nothing leaves your browser."
        )

    def outro(self, calls: int, tokens: int, cost_usd: float, live: bool = False) -> str:
        tail = (
            "Approve or reject actions to see the sandbox result."
            if live else "Approve or reject actions to see the sandbox panel."
        )
        return f"That was the whole run: {calls} model call(s), {tokens} tokens, ${cost_usd:.4f}. Everything else was code. {tail}"

    # ---- steps ----

    def parse(self) -> str:
        return (
            f"The PDF has a text layer on all {self.pages} page(s), so no OCR was needed. This step is deterministic: PyMuPDF reads "
            "every word and its exact position straight from the file. No model is involved."
        )

    def classify(self, cls: dict | None, doc_type: str | None = None) -> str:
        cls = cls or {}
        if doc_type == "generic":
            return (
                "Keyword and layout heuristics did not recognise a lease or an insurance certificate, so a small LLM call decided. "
                + cls.get("reason", "")
                + " Instead of stopping, Paperbound reads it with a generic schema. The type guess is only a label for you: it never "
                "chooses a schema, and every field is held to a lower confidence ceiling."
            ).strip()
        if doc_type == "other":
            return (
                "Keyword and layout heuristics did not recognise a lease or an insurance certificate. " + cls.get("reason", "")
                + " Paperbound stops here instead of guessing."
            ).strip()
        if cls.get("method") == "llm":
            return "The keyword and layout heuristics were not sure what this document is, so a small LLM call decided. " + cls.get("reason", "")
        scores = cls.get("scores", {})
        top = sorted(scores.values(), reverse=True)
        lead = f" (top score {top[0]:g}, runner-up {top[1]:g})" if len(top) > 1 else ""
        return (
            f"Keyword and layout heuristics recognised a {self.kind}{lead}, so no model was called. Code decides whenever it "
            "can: it is free and gives the same answer every time. An LLM is only the fallback when the heuristics are unsure."
        )

    def extract(self, n_present: int, step: dict) -> str:
        return (
            f"This is the first place a model is used. gpt-4o-mini read the {self.pages} page(s) and returned {n_present} fields, each "
            "with the exact quote it relied on and the page it found it on. Rules cannot read free-form clauses, so an LLM does. "
            f"Nothing it says is trusted yet ({step.get('tokens', 0)} tokens, ${step.get('cost_usd', 0.0):.4f})."
        )

    def validate(self, fields: list[dict]) -> str:
        present = [f for f in fields if f["value"] is not None]
        grounded = [f for f in present if "ungrounded" not in f["flags"]]
        flagged = [f for f in fields if f["flags"]]
        flag_text = (
            "No field was flagged." if not flagged
            else "Flagged: " + "; ".join(f"{f['name']} ({', '.join(f['flags'])})" for f in flagged) + "."
        )
        return (
            f"Now code checks the model. {len(grounded)} of {len(present)} fields were found word for word on the page, and each "
            "got a box around its source (click a field to see it). Dates, amounts and ranges are checked, the value must appear "
            "in its quote, and a quote that reads like an instruction is blocked. Confidence is 0.6 x grounding + 0.3 x checks "
            f"+ 0.1 x page quality; the model's own confidence is never used. {flag_text}"
        )

    def obligations(self, obligations: list[dict]) -> str:
        first = obligations[0]["description"] if obligations else "none"
        return (
            f"Code turned the checked fields into {len(obligations)} obligation(s): {first}"
            + (" (and others below)." if len(obligations) > 1 else ".")
            + " This is plain date arithmetic, with no model, and it only uses fields that passed validation."
        )

    def impact(self, lines: list[dict]) -> str:
        shown = [f"{ln['label']}: {ln['text']}" for ln in lines[:4]]
        return (
            f"The decision brief, as of {self.as_of}: " + "; ".join(shown)
            + ". Every number is computed by code from fields that passed validation, and each one cites the fields it came "
            "from (click a number to see them). A line is skipped, with a reason, if its inputs are missing or blocked."
        )

    def planner(self, n_actions: int, step_reason: str = "") -> str:
        return (
            f"The second model step. The LLM proposes {n_actions} action(s) with tool calls, but it only writes the wording: code "
            "supplies every number, date and name, chooses the reminder dates, and rejects any proposal whose numbers do not match "
            f"the brief. {step_reason}"
        ).strip()

    def planner_generic(self, n_actions: int) -> str:
        return (
            f"No model is used here. Code proposed {n_actions} action(s): a calendar reminder for each verified future deadline, and "
            "a flag asking a person to review. There are no email drafts, because a document of an unknown type gives Paperbound no "
            "trusted recipient, and a flagged or low-confidence field can never produce an action."
        )

    def extract_table(self, summary: str, step: dict) -> str:
        return (
            f"Code found the table with PyMuPDF and read every cell itself: {summary}. The model was used for one thing only, to say "
            "which column (or which line label) means what; it never read or wrote a number "
            f"({step.get('tokens', 0)} tokens, ${step.get('cost_usd', 0.0):.4f})."
        )

    def validate_table(self, fields: list[dict]) -> str:
        flagged = [f for f in fields if f["flags"]]
        names = "; ".join(f"{f['name']} ({', '.join(f['flags'])})" for f in flagged[:6]) + ("; ..." if len(flagged) > 6 else "")
        return (
            "Code checks the table with arithmetic: every figure must agree with the others on its row or line, dates must run forward, "
            "and the parts must add up to the stated totals. A flagged row stays visible "
            "but never feeds a metric or an action. " + (f"Flagged: {names}." if flagged else "No row, line or total was flagged.")
        )

    def planner_table(self, n_actions: int) -> str:
        if self.table == "t12":
            return (
                f"No model is used here. Code proposed {n_actions} action(s): a review flag if a line, subtotal or NOI did not check out or a "
                "month looks far from its line's median. A T-12 states no dates, so there are no reminders, and there are no email drafts."
            )
        return (
            f"No model is used here. Code proposed {n_actions} action(s): a calendar reminder for each verified lease that ends soon, and "
            "a review flag if anything failed a check. A flagged row never produces a reminder, and there are no email drafts "
            "because a table gives Paperbound no trusted recipient."
        )

    def gate(self, group_keys: set[str], live: bool = False) -> str:
        return (
            "The approval gate is deterministic code, not the model: every action starts pending, an email is always only a draft "
            "(never sent), and the recipient stays a placeholder."
            + (" Approving one notice rejects the other, because they are one decision." if "notice_choice" in group_keys else "")
            + (
                " Try approving in the Actions tab. Approvals run in a sandbox: nothing is ever sent anywhere."
                if live else " Try approving below. In this demo, approvals run in a sandbox inside your browser: nothing is sent to a server."
            )
        )

    def skipped(self, stage: str, reason: str) -> str:
        return f"This step did not run. {reason}".strip()

    def failed(self, stage: str, error: str) -> str:
        return f"This step failed ({error}). The run keeps whatever finished before it and stops here, rather than guessing."

    def no_planner(self) -> str:
        return "There were no obligations, so there was nothing for the model to plan and it was not called."


def draft_narration(snap: dict) -> dict:
    """The full narration.json for a recorded snapshot."""
    steps = {s["stage"]: s for s in snap["steps"]}
    n = Narrator(snap["label"].lower(), snap["filename"], snap["page_count"], snap["as_of"])
    groups = {a["group_key"] for a in snap["actions"] if a["group_key"]}
    text = {
        "parse": n.parse(),
        "classify": n.classify(snap["classification"], snap["doc_type"]),
        "extract": n.extract(sum(1 for f in snap["fields"] if f["value"] is not None), steps["extract"]),
        "validate": n.validate(snap["fields"]),
        "obligations": n.obligations(snap["obligations"]),
        "impact": n.impact(snap["brief"]["lines"]),
        "planner": n.planner(len(snap["actions"]), steps.get("planner", {}).get("reason", "")),
        "gate": n.gate(groups),
    }
    return {
        "intro": n.intro(),
        "steps": text,
        "actions": dict(DEFAULT_ACTION_TEXT),
        "outro": n.outro(snap["openai_calls"], snap["tokens"], snap["cost_usd"]),
    }
