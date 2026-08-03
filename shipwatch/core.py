"""Shared types, state handling, and HTTP.

Kept deliberately small. Everything here is used by both watchers and both
sinks, and nothing here knows what a tag or an ISO message is.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

STATE_DIR = Path(__file__).resolve().parent.parent / "state"


class Event(NamedTuple):
    """Something changed and the outside world should hear about it.

    `repo` is where an issue should be filed, or "" for Telegram only.
    `key` is a stable identity used to avoid filing the same issue twice.
    """

    kind: str
    repo: str
    key: str
    title: str
    body: str


class ParseError(Exception):
    """The response arrived but did not look like what we expect.

    Raised rather than returning an empty result, because an empty result
    would be saved as state and would silence the monitor permanently.
    """


def now() -> datetime:
    return datetime.now(timezone.utc)


def load_state(name: str) -> dict:
    """Read state/<name>.json, or {} when it does not exist yet."""
    path = STATE_DIR / f"{name}.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        # Corrupt state is recoverable via git; treating it as empty here
        # would silently reseed and suppress notifications, so refuse.
        raise ParseError(f"state/{name}.json is not valid JSON")


def save_state(name: str, data: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    path = STATE_DIR / f"{name}.json"
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def due(name: str, interval_hours: float) -> bool:
    """True when this watcher has not run inside its interval.

    One schedule drives every watcher; each decides for itself whether enough
    time has passed. That avoids cron gymnastics and keeps the cadence in one
    place that is also visible in committed state.
    """
    last = load_state("heartbeat").get("last_success", {}).get(name)
    if not last:
        return True
    try:
        prev = datetime.fromisoformat(last)
    except ValueError:
        return True
    return (now() - prev).total_seconds() >= interval_hours * 3600


def http_get(url: str, headers: dict | None = None, retries: int = 3,
             timeout: int = 25) -> str:
    """GET with exponential backoff. Raises on final failure — never returns "".

    The ISO source rate-limits after a handful of requests, so backoff is not
    decoration. An exhausted retry budget must propagate as an exception so the
    caller skips writing state.
    """
    req = urllib.request.Request(url, headers=headers or {})
    req.add_header("user-agent", "shipwatch (+https://github.com/tempoloss/shipwatch)")
    last: Exception | None = None
    for attempt in range(retries):
        if attempt:
            time.sleep(2 ** attempt)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
            last = exc
    raise ConnectionError(f"GET {url} failed after {retries} attempts: {last}")


def http_post_json(url: str, payload: dict, headers: dict | None = None,
                   timeout: int = 25) -> str:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=headers or {}, method="POST")
    req.add_header("content-type", "application/json")
    req.add_header("user-agent", "shipwatch")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"environment variable {name} is not set")
    return value


def env_opt(name: str) -> str:
    """Optional environment variable. Empty string when unset."""
    return os.environ.get(name, "").strip()


def ping(url: str, timeout: int = 10) -> None:
    """Fire-and-forget GET, used for the external dead-man's switch."""
    req = urllib.request.Request(url, method="GET")
    req.add_header("user-agent", "shipwatch")
    with urllib.request.urlopen(req, timeout=timeout):
        pass
