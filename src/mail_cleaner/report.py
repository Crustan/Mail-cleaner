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

    unsure = [row for row in rows if row["verdict"] == "unsure"]
    if unsure:
        lines += ["", f"Unsure ({len(unsure)}) — label these to tune thresholds:"]
        for row in unsure[:20]:
            lines.append(f"  {row['sender_email']}: {row['subject'][:60]}")

    return "\n".join(lines)
