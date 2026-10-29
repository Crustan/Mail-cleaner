"""Gmail access. OAuth installed-app flow for personal testing mode."""

from __future__ import annotations

import email.utils
import sys
import time

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from mail_cleaner import config
from mail_cleaner.models import EmailMeta

METADATA_HEADERS = [
    "From", "Subject", "List-Unsubscribe", "Precedence", "Sender",
    "Return-Path", "DKIM-Signature", "X-Campaign", "X-MC-User",
]


def get_service():
    """Authenticate (browser flow on first run) and return the Gmail service."""
    creds = None
    if config.TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(
            str(config.TOKEN_FILE), config.GMAIL_SCOPES
        )
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not config.CREDENTIALS_FILE.exists():
                raise SystemExit(
                    "credentials.json missing — create an OAuth client "
                    "(Desktop app) in Google Cloud Console and download it. See README."
                )
            flow = InstalledAppFlow.from_client_secrets_file(
                str(config.CREDENTIALS_FILE), config.GMAIL_SCOPES
            )
            creds = flow.run_local_server(port=0)
        config.TOKEN_FILE.write_text(creds.to_json())
    return build("gmail", "v1", credentials=creds)


# Gmail search per scope. Spam and trash are excluded everywhere: Gmail already
# handles spam, and acting on it would double-handle (seen leaking through in:all).
# Already-quarantined mail is excluded too, so repeat sweeps don't redo work.
_EXCLUDE = "-in:spam -in:trash -label:cleanup-ads -label:cleanup-expired"
SCOPE_QUERIES = {
    "inbox": f"in:inbox {_EXCLUDE}",
    "archive": f"in:all -in:inbox {_EXCLUDE}",
    "all": f"in:all {_EXCLUDE}",
}
EXCLUDED_LABELS = {"SPAM", "TRASH"}


# Errors the Google client doesn't recover from by itself — typically a stale
# connection after the machine slept. Rebuild the service and go on.
_CONNECTION_ERRORS = (ConnectionError, TimeoutError, OSError)


def iter_messages(
    service,
    days: int,
    scope: str = "inbox",
    limit: int | None = None,
    skip_ids: set[str] | None = None,
):
    """Yield EmailMeta for mail in `scope` from the last `days` days, metadata only.

    Ids in `skip_ids` are skipped before any metadata fetch, so resuming an
    interrupted sweep fast-forwards through classified mail at list speed.
    Transient failures retry with a rebuilt connection instead of crashing.
    """
    query = f"{SCOPE_QUERIES[scope]} newer_than:{days}d"
    skip_ids = skip_ids or set()
    page_token = None
    yielded = 0
    while True:
        result, service = _with_recovery(
            service,
            lambda svc: svc.users()
            .messages()
            .list(userId="me", q=query, pageToken=page_token, maxResults=100)
            .execute(num_retries=5),
        )
        for ref in result.get("messages", []):
            if ref["id"] in skip_ids:
                continue
            msg, service = _with_recovery(
                service,
                lambda svc: svc.users()
                .messages()
                .get(
                    userId="me",
                    id=ref["id"],
                    format="metadata",
                    metadataHeaders=METADATA_HEADERS,
                )
                .execute(num_retries=5),
            )
            meta = _to_meta(msg)
            if EXCLUDED_LABELS.intersection(meta.labels):
                continue
            yield meta
            yielded += 1
            if limit and yielded >= limit:
                return
        page_token = result.get("nextPageToken")
        if not page_token:
            return


def _with_recovery(service, call, attempts: int = 3):
    """Run call(service); on a connection-level error, rebuild the service and
    retry. Returns (result, service) so the caller keeps the fresh connection."""
    for attempt in range(attempts):
        try:
            return call(service), service
        except _CONNECTION_ERRORS as exc:
            if attempt == attempts - 1:
                raise
            print(
                f"Connection lost ({type(exc).__name__}); reconnecting...",
                file=sys.stderr,
            )
            time.sleep(5 * (attempt + 1))
            service = get_service()


def _to_meta(msg: dict) -> EmailMeta:
    headers = {
        h["name"].lower(): h["value"]
        for h in msg.get("payload", {}).get("headers", [])
    }
    sender = headers.get("from", "")
    _, addr = email.utils.parseaddr(sender)
    addr = addr.lower()
    domain = addr.rsplit("@", 1)[-1] if "@" in addr else ""
    return EmailMeta(
        message_id=msg["id"],
        thread_id=msg.get("threadId", ""),
        sender=sender,
        sender_email=addr,
        sender_domain=domain,
        subject=headers.get("subject", ""),
        date_epoch_ms=int(msg.get("internalDate", 0)),
        labels=msg.get("labelIds", []),
        headers=headers,
        snippet=msg.get("snippet", ""),
    )
