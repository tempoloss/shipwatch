# 3. GitHub Actions, because there is nowhere else to run

Status: accepted

Decided in `01cfd1c` (2026-08-03). Written down as an ADR on 2026-08-05, when the
decisions that until then lived only in the design doc and commit messages were
filed here.

## Context

`shipwatch` needs to run when nobody is already thinking about it. A new git tag
and a new ISO 20022 message version are events that have already happened, and
the tool exists because public follow-through is otherwise forgotten.

That needs a runner, but not an application server. The project has no web UI,
no command surface, no incoming requests, and no conversation loop. It polls,
diffs current reality against committed state, delivers issues and Telegram
messages, then commits the new state.

The platform choice was forced by circumstance rather than chosen. No always-on
personal hardware is available, and the machine that is always on is not one
where credentials belong.

The README states the operational shape directly: "Runs on GitHub Actions. No
server, no hosting, no cost." The workflow is the implementation of that shape.
It runs on a daily schedule and can also be dispatched manually for the first
adoption run.

## Decision

Run `shipwatch` as a single GitHub Actions workflow, `.github/workflows/watch.yml`,
on the schedule `17 6 * * *`.

One daily schedule drives all watchers. Cadence is not split across cron lines:
`config.json` says tags are due every 20 hours and ISO is due weekly, and the
runner decides which watcher should run.

The workflow declares repository contents write access explicitly:

```yaml
permissions:
  contents: write
```

That is not decorative. The default workflow permission for new personal
repositories is read-only, so inheriting the default would fail the state push on
every run from run one. Issues are created with `GH_PAT`; this permission exists
for the committed `state/` files.

Runs are serialised with this exact concurrency group:

```yaml
concurrency:
  group: shipwatch-watch
  cancel-in-progress: false
```

The choice is queued, not cancelled. A manual retry or a delayed schedule must
not race another run and double-deliver, but cancelling mid-run could stop after
delivery and before state is saved. That is the silent-loss failure this project
is built around.

## Alternatives rejected

- **Self-hosted Go daemon using `moxy` as the queue.** Most educational, and the
  correct answer for learning the missing concurrency primitives, but rejected
  on hardware. Correct answer, wrong month; the ISO logic ports unchanged.
- **Cloudflare Worker with KV.** Free and always-on, but it adds a new platform
  plus a second state store for no benefit over Actions.
- **`repository_dispatch` per watched repo.** Lower latency, but it requires a
  workflow and token in every watched repository, forever, to buy latency that
  does not matter.

## Consequences

There is no process to keep alive and no host to patch. The audit trail is the
git log of `state:` commits.

Latency is hours. That is acceptable here: a public-side checklist appearing
later the same day still solves the problem, while a permanently installed token
in every watched repo would create a new thing to maintain forever.
