# shipwatch — design

**Date:** 2026-08-03
**Status:** approved for implementation
**Owner:** tempoloss

## Problem

Public-facing follow-through does not happen. Work ships and nobody hears about
it: `quackiso` was merged into `duckdb/community-extensions` on 2026-08-01 and
has 1 star, no release notes, no post. The failure is not motivation and not
task capture — it is that the obligation appears at a moment when attention is
elsewhere, and nothing reminds anyone it exists.

A second, related cost: `quackiso` parses 15 ISO 20022 message families. ISO
publishes maintenance releases annually and message versions increment. Noticing
that by hand is real toil, and missing it means the extension silently falls
behind the standard it claims to implement.

## Non-goals

Explicitly out of scope, and they are the majority of what a "personal
assistant bot" would normally mean:

- **Not a task vault.** Tasks are captured elsewhere. Adding a second place to
  write tasks solves nothing.
- **Not a reminder system.** No cron-driven nagging, no time-based todos.
- **Not a chatbot.** No command surface, no conversation, no NLU.

The design rule this follows: *remove decisions, do not add discipline.* A tool
whose upkeep depends on the discipline it substitutes for is circular. Every
trigger here is an event that already happened, so nothing has to be remembered.

## Core insight

The two event sources look different but are the same problem:

> diff current reality against what was last seen

A new git tag and a new ISO 20022 message version are structurally identical.
So there is one mechanism with pluggable watchers, not two systems.

## Architecture

Single GitHub Actions repository. No server, no hosting, no cost — forced by
circumstance rather than chosen: no always-on personal hardware is available,
and the machine that is always on is not one where credentials belong.

```
schedule ──► runner ──► watchers ──► events ──► sinks
                          │                      ├── GitHub issue
                          │                      └── Telegram message
                          └── state (committed JSON)
```

Polling, not `repository_dispatch`. Watching N repos therefore needs no workflow
and no PAT installed in any of them; everything lives in one place. Latency is
hours, which is correct — release follow-through was never seconds-sensitive.

### Components

| unit | responsibility | depends on |
|---|---|---|
| `watchers/tags.py` | repo → current tag names | GitHub API |
| `watchers/iso.py` | → latest version per message family | iso20022.org |
| `sinks/issue.py` | event → GitHub issue | `GITHUB_TOKEN` |
| `sinks/telegram.py` | event → chat message | bot token |
| `state.py` | load/save last-seen JSON | filesystem + git |
| `main.py` | wire the above, decide what to commit | — |

### The watcher contract

Every watcher splits into a network half and a pure half:

```python
fetch(cfg) -> str                      # impure, may raise
parse(text, prior) -> (events, state)  # pure, total, never raises on valid input
```

`parse` is a pure function, so it is unit-testable against recorded fixtures
with no network. That is the entire testing strategy: one fixture per watcher
captured from a real response, asserting both the no-change and the
change-detected paths.

### State

Committed JSON in the repository. No database.

- `state/tags.json` — `{repo: [tag, ...]}`
- `state/iso.json` — `{family: version}` e.g. `{"camt.053": 8}`
- `state/heartbeat.json` — last run timestamp and per-watcher outcome counts

The git diff is the audit trail: reviewable, revertable, and free. A wrong state
transition can be inspected and reverted like any other commit.

## The critical correctness rule

> **Never write state unless fetch AND parse both succeeded.**

This is load-bearing rather than hygienic, because the ISO source is known to be
unreliable (see Evidence). If a timeout yields an empty parse and that empty
result is saved, every family is marked as already-seen and the monitor goes
permanently silent while continuing to report success. No error, no crash,
no alert — it simply never notifies again.

This is the same failure class as a `NULL` silently dropping out of a `SUM`:
wrong, quiet, and trusted. It is the exact bug `quackiso` was designed to
prevent in amounts, applied one layer up.

Enforcement is structural, not conventional: a watcher run returns either a
result or an error, and state is written only on the result branch. Each watcher
commits its own state independently, so one failing watcher cannot block or
corrupt the other.

## Liveness

Every run commits `state/heartbeat.json`. This does two jobs:

1. **Proves the system is alive.** A stalled monitor shows as a stale commit,
   and a weekly digest means the *absence* of the digest is itself the alarm.
2. **Keeps the schedule enabled.** GitHub disables scheduled workflows on public
   repositories after roughly 60 days of repository inactivity. The heartbeat
   commit is activity, so it resets that clock.

Because the ISO source is flaky, "no alerts" is otherwise ambiguous — nothing
changed, or nothing worked? The digest therefore reports attempt outcomes, not
only changes: *"7 checks, 5 ok, 2 timed out, no version changes."*

## Evidence gathered before implementation

Measured on 2026-08-03:

- `https://www.iso20022.org/iso-20022-message-definitions` returned HTTP 200 in
  1.7 s, 1,490,514 bytes, containing 122 distinct message-definition
  identifiers with versions (e.g. `camt.003.001.08`).
- Family and version parse cleanly from the identifier with
  `\b(pacs|pain|camt)\.\d{3}\.\d{3}\.\d{2}\b`, taking the maximum version per
  family.
- **After four requests the host stopped responding: three consecutive
  retries timed out at 25 s each.** The source rate-limits.

Consequences, all folded into this design:

- ISO poll frequency is **weekly**, not the 6 hours originally proposed. ISO
  publishes annually; 6-hourly polling is roughly 1,460 requests to catch one
  event, and it is what triggered the rate limiting.
- Exponential backoff, and a cached last-good response.
- Multi-week ISO outages must not raise alarms; only a stalled *runner* should.
- The tags watcher is independent and unaffected, so the release-checklist half
  — the part that addresses the stated problem — works regardless of ISO
  behaviour.

## Release checklist emitted on a new tag

```
- [ ] GitHub Release created with notes
- [ ] README / docs reflect the new version
- [ ] blog post
- [ ] Telegram / X announcement
- [ ] quackiso only: bump version + ref in the community-extensions fork, open PR
```

The last item is a real recurring obligation that is otherwise dropped every
release, because it lives in a different repository from the work.

## Language

Python, standard library only. No `pip install`, so no dependency drift and no
lockfile in a scheduled job that must still run untouched in six months.
`urllib.request` and `json` are sufficient. Chosen for speed of delivery rather
than education — the learning vehicle is the moxy concept audit, and this must
not become a second curriculum.

## Security

The Telegram bot token lives in GitHub Actions secrets and is referenced as
`${{ secrets.TELEGRAM_TOKEN }}`. It is never committed. This is not a security
posture argument: GitHub secret-scans public repositories, reports leaked
Telegram tokens upstream, and Telegram revokes them — so a committed token
results in a silently dead bot, which is a reliability problem.

## Rejected alternatives

**Self-hosted daemon in Go, using moxy as the queue.** The most educational
option, and it would force the missing concurrency primitives to be learned by
necessity. Rejected on hardware: no always-on machine is available, and the
employer workstation is unsuitable. Correct answer, wrong month. The ISO
watcher logic ports unchanged if this is revisited.

**Cloudflare Worker with KV.** Free and always-on with real cron, but a new
platform plus a second state store for no benefit over Actions.

**`repository_dispatch` from each watched repo.** Lower latency, but requires a
workflow and a token in every watched repository. Rejected: latency does not
matter here and the operational cost is per-repo forever.
