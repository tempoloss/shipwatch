# shipwatch

Makes the public side of shipping happen.

[Русская версия](README.ru.md)

Not a task vault. Not reminders. Not a chatbot. One job: when something
changes, produce the follow-through that otherwise gets forgotten.

- **You tag a release** → an issue appears in that repo with the public-side
  checklist, and a Telegram message links to it.
- **ISO 20022 publishes a new message version** → an issue appears on
  `quackiso` saying which message moved and what to check.

Runs on GitHub Actions. No server, no hosting, no cost.

## Why an issue and not a todo list

An open issue next to the code is visibly unfinished. That is the entire
mechanism. Nothing has to be remembered, because every trigger is an event that
already happened.

## Why these choices

| decision | why | detail |
|---|---|---|
| An open issue is the reminder | visibly unfinished, next to the code, and nothing has to be remembered | [ADR 0001](docs/adr/0001-an-open-issue-is-the-reminder.md) |
| A new tag and a new ISO version are one problem | both are "diff reality against what was last seen", so one runner and pluggable watchers | [ADR 0002](docs/adr/0002-one-mechanism-two-watchers.md) |
| GitHub Actions, not a server | no always-on hardware, and no credentials on a machine that is not yours | [ADR 0003](docs/adr/0003-github-actions-not-a-server.md) |
| State written only after every sink accepts | at-least-once beats silent loss; a duplicate is recoverable | [ADR 0004](docs/adr/0004-state-is-written-only-after-delivery.md) |
| First sight is adopted silently | otherwise run one files an issue per historical tag at once | [ADR 0005](docs/adr/0005-adopt-on-first-sight.md) |
| Two GitHub tokens | `GITHUB_TOKEN` cannot open an issue in another repository | [ADR 0006](docs/adr/0006-two-github-tokens.md) |
| The heartbeat records what happened | it cannot silence anything, and it keeps the schedule from being disabled | [ADR 0007](docs/adr/0007-the-heartbeat-records-what-happened.md) |
| An archived snapshot beats silence | a third-party capture can hold a version we have not seen; our own cache cannot | [ADR 0008](docs/adr/0008-an-archived-snapshot-beats-silence.md) |

## Setup

**1. Get the Telegram chat id.** Send any message to your bot *first* —
`getUpdates` returns nothing until you do, and a bot cannot message you until
you have messaged it.

```
https://api.telegram.org/bot<TOKEN>/getUpdates
```

Read `result[0].message.chat.id`.

**2. Create a fine-grained personal access token.** `GITHUB_TOKEN` is scoped to
the repository the workflow runs in, so it can read public repos but **cannot**
open an issue in `tempoloss/quackiso` from `tempoloss/shipwatch`. The PAT needs
**Issues: write** on every watched repo.

**3. Add repository secrets** (Settings → Secrets and variables → Actions):

| secret | value |
|---|---|
| `GH_PAT` | the fine-grained token from step 2 |
| `TELEGRAM_TOKEN` | your bot token from `@BotFather` |
| `TELEGRAM_CHAT_ID` | the id from step 1 |
| `HEALTHCHECK_URL` | *optional, recommended* — a free healthchecks.io ping URL |

`HEALTHCHECK_URL` makes liveness an event instead of an absence you have to
notice. Set its grace period to two intervals: GitHub scheduled runs are
best-effort and a single delayed run is routine. Without it, a silently disabled
schedule looks exactly like a quiet week — and a schedule disabled for
inactivity does **not** self-recover.

Never commit the bot token. GitHub secret-scans public repositories, reports
leaked Telegram tokens upstream, and Telegram revokes them — so a committed
token yields a silently dead bot.

**4. Push, then run the workflow manually once** (Actions → watch → Run
workflow). The first run **adopts**: it records every existing tag and version
*without* announcing them. Without this, run one would file an issue for every
historical release at once.

**5. Confirm it is alive.** A `state:` commit should appear. That commit is both
the liveness proof and what keeps the schedule from being auto-disabled.

## Configuration

`config.json`:

- `repos` — watched for new tags
- `iso_families` — the ISO 20022 messages quackiso implements
- `iso_issue_repo` — where ISO issues are filed
- `intervals_hours` — `tags` 20h, `iso` 168h

One daily schedule drives everything; each watcher decides whether it is due, so
cadence lives in one reviewable file rather than several cron lines.

ISO is weekly on purpose. The source does not merely rate-limit, it is
unreachable most of the time: measured 2026-08-03 it answered once in 1.7 s with
122 identifiers, then timed out on three consecutive retries. ISO publishes
maintenance releases annually, so polling 6-hourly would be ~1,460 requests to
catch one event.

Because the live page usually fails, the fetch is a waterfall: one attempt at
`iso20022.org`, then the Wayback Machine's most recent capture. A snapshot older
than 180 days is refused, and the digest prints `live` or `archive (2026-05-13)`
so a successful check is never mistaken for a live one.

## The rule this is built around

> **State is never written unless fetch and parse both succeeded, and never
> advanced for an event until every sink has accepted it.**

Both halves matter. If a timeout produced an empty result and that were saved,
every message would be marked already-seen and the monitor would go permanently
silent while still reporting success. And if state were committed before a sink
failed, the event would never be re-emitted — while the state file asserted it
had been.

This is the same failure class as a `NULL` silently dropping out of a `SUM`:
wrong, quiet, and trusted.

Delivery is therefore **at-least-once**. A duplicate is recoverable; silent loss
is not. Each event carries a deterministic key and the issue sink checks for it
before filing.

Enforcement is structural, not conventional:

- watchers raise `ParseError` instead of returning empty
- **writes are a monotone merge** — state only ever gains a tag or raises a
  version, so a partially rendered page is a no-op rather than a key deletion
  followed by silent re-adoption
- the ISO watcher rejects a page with under 100 identifiers or under 3 business
  areas, which catches a long error page as well as a thin one
- state is keyed by `area.message`, never by business area alone — `camt.003`
  and `camt.053` version independently
- the version group is `\d{2,}`: a fixed `\d{2}` would make a v100 message
  vanish from the parse and freeze it silently forever
- the boundary is per target, so one failing repo cannot reseed its siblings
- a version moving backwards keeps the higher recorded value
- an undelivered event blocks the state write, so it retries

The heartbeat is the one deliberate exception: it records what *happened*, never
what was *seen*, so it cannot silence anything, and it is committed with
`if: always()` so a crash still produces it.

## Tests

```
python -m unittest discover -s tests
```

38 tests, no network. The watchers split into `fetch` (impure) and `parse`
(pure), so every parse path is testable. Most tests defend the rule above rather
than the happy path. The runner ones exist because a missing secret once aborted
`main` before the heartbeat was written, and the `always()` commit step then died
on an absent `state/`: the mechanism written to survive a crash was removed by
the crash. The fetch ones pin both sides of the snapshot-age ceiling, which is
the only thing separating a useful archive from a stale one.

## Layout

```
config.json                 watched repos, messages, intervals
shipwatch/core.py           types, state, HTTP with optional retry
shipwatch/watchers/tags.py  repo -> tag names
shipwatch/watchers/iso.py   -> latest version per message
shipwatch/sinks/issue.py    event -> GitHub issue, deduplicated by marker
shipwatch/sinks/telegram.py event -> message
shipwatch/main.py           runner
tests/test_watchers.py      parse paths, per watcher
tests/test_iso_fetch.py     the live-then-archive waterfall
tests/test_runner.py        the heartbeat survives a misconfigured run
state/*.json                last-seen, committed; the git diff is the audit trail
docs/adr/                   one decision per file, and why
docs/design.md              the whole design in one document
```

## Known limits

- Version identifiers are read from page HTML. A redesign breaks it — loudly,
  via the plausibility floor, rather than by silently reporting no changes.
- "Highest version seen on the page" is a heuristic, and can be fooled by
  versions appearing in changelogs or examples.
- Business-area prefixes are a fixed list in the regex. A message outside it is
  unwatched; the weekly digest reports how many configured messages were found,
  so the gap is visible rather than silent.
- The archived fallback only refreshes if something archives the page. If nothing
  does and the live source stays down, the capture ages until the 180-day ceiling
  refuses it, and ISO then fails loudly with no path back except the live source
  recovering.
