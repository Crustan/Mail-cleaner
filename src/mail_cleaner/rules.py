"""Layers 2–3: cheap header/metadata rules. Most mail is decided here."""

from __future__ import annotations

import time

from mail_cleaner import config
from mail_cleaner.models import EmailMeta


def ad_score(meta: EmailMeta) -> tuple[float, list[str]]:
    """Score 0–1 that this message is advertising, with the signals that fired."""
    score = 0.0
    signals: list[str] = []

    if "list-unsubscribe" in meta.headers:
        score += config.AD_HEADER_SIGNALS["list-unsubscribe"]
        signals.append("header:list-unsubscribe")

    precedence = meta.headers.get("precedence", "").lower()
    if precedence in ("bulk", "list"):
        score += config.AD_HEADER_SIGNALS["precedence"]
        signals.append(f"header:precedence={precedence}")

    if any(h.startswith("x-campaign") or h.startswith("x-mc-") for h in meta.headers):
        score += config.AD_HEADER_SIGNALS["x-campaign"]
        signals.append("header:campaign-platform")

    if _via_bulk_platform(meta):
        score += 0.20
        signals.append("sender:bulk-platform")

    subject = meta.subject.lower()
    promo_hits = [k for k in config.PROMO_SUBJECT_KEYWORDS if k in subject]
    if promo_hits:
        score += min(0.25, 0.15 + 0.05 * (len(promo_hits) - 1))
        signals.append(f"subject:promo:{promo_hits[0]}")

    if "CATEGORY_PROMOTIONS" in meta.labels:
        score += 0.15
        signals.append("gmail:category-promotions")

    return min(score, 1.0), signals


def _via_bulk_platform(meta: EmailMeta) -> bool:
    haystack = " ".join(
        meta.headers.get(h, "")
        for h in ("return-path", "received", "sender", "dkim-signature")
    ).lower()
    return any(domain in haystack for domain in config.BULK_SENDER_DOMAINS)


def expired_score(meta: EmailMeta, now_ms: int | None = None) -> tuple[float, list[str]]:
    """Score 0–1 that this is an expired notification. Age-based rule only;
    external state (PR closed, order completed) is out of spike scope."""
    now_ms = now_ms or int(time.time() * 1000)

    expiry_days = None
    for domain, days in config.load_notification_senders().items():
        if meta.sender_domain == domain or meta.sender_domain.endswith("." + domain):
            expiry_days = days
            break
    if expiry_days is None:
        return 0.0, []

    age_days = (now_ms - meta.date_epoch_ms) / 86_400_000
    if age_days <= expiry_days:
        return 0.0, [f"notification-sender:{meta.sender_domain}:not-yet-expired"]

    # Known notification sender and past its type's expiry window.
    return 0.99, [
        f"notification-sender:{meta.sender_domain}",
        f"age:{age_days:.0f}d>expiry:{expiry_days}d",
    ]
