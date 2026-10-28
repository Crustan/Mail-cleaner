"""Native Gmail filters for stable ad senders (brief finding 19).

A filter acts at delivery — Gmail labels and archives the mail itself, with no
API or LLM cost, even when this tool isn't running. Only ad senders qualify:
expiry is age-based and cannot be expressed as a delivery-time filter.
"""

from __future__ import annotations

from collections import Counter, defaultdict

from mail_cleaner import actions, config


def candidates(rows: list[dict], min_ads: int | None = None) -> list[tuple[str, int]]:
    """Senders consistently classified ad: ≥ min_ads ads, zero keep/protected,
    not on the user's protected list. Sorted by volume."""
    min_ads = min_ads or config.FILTER_MIN_ADS
    protected = config.load_protected_senders()
    per_sender: dict[str, Counter] = defaultdict(Counter)
    for row in rows:
        per_sender[row["sender_email"]][row["verdict"]] += 1

    out = []
    for sender, counts in per_sender.items():
        if sender in protected or sender.rsplit("@", 1)[-1] in protected:
            continue
        if counts["ad"] >= min_ads and counts["keep"] == 0 and counts["protected"] == 0:
            out.append((sender, counts["ad"]))
    return sorted(out, key=lambda kv: -kv[1])


def existing_filter_senders(service) -> set[str]:
    result = service.users().settings().filters().list(userId="me").execute()
    return {
        f.get("criteria", {}).get("from", "").lower()
        for f in result.get("filter", [])
        if f.get("criteria", {}).get("from")
    }


def create_filters(service, senders: list[str]) -> int:
    """Create one filter per sender: label Cleanup/Ads + skip the inbox."""
    label_id = actions.ensure_labels(service)[actions.QUARANTINE_LABELS["ad"]]
    created = 0
    for sender in senders:
        service.users().settings().filters().create(
            userId="me",
            body={
                "criteria": {"from": sender},
                "action": {
                    "addLabelIds": [label_id],
                    "removeLabelIds": ["INBOX"],
                },
            },
        ).execute()
        created += 1
    return created
