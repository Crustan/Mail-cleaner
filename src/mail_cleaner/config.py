"""Thresholds and rule data. Mirrors the product brief; tune from shadow data."""

from __future__ import annotations

import functools
import os
from pathlib import Path

# Phase 2 (suggest mode): modify = read + labels + trash. Still no send, and
# permanent deletion is not possible with this scope (Gmail trash auto-purges
# after 30 days — the brief's retention window, provider-enforced).
# settings.basic is for native Gmail filters only (brief finding 19).
GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.settings.basic",
]

# A sender becomes a native-filter candidate after this many ad verdicts
# with zero keep/protected verdicts.
FILTER_MIN_ADS = 3

DATA_DIR = Path(os.environ.get("MAIL_CLEANER_DATA_DIR", "data"))
SHADOW_LOG = DATA_DIR / "shadow_log.jsonl"
ACTIONS_LOG = DATA_DIR / "actions_log.jsonl"
CREDENTIALS_FILE = Path("credentials.json")
TOKEN_FILE = Path("token.json")

# Confidence floors per the brief's data-loss-prevention table (rule scores).
CONFIDENCE_FLOOR = {"ad": 0.95, "expired": 0.98}

# Acceptance threshold for an LLM "ad" verdict. The model's self-reported
# confidence is conservative (brief finding 7): in live runs it lands at
# 0.75–0.90 on ads that are plainly correct, so 0.95 strands them in unsure.
# Owner reviewed the full 2,000-message sweep (2026-10-28): every remaining
# low-confidence ad call was correct too, so the bar sits at 0.50 now.
# Revisit against restore rates once actions exist.
LLM_AD_ACCEPT = 0.50

LLM_MODEL = os.environ.get("MAIL_CLEANER_MODEL", "claude-opus-5-5")
LLM_DISABLED = os.environ.get("MAIL_CLEANER_NO_LLM", "0") == "1"

# Protected categories: any hit short-circuits to PROTECTED before rules or model.
# Swedish + English (open question in the brief: both kept until decided otherwise).
PROTECTED_SUBJECT_KEYWORDS = [
    # finance / transactional
    "receipt", "kvitto", "invoice", "faktura", "payment", "betalning",
    "order confirmation", "orderbekräftelse", "contract", "avtal",
    # security / account
    "verification code", "verifieringskod", "password", "lösenord",
    "security alert", "säkerhetsvarning", "sign-in", "inloggning", "2fa",
    # travel / legal / government
    "boarding pass", "itinerary", "resplan", "booking", "bokning",
    "skatteverket", "försäkringskassan", "tax", "deklaration",
]

# Domains never touched regardless of content. Extend via data/protected_domains.txt.
PROTECTED_DOMAINS = {
    "skatteverket.se", "forsakringskassan.se", "1177.se",
}

# Gmail labels that short-circuit to PROTECTED. IMPORTANT is deliberately absent:
# Gmail auto-applies it to ~90% of archived mail (measured in a live sweep), and the
# API cannot distinguish user-marked from auto-marked importance.
PROTECTED_LABELS = {"STARRED", "SENT"}

# Advertising signals (rule layer). Weights sum per message; see rules.py.
AD_HEADER_SIGNALS = {
    "list-unsubscribe": 0.55,
    "precedence": 0.15,       # only counted when value is bulk/list
    "x-campaign": 0.20,
    "x-mailer": 0.05,
}

BULK_SENDER_DOMAINS = {
    # ESP platforms commonly used for marketing sends
    "mailchimp.com", "mailchimpapp.net", "sendgrid.net", "rsgsv.net",
    "mailgun.org", "amazonses.com", "braze.com", "klaviyomail.com",
    "mailjet.com", "getresponse.com", "cmail19.com", "cmail20.com",
}

PROMO_SUBJECT_KEYWORDS = [
    # English
    "sale", "% off", "discount", "deal", "offer", "free shipping",
    "last chance", "limited time", "new arrivals", "newsletter",
    # Swedish
    "rea", "rabatt", "erbjudande", "kampanj", "fri frakt",
    "sista chansen", "nyhetsbrev", "utförsäljning",
]

# Expired-notification senders with per-type expiry in days (brief: rule-driven
# expiry per sender type; external state like "PR closed" is out of spike scope —
# age past expiry is the only rule here). Protected categories run first, so
# receipts/order confirmations from these domains are still shielded by keyword.
NOTIFICATION_SENDERS = {
    "github.com": 14,
    "azuredevops.microsoft.com": 14,
    "visualstudio.com": 14,
    "vinted.se": 7,
    "vinted.com": 7,
    "facebookmail.com": 7,
    "linkedin.com": 14,
    # Delivery/engagement notices — disposable once aged (owner feedback)
    "airmee.com": 7,
    "amazon.se": 14,
    "amazon.com": 14,
    "dev.to": 14,
}


@functools.cache
def load_notification_senders() -> dict[str, int]:
    """Defaults merged with user overrides from data/notification_senders.txt
    (one `domain days` per line; days 0 disables expiry for that domain)."""
    senders = dict(NOTIFICATION_SENDERS)
    path = DATA_DIR / "notification_senders.txt"
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) == 2 and parts[1].isdigit():
                days = int(parts[1])
                if days == 0:
                    senders.pop(parts[0].lower(), None)
                else:
                    senders[parts[0].lower()] = days
    return senders


def load_protected_senders() -> set[str]:
    """User-maintained allowlist: one address or domain per line."""
    path = DATA_DIR / "protected_senders.txt"
    if not path.exists():
        return set()
    return {
        line.strip().lower()
        for line in path.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    }


def load_quarantine_senders() -> set[str]:
    """User-maintained force-quarantine list (sender-level rules): one address
    or domain per line. Protected checks still win over this list."""
    path = DATA_DIR / "quarantine_senders.txt"
    if not path.exists():
        return set()
    return {
        line.strip().lower()
        for line in path.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    }


def add_quarantine_senders(senders: set[str]) -> set[str]:
    """Append senders not already on the force-quarantine list."""
    new = {s.lower() for s in senders} - load_quarantine_senders()
    if new:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with (DATA_DIR / "quarantine_senders.txt").open("a") as f:
            for sender in sorted(new):
                f.write(sender + "\n")
    return new


def add_notification_sender(domain: str, days: int) -> None:
    """Append a notification-expiry override and refresh the cached list."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with (DATA_DIR / "notification_senders.txt").open("a") as f:
        f.write(f"{domain.lower()} {days}\n")
    load_notification_senders.cache_clear()


def add_protected_senders(senders: set[str]) -> set[str]:
    """Append senders not already listed; returns the ones actually added."""
    new = {s.lower() for s in senders} - load_protected_senders()
    if new:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with (DATA_DIR / "protected_senders.txt").open("a") as f:
            for sender in sorted(new):
                f.write(sender + "\n")
    return new
