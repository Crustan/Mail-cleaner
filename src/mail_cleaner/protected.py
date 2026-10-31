"""Layer 1: protected beats predicted. Runs before any rule or model."""

from __future__ import annotations

from mail_cleaner import config
from mail_cleaner.models import EmailMeta


def protected_reason(meta: EmailMeta, protected_senders: set[str]) -> str | None:
    """Return why this mail is protected, or None if it may be classified."""
    label_hits = config.PROTECTED_LABELS.intersection(meta.labels)
    if label_hits:
        return f"label:{sorted(label_hits)[0]}"

    if meta.sender_email in protected_senders:
        return "protected-sender:address"
    if meta.sender_domain in protected_senders:
        return "protected-sender:domain"
    if meta.sender_domain in config.PROTECTED_DOMAINS:
        return "protected-domain"

    subject = meta.subject.lower()
    for keyword in config.PROTECTED_SUBJECT_KEYWORDS:
        if keyword in subject:
            return f"protected-category:{keyword}"
    for first, second in config.PROTECTED_SUBJECT_PAIRS:
        if first in subject and second in subject:
            return f"protected-category:{first}+{second}"

    return None
