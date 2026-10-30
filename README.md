# Mail Cleaner — shadow-mode spike

Gmail inbox triage: classifies mail as **advertising**, **expired notification** or **keep**, logs every decision with its reason, and **takes no action on the mailbox**. This is the shadow-mode spike from the [product brief](https://claude.ai/artifact/Qs33NN84AaSzXRaQCWEiQH) — its job is to produce a week of real precision data before any UX or write-scope is built.

## Safety properties

- **No send scope, no permanent deletion.** Phase 2 uses `gmail.modify`: labels and Gmail trash only. Trash is Gmail-reversible for 30 days and purged by the provider — the brief's retention window, enforced by Gmail itself.
- **Dry run by default.** `apply` only prints its per-sender plan; `--yes` executes, and every run is undoable (`mail-cleaner undo --run <id>`).
- **Protected beats predicted**: starred/important/sent mail, protected categories (receipts, invoices, security, travel, government — Swedish and English) and your allowlist are excluded before any classification runs.
- **Metadata first**: classification uses headers, labels and the Gmail snippet. Full bodies and attachments are never fetched, and nothing but the decision log is stored.
- **LLM is the last layer**: most mail is decided by header rules; only rule-ambiguous mail (expected: a small share) reaches the model, with addresses and digit runs masked first.
- **Unsure = inbox**: refusals, errors or low confidence all degrade to leaving mail alone.

## Setup

1. Python 3.11+ in a virtualenv (macOS has no bare `pip`; system python may be too old — `brew install python@3.12` if needed):

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate   # rerun in every new terminal
   pip install -e ".[dev]"
   ```
2. Google Cloud Console: create a project → enable **Gmail API** → OAuth consent screen in **Testing** mode (add your own address as test user) → create an **OAuth client ID (Desktop app)** → download as `credentials.json` into the repo root. Testing mode avoids the restricted-scope (CASA) review entirely for personal use.
3. For the LLM layer: `export ANTHROPIC_API_KEY=...` (see `.env.example`). Refusal fallback is enabled server-side so a safety decline degrades gracefully. Skip with `MAIL_CLEANER_NO_LLM=1` for a rules-only run.

## Run

```bash
mail-cleaner auth                 # one-time browser OAuth; verifies read access
mail-cleaner shadow --days 7      # classify last 7 days of inbox, append to data/shadow_log.jsonl
mail-cleaner report               # per-sender totals, verdict split, LLM share, unsure list
```

Scopes: `--scope inbox` (default), `--scope archive` (All Mail minus inbox — the backlog), `--scope all`. A backlog sweep:

```bash
caffeinate -i mail-cleaner shadow --scope archive --days 3650   # caffeinate keeps the Mac awake (macOS)
mail-cleaner report
```

Sweeps are interruptible: every decision is persisted as it happens, and rerunning the same command resumes — already-classified mail is skipped at list speed (no metadata fetch, no LLM; failed LLM calls are retried). `--fresh` clears the log and starts over. A connection lost mid-run (e.g. the machine slept) reconnects automatically; if it still fails, progress is saved and the printed message says to rerun.

Acting on verdicts (phase 2 — after reviewing the report; first re-auth for the modify scope with `rm token.json && mail-cleaner auth`):

```bash
mail-cleaner apply                      # dry run: per-sender plan, nothing happens
mail-cleaner apply --yes                # label as Cleanup/Ads / Cleanup/Expired
mail-cleaner apply --trash --yes        # or move to Gmail trash (30-day auto-purge)
mail-cleaner undo --run <id>            # reverse a run (id printed by apply)
mail-cleaner rescue --sender x@y.se     # restore that sender's mail + always keep them
mail-cleaner filters                    # dry run: stable ad senders worth a native Gmail filter
mail-cleaner filters --yes              # create the filters (label Cleanup/Ads + skip inbox)
```

Native filters run inside Gmail at delivery — zero API/LLM cost, works when the tool is off. Only consistently-ad senders qualify (≥ 3 ad verdicts, zero keep/protected); expiry can't be a filter since it's age-based. The `filters` command needs the `gmail.settings.basic` scope — re-consent with `rm token.json && mail-cleaner auth` after upgrading.

Filters are **label-only** (inbox-zero flow): the mail still lands in the inbox, pre-labeled. Read it, archive it as usual — then `mail-cleaner apply --labeled --yes` trashes everything that is Cleanup-labeled and no longer in the inbox. The label is the decision; your archiving is the trigger. No classification or LLM involved, and it's logged/undoable like any apply run.

Daily cleanup on a Mac — run the inbox sweep on a schedule (crontab -e):

```cron
0 7,19 * * * cd /path/to/Mail-cleaner && .venv/bin/mail-cleaner shadow --scope archive --days 7 && .venv/bin/mail-cleaner apply --trash --yes && .venv/bin/mail-cleaner apply --labeled --yes >> data/cron.log 2>&1
```

(Archive-scope for inbox-zero users: mail stays in the inbox until you archive it; the sweep then classifies and removes what you're done with, and `--labeled` picks up filter-labeled mail you've archived.)

Sender-level rules, one address or domain per line: always keep in `data/protected_senders.txt`; always quarantine in `data/quarantine_senders.txt` (protected wins on conflict). Override notification expiry in `data/notification_senders.txt` (`domain days` per line, `0` disables; defaults in `config.py`). Repeat sweeps skip already-quarantined mail.

## What to measure in the week

- **Precision proxy**: scan the report's would-quarantine senders — anything you'd want kept is a false positive. Target per brief: ≤ 0.1%.
- **LLM share and cost**: `report` shows how much mail reached the model (brief finding 12).
- **Threshold calibration**: label the `unsure` list; move thresholds in `config.py` from evidence, not guesses (brief finding 7).

## Out of spike scope (by design)

Quarantine moves, sender rules UI, digests, backlog sweeps, external-state expiry (PR closed, order delivered), Microsoft 365 / IMAP.
