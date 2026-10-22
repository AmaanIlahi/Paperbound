from paperbound.agent.tools.base import BaseTool, RunContext, ToolResult


class GetObligations(BaseTool):
    name = "get_obligations"
    description = "Read-only. Obligations already stored for this document from earlier runs, to avoid duplicates."
    side_effect = False
    parameters = {}

    async def run(self, args: dict, ctx: RunContext) -> ToolResult:
        return ToolResult(True, {"obligations": ctx.existing_obligations})
