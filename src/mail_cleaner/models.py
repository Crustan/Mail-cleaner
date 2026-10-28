"""Core data types shared by the pipeline layers."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Verdict(str, Enum):
    PROTECTED = "protected"  # excluded before any classification ran
    KEEP = "keep"            # classified, but stays in inbox
    AD = "ad"                # would move to Cleanup/Ads
    EXPIRED = "expired"      # would move to Cleanup/Expired
    UNSURE = "unsure"        # below confidence floor — stays in inbox


@dataclass
class EmailMeta:
    """Headers and metadata only; body is fetched separately and never stored."""

    message_id: str
    thread_id: str
    sender: str              # full From header
    sender_email: str        # extracted address, lowercase
    sender_domain: str
    subject: str
    date_epoch_ms: int
    labels: list[str] = field(default_factory=list)
    headers: dict[str, str] = field(default_factory=dict)  # lowercase keys
    snippet: str = ""


@dataclass
class Decision:
    message_id: str
    sender_email: str
    sender_domain: str
    subject: str
    verdict: Verdict
    confidence: float
    source: str              # "protected" | "rules" | "llm"
    signals: list[str]
    used_llm: bool = False
    model: str | None = None  # LLM model configured for this decision, if used

    def to_log(self) -> dict:
        return {
            **({"model": self.model} if self.used_llm and self.model else {}),
            "message_id": self.message_id,
            "sender_email": self.sender_email,
            "sender_domain": self.sender_domain,
            "subject": self.subject,
            "verdict": self.verdict.value,
            "confidence": round(self.confidence, 3),
            "source": self.source,
            "signals": self.signals,
            "used_llm": self.used_llm,
        }
