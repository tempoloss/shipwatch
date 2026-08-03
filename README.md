# shipwatch

Makes the public side of shipping happen.

Not a task vault. Not reminders. Not a chatbot. One job: when something
changes, produce the follow-through that otherwise gets forgotten.

- **You tag a release** → an issue appears in that repo with the public-side
  checklist, and a Telegram message links to it.
- **ISO 20022 publishes a new message version** → an issue appears on
  `quackiso` saying which family moved and what to check.

Runs on GitHub Actions. No server, no hosting, no cost.

## Why an issue and not a todo list

An open issue next to the code is visibly unfinished. That is the entire
mechanism. Nothing has to be remembered, because every trigger is an event that
already happened.

## Setup

**1. Get the Telegram chat id.** Send any message to your bot first —
`getUpdates` returns nothing until you do, and the bot cannot message you until
you have messaged it.

```
https://api.telegram.org/bot<TOKEN>/getUpdates
```

Read `result[0].message.chat.id`.

**2. Create a fine-grained personal access token.** `GITHUB_TOKEN` is scoped to
the repository the workflow runs in, so it can read public repos but **cannot**
open an issue in `tempoloss/quackiso` from `tempoloss/shipwatch`. The PAT needs
**Issues: write** on every watched repo.

**3. Add three repository secrets** (Settings → Secrets and variables →
Actions):

| secret | value |
|---|---|
| `GH_PAT` | the fine-grained token from step 2 |
| `TELEGRAM_TOKEN` | your bot token from `@BotFather` |
| `TELEGRAM_CHAT_ID` | the id from step 1 |

Never commit the bot token. GitHub secret-scans public repositories, reports
leaked Telegram tokens upstream, and Telegram revokes them — so a committed
token results in a silently dead bot.

**4. Push, then run the workflow manually once** (Actions → watch → Run
workflow). The first run **seeds** state: it records every existing tag and
version *without* announcing them. Without this, the first scheduled run would
file an issue for every historical release at once.

**5. Confirm it is alive.** A `state:` commit should appear. That commit is both
the liveness proof and what keeps the schedule from being auto-disabled.

## Configuration

`config.json`:

- `repos` — watched for new tags
- `iso_families` — the 15 ISO 20022 families quackiso implements
- `iso_issue_repo` — where ISO issues are filed
- `intervals_hours` — `tags` 20h, `iso` 168h

One daily schedule drives everything; each watcher decides whether it is due,
so cadence lives in one reviewable file rather than several cron lines.

ISO is weekly on purpose. The source rate-limits — measured 2026-08-03, it
answered once in 1.7 s with 122 identifiers, then timed out on three
consecutive retries — and ISO publishes maintenance releases annually. Polling
6-hourly would be ~1,460 requests to catch one event.

## The rule this is built around

> **State is never written unless fetch and parse both succeeded.**

If a timeout or an error page produced an empty result and that were saved,
every family would be marked already-seen and the monitor would go permanently
silent while still reporting success. No error, no crash, no alert.

This is the same failure class as a `NULL` silently dropping out of a `SUM`:
wrong, quiet, and trusted.

Enforcement is structural, not conventional:

- watchers raise `ParseError` instead of returning empty
- the ISO watcher refuses a page with fewer than 50 identifiers
- the tags watcher refuses a response that is not a JSON array, so a rate-limit
  body cannot be recorded as "no tags"
- a version moving backwards keeps the higher recorded value
- each repo's state is independent, so one failing repo cannot reseed the others
- events are delivered **before** state is saved, because a duplicate is
  harmless and a lost notification is not

## Tests

```
python -m unittest discover -s tests
```

16 tests, no network. The watchers split into `fetch` (impure) and `parse`
(pure), so every parse path is testable against fixtures. Most tests defend the
rule above rather than the happy path.

## Layout

```
config.json                 watched repos, families, intervals
shipwatch/core.py           types, state, HTTP with backoff
shipwatch/watchers/tags.py  repo -> tag names
shipwatch/watchers/iso.py   -> latest version per family
shipwatch/sinks/issue.py    event -> GitHub issue (deduplicated by marker)
shipwatch/sinks/telegram.py event -> message
shipwatch/main.py           runner
state/*.json                last-seen, committed; the git diff is the audit trail
```

## Known limits

- The ISO watcher reads version identifiers out of page HTML. A redesign breaks
  it — loudly, by design, via the 50-identifier floor rather than by silently
  reporting no changes.
- "Highest version seen on the page" is a heuristic. It can be fooled by
  versions appearing in unrelated contexts such as changelogs or examples.
- Whether a bot-authored commit resets GitHub's ~60-day scheduled-workflow
  inactivity timer is **not verified**. If the schedule ever goes quiet, run it
  manually via `workflow_dispatch` and treat it as confirmed broken.
