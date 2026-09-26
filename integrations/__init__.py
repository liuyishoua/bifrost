"""Adapters translate each application's read-only status into Bifrost's contract."""
from .ticket import TicketAdapter
from .douyin import DouyinAdapter
from .base import HttpContractAdapter

ADAPTERS = {"ticket": TicketAdapter(), "douyin": DouyinAdapter()}


def adapter_for(app_id):
    return ADAPTERS.get(app_id, HttpContractAdapter())
