import time

from mail_cleaner import config
from mail_cleaner.models import EmailMeta, Verdict
from mail_cleaner.pipeline import decide


def meta(**overrides) -> EmailMeta:
    base = dict(
        message_id="m1",
        thread_id="t1",
        sender="Shop <news@shop.example>",
        sender_email="news@shop.example",
        sender_domain="shop.example",
        subject="Hello",
        date_epoch_ms=int(time.time() * 1000),
        labels=[],
        headers={},
        snippet="",
    )
    base.update(overrides)
    return EmailMeta(**base)


def test_starred_mail_is_protected_before_any_rule():
    m = meta(
        labels=["STARRED"],
        subject="50% off everything",
        headers={"list-unsubscribe": "<mailto:u@x>"},
    )
    decision = decide(m, protected_senders=set())
    assert decision.verdict == Verdict.PROTECTED
    assert decision.source == "protected"


def test_protected_sender_domain_beats_ad_signals():
    m = meta(headers={"list-unsubscribe": "<mailto:u@x>"})
    decision = decide(m, protected_senders={"shop.example"})
    assert decision.verdict == Verdict.PROTECTED


def test_invoice_subject_is_protected_category():
    decision = decide(meta(subject="Din faktura för oktober"), protected_senders=set())
    assert decision.verdict == Verdict.PROTECTED


def test_clear_ad_decided_by_rules_without_llm():
    m = meta(
        subject="Last chance: 50% off — free shipping",
        headers={"list-unsubscribe": "<mailto:u@x>", "precedence": "bulk"},
        labels=["CATEGORY_PROMOTIONS"],
    )
    decision = decide(m, protected_senders=set())
    assert decision.verdict == Verdict.AD
    assert decision.confidence >= config.CONFIDENCE_FLOOR["ad"]
    assert not decision.used_llm


def test_plain_mail_kept_without_llm():
    decision = decide(meta(subject="Re: lunch tomorrow?"), protected_senders=set())
    assert decision.verdict == Verdict.KEEP
    assert not decision.used_llm


def test_old_github_notification_expired():
    m = meta(
        sender_email="notifications@github.com",
        sender_domain="github.com",
        date_epoch_ms=int(time.time() * 1000) - 60 * 86_400_000,
    )
    decision = decide(m, protected_senders=set())
    assert decision.verdict == Verdict.EXPIRED


def test_midband_score_goes_to_llm_and_degrades_to_unsure_when_disabled(monkeypatch):
    monkeypatch.setattr(config, "LLM_DISABLED", True)
    # Bare list-unsubscribe ≈ 0.55: above LLM_CONSULT_AT, below the 0.95 floor →
    # LLM path; disabled LLM must leave it in the inbox.
    m = meta(subject="Weekly digest", headers={"list-unsubscribe": "<mailto:u@x>"})
    decision = decide(m, protected_senders=set())
    assert decision.verdict == Verdict.UNSURE
    assert decision.used_llm


def test_security_localpart_is_protected_despite_notification_age():
    m = meta(
        sender_email="security@facebookmail.com",
        sender_domain="facebookmail.com",
        date_epoch_ms=int(time.time() * 1000) - 60 * 86_400_000,
    )
    decision = decide(m, protected_senders=set())
    assert decision.verdict == Verdict.PROTECTED
