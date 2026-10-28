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
        for label in service.users().labels().list(userId="me").execute().get("labels", [])
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
                .execute()
            )
            ids[name] = created["id"]
    return ids


def plan(rows: list[dict], verdicts: set[str], max_actions: int | None) -> list[dict]:
    actionable = [row for row in rows if row["verdict"] in verdicts]
    return actionable[:max_actions] if max_actions else actionable


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


def apply(service, planned: list[dict], trash: bool) -> str:
    """Execute the plan. Returns the run id for undo."""
    label_ids = ensure_labels(service)
    run_id = uuid.uuid4().hex[:8]
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)

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
                    .execute()
                )
                had_inbox = "INBOX" in before.get("labelIds", [])
                if trash:
                    service.users().messages().trash(userId="me", id=msg_id).execute()
                else:
                    service.users().messages().modify(
                        userId="me",
                        id=msg_id,
                        body={
                            "addLabelIds": [label_ids[label_name]],
                            "removeLabelIds": ["INBOX"] if had_inbox else [],
                        },
                    ).execute()
            except HttpError as exc:
                if exc.resp.status == 403:
                    print(REAUTH_HINT, file=sys.stderr)
                    break
                if exc.resp.status == 404:
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
            done += 1
            if done % 50 == 0:
                print(f"...{done} applied", file=sys.stderr)

    return run_id if done else ""


def undo(service, run_id: str) -> int:
    """Reverse every action of a run: untrash, remove labels, restore INBOX."""
    if not config.ACTIONS_LOG.exists():
        return 0
    entries = [
        json.loads(line)
        for line in config.ACTIONS_LOG.read_text().splitlines()
        if line.strip() and json.loads(line)["run_id"] == run_id
    ]
    label_ids = ensure_labels(service)

    undone = 0
    for entry in entries:
        msg_id = entry["message_id"]
        try:
            if entry["action"] == "trash":
                service.users().messages().untrash(userId="me", id=msg_id).execute()
            body = {
                "addLabelIds": ["INBOX"] if entry["had_inbox"] else [],
                "removeLabelIds": [label_ids[entry["label"]]]
                if entry["action"] == "label"
                else [],
            }
            if body["addLabelIds"] or body["removeLabelIds"]:
                service.users().messages().modify(
                    userId="me", id=msg_id, body=body
                ).execute()
        except HttpError as exc:
            if exc.resp.status == 404:
                continue
            raise
        undone += 1
    return undone
