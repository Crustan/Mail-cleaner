"""Layered decision pipeline: protected → rules → LLM → confidence floor.

Any layer can return mail to the inbox; shadow mode records verdicts only.
"""

from __future__ import annotations

import anthropic

from mail_cleaner import config, llm, protected, rules
from mail_cleaner.models import Decision, EmailMeta, Verdict

# Rules alone decide at or above this; between the floor and this, the LLM confirms.
RULES_DECIDE_AT = 0.80


def decide(
    meta: EmailMeta,
    protected_senders: set[str],
    client: anthropic.Anthropic | None = None,
) -> Decision:
    reason = protected.protected_reason(meta, protected_senders)
    if reason:
        return _decision(meta, Verdict.PROTECTED, 1.0, "protected", [reason])

    exp_score, exp_signals = rules.expired_score(meta)
    if exp_score >= config.CONFIDENCE_FLOOR["expired"]:
        return _decision(meta, Verdict.EXPIRED, exp_score, "rules", exp_signals)

    score, signals = rules.ad_score(meta)
    if score >= config.CONFIDENCE_FLOOR["ad"]:
        return _decision(meta, Verdict.AD, score, "rules", signals)
    if score < RULES_DECIDE_AT:
        verdict = Verdict.KEEP if score < 0.30 else Verdict.UNSURE
        return _decision(meta, verdict, score, "rules", signals or ["no-ad-signals"])

    # Rules lean ad but below the floor — ask the LLM to confirm.
    category, llm_conf, llm_reason = llm.classify(meta, client)
    signals = signals + [f"llm:{category}:{llm_reason}"]
    if category == "ad" and llm_conf >= config.CONFIDENCE_FLOOR["ad"]:
        return _decision(meta, Verdict.AD, llm_conf, "llm", signals, used_llm=True)
    if category == "keep":
        return _decision(meta, Verdict.KEEP, llm_conf, "llm", signals, used_llm=True)
    return _decision(meta, Verdict.UNSURE, max(score, llm_conf), "llm", signals, used_llm=True)


def _decision(
    meta: EmailMeta,
    verdict: Verdict,
    confidence: float,
    source: str,
    signals: list[str],
    used_llm: bool = False,
) -> Decision:
    return Decision(
        message_id=meta.message_id,
        sender_email=meta.sender_email,
        sender_domain=meta.sender_domain,
        subject=meta.subject,
        verdict=verdict,
        confidence=confidence,
        source=source,
        signals=signals,
        used_llm=used_llm,
    )
