"""Apply phase (suggest mode): act on shadow verdicts. Requires gmail.modify.

Default action is a label move to a Cleanup folder-label; --trash uses Gmail's
trash, which the provider auto-purges after 30 days (the brief's retention
window). Every action is logged to data/actions_log.jsonl and undoable per run.
"""

from __future__ import annotations

import json
import sys
import uuid
from collections import Counter

from googleapiclient.errors import HttpError

from mail_cleaner import config

QUARANTINE_LABELS = {"ad": "Cleanup/Ads", "expired": "Cleanup/Expired"}
ACTIONABLE_VERDICTS = set(QUARANTINE_LABELS)

REAUTH_HINT = (
    "Gmail refused the write (403). The stored token is read-only from the "
    "shadow phase: run `rm token.json && mail-cleaner auth` to re-consent "
    "with the modify scope."
)


def ensure_labels(service) -> dict[str, str]:
    """Create the Cleanup labels if missing; return {label_name: label_id}."""
    existing = {
        label["name"]: label["id"]
        for label in service.users().labels().list(userId="me").execute(num_retries=5).get("labels", [])
    }
    ids = {}
    for name in QUARANTINE_LABELS.values():
        if name in existing:
            ids[name] = existing[name]
        else:
            created = (
                service.users()
                .labels()
                .create(
                    userId="me",
                    body={
                        "name": name,
                        "labelListVisibility": "labelShow",
                        "messageListVisibility": "show",
                    },
                )
                .execute(num_retries=5)
            )
            ids[name] = created["id"]
    return ids


def actioned_ids() -> set[str]:
    """Message ids any previous apply run already acted on."""
    return {entry["message_id"] for entry in _load_entries()}


def plan(
    rows: list[dict],
    verdicts: set[str],
    max_actions: int | None,
    exclude: set[str] | None = None,
) -> list[dict]:
    exclude = exclude or set()
    actionable = [
        row
        for row in rows
        if row["verdict"] in verdicts and row["message_id"] not in exclude
    ]
    return actionable[:max_actions] if max_actions else actionable


def labeled_archived(service) -> list[dict]:
    """Mail carrying a Cleanup label that is no longer in the inbox (and not in
    trash): labeled at delivery by a Gmail filter, then read and archived by the
    user — archiving is the removal trigger. Rows are apply()-compatible."""
    import email.utils

    label_ids = ensure_labels(service)
    rows = []
    for verdict, name in QUARANTINE_LABELS.items():
        page_token = None
        while True:
            result = (
                service.users()
                .messages()
                .list(
                    userId="me",
                    labelIds=[label_ids[name]],
                    q="-in:inbox -in:trash",
                    pageToken=page_token,
                    maxResults=100,
                )
                .execute(num_retries=5)
            )
            for ref in result.get("messages", []):
                msg = (
                    service.users()
                    .messages()
                    .get(
                        userId="me",
                        id=ref["id"],
                        format="metadata",
                        metadataHeaders=["From"],
                    )
                    .execute(num_retries=5)
                )
                headers = {
                    h["name"].lower(): h["value"]
                    for h in msg.get("payload", {}).get("headers", [])
                }
                _, sender = email.utils.parseaddr(headers.get("from", ""))
                rows.append(
                    {
                        "message_id": ref["id"],
                        "sender_email": sender.lower(),
                        "verdict": verdict,
                    }
                )
            page_token = result.get("nextPageToken")
            if not page_token:
                break
    return rows


def describe_plan(planned: list[dict], trash: bool) -> str:
    action = "trash" if trash else "label + keep out of inbox"
    per_sender: Counter = Counter(
        (row["sender_email"], row["verdict"]) for row in planned
    )
    lines = [f"Planned: {len(planned)} messages → {action}", ""]
    for (sender, verdict), count in per_sender.most_common(40):
        lines.append(f"  {count:>4}  {sender}  ({verdict})")
    if len(per_sender) > 40:
        lines.append(f"  ... and {len(per_sender) - 40} more senders")
    return "\n".join(lines)


def apply(service, planned: list[dict], trash: bool) -> tuple[str, Counter]:
    """Execute the plan. Returns (run id for undo, per-outcome counts)."""
    label_ids = ensure_labels(service)
    run_id = uuid.uuid4().hex[:8]
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)

    outcomes: Counter = Counter()
    done = 0
    with config.ACTIONS_LOG.open("a") as log:
        for row in planned:
            msg_id = row["message_id"]
            label_name = QUARANTINE_LABELS[row["verdict"]]
            try:
                before = (
                    service.users()
                    .messages()
                    .get(userId="me", id=msg_id, format="minimal")
                    .execute(num_retries=5)
                )
                had_inbox = "INBOX" in before.get("labelIds", [])
                if trash:
                    service.users().messages().trash(userId="me", id=msg_id).execute(num_retries=5)
                else:
                    service.users().messages().modify(
                        userId="me",
                        id=msg_id,
                        body={
                            "addLabelIds": [label_ids[label_name]],
                            "removeLabelIds": ["INBOX"] if had_inbox else [],
                        },
                    ).execute(num_retries=5)
            except HttpError as exc:
                if exc.resp.status == 403:
                    print(REAUTH_HINT, file=sys.stderr)
                    break
                if exc.resp.status == 404:
                    outcomes[f"{row['verdict']}:missing"] += 1
                    continue  # message gone since the shadow run
                raise
            log.write(
                json.dumps(
                    {
                        "run_id": run_id,
                        "message_id": msg_id,
                        "sender_email": row["sender_email"],
                        "action": "trash" if trash else "label",
                        "label": label_name,
                        "had_inbox": had_inbox,
                    }
                )
                + "\n"
            )
            outcomes[f"{row['verdict']}:done"] += 1
            done += 1
            if done % 50 == 0:
                print(f"...{done} applied", file=sys.stderr)

    return (run_id if done else ""), outcomes


def _load_entries(run_id: str | None = None, sender: str | None = None) -> list[dict]:
    if not config.ACTIONS_LOG.exists():
        return []
    entries = [
        json.loads(line)
        for line in config.ACTIONS_LOG.read_text().splitlines()
        if line.strip()
    ]
    if run_id:
        entries = [e for e in entries if e["run_id"] == run_id]
    if sender:
        entries = [e for e in entries if e["sender_email"] == sender.lower()]
    return entries


def _reverse_entry(service, entry: dict, label_ids: dict[str, str]) -> bool:
    msg_id = entry["message_id"]
    try:
        if entry["action"] == "trash":
            service.users().messages().untrash(userId="me", id=msg_id).execute(num_retries=5)
        body = {
            "addLabelIds": ["INBOX"] if entry["had_inbox"] else [],
            "removeLabelIds": [label_ids[entry["label"]]]
            if entry["action"] == "label"
            else [],
        }
        if body["addLabelIds"] or body["removeLabelIds"]:
            service.users().messages().modify(userId="me", id=msg_id, body=body).execute(num_retries=5)
    except HttpError as exc:
        if exc.resp.status == 404:
            return False
        raise
    return True


def undo(service, run_id: str) -> int:
    """Reverse every action of a run: untrash, remove labels, restore INBOX.
    Mechanical reversal — teaches nothing (a bulk undo is not a keep signal)."""
    label_ids = ensure_labels(service)
    return sum(
        _reverse_entry(service, entry, label_ids) for entry in _load_entries(run_id)
    )


def _strip_quarantine_labels(service, sender: str, label_ids: dict[str, str]) -> int:
    """Remove Cleanup labels from the sender's mail wherever they came from
    (this tool, a Gmail filter, or a manual label)."""
    removed = 0
    for name in QUARANTINE_LABELS.values():
        label_id = label_ids[name]
        page_token = None
        while True:
            result = (
                service.users()
                .messages()
                .list(
                    userId="me",
                    labelIds=[label_id],
                    q=f"from:{sender}",
                    pageToken=page_token,
                )
                .execute(num_retries=5)
            )
            ids = [m["id"] for m in result.get("messages", [])]
            if ids:
                service.users().messages().batchModify(
                    userId="me", body={"ids": ids, "removeLabelIds": [label_id]}
                ).execute(num_retries=5)
                removed += len(ids)
            page_token = result.get("nextPageToken")
            if not page_token:
                break
    return removed


def rescue(service, sender: str, run_id: str | None = None, learn: bool = True) -> tuple[int, bool]:
    """Restore everything quarantined for one sender — the tool's own logged
    actions (untrash, INBOX restore) plus any Cleanup label on their mail,
    however it got there — and (by default) add them to the protected list:
    the brief's "one-tap restore + always keep sender".
    Returns (restored, newly_learned)."""
    entries = _load_entries(run_id, sender)
    label_ids = ensure_labels(service)
    restored = sum(_reverse_entry(service, entry, label_ids) for entry in entries)
    # Log reversal already stripped the tool's own labels, so the live sweep
    # only finds mail labeled by other means (manual, Gmail filter).
    restored += _strip_quarantine_labels(service, sender, label_ids)
    learned = bool(config.add_protected_senders({sender})) if learn else False
    return restored, learned
