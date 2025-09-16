import datetime
from dataclasses import dataclass

from bot.handlers.feedback.model.review_payload import ReviewPayload


@dataclass
class HistoryItem:
    item_id: int
    created_at: datetime
    payload: ReviewPayload
    final_text: str