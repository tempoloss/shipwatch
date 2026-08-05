# shipwatch — design

**Date:** 2026-08-03
**Status:** implemented; revised after independent review; one decision superseded
**Owner:** tempoloss

This is the whole design in one document, written before implementation. The
decisions were later filed individually under [`adr/`](adr/), which is where a
reversal is recorded: this document is kept as written, with supersessions marked
in place rather than edited away, because the reasoning that turned out wrong is
the useful part.

## Problem

Public-facing follow-through does not happen. Work ships and nobody hears about
it: `quackiso` was merged into `duckdb/community-extensions` on 2026-08-01 and
has 1 star, no release notes, no post. The failure is not motivation and not
task capture — the obligation appears when attention is elsewhere, and nothing
reminds anyone it exists.

A second cost: `quackiso` parses 15 ISO 20022 messages. ISO publishes
maintenance releases annually and versions increment. Noticing that by hand is
real toil, and missing it means the extension silently falls behind the standard
it claims to implement.

## Non-goals

- **Not a task vault.** A second place to write tasks solves nothing.
- **Not a reminder system.** No cron-driven nagging, no time-based todos.
- **Not a chatbot.** No command surface, no conversation.

The rule this follows: *remove decisions, do not add discipline.* A tool whose
upkeep depends on the discipline it substitutes for is circular. Every trigger
is an event that already happened, so nothing has to be remembered.

## Core insight

A new git tag and a new ISO 20022 message version are the same problem:

> diff current reality against what was last seen

One mechanism, pluggable watchers. Not two systems.

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

Polling, not `repository_dispatch`: watching N repos then needs no workflow and
no token installed in any of them. Latency is hours, which is correct.

Runs are serialised with `concurrency: {group: shipwatch-watch,
cancel-in-progress: false}` — queued rather than cancelled, because cancelling
mid-run could stop the process after delivery but before state is saved.

### Components

| unit | responsibility | depends on |
|---|---|---|
| `watchers/tags.py` | repo → tag names | GitHub API |
| `watchers/iso.py` | → latest version per message | iso20022.org |
| `sinks/issue.py` | event → GitHub issue, deduplicated | `GH_PAT` |
| `sinks/telegram.py` | event → message | bot token |
| `core.py` | types, state, HTTP with optional retry | — |
| `main.py` | wire the above, decide what to commit | — |

### The watcher contract

```python
fetch(cfg) -> str                      # impure, may raise
parse(text, prior) -> (events, state)  # pure; raises ParseError when the
                                       # response does not look like the page
```

`parse` is pure, so every path is testable against fixtures with no network.

**Implausibility is an error, not a result.** A WAF interstitial, maintenance
page, login wall or redesign all return HTTP 200 with a body that is not the
catalogue. Those do not raise on fetch, so the parse must reject them or state
gets overwritten with nonsense while the digest reports success. The ISO floor
is calibrated from the measurement below: fewer than 100 identifiers, or fewer
than 3 distinct business areas, raises.

### State

- `state/tags.json` — `{repo: [tag, ...]}`
- `state/iso.json` — `{message: version}`, e.g. `{"camt.053": 8}`
- `state/heartbeat.json` — facts about the last run only

**Adopt on first sight.** A key with no prior entry is recorded silently, no
event emitted. Without this, run one files an issue per historical tag and ~122
ISO events at once — in a tool whose premise is that noise destroys
follow-through. This also makes any run after a state revert a seeding run.

No cumulative counters. Those need read-modify-write, which would make the
heartbeat the one file a lost push corrupts rather than merely stales. The git
log is the series.

## The critical correctness rule

> **Never write state unless fetch AND parse both succeeded, and never advance
> state for an event until every sink has accepted it.**

Both halves are load-bearing, and the second was missing from the first draft.

**Input side.** If a timeout or error page produced an empty result and that
were saved, the monitor would go silent while still reporting success.

**Output side.** If state is committed and then a sink fails — Telegram 403,
a 429, a stale chat id, a runner cancellation — the event is never re-emitted.
The state file asserts success. This matters most for the tags watcher, which is
the half that addresses the actual problem: a dropped checklist means the tool
silently fails at its one job.

Delivery is therefore **at-least-once**: duplicates are recoverable, silent loss
is not. Each event carries a deterministic key (`tag:owner/repo:v1.2.0`,
`iso:camt.053:9`) and the issue sink checks for that marker before creating one.
Duplicate Telegram messages are harmless and not deduplicated.

Enforcement is structural:

- watchers raise `ParseError` rather than returning empty
- **writes are a monotone merge**: state starts from `prior` and only ever
  raises a version or appends a tag. No parse can delete a key or lower a value,
  so a partially rendered page degrades to a no-op instead of dropping a message
  and silently re-adopting it later
- events are emitted by iterating the *parsed* result, so a key vanishing from
  the page is never reported as a change
- the boundary is **per target**, not per watcher: one repo failing keeps its own
  entry untouched and cannot block or reseed its siblings
- a version moving backwards keeps the higher recorded value

## Liveness

`state/heartbeat.json` is written on **every** run, including runs where every
watcher failed. It is the one deliberate exception to the rule above: it records
what *happened*, never what was *seen*, so it cannot silence anything. Its
commit step is `if: always()`, so a crash still produces it.

1. **Proves the system is alive.** Each run pings an optional external
   dead-man's switch (`HEALTHCHECK_URL`, one `urllib` GET). That service pushes
   an alert when a ping fails to arrive, so liveness is an event that happens
   rather than an absence someone must notice. Absence-as-alarm was the first
   draft's weakest point: noticing that nothing arrived requires exactly the
   attention this tool exists to replace, and a digest that stops arriving is
   indistinguishable from one that arrived unread. A stale commit in `state/` is
   corroborating evidence, not the primary alarm.
2. **Keeps the schedule enabled.** GitHub disables scheduled workflows on public
   repositories after 60 days without "repository activity". GitHub does not
   define the term, but pushes reset it, including those authored by a bot, so
   the heartbeat commit resets the clock. The separate rule that `GITHUB_TOKEN`
   pushes do not *trigger* workflows is unrelated — this workflow is
   `schedule`-only. A schedule auto-disabled for inactivity **does not
   self-recover**; re-enabling is manual. A private repo is exempt from the rule
   entirely; public is chosen for portfolio value.

Because the ISO source is flaky, "no alerts" would otherwise be ambiguous, so
the weekly digest reports per-target outcomes rather than only changes.

## Evidence gathered before implementation

Measured 2026-08-03:

- `https://www.iso20022.org/iso-20022-message-definitions` returned HTTP 200 in
  1.7 s, 1,490,514 bytes, 122 distinct message-definition identifiers with
  versions.
- **After four requests the host stopped responding: three consecutive retries
  timed out at 25 s each.** The source rate-limits. An independent reviewer on a
  different network also timed out twice.

Identifier structure — `camt.053.001.08` is *business area* `camt`, *message*
`053`, *variant* `001`, *version* `08`. State is keyed by
`area.message`, never by area alone: `camt.003` and `camt.053` are unrelated
messages that version independently, so an area-level watermark would collapse
~70 messages into one and permanently silence all but the highest.

The version group is `\d{2,}`. A fixed `\d{2}` stops matching at version 100 —
and the failure is not a wrong version but the message vanishing from the parse
entirely, frozen silently forever under a monotone merge.

Consequences folded into the design:

- ISO poll frequency is **weekly**, not the 6 hours first proposed. ISO
  publishes annually; 6-hourly polling is ~1,460 requests to catch one event and
  is what triggered the rate limiting.
- **One attempt per run, no retry, no cached fallback.** Retrying inside a run
  recreates the burst that caused the throttling and spends 75 s to reach the
  conclusion next week gives free. A cached last-good body would be worse: the
  run would parse it, find no change, and report a successful check for a fetch
  that never happened — reintroducing the exact ambiguity the digest exists to
  remove. The tags watcher keeps 3 retries, where transient 5xx are real and the
  endpoint does not throttle at this volume.

  > **Superseded on 2026-08-05 by [ADR 0008](adr/0008-an-archived-snapshot-beats-silence.md).**
  > The no-retry half stands. The no-cache half does not: the live page turned out
  > to be unreachable on every run, not occasionally, so "no fallback" meant the
  > ISO watcher never reported at all. The paragraph above is right about a
  > self-cache, which finds no change by construction, and wrong to generalise
  > that to a third-party snapshot, which can hold a version we have not seen. The
  > ambiguity it worried about is closed separately, by labelling the source in
  > the digest.
- Multi-week ISO outages must not raise alarms; only a stalled runner should.

## Security

`GITHUB_TOKEN` is scoped to the repository the workflow runs in: it can read
public repos but **cannot** open an issue in `tempoloss/quackiso` from
`tempoloss/shipwatch`. Cross-repo issue creation uses a fine-grained PAT
(`GH_PAT`) with Issues: write.

The workflow declares `permissions: contents: write` explicitly rather than
inheriting, because the default for new personal repositories is read-only,
which would fail the state push on every run from run one.

The Telegram token lives in Actions secrets. Not a posture argument: GitHub
secret-scans public repositories, reports leaked Telegram tokens upstream, and
Telegram revokes them, so a committed token yields a silently dead bot.

## Rejected alternatives

**Self-hosted Go daemon using moxy as the queue.** Most educational, and would
force the missing concurrency primitives to be learned. Rejected on hardware.
Correct answer, wrong month; the ISO logic ports unchanged.

**Cloudflare Worker with KV.** Free and always-on, but a new platform plus a
second state store for no benefit over Actions.

**`repository_dispatch` per watched repo.** Lower latency, but a workflow and a
token in every watched repo, forever, to buy latency that does not matter.

## Known limits

- Version identifiers are read from page HTML. A redesign breaks it — loudly, by
  the plausibility floor, rather than by silently reporting no changes.
- "Highest version seen on the page" is a heuristic and can be fooled by
  versions appearing in changelogs or examples.
- Business-area prefixes in the regex are a fixed list. A message quackiso adds
  outside that list is unwatched; the digest reports how many configured
  messages were found so the gap is visible rather than silent.
