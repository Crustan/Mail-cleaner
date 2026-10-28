"""CLI: auth check, shadow run, report. No command writes to the mailbox."""

from __future__ import annotations

import argparse
import json
import sys

from mail_cleaner import actions, config, filters, gmail_client, pipeline, report


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

    apply_cmd = sub.add_parser(
        "apply", help="Act on shadow verdicts (dry run unless --yes)"
    )
    apply_cmd.add_argument(
        "--verdict",
        action="append",
        choices=sorted(actions.ACTIONABLE_VERDICTS),
        help="Verdicts to act on (default: ad and expired)",
    )
    apply_cmd.add_argument(
        "--trash",
        action="store_true",
        help="Move to Gmail trash (auto-purged by Gmail after 30 days) instead of labeling",
    )
    apply_cmd.add_argument("--max", type=int, default=None, help="Cap actions this run")
    apply_cmd.add_argument("--yes", action="store_true", help="Execute (default: dry run)")

    undo_cmd = sub.add_parser("undo", help="Reverse an apply run")
    undo_cmd.add_argument("--run", required=True, help="Run id printed by apply")

    rescue_cmd = sub.add_parser(
        "rescue", help="Restore one sender's actioned mail and always keep them"
    )
    rescue_cmd.add_argument("--sender", required=True, help="Sender email to restore")
    rescue_cmd.add_argument("--run", default=None, help="Limit to one run (default: all)")
    rescue_cmd.add_argument(
        "--no-learn", action="store_true", help="Restore only; don't add to protected list"
    )

    filters_cmd = sub.add_parser(
        "filters", help="Create native Gmail filters for stable ad senders (dry run unless --yes)"
    )
    filters_cmd.add_argument(
        "--min", type=int, default=None, help=f"Min ad verdicts per sender (default {config.FILTER_MIN_ADS})"
    )
    filters_cmd.add_argument("--yes", action="store_true", help="Create the filters (default: dry run)")

    args = parser.parse_args(argv)

    if args.command == "auth":
        service = gmail_client.get_service()
        profile = service.users().getProfile(userId="me").execute()
        granted = json.loads(config.TOKEN_FILE.read_text()).get("scopes", [])
        scopes = ", ".join(s.rsplit("/", 1)[-1] for s in granted) or "unknown"
        print(f"Authenticated as {profile['emailAddress']} (scopes: {scopes})")
        if not set(config.GMAIL_SCOPES).issubset(granted):
            print(
                "Token is missing a configured scope — run: rm token.json && mail-cleaner auth"
            )
        return 0

    if args.command == "shadow":
        return _run_shadow(args.days, args.scope, args.limit)

    if args.command == "report":
        print(report.render(report.load_log()))
        return 0

    if args.command == "apply":
        verdicts = set(args.verdict) if args.verdict else actions.ACTIONABLE_VERDICTS
        planned = actions.plan(report.load_log(), verdicts, args.max)
        if not planned:
            print("Nothing to act on. Run a shadow sweep first.")
            return 0
        print(actions.describe_plan(planned, args.trash))
        if not args.yes:
            print("\nDry run — rerun with --yes to execute.")
            return 0
        run_id = actions.apply(gmail_client.get_service(), planned, args.trash)
        if run_id:
            print(f"Applied. Undo with: mail-cleaner undo --run {run_id}")
        return 0

    if args.command == "undo":
        undone = actions.undo(gmail_client.get_service(), args.run)
        print(f"Undid {undone} actions from run {args.run}.")
        return 0

    if args.command == "filters":
        cands = filters.candidates(report.load_log(), args.min)
        if not cands:
            print("No stable ad senders yet. Run more shadow sweeps first.")
            return 0
        service = gmail_client.get_service()
        already = filters.existing_filter_senders(service)
        new = [(s, n) for s, n in cands if s not in already]
        skipped = len(cands) - len(new)
        print(f"Filter candidates ({len(new)} new, {skipped} already filtered):")
        for sender, n in new:
            print(f"  {n:>4}  {sender}")
        if not new:
            return 0
        if not args.yes:
            print("\nDry run — rerun with --yes to create Gmail filters (label Cleanup/Ads, skip inbox).")
            return 0
        created = filters.create_filters(service, [s for s, _ in new])
        print(f"Created {created} Gmail filters. Manage them under Gmail Settings → Filters.")
        return 0

    if args.command == "rescue":
        restored, learned = actions.rescue(
            gmail_client.get_service(), args.sender, args.run, learn=not args.no_learn
        )
        print(f"Restored {restored} messages from {args.sender}.")
        if learned:
            print(f"{args.sender} added to the protected list — never touched again.")
        elif not args.no_learn:
            print(f"{args.sender} was already on the protected list.")
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
