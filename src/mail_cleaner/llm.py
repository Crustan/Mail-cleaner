"""Layer 4: LLM confirmation for mail the rules could not decide.

Sends masked metadata + snippet only (never the full body, never attachments).
A refusal or parse failure degrades to UNSURE — mail stays in the inbox.
"""

from __future__ import annotations

import json
import re

import anthropic

from mail_cleaner import config
from mail_cleaner.models import EmailMeta

SYSTEM_PROMPT = """You classify a single email from its metadata for an inbox-cleaning tool.
Categories:
- "ad": marketing, newsletters, sales, promotions from brands or retailers.
- "keep": anything personal, work-related, transactional (receipts, invoices,
  orders, security, travel, legal, health, government) or that you cannot rule out
  as important. When unsure, answer "keep" — a wrongly removed email is expensive,
  a missed ad is cheap.
Content may be Swedish or English.
Respond with only a JSON object: {"category": "ad"|"keep", "confidence": 0.0-1.0, "reason": "<short>"}"""

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
_DIGITS_RE = re.compile(r"\d{5,}")


def mask(text: str) -> str:
    """Mask addresses and long digit runs before anything leaves the machine."""
    return _DIGITS_RE.sub("#####", _EMAIL_RE.sub("<email>", text))


def classify(meta: EmailMeta, client: anthropic.Anthropic | None = None) -> tuple[str, float, str]:
    """Return (category, confidence, reason). Degrades to ("unsure", 0, ...) on any failure."""
    if config.LLM_DISABLED:
        return "unsure", 0.0, "llm-disabled"

    client = client or anthropic.Anthropic()
    payload = {
        "from_domain": meta.sender_domain,
        "subject": mask(meta.subject),
        "snippet": mask(meta.snippet)[:500],
        "has_list_unsubscribe": "list-unsubscribe" in meta.headers,
        "gmail_category_promotions": "CATEGORY_PROMOTIONS" in meta.labels,
    }

    try:
        response = client.beta.messages.create(
            model=config.LLM_MODEL,
            max_tokens=2048,
            output_config={"effort": "low"},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            system=[{
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }],
            messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
        )
    except anthropic.APIError as exc:
        return "unsure", 0.0, f"llm-error:{type(exc).__name__}"

    if response.stop_reason == "refusal":
        return "unsure", 0.0, "llm-refusal"

    text = next((b.text for b in response.content if b.type == "text"), "")
    try:
        parsed = json.loads(text[text.index("{"): text.rindex("}") + 1])
        category = parsed["category"] if parsed.get("category") in ("ad", "keep") else "unsure"
        confidence = max(0.0, min(1.0, float(parsed.get("confidence", 0.0))))
        return category, confidence, str(parsed.get("reason", ""))[:200]
    except (ValueError, KeyError, TypeError):
        return "unsure", 0.0, "llm-parse-error"
