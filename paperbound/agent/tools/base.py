from dataclasses import dataclass, field
from typing import Any, Protocol

PROVENANCE_PARAMS = {
    "source_field_ids": {
        "type": "array",
        "items": {"type": "integer"},
        "description": "Ids of the provided fields that justify this action. Required.",
    },
    "rationale": {"type": "string", "description": "One sentence on why this action is proposed."},
}


class AdapterNotConnected(Exception):
    """Raised by a live adapter when the external service is not connected."""


@dataclass
class ToolResult:
    ok: bool
    data: dict = field(default_factory=dict)
    error: str | None = None


@dataclass
class RunContext:
    mode: str = "sandbox"  # live | sandbox
    run_id: int | None = None
    session: Any = None  # SQLAlchemy session; sandbox adapters write their rows through it
    action_id: int | None = None
    existing_obligations: list[dict] = field(default_factory=list)


class Tool(Protocol):
    name: str
    side_effect: bool  # False = read-only
    reversible: bool

    def schema(self) -> dict: ...
    async def run(self, args: dict, ctx: RunContext) -> ToolResult: ...


class BaseTool:
    """Shared plumbing: OpenAI function schema, and adapter selection by ctx.mode."""

    name: str
    description: str
    parameters: dict  # JSON schema "properties" for the tool's own args
    required: list[str] = []
    side_effect = False
    reversible = True
    always_draft = False

    def schema(self) -> dict:
        properties = dict(self.parameters)
        required = list(self.required)
        if self.side_effect:
            properties.update(PROVENANCE_PARAMS)
            required += ["source_field_ids", "rationale"]
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {"type": "object", "properties": properties, "required": required},
            },
        }

    def allowed_args(self) -> set[str]:
        return set(self.parameters)

    def finalize_args(self, args: dict) -> dict:
        """Keep only the tool's own args. Tools with placeholders override this to force them."""
        return {k: v for k, v in args.items() if k in self.allowed_args()}

    def validate_args(self, args: dict) -> str | None:
        """Return an error message if the args are unusable, else None."""
        return None

    async def run(self, args: dict, ctx: RunContext) -> ToolResult:
        adapter = getattr(self, f"run_{ctx.mode}", None)
        if adapter is None:
            return ToolResult(False, error=f"unknown mode {ctx.mode!r}")
        try:
            return await adapter(args, ctx)
        except AdapterNotConnected as exc:
            return ToolResult(False, error=str(exc))

    async def run_live(self, args: dict, ctx: RunContext) -> ToolResult:
        raise AdapterNotConnected(f"{self.name}: no live adapter is connected")

    async def run_sandbox(self, args: dict, ctx: RunContext) -> ToolResult:
        raise NotImplementedError


def record_sandbox(tool: "BaseTool", args: dict, ctx: RunContext, response: dict) -> ToolResult:
    """Persist what a sandbox adapter 'did' (full args + fake response) and return it."""
    if ctx.session is not None:
        from paperbound.db.models import SandboxAction

        row = SandboxAction(
            run_id=ctx.run_id, action_id=ctx.action_id, tool=tool.name, args_json=args, response_json=response
        )
        ctx.session.add(row)
        ctx.session.flush()
        response = {**response, "sandbox_action_id": row.id}
    return ToolResult(True, response)
