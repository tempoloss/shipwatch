# 2. A new tag and a new ISO version are the same problem

Status: accepted

Decided in `01cfd1c` (2026-08-03). Written down as an ADR on 2026-08-05, when the
decisions that until then lived only in the design doc and commit messages were
filed here.

## Context

shipwatch exists because follow-through appears when attention is elsewhere.
A git tag says a repository shipped and the public side is now owed. An ISO
20022 message version says the standard moved and `quackiso` may be behind.
Those look like different products only if the trigger is mistaken for the
mechanism.

The shared problem is smaller:

```python
diff current reality against what was last seen
```

For tags, current reality is the GitHub API response for
`/repos/{repo}/tags?per_page=100`. For ISO, current reality is the message
catalogue at `iso20022.org`, currently reduced to keys like `camt.053` and an
integer version. Both produce events. Both update committed JSON state. Both
must refuse pages that do not look like the source, because a silent empty parse
would mark everything as seen and stop the monitor while reporting success.

## Decision

Use one runner, one state mechanism, and one sink layer. Watchers are the only
pluggable part.

The watcher contract is:

```python
fetch(cfg) -> str                      # impure, may raise
parse(text, prior) -> (events, state)  # pure; raises ParseError when the
                                       # response does not look like the page
```

`fetch` owns network and credentials. The tags watcher calls GitHub with a token.
The ISO watcher calls the live site, and later revisions may choose another
source, but the runner only sees returned text or an exception.

`parse` owns interpretation. It gets bytes already fetched and the prior state,
then returns the events to deliver and the next state to commit. It raises
`ParseError` for a response in the wrong shape: non-list tag JSON, a tag entry
without `name`, an ISO page with too few identifiers, or an ISO page with too
few business areas.

This split is what keeps the suite off the network. Every success path and every
implausible response can be tested from fixtures, because `parse` is pure. The
README records the result: `27 tests, no network`.

The state merge is monotone inside each watcher. Tags append fresh tag names to
the prior list rather than replacing it with the latest API page. ISO starts
from `prior` and only raises a version. A partial page can therefore become a
no-op, but it cannot delete knowledge and silently re-adopt it later.

Polling is part of the same decision. Watching N repositories requires no
workflow installed in those repositories and no token stored in them. One daily
schedule drives every watcher, with each watcher deciding whether it is due.
Latency of hours is correct here: a public checklist filed later the same day is
useful, and a lower-latency trigger does not make the forgotten work more true.

## Alternatives rejected

- **`repository_dispatch` per watched repo.** Lower latency, but every watched
  repository would need a workflow and a token installed forever, to buy latency
  that does not matter.
- **Two separate tools, one for tags and one for ISO.** Mirrors the surface
  inputs and duplicates the dangerous parts: state writes, sink delivery,
  adoption, deduplication, and the rule that bad input must not advance state.
