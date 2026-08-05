# 7. The heartbeat records what happened, never what was seen

Status: accepted

Decided in `01cfd1c` (2026-08-03), corrected in `5d3d5d4` (2026-08-05).
Written down as an ADR on 2026-08-05, when the decisions that until then lived
only in the design doc and commit messages were filed here.

## Context

[ADR 0004](0004-state-is-written-only-after-delivery.md) says state is written
only after fetch, parse and every sink have succeeded. That rule prevents the
quiet failure where a source lies, a sink rejects, and the state file still
asserts that the event has been handled.

`state/heartbeat.json` is the one deliberate exception. It is written on every
run, including a run where every watcher failed. The exception is safe because
the heartbeat records what happened, never what was seen. It can say the runner
started, which watchers ran, which targets failed, how many events were
delivered, and when each watcher last completed successfully. It does not adopt
a tag or an ISO version, so it cannot silence a future notification.

The heartbeat has no cumulative counters. Those need read-modify-write, which
would make this the one file a lost push corrupts instead of merely stales. The
git log is the series.

The first job is liveness. `HEALTHCHECK_URL` is optional but recommended, and it
is just one `urllib` GET. A dead-man's switch alerts on a missing ping, so
liveness is an event that happens rather than an absence someone must notice.
Absence-as-alarm was the first draft's weakest point: noticing that nothing
arrived requires exactly the attention this tool exists to replace, and a digest
that stops arriving is indistinguishable from one that arrived unread.

The second job is keeping the schedule enabled. GitHub disables scheduled
workflows on public repositories after 60 days without repository activity.
Pushes reset that clock, including bot-authored pushes. A schedule disabled for
inactivity does not self-recover.

## Decision

Write `state/heartbeat.json` unconditionally at the end of `main()`. The payload
is facts about the current run: `last_run`, `ran`, `outcomes`, `delivered`,
`failures`, and `last_success` carried forward or advanced only after a watcher
state write succeeds.

Collect the four required secrets without raising. `secret()` calls `env()` and
turns a missing or blank value into a named failure. If any required secret is
missing, `main()` skips every watcher, writes the heartbeat, pings the optional
healthcheck if configured, and returns 1. The run is red every day until the
configuration is fixed, because this is not a rate-limited source. It could
never have worked.

The workflow commit step remains `if: always()`, and it creates `state/` before
adding it. `mkdir -p state` is part of the liveness mechanism, not tidiness:
`git add state/` is fatal when the path does not exist, but clean when an empty
directory exists.

## Alternatives rejected

- **Treat a missing digest as the alarm.** Requires someone to notice absence,
  which is the attention this tool exists to replace.
- **Count total runs or failures in the heartbeat.** Makes the heartbeat a
  read-modify-write counter, so a lost push corrupts history. The git log is the
  history.
- **Let the `always()` commit step handle crashes by itself.** It runs after a
  crash, but it cannot add a path that was never created.

## Consequences and measured behaviour

Measured 2026-08-04: the scheduled run failed inside the mechanism written to
survive failure.

`GH_PAT` had not been added to the repository secrets. The original `main()`
read its secrets eagerly, so `env()` raised before the heartbeat write. Nothing
called `save_state`, so `state/` was never created. Git cannot track an empty
directory, so a fresh checkout did not contain `state/` either.

The `if: always()` commit step then ran. It executed `git add state/` against a
path that did not exist. That is fatal, and under `bash -e` the step exited 128.
The run produced no heartbeat commit, so there was no liveness proof and no reset
of the inactivity clock.

Measured 2026-08-05: `git add state/` on an existing empty directory exits 0. On
a clean repository where `state/` has never been committed, it reports
`fatal: pathspec 'state/' did not match any files`.

The trap only exists before any state file has ever been committed. After that,
a checkout materialises `state/`, so the pathspec matches. It is a first-run trap,
which is exactly when the schedule has the least margin.
