"""File a GitHub issue for an event.

An open issue sitting next to the code is visibly unfinished, which is the
pressure this whole tool exists to create.
"""

from __future__ import annotations

import json

from ..core import Event, http_get, http_post_json

MARKER = "<!-- shipwatch:{key} -->"


def _headers(token: str) -> dict:
    return {"authorization": f"Bearer {token}",
            "accept": "application/vnd.github+json"}


def already_filed(event: Event, token: str) -> bool:
    """True when an issue for this exact event already exists.

    Runs can be retried, and state is only committed after sinks fire, so the
    same event can legitimately be emitted twice. Deduplication lives here
    rather than in state because the issue itself is the durable record.
    Both open and closed issues count — a closed one means it was handled.
    """
    text = http_get(
        f"https://api.github.com/repos/{event.repo}/issues"
        f"?state=all&per_page=100&labels=shipwatch",
        headers=_headers(token),
    )
    marker = MARKER.format(key=event.key)
    for item in json.loads(text):
        if marker in (item.get("body") or ""):
            return True
    return False


def send(event: Event, token: str) -> str:
    """Create the issue. Returns its html_url."""
    body = f"{event.body}\n\n{MARKER.format(key=event.key)}"
    text = http_post_json(
        f"https://api.github.com/repos/{event.repo}/issues",
        {"title": event.title, "body": body, "labels": ["shipwatch"]},
        headers=_headers(token),
    )
    return json.loads(text).get("html_url", "")
