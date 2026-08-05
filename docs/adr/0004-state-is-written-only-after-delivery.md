# 4. State is never written until fetch, parse and every sink have succeeded

Status: accepted

Decided in `01cfd1c` (2026-08-03), hardened in `2a00bdb` (2026-08-03). Written down as an ADR on 2026-08-05, when the decisions that until then lived only in the design doc and commit messages were filed here.

## Context

Shipwatch trusts committed JSON as the record of what has already been seen.
That makes the state write the dangerous operation, not the notification.

The input side can fail quietly. A timeout, a GitHub error body, a WAF
interstitial, a maintenance page, a login wall or a redesign can all produce
HTTP 200 with content that is not the page being watched. If that becomes an
empty result and is saved, every message is marked already-seen and the monitor
goes permanently silent while still reporting success.

The output side can fail quietly too. If state is committed and then Telegram
returns 403, a sink hits 429, a chat id is stale or a runner is cancelled, the
event is never re-emitted. The state file asserts it succeeded.

This is the same failure class as a `NULL` silently dropping out of a `SUM`:
wrong, quiet, and trusted.

The first draft in `01cfd1c` guarded the input side. Review in `2a00bdb` added
the output half, because a dropped checklist is the tool silently failing at
its one job.

## Decision

State is written only after fetch succeeded, parse accepted the response as
plausible, and every event from that parsed result was accepted by every sink.
A failed target leaves its prior state untouched.

Delivery is therefore at-least-once. A duplicate is recoverable; silent loss is
not. Each event carries a deterministic key, such as `tag:owner/repo:v1.2.0` or
`iso:camt.053:9`. The issue sink checks for the matching `shipwatch` marker
before creating an issue, and both open and closed issues count. Duplicate
Telegram messages are harmless and are not deduplicated.

Adoption is the only way a real key enters state without a notification: a key
seen for the first time is recorded silently, as described in [ADR 0005](0005-adopt-on-first-sight.md). That is seeding, not delivery.

The heartbeat is outside this rule. It records what happened during the run,
never what was seen, so it cannot silence a watcher. That exception is recorded
in [ADR 0007](0007-the-heartbeat-records-what-happened.md).

Enforcement is structural, not conventional:

- Watchers raise `ParseError` rather than returning empty.
- Writes are a monotone merge. State starts from `prior` and only ever raises a
  version or appends a tag.
- A partially rendered page therefore degrades to a no-op instead of dropping a
  message and silently re-adopting it later.
- Events are emitted by iterating the parsed result, so a key vanishing from the
  page is never reported as a change.
- The boundary is per target, not per watcher. One repository failing keeps its
  own entry untouched and cannot block or reseed its siblings.
- A version moving backwards keeps the higher recorded value.
- The ISO plausibility floor rejects a page with under 100 identifiers or under
  3 business areas. That catches a long error page as well as a thin one.
- The version group is `\d{2,}`. A fixed `\d{2}` would make a v100 message
  vanish from the parse and freeze it silently forever.
- ISO state is keyed by `area.message`, never by business area alone, because
  `camt.003` and `camt.053` version independently and an area-level watermark
  would collapse about 70 messages into one.
- `main.py` calls `deliver()` before `save_state()`. Any undelivered event makes
  the runner skip the state write, so the event retries on the next run.

## Alternatives rejected

- **Commit state after parse and deliver later.** A sink failure would turn an
  owed event into a trusted record of success.
- **Treat an implausible page as an empty result.** That is the `NULL` in the
  `SUM`: the output looks clean because the evidence was silently discarded.
- **Store only the current page.** A partial page or a one-page tag API window
  would delete known keys, then quietly adopt or re-announce them later.
- **Use a business-area watermark for ISO.** `camt.003` and `camt.053` are
  independent messages, so the watermark would silence real work.
