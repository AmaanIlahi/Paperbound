from paperbound.agent.tools.base import BaseTool, RunContext, ToolResult, record_sandbox

URL_PLACEHOLDER = "{{WEBHOOK_URL}}"


class FireWebhook(BaseTool):
    name = "fire_webhook"
    description = "Propose an HTTP POST to a webhook. NOT reversible. The URL is a placeholder the user fills in."
    side_effect = True
    reversible = False
    parameters = {
        "event": {"type": "string"},
        "payload": {"type": "object"},
    }
    required = ["event", "payload"]

    def finalize_args(self, args: dict) -> dict:
        clean = super().finalize_args(args)
        clean["url"] = URL_PLACEHOLDER  # never taken from the model or the document
        return clean

    def validate_args(self, args: dict) -> str | None:
        if not str(args.get("event", "")).strip() or not isinstance(args.get("payload"), dict):
            return "event and payload are required"
        return None

    async def run_sandbox(self, args: dict, ctx: RunContext) -> ToolResult:
        response = {"status": 200, "body": "ok (sandbox: nothing was sent)", "request_logged": True}
        return record_sandbox(self, args, ctx, response)
