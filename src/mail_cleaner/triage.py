"""Interactive sender triage: walk keep senders biggest-first, one keypress each.

Decisions land in the sender-rule files; the next sweep re-evaluates the
affected mail (resume logic re-checks rows a new rule would flip).
"""

from __future__ import annotations

import json

import anthropic

from mail_cleaner import config, llm

SUGGEST_SYSTEM = """You triage email SENDERS for an inbox-cleaning tool, from
aggregate stats of mail the pipeline classified "keep". Content may be Swedish
or English. Per sender choose:
- "ad": marketing/newsletter sender; all their mail quarantines.
- "notification": automated transactional/engagement notices whose value lapses;
  mail quarantines once older than "days" (pick 3-30 by how fast it goes stale).
- "keep": a person, or mail with lasting value. When unsure, "keep".
Respond with only a JSON array: [{"sender": "...", "category": "ad"|"notification"|"keep",
"days": <int, notification only>, "reason": "<short>"}]"""


def keep_senders(rows: list[dict], min_count: int = 3) -> list[dict]:
    """Keep-verdict senders not yet covered by any rule file, biggest first."""
    covered = (
        config.load_protected_senders()
        | config.load_quarantine_senders()
        | set(config.load_notification_senders())
    )
    by_sender: dict[str, dict] = {}
    for row in rows:
        if row["verdict"] != "keep":
            continue
        sender = row["sender_email"]
        domain = row.get("sender_domain") or sender.rsplit("@", 1)[-1]
        if sender in covered or domain in covered:
            continue
        entry = by_sender.setdefault(
            sender, {"sender": sender, "domain": domain, "count": 0, "subjects": []}
        )
        entry["count"] += 1
        subject = row.get("subject", "")
        if subject and subject not in entry["subjects"]:
            entry["subjects"] = (entry["subjects"] + [subject])[-3:]
    return sorted(
        (e for e in by_sender.values() if e["count"] >= min_count),
        key=lambda e: -e["count"],
    )


def suggest(
    senders: list[dict],
    client: anthropic.Anthropic | None = None,
    on_progress=None,
) -> dict[str, dict]:
    """One LLM call per 30 senders; returns {sender: {category, days, reason}}.
    on_progress(senders_processed) is called after each batch. Any failure
    returns what was parsed so far — triage works without it."""
    if config.LLM_DISABLED:
        return {}
    client = client or anthropic.Anthropic()
    suggestions: dict[str, dict] = {}
    for start in range(0, len(senders), 30):
        chunk = senders[start : start + 30]
        if on_progress:
            on_progress(start)
        payload = [
            {
                "sender": e["sender"],
                "domain": e["domain"],
                "kept_count": e["count"],
                "sample_subjects": [llm.mask(s)[:80] for s in e["subjects"]],
            }
            for e in chunk
        ]
        try:
            response = client.beta.messages.create(
                model=config.LLM_MODEL,
                max_tokens=8192,
                output_config={"effort": "low"},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                system=[{"type": "text", "text": SUGGEST_SYSTEM, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
            )
            if response.stop_reason == "refusal":
                continue
            text = next((b.text for b in response.content if b.type == "text"), "")
            for item in json.loads(text[text.index("["): text.rindex("]") + 1]):
                if item.get("category") in ("ad", "notification", "keep"):
                    suggestions[item.get("sender", "")] = item
        except (anthropic.APIError, ValueError, KeyError, TypeError):
            continue
    return suggestions


def run_interactive(rows: list[dict], min_count: int = 3, use_llm: bool = True) -> dict:
    """Prompt per sender; write each decision immediately (quit-safe)."""
    queue = keep_senders(rows, min_count)
    if not queue:
        print("Nothing to triage: every keep sender is below --min or already ruled.")
        return {}

    suggestions = {}
    if use_llm:
        from rich.console import Console
        from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeRemainingColumn

        with Progress(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TimeRemainingColumn(),
            console=Console(stderr=True),
        ) as progress:
            task = progress.add_task(
                f"asking {config.LLM_MODEL} about senders", total=len(queue)
            )
            suggestions = suggest(
                queue, on_progress=lambda n: progress.update(task, completed=n)
            )
            progress.update(task, completed=len(queue))

    counts = {"ad": 0, "notification": 0, "protect": 0, "skip": 0}
    print("\nKeys: [a]d  [e]xpire 14d  [E]xpire custom  [p]rotect  [s]kip  [q]uit  ⏎=accept suggestion\n")
    for i, entry in enumerate(queue, 1):
        sender, domain = entry["sender"], entry["domain"]
        print(f"[{i}/{len(queue)}] {sender}  ({entry['count']} kept)")
        for subject in entry["subjects"]:
            print(f"    · {subject[:70]}")
        hint = suggestions.get(sender)
        if hint:
            days = f" {hint.get('days', 14)}d" if hint["category"] == "notification" else ""
            print(f"    suggestion: {hint['category']}{days} — {hint.get('reason', '')[:70]}")

        try:
            choice = input("  > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if choice == "" and hint:
            choice = {"ad": "a", "notification": "e", "keep": "s"}[hint["category"]]
        if choice == "q":
            break
        elif choice == "a":
            config.add_quarantine_senders({sender})
            counts["ad"] += 1
        elif choice in ("e", "E"):
            days = hint.get("days", 14) if (hint and choice == "e") else 14
            if choice == "E":
                days = int(input("  days until expired: ").strip() or "14")
            config.add_notification_sender(domain, days)
            print(f"    note: expiry applies to the whole domain {domain}")
            counts["notification"] += 1
        elif choice == "p":
            config.add_protected_senders({sender})
            counts["protect"] += 1
        else:
            counts["skip"] += 1
    return counts
