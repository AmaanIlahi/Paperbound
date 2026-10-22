from paperbound.agent.tools.base import BaseTool, RunContext, ToolResult, record_sandbox

RECIPIENT_PLACEHOLDER = "{{RECIPIENT}}"


class DraftEmail(BaseTool):
    name = "draft_email"
    description = (
        "Propose an email DRAFT. It is never sent. The recipient is not yours to choose: "
        "it is a placeholder the user fills in."
    )
    side_effect = True
    reversible = True
    always_draft = True
    parameters = {
        "variant": {"type": "string", "enum": ["renew", "terminate"], "description": "Which choice this notice states."},
        "subject": {"type": "string"},
        "body": {"type": "string", "description": "Plain text. Use [PLACEHOLDERS] for anything the user must decide or fill in."},
    }
    required = ["variant", "subject", "body"]

    def finalize_args(self, args: dict) -> dict:
        clean = super().finalize_args(args)
        clean["to"] = RECIPIENT_PLACEHOLDER  # never taken from the model or the document
        return clean

    def validate_args(self, args: dict) -> str | None:
        if not str(args.get("subject", "")).strip() or not str(args.get("body", "")).strip():
            return "subject and body are required"
        return None

    async def run_sandbox(self, args: dict, ctx: RunContext) -> ToolResult:
        response = {"status": "draft saved (sandbox), not sent", "draft_id": f"sandbox-draft-{ctx.action_id or 0}", "mailbox": "Sandbox Inbox"}
        return record_sandbox(self, args, ctx, response)
