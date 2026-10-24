from paperbound.agent.tools.base import BaseTool, RunContext, ToolResult, record_sandbox


class FlagForReview(BaseTool):
    name = "flag_for_review"
    description = "Propose flagging the document for a person to check before anyone relies on it. Reversible. Requires human approval."
    side_effect = True
    reversible = True
    parameters = {"reason": {"type": "string"}}
    required = ["reason"]

    def validate_args(self, args: dict) -> str | None:
        return None if str(args.get("reason", "")).strip() else "reason is required"

    async def run_sandbox(self, args: dict, ctx: RunContext) -> ToolResult:
        response = {"status": "flagged (sandbox)", "queue": "Sandbox review queue", "flag_id": f"sandbox-flag-{ctx.action_id or 0}"}
        return record_sandbox(self, args, ctx, response)
