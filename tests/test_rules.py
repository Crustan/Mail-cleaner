import time

from mail_cleaner.models import EmailMeta
from mail_cleaner.rules import ad_score, expired_score


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


def test_plain_personal_mail_scores_low():
    score, signals = ad_score(meta(subject="Lunch tomorrow?"))
    assert score < 0.30
    assert signals == []


def test_newsletter_headers_and_promo_subject_clear_floor():
    m = meta(
        subject="Last chance: 50% off everything — free shipping",
        headers={"list-unsubscribe": "<mailto:u@x>", "precedence": "bulk"},
        labels=["CATEGORY_PROMOTIONS"],
    )
    score, signals = ad_score(m)
    assert score >= 0.95
    assert "header:list-unsubscribe" in signals


def test_swedish_promo_keywords_count():
    score, _ = ad_score(meta(subject="Stor rea – 30% rabatt och fri frakt"))
    assert score > 0


def test_expired_requires_known_sender():
    old = int(time.time() * 1000) - 30 * 86_400_000
    score, _ = expired_score(meta(date_epoch_ms=old))
    assert score == 0.0


def test_expired_github_notification_past_window():
    old = int(time.time() * 1000) - 30 * 86_400_000
    m = meta(
        sender_email="notifications@github.com",
        sender_domain="github.com",
        date_epoch_ms=old,
    )
    score, signals = expired_score(m)
    assert score >= 0.98
    assert any(s.startswith("age:") for s in signals)


def test_fresh_github_notification_not_expired():
    m = meta(sender_email="notifications@github.com", sender_domain="github.com")
    score, _ = expired_score(m)
    assert score == 0.0


def test_old_shipping_update_expires_for_any_sender():
    old = int(time.time() * 1000) - 30 * 86_400_000
    m = meta(subject="Your order 919507 is on its way", date_epoch_ms=old)
    score, signals = expired_score(m)
    assert score >= 0.98
    assert any(s.startswith("shipping-update:") for s in signals)


def test_fresh_shipping_update_not_expired():
    m = meta(subject="A shipment from order KS-1010919507 is on the way")
    score, _ = expired_score(m)
    assert score == 0.0
