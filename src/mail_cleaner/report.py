"""Aggregate the shadow log into the per-sender report the brief's dry run requires."""

from __future__ import annotations

import json
from collections import Counter, defaultdict

from mail_cleaner import config


def load_log() -> list[dict]:
    if not config.SHADOW_LOG.exists():
        return []
    rows = []
    for line in config.SHADOW_LOG.read_text().splitlines():
        if line.strip():
            rows.append(json.loads(line))
    # Keep the latest decision per message (reruns overwrite).
    latest = {row["message_id"]: row for row in rows}
    return list(latest.values())


def resume_ids(rows: list[dict]) -> set[str]:
    """Message ids a resumed sweep may skip: everything already decided, except
    rows that deserve re-evaluation —
    - the LLM call failed (retry it),
    - a sender rule added since would change a keep/unsure verdict,
    - keep mail from a notification domain (it expires as it ages)."""
    quarantine = config.load_quarantine_senders()
    notification_domains = config.load_notification_senders()

    def domain_is_notification(domain: str) -> bool:
        return any(
            domain == d or domain.endswith("." + d) for d in notification_domains
        )

    skip = set()
    for row in rows:
        if any("llm-error" in signal for signal in row.get("signals", [])):
            continue
        if row["verdict"] in ("keep", "unsure"):
            sender = row["sender_email"]
            domain = row.get("sender_domain", sender.rsplit("@", 1)[-1])
            if sender in quarantine or domain in quarantine:
                continue
            if domain_is_notification(domain):
                continue
        skip.add(row["message_id"])
    return skip


def render(rows: list[dict]) -> str:
    if not rows:
        return "No shadow log yet. Run: mail-cleaner shadow --days 7"

    total = len(rows)
    by_verdict = Counter(row["verdict"] for row in rows)
    llm_count = sum(1 for row in rows if row["used_llm"])

    lines = [
        f"Messages classified: {total}",
        "",
        "Verdicts:",
    ]
    for verdict in ("ad", "expired", "unsure", "keep", "protected"):
        count = by_verdict.get(verdict, 0)
        lines.append(f"  {verdict:<10} {count:>5}  ({count / total:.0%})")

    protected_reasons = Counter(
        ":".join(row["signals"][0].split(":")[:2]) if row["signals"] else "unknown"
        for row in rows
        if row["verdict"] == "protected"
    )
    if protected_reasons:
        lines += ["", "Protected by:"]
        for reason, count in protected_reasons.most_common():
            lines.append(f"  {reason:<20} {count:>5}")

    would_move = by_verdict.get("ad", 0) + by_verdict.get("expired", 0)
    lines += [
        "",
        f"Would quarantine: {would_move} ({would_move / total:.0%} of classified mail)",
        f"LLM calls: {llm_count} ({llm_count / total:.0%} — the rest decided by rules)",
        "",
        "Top senders by would-quarantine count (review these before trusting auto mode):",
    ]

    per_sender: dict[str, Counter] = defaultdict(Counter)
    for row in rows:
        if row["verdict"] in ("ad", "expired"):
            per_sender[row["sender_email"]][row["verdict"]] += 1
    ranked = sorted(per_sender.items(), key=lambda kv: -sum(kv[1].values()))
    for sender, counts in ranked[:30]:
        detail = ", ".join(f"{v}:{n}" for v, n in counts.most_common())
        lines.append(f"  {sum(counts.values()):>4}  {sender}  ({detail})")

    keep_rows = [row for row in rows if row["verdict"] == "keep"]
    if keep_rows:
        per_keep_sender: dict[str, list[dict]] = defaultdict(list)
        for row in keep_rows:
            per_keep_sender[row["sender_email"]].append(row)
        ranked_keep = sorted(per_keep_sender.items(), key=lambda kv: -len(kv[1]))
        lines += [
            "",
            "Top keep senders — triage candidates (add ad senders to",
            "data/quarantine_senders.txt, notification senders to data/notification_senders.txt;",
            "the next sweep re-evaluates them):",
        ]
        for sender, sender_rows in ranked_keep[:30]:
            sample = sender_rows[-1]["subject"][:55]
            lines.append(f"  {len(sender_rows):>5}  {sender}  — {sample}")

    unsure = [row for row in rows if row["verdict"] == "unsure"]
    if unsure:
        lines += ["", f"Unsure ({len(unsure)}) — label these to tune thresholds:"]
        for row in unsure[:20]:
            lines.append(f"  {row['sender_email']}: {row['subject'][:60]}")

    return "\n".join(lines)
