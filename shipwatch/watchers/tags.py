"""Watch git tags across repositories.

A new tag means something shipped, which means the public side is now owed.
"""

from __future__ import annotations

import json

from ..core import Event, ParseError, http_get

CHECKLIST = """A new tag landed in `{repo}`: **{tag}**

The public side is not done yet:

- [ ] GitHub Release created, with notes
- [ ] README / docs reflect {tag}
- [ ] blog post
- [ ] Telegram / X announcement
{extra}
_Filed by shipwatch. Close this when the list is done._
"""

# Per-repo checklist additions come from config, not from a name test in code.
# This used to be `if repo.endswith("/quackiso")`, which made one deployment's
# registry chore a property of the watcher: anyone forking this had to edit the
# source to change a checklist line, and the special case was invisible from
# config.json where every other target lives.


def fetch(repo: str, token: str) -> str:
    """Raw JSON text of a repo's tags."""
    return http_get(
        f"https://api.github.com/repos/{repo}/tags?per_page=100",
        headers={"authorization": f"Bearer {token}",
                 "accept": "application/vnd.github+json"},
    )


def parse(repo: str, text: str, prior: list[str] | None,
          extras: list[str] | None = None) -> tuple[list[Event], list[str]]:
    """Pure. Returns (events, known tag names).

    `prior` of None means this repo has never been seen: the current tags are
    adopted WITHOUT emitting events, otherwise the first run would file an issue
    for every historical release at once.

    `extras` are checklist lines for this repo only, from `repo_extras` in
    config. Defaulted so existing callers and tests keep working.
    """
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ParseError(f"{repo}: tags response is not JSON: {exc}") from exc

    if not isinstance(payload, list):
        # A rate-limit or error body is a dict with a "message" key. Refusing
        # here is what stops an error page from being recorded as "no tags",
        # which would re-announce every tag on the following run.
        raise ParseError(f"{repo}: expected a JSON array of tags, got {type(payload).__name__}")

    current = []
    for item in payload:
        if not isinstance(item, dict) or "name" not in item:
            raise ParseError(f"{repo}: tag entry missing 'name'")
        current.append(str(item["name"]))

    if prior is None:
        return [], current

    known = set(prior)
    fresh = [t for t in current if t not in known]

    extra = "".join(f"- [ ] {line}\n" for line in (extras or []))

    events = []
    for tag in fresh:
        events.append(Event(
            kind="new_tag",
            repo=repo,
            key=f"tag:{repo}:{tag}",
            title=f"Ship the public side of {repo.split('/')[-1]} {tag}",
            body=CHECKLIST.format(repo=repo, tag=tag, extra=extra),
        ))

    # Monotone: the union, never just `current`. The API returns one page, so a
    # repo with more than 100 tags drops the oldest. Storing only `current`
    # would forget them, and a tag that reappeared in the window later would be
    # announced again. The union means a tag is announced exactly once, ever.
    return events, list(prior) + fresh
