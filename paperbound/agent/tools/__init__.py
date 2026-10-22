from paperbound.agent.tools.base import BaseTool
from paperbound.agent.tools.create_calendar_event import CreateCalendarEvent
from paperbound.agent.tools.draft_email import DraftEmail
from paperbound.agent.tools.fire_webhook import FireWebhook
from paperbound.agent.tools.get_entity import GetEntity
from paperbound.agent.tools.get_obligations import GetObligations
from paperbound.agent.tools.upsert_record import UpsertRecord

TOOLS: dict[str, BaseTool] = {
    t.name: t
    for t in (GetObligations(), GetEntity(), CreateCalendarEvent(), DraftEmail(), UpsertRecord(), FireWebhook())
}


def get_tool(name: str) -> BaseTool | None:
    return TOOLS.get(name)
