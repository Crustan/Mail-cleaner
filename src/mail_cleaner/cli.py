"""CLI: auth check, shadow run, report. No command writes to the mailbox."""

from __future__ import annotations

import argparse
import json
import sys

from mail_cleaner import config, gmail_client, pipeline, report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="mail-cleaner",
        description="Shadow-mode Gmail triage spike. Read-only; logs decisions, moves nothing.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("auth", help="Run the OAuth flow and verify read access")

    shadow = sub.add_parser("shadow", help="Classify recent mail, log verdicts")
    shadow.add_argument("--days", type=int, default=7, help="How far back to read (default 7)")
    shadow.add_argument("--limit", type=int, default=None, help="Max messages (for a quick test)")
    shadow.add_argument(
        "--scope",
        choices=("inbox", "archive", "all"),
        default="inbox",
        help="inbox (default), archive (All Mail minus inbox), or all",
    )

    sub.add_parser("report", help="Per-sender report from the shadow log")

    args = parser.parse_args(argv)

    if args.command == "auth":
        service = gmail_client.get_service()
        profile = service.users().getProfile(userId="me").execute()
        print(f"Authenticated as {profile['emailAddress']} (read-only scope)")
        return 0

    if args.command == "shadow":
        return _run_shadow(args.days, args.scope, args.limit)

    if args.command == "report":
        print(report.render(report.load_log()))
        return 0

    return 1


def _run_shadow(days: int, scope: str, limit: int | None) -> int:
    service = gmail_client.get_service()
    protected_senders = config.load_protected_senders()
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)

    count = 0
    with config.SHADOW_LOG.open("a") as log:
        for meta in gmail_client.iter_messages(service, days=days, scope=scope, limit=limit):
            decision = pipeline.decide(meta, protected_senders)
            log.write(json.dumps(decision.to_log(), ensure_ascii=False) + "\n")
            count += 1
            if count % 50 == 0:
                print(f"...{count} classified", file=sys.stderr)

    print(f"Shadow run done: {count} messages classified, 0 actions taken.")
    print(f"Log: {config.SHADOW_LOG}. Next: mail-cleaner report")
    return 0


if __name__ == "__main__":
    sys.exit(main())
