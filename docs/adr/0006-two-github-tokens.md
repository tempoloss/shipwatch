# 6. Two GitHub tokens, because GITHUB_TOKEN cannot leave its repository

Status: accepted

Decided in `01cfd1c` (2026-08-03). Written down as an ADR on 2026-08-05, when the
decisions that until then lived only in the design doc and commit messages were
filed here.

## Context

`shipwatch` runs in `tempoloss/shipwatch` and writes issues in the repositories
it watches. The first watched repository is `tempoloss/quackiso`, and ISO change
issues are filed there too.

That crosses a boundary GitHub deliberately enforces. The workflow's
`GITHUB_TOKEN` is scoped to the repository the workflow runs in. From
`tempoloss/shipwatch` it can read public repositories, but it cannot open an
issue in `tempoloss/quackiso` or in any other watched repository.

This is easy to miss because reads do work. A monitor can fetch tags, parse ISO
versions, report success, and still have no credential capable of creating the
issue that is the whole point of the run.

The setup therefore asks for a fine-grained personal access token before the
workflow is trusted. The token must have `Issues: write` on every watched
repository: `tempoloss/quackiso`, `tempoloss/moxy`, `tempoloss/chessview`,
`tempoloss/search-gateway`, `tempoloss/db-seed`, and `tempoloss/mentor-skill`.

There is a second secret with a different failure mode. The Telegram bot token
lives in Actions secrets for a mechanical reason, not a posture argument. GitHub
secret-scans public repositories, reports leaked Telegram tokens upstream, and
Telegram revokes them, so a committed token yields a silently dead bot.

## Decision

Use two GitHub tokens.

`GITHUB_TOKEN` is the read token. It is supplied by Actions and is used for the
public GitHub API reads that discover tags.

`GH_PAT` is the write token. It is a fine-grained PAT with `Issues: write` on
every watched repository, and it is used by the issue sink when an event must be
turned into a GitHub issue.

Do not spend the PAT on reads. Keeping reads on `GITHUB_TOKEN` keeps the wider
credential on the only operation that needs it: cross-repository issue creation.

Keep `TELEGRAM_TOKEN` in Actions secrets with `TELEGRAM_CHAT_ID`. A committed
Telegram token does not merely leak authority. It can be revoked upstream and
leave the bot present but dead.

## Alternatives rejected

- **Use `GITHUB_TOKEN` for everything.** It can read public repositories, but it
  cannot create an issue outside `tempoloss/shipwatch`, so the first real
  delivery to `tempoloss/quackiso` fails.
- **Use the PAT for reads and writes.** Works, but spends the broader credential
  on reads that already succeed with `GITHUB_TOKEN`.
- **Scope the PAT to the repositories that are active today.** Fails loudly on
  the first new tag in any watched repository outside that subset, which can be
  weeks after setup.
- **Commit the Telegram bot token.** GitHub reports it, Telegram revokes it, and
  the next message disappears into a bot that still exists but cannot send.

## Consequences and measured behaviour

Adoption makes this trap worse. The first run records every existing tag and ISO
version without announcing them, as described in [ADR 0005](0005-adopt-on-first-sight.md).
That is the correct behaviour, but it means the first successful run never
exercises the issue sink and never proves that `GH_PAT` can write.

Measured 2026-08-05: dropping `quackiso`'s newest tag from `state/tags.json` for
exactly one run forced a real cross-repository write. Issue #1 was created in
`tempoloss/quackiso` from `tempoloss/shipwatch`, and the next run re-adopted the
tag.

A PAT scoped to only some of the six watched repositories does not fail during
adoption. It fails at the first tag in any of the other repositories, loudly but
late.
