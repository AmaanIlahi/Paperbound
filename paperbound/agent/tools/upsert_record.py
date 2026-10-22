from paperbound.agent.tools.base import BaseTool, RunContext, ToolResult, record_sandbox


class UpsertRecord(BaseTool):
    name = "upsert_record"
    description = "Propose creating or updating a tracked record (for example an obligation). Reversible. Requires human approval."
    side_effect = True
    reversible = True
    parameters = {
        "record_type": {"type": "string", "enum": ["obligation"]},
        "key": {"type": "string", "description": "Stable identifier so a repeat updates instead of duplicating"},
        "data": {"type": "object", "description": "Record fields"},
    }
    required = ["record_type", "key", "data"]

    def validate_args(self, args: dict) -> str | None:
        if not str(args.get("key", "")).strip() or not isinstance(args.get("data"), dict):
            return "key and data are required"
        return None

    async def run_sandbox(self, args: dict, ctx: RunContext) -> ToolResult:
        response = {"status": "upserted (sandbox)", "record_key": args.get("key"), "table": "sandbox session table"}
        return record_sandbox(self, args, ctx, response)
