from datetime import date

from paperbound.agent.tools.base import BaseTool, RunContext, ToolResult, record_sandbox


class CreateCalendarEvent(BaseTool):
    name = "create_calendar_event"
    description = "Propose an all-day calendar reminder. Reversible. Requires human approval."
    side_effect = True
    reversible = True
    parameters = {
        "title": {"type": "string"},
        "date": {"type": "string", "description": "ISO date YYYY-MM-DD"},
        "description": {"type": "string"},
    }
    required = ["title", "date", "description"]

    def validate_args(self, args: dict) -> str | None:
        try:
            date.fromisoformat(str(args.get("date")))
        except ValueError:
            return "date must be an ISO date (YYYY-MM-DD)"
        if not str(args.get("title", "")).strip():
            return "title is required"
        return None

    async def run_sandbox(self, args: dict, ctx: RunContext) -> ToolResult:
        response = {"status": "created (sandbox)", "event_id": f"sandbox-evt-{ctx.action_id or 0}", "calendar": "Sandbox Calendar"}
        return record_sandbox(self, args, ctx, response)
