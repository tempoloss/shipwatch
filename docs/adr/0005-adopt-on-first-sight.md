# 5. A key seen for the first time is adopted silently

Status: accepted

Decided in `05bd807` (2026-08-03). Written down as an ADR on 2026-08-05, when the
decisions that until then lived only in the design doc and commit messages were
filed here.

## Context

Shipwatch exists because noise destroys follow-through. A tag means release work
has appeared in public, and an ISO version means `quackiso` may have fallen
behind a public standard. Both are obligations, but only when they are new to a
monitor that has already been seeded.

A repository with no entry in `state/tags.json`, or an ISO family with no entry in
`state/iso.json`, has no baseline. Treating the whole current world as new would
make the first run the loudest run the system ever has.

Measured 2026-08-05 with `gh api repos/tempoloss/<repo>/tags --jq length`, the six
configured repositories currently have 11 tags:

- `tempoloss/quackiso`: 7
- `tempoloss/moxy`: 1
- `tempoloss/chessview`: 1
- `tempoloss/search-gateway`: 1
- `tempoloss/db-seed`: 1
- `tempoloss/mentor-skill`: 0

The design measurement for the ISO source found 122 distinct message-definition
identifiers with versions. Without adoption, run one would therefore try to file
about 133 historical issues before it ever found a real change.

That is the failure case. The tool meant to prevent forgotten follow-through
would teach its maintainer to ignore it on day one.

## Decision

A key seen for the first time is recorded silently.

For tags, `prior is None` means the repository has never been seen. The parser
returns no events and stores the current tag list. For ISO, a missing family key
is set to the current version and emits no event.

This is adoption, not success delivery. No issue is opened and no Telegram
message is sent. The state write is still governed by [ADR 0004](0004-state-is-written-only-after-delivery.md):
fetch and parse must succeed, and any emitted event must be accepted by every
sink before state advances.

## Alternatives rejected

- **Emit events for every historical value.** Honest about what exists, but wrong
  for a follow-through system. It turns setup into an issue flood and makes the
  first useful signal indistinguishable from backlog noise.
- **Require hand-written seed files.** Avoids the flood, but moves the exact list
  of historical values into a manual setup step. That is a second place to be
  wrong before the first run.
- **Omit empty repositories from state.** Looks smaller, but silently changes the
  meaning of an empty watch. `mentor-skill` has no tags today; the first tag
  pushed there must be an event, not another adoption.

## Consequences and measured behaviour

The first run must be dispatched manually and understood as a seeding run. README
setup step 4 exists for this: push, then run the workflow once by hand, so it
records existing tags and versions without announcing them.

Any run after a state revert is also a seeding run. That is the safety property:
a revert recovers from corrupt or bad state without re-notifying every historical
release and ISO version.

Measured 2026-08-05, the adoption run recorded 7 tags for `quackiso`, 1 each for
`moxy`, `chessview`, `search-gateway` and `db-seed`, and an empty list for
`mentor-skill`, with `delivered: 0`.

The empty list matters. It records that `mentor-skill` was seen, but had no tags.
The first tag pushed to it after that state write is therefore a real event.