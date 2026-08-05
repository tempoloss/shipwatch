# 8. An archived snapshot beats silence, and a self-cache does not

Status: accepted

Decided in `7f25550` (2026-08-05). Written down as an ADR on 2026-08-05, when the
decisions that until then lived only in the design doc and commit messages were
filed here.

## Context

The ISO source is not just slow. It is absent often enough that silence became
the normal outcome.

Measured 2026-08-03: the live catalogue answered once in 1.7 s with 1,490,514
bytes and 122 distinct message-definition identifiers. After that, three
consecutive retries timed out at 25 s each. An independent reviewer on another
network also timed out twice.

Both scheduled runs after that failed the same way. `last_success.iso` was never
set, and `due()` reads exactly that field, so a source justified as weekly was in
fact due on every daily run. The digest is sent whenever `iso` appears in `ran`,
so it fired daily as well, reporting the same failure each time.

The design doc recorded the right first defence against this host: one live
attempt per run, no retry. Retrying inside a run recreates the burst that made the
host stop answering, and spends 75 s to learn nothing durable.

It also rejected a cache, and that part is superseded here. The words to preserve
are: `One attempt per run, no retry, no cached fallback.` The warning after it
was also right for the thing it named: `A cached last-good body would be worse:
the run would parse it, find no change, and report a successful check for a fetch
that never happened`.

## Decision

`fetch()` is now a waterfall.

First, `_fetch_live()` makes one direct request to
`https://www.iso20022.org/iso-20022-message-definitions`. It has no retry.
Failure is expected, not exceptional enough to end the run.

Second, `_fetch_wayback()` asks the Wayback Machine for the latest capture through
the `2id_` redirect. The `2` timestamp means most-recent capture. The `id_` mode
returns the original bytes without the Wayback toolbar injection.

The capture timestamp is read from the redirect URL, for example
`/web/20260513133352id_/...`. A snapshot older than
`MAX_SNAPSHOT_AGE_DAYS` is refused. The ceiling is 180 days because ISO publishes
maintenance releases annually, usually between February and May. Six months is
old enough to be useful and young enough not to pretend a year-old catalogue is a
fresh check.

`fetch()` returns `(html, source)`. The runner keeps parsing the same HTML shape,
but the digest now says `live` or `archive (2026-05-13)` instead of only `ok`.
A successful check is therefore never confused with a live check.

## Alternatives rejected

- **Retry the live page inside a run.** Rejected because it recreates the request
  burst that caused the throttling and converts one outage into three slow
  outages.
- **Use our own last-good body.** Rejected for the same reason the design doc
  gave. A self-cache is our previous body, so parsing it finds no change by
  construction and can never detect a version bump.
- **Treat Wayback as the same cache.** Rejected because a third-party snapshot is
  a different object. It may contain a catalogue version shipwatch has never seen,
  and the source label closes the ambiguity independently.
- **Accept any archived page no matter its age.** Rejected because ISO publishes
  annually. After 180 days the snapshot is no longer evidence that this year's
  catalogue has been checked.

## Consequences and measured behaviour

Measured 2026-08-05: the waterfall fell through to a 2026-05-13 capture in 39 s
total. The body was 1,492,381 bytes, and `parse()` found all 15 configured
messages.

The existing guards carry over untouched. The plausibility floor rejects a thin
or single-area page whatever the source, and the monotone merge means a stale
snapshot degrades to a no-op rather than lowering a version. Those rules are part
of [ADR 0004](0004-state-is-written-only-after-delivery.md).

Because ISO now completes, `last_success.iso` is set and the 168-hour interval
starts working. While ISO failed permanently, a source justified as weekly was in
fact polled daily, and the digest fired daily.

The remaining limit is honest. The Wayback snapshot refreshes only if something
archives the page. If nothing does and the live site stays down, the capture ages
until the 180-day ceiling refuses it. At that point ISO fails loudly, with no path
back except the live source recovering.
