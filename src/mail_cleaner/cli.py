"""Mail Cleaner CLI. Bare `mail-cleaner` shows status and the suggested next step."""

from __future__ import annotations

import json
import sys
from collections import Counter

import typer
from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.table import Table

from mail_cleaner import __version__, actions, config, gmail_client, pipeline
from mail_cleaner import filters as filters_mod
from mail_cleaner import report as report_mod
from mail_cleaner import triage as triage_mod

app = typer.Typer(
    help="AI email triage for Gmail: classify ads and expired notifications, "
    "quarantine reviewed mail, teach it per sender. Run without a command for status.",
    invoke_without_command=True,
    no_args_is_help=False,
    context_settings={"help_option_names": ["-h", "--help"]},
)
console = Console()
err = Console(stderr=True)

SCOPES = ("inbox", "archive", "all")


@app.callback()
def _root(
    ctx: typer.Context,
    version: bool = typer.Option(False, "--version", help="Show version and exit."),
):
    if version:
        console.print(f"mail-cleaner {__version__}")
        raise typer.Exit()
    if ctx.invoked_subcommand is None:
        _status()


def _status() -> None:
    rows = report_mod.load_log()
    verdicts = Counter(row["verdict"] for row in rows)
    actioned = actions.actioned_ids()
    pending = len(actions.plan(rows, actions.ACTIONABLE_VERDICTS, None, exclude=actioned))
    triage_queue = len(triage_mod.keep_senders(rows)) if rows else 0

    token_state = "[red]missing[/red] — run mail-cleaner auth"
    if config.TOKEN_FILE.exists():
        try:
            granted = json.loads(config.TOKEN_FILE.read_text()).get("scopes", [])
            short = ", ".join(s.rsplit("/", 1)[-1] for s in granted)
            token_state = short if set(config.GMAIL_SCOPES).issubset(granted) else (
                f"{short} [yellow](missing a configured scope — rm token.json && mail-cleaner auth)[/yellow]"
            )
        except (ValueError, OSError):
            token_state = "[red]unreadable[/red] — rm token.json && mail-cleaner auth"

    table = Table(title="Mail Cleaner", show_header=False, title_justify="left")
    table.add_column(style="bold")
    table.add_column()
    table.add_row("Auth", token_state)
    table.add_row(
        "Classified",
        f"{len(rows)} (ad {verdicts['ad']}, expired {verdicts['expired']}, "
        f"keep {verdicts['keep']}, protected {verdicts['protected']}, unsure {verdicts['unsure']})"
        if rows
        else "none yet",
    )
    table.add_row("Pending actions", str(pending))
    table.add_row("Actions done", str(len(actioned)))
    table.add_row(
        "Sender rules",
        f"protected {len(config.load_protected_senders())}, "
        f"quarantine {len(config.load_quarantine_senders())}, "
        f"notification {len(config.load_notification_senders())}",
    )
    table.add_row(
        "LLM", "disabled" if config.LLM_DISABLED else config.LLM_MODEL
    )
    console.print(table)

    if not config.TOKEN_FILE.exists():
        next_cmd = "mail-cleaner auth"
    elif not rows:
        next_cmd = "mail-cleaner scan --scope archive --days 7"
    elif pending:
        next_cmd = "mail-cleaner apply"
    elif triage_queue:
        next_cmd = f"mail-cleaner triage   ({triage_queue} senders waiting)"
    else:
        next_cmd = "mail-cleaner scan --scope archive --days 7"
    console.print(f"\nNext: [bold cyan]{next_cmd}[/bold cyan]")


@app.command()
def auth() -> None:
    """Run the OAuth flow and show the granted scopes."""
    service = gmail_client.get_service()
    profile = service.users().getProfile(userId="me").execute()
    granted = json.loads(config.TOKEN_FILE.read_text()).get("scopes", [])
    scopes = ", ".join(s.rsplit("/", 1)[-1] for s in granted) or "unknown"
    console.print(f"Authenticated as [bold]{profile['emailAddress']}[/bold] (scopes: {scopes})")
    if not set(config.GMAIL_SCOPES).issubset(granted):
        console.print(
            "[yellow]Token is missing a configured scope — run: rm token.json && mail-cleaner auth[/yellow]"
        )


def _scan(
    days: int = typer.Option(7, help="How far back to read."),
    scope: str = typer.Option("inbox", help="inbox, archive (All Mail minus inbox), or all."),
    limit: int | None = typer.Option(None, help="Max messages (for a quick test)."),
    fresh: bool = typer.Option(False, "--fresh", help="Clear the log instead of resuming."),
) -> None:
    """Classify mail and log verdicts — reads only, moves nothing. Resumes by default."""
    if scope not in SCOPES:
        err.print(f"[red]--scope must be one of {', '.join(SCOPES)}[/red]")
        raise typer.Exit(2)
    service = gmail_client.get_service()
    protected_senders = config.load_protected_senders()
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)

    if fresh:
        config.SHADOW_LOG.unlink(missing_ok=True)
        skip_ids: set[str] = set()
    else:
        skip_ids = report_mod.resume_ids(report_mod.load_log())
        if skip_ids:
            err.print(f"Resuming: {len(skip_ids)} already classified, skipping.")

    count = 0
    progress = Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        TimeElapsedColumn(),
        console=err,
    )
    try:
        with config.SHADOW_LOG.open("a") as log, progress:
            task = progress.add_task("scanning", total=None)
            for meta in gmail_client.iter_messages(
                service, days=days, scope=scope, limit=limit, skip_ids=skip_ids
            ):
                decision = pipeline.decide(meta, protected_senders)
                log.write(json.dumps(decision.to_log(), ensure_ascii=False) + "\n")
                count += 1
                progress.update(task, description=f"scanning — {count} classified")
    except KeyboardInterrupt:
        console.print(f"\nInterrupted after {count} messages — progress saved; rerun to resume.")
        raise typer.Exit(130)
    except Exception as exc:  # noqa: BLE001 — progress is safe, say so instead of a traceback
        console.print(
            f"\n[red]Failed after {count} messages ({type(exc).__name__}: {exc})[/red] — "
            "progress saved; rerun the same command to resume."
        )
        raise typer.Exit(1)

    console.print(f"Scan done: {count} messages classified, 0 actions taken.")
    console.print(f"Log: {config.SHADOW_LOG}. Next: [bold cyan]mail-cleaner report[/bold cyan]")


app.command("scan")(_scan)
app.command("shadow", hidden=True)(_scan)  # legacy name


@app.command()
def report() -> None:
    """Verdict totals, per-sender quarantine counts, triage candidates."""
    console.print(report_mod.render(report_mod.load_log()), highlight=False)


@app.command("apply")
def apply_cmd(
    yes: bool = typer.Option(False, "--yes", help="Execute (default: dry run)."),
    trash: bool = typer.Option(
        False, "--trash", help="Move to Gmail trash (auto-purged after 30 days) instead of labeling."
    ),
    labeled: bool = typer.Option(
        False,
        "--labeled",
        help="Instead of shadow verdicts: trash Cleanup-labeled mail you have archived out of the inbox.",
    ),
    redo: bool = typer.Option(
        False, "--redo", help="Include messages earlier runs already handled (e.g. after an undo)."
    ),
    max_actions: int | None = typer.Option(None, "--max", help="Cap actions this run."),
    verdict: list[str] = typer.Option(
        None, "--verdict", help="Verdicts to act on: ad, expired (default both)."
    ),
) -> None:
    """Act on classified mail. Dry run unless --yes; every run is undoable."""
    if labeled:
        # No actioned-id exclusion: label-now-trash-later is this path's point,
        # and its query already excludes trashed mail.
        exclude: set[str] = set()
        service = gmail_client.get_service()
        rows = actions.labeled_archived(service)
        trash = True  # labeled + archived = read and done; removal is the point
    else:
        exclude = set() if redo else actions.actioned_ids()
        service = None
        rows = report_mod.load_log()
    verdicts = set(verdict) if (verdict and not labeled) else actions.ACTIONABLE_VERDICTS
    if not verdicts <= actions.ACTIONABLE_VERDICTS:
        err.print(f"[red]--verdict must be among: {', '.join(sorted(actions.ACTIONABLE_VERDICTS))}[/red]")
        raise typer.Exit(2)

    planned = actions.plan(rows, verdicts, max_actions, exclude=exclude)
    skipped = len(actions.plan(rows, verdicts, None)) - len(
        actions.plan(rows, verdicts, None, exclude=exclude)
    )
    if skipped:
        console.print(f"Skipping {skipped} messages already handled by earlier runs (--redo includes them).")
    if not planned:
        console.print("Nothing new to act on." + ("" if labeled else " Run a scan first."))
        raise typer.Exit()
    console.print(actions.describe_plan(planned, trash), highlight=False)
    if not yes:
        console.print("\nDry run — rerun with [bold]--yes[/bold] to execute.")
        raise typer.Exit()

    progress = Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeRemainingColumn(),
        console=err,
    )
    with progress:
        task = progress.add_task("applying", total=len(planned))
        run_id, outcomes = actions.apply(
            service or gmail_client.get_service(),
            planned,
            trash,
            on_progress=lambda n: progress.update(task, completed=n),
        )
    for key in sorted(outcomes):
        console.print(f"  {key}: {outcomes[key]}")
    if run_id:
        console.print(f"Applied. Undo with: [bold cyan]mail-cleaner undo --run {run_id}[/bold cyan]")


@app.command()
def undo(run: str = typer.Option(..., "--run", help="Run id printed by apply.")) -> None:
    """Reverse an apply run (restore-to-origin: archive mail stays archived)."""
    undone = actions.undo(gmail_client.get_service(), run)
    console.print(f"Undid {undone} actions from run {run}.")


@app.command()
def rescue(
    sender: str = typer.Option(..., "--sender", help="Sender email to restore."),
    run: str | None = typer.Option(None, "--run", help="Limit to one run (default: all)."),
    no_learn: bool = typer.Option(
        False, "--no-learn", help="Restore only; don't add to the protected list."
    ),
) -> None:
    """Restore one sender's quarantined mail and always keep them."""
    restored, learned = actions.rescue(gmail_client.get_service(), sender, run, learn=not no_learn)
    console.print(f"Restored {restored} messages from {sender}.")
    if learned:
        console.print(f"[green]{sender} added to the protected list — never touched again.[/green]")
    elif not no_learn:
        console.print(f"{sender} was already on the protected list.")


@app.command()
def filters(
    min_ads: int | None = typer.Option(
        None, "--min", help=f"Min ad verdicts per sender (default {config.FILTER_MIN_ADS})."
    ),
    yes: bool = typer.Option(False, "--yes", help="Create the filters (default: dry run)."),
) -> None:
    """Create native Gmail filters (label-only) for consistently-ad senders."""
    cands = filters_mod.candidates(report_mod.load_log(), min_ads)
    if not cands:
        console.print("No stable ad senders yet. Run more scans first.")
        raise typer.Exit()
    service = gmail_client.get_service()
    already = filters_mod.existing_filter_senders(service)
    new = [(s, n) for s, n in cands if s not in already]
    console.print(f"Filter candidates ({len(new)} new, {len(cands) - len(new)} already filtered):")
    for sender, n in new:
        console.print(f"  {n:>4}  {sender}")
    if not new:
        raise typer.Exit()
    if not yes:
        console.print(
            "\nDry run — rerun with [bold]--yes[/bold] to create Gmail filters "
            "(label Cleanup/Ads; mail stays in the inbox)."
        )
        raise typer.Exit()
    created = filters_mod.create_filters(service, [s for s, _ in new])
    console.print(f"Created {created} Gmail filters. Manage them under Gmail Settings → Filters.")


@app.command()
def triage(
    min_count: int = typer.Option(
        3, "--min", help="Only senders with at least this many keeps."
    ),
    no_llm: bool = typer.Option(False, "--no-llm", help="Skip LLM suggestions."),
) -> None:
    """Interactive sender triage of keep mail — one keypress per sender."""
    counts = triage_mod.run_interactive(
        report_mod.load_log(), min_count=min_count, use_llm=not no_llm
    )
    if counts:
        console.print(
            f"\nTriaged — ad: {counts['ad']}, notification: {counts['notification']}, "
            f"protected: {counts['protect']}, skipped: {counts['skip']}."
        )
        console.print(
            "Next: [bold cyan]mail-cleaner scan --scope archive --days 5000 "
            "&& mail-cleaner apply --trash --yes[/bold cyan]"
        )


@app.command("config")
def config_cmd() -> None:
    """Show effective configuration: paths, model, thresholds, rule counts."""
    table = Table(show_header=False)
    table.add_column(style="bold")
    table.add_column()
    table.add_row("Data dir", str(config.DATA_DIR.resolve()))
    table.add_row("Shadow log", str(config.SHADOW_LOG))
    table.add_row("Actions log", str(config.ACTIONS_LOG))
    table.add_row("Scopes", ", ".join(s.rsplit("/", 1)[-1] for s in config.GMAIL_SCOPES))
    table.add_row("LLM model", "disabled" if config.LLM_DISABLED else config.LLM_MODEL)
    table.add_row(
        "Confidence floors",
        f"ad {config.CONFIDENCE_FLOOR['ad']}, expired {config.CONFIDENCE_FLOOR['expired']}, "
        f"LLM ad accept {config.LLM_AD_ACCEPT}",
    )
    table.add_row("Filter min ads", str(config.FILTER_MIN_ADS))
    table.add_row(
        "Sender rules",
        f"protected {len(config.load_protected_senders())}, "
        f"quarantine {len(config.load_quarantine_senders())}, "
        f"notification {len(config.load_notification_senders())}",
    )
    console.print(table)


def main() -> int:
    app()
    return 0


if __name__ == "__main__":
    sys.exit(main())
