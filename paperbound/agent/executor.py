import asyncio

from paperbound.agent.tools import get_tool
from paperbound.agent.tools.base import RunContext
from paperbound.db.models import ActionResult, ProposedAction


def execute_action(session, action: ProposedAction, mode: str = "sandbox") -> ActionResult:
    """Run an approved action through its adapter. Only sandbox adapters exist so far."""
    tool = get_tool(action.tool)
    if tool is None:
        out_ok, data, error = False, {}, f"unknown tool {action.tool}"
    else:
        ctx = RunContext(mode=mode, run_id=action.run_id, session=session, action_id=action.id)
        out = asyncio.run(tool.run(action.args_json, ctx))
        out_ok, data, error = out.ok, out.data, out.error

    action.status = "executed" if out_ok else "failed"
    result = ActionResult(
        action_id=action.id,
        adapter=mode,
        response_json=data if out_ok else {"error": error},
    )
    session.add(result)
    session.flush()
    return result
