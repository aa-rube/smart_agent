from dataclasses import dataclass
from typing import Optional


@dataclass
class ReviewPayload:
    client_name: str
    agent_name: str
    company: Optional[str]
    city: str
    address: Optional[str]
    deal_type: str  # sale|buy|rent|lease|custom
    deal_custom: Optional[str]
    situation: str
    style: str  # friendly|neutral|formal|brief|long