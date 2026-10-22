from paperbound.agent.tools.base import BaseTool, RunContext, ToolResult


class GetEntity(BaseTool):
    name = "get_entity"
    description = "Read-only. Look up a known counterparty (vendor, landlord) by name."
    side_effect = False
    parameters = {"name": {"type": "string"}}
    required = ["name"]

    async def run(self, args: dict, ctx: RunContext) -> ToolResult:
        # Stub: the entities table does not exist yet, so nothing is ever known.
        return ToolResult(True, {"entity": None})
