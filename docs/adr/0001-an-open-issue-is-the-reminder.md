# 1. An open issue is the reminder, and a todo list is not

Status: accepted

Decided in `05bd807` (2026-08-03). Written down as an ADR on 2026-08-05, when the decisions that until then lived only in the design doc and commit messages were filed here.

## Context

Public-facing follow-through does not happen at the moment the work ships.
The work is already done, attention has moved, and nobody hears about it.

The concrete failure was `quackiso`. It was merged into
`duckdb/community-extensions` on 2026-08-01 and still had 1 star, no release
notes, and no post when this tool was designed.

That failure was not motivation. It was not task capture. The obligation appeared
when attention was elsewhere, and nothing made it visible again.

A second todo list would only move the missing obligation into another place
that has to be checked. A time-based reminder would ask the maintainer to guess
when future attention should be interrupted. A chatbot would add a command
surface and a conversation.

The rule is narrower than all of those: remove decisions, do not add discipline.
A tool whose upkeep depends on the discipline it substitutes for is circular.

The repository already has the event that matters. A new tag lands. ISO 20022
publishes a new message version. The trigger is not a thought to preserve, it is
an event that already happened.

## Decision

When shipwatch sees an event, it files a GitHub Issue in the affected repository.
It does not write a todo into shipwatch, a notebook, a queue, or a private task
vault.

The issue sits next to the code that needs public follow-through. For a release,
that means an issue in the repository that was tagged, with the public-side
checklist. For an ISO 20022 version change, that means an issue on `quackiso`
saying which message moved and what to check.

An open issue next to the code is visibly unfinished. That visibility is the
mechanism. Nothing has to be remembered, because every trigger is an event that
already happened.

The issue is also the durable record for the event. The sink deduplicates with a
`shipwatch` marker in GitHub Issues, and both open and closed issues count. An
open issue means the follow-through is still unfinished. A closed issue means it
was handled.

## Alternatives rejected

- **A second task vault.** It solves nothing, because the failure was not a lack
  of somewhere to write tasks. It was the absence of visible unfinished work next
  to the code.
- **Time-based reminders.** They replace one decision with another: now the
  maintainer must decide when to be nagged. The trigger already happened, so the
  reminder should be attached to that event, not to a guessed future time.
