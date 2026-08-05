"""Watch ISO 20022 for new message-definition versions.

quackiso implements a fixed set of messages. ISO publishes maintenance releases
annually and versions increment, so a message moving from .08 to .09 is real
work arriving.

The source rate-limits: measured 2026-08-03 it answered once in 1.7s with
1,490,514 bytes and 122 identifiers, then timed out on three consecutive
retries. The live site is effectively unreachable most of the time.

Fetching therefore uses a waterfall:
1. Try the live page directly (one attempt, no retry).
2. On failure, fall back to the Wayback Machine's latest archived snapshot.
   The ``2id_`` redirect resolves to the most recent capture and returns the
   original HTML without Wayback's toolbar injection.  The ``memento-datetime``
   response header or the redirect URL carries the snapshot timestamp, which is
   checked against MAX_SNAPSHOT_AGE_DAYS.

The existing plausibility guards in parse() protect against garbage regardless
of source, and the monotone merge means even a slightly stale snapshot is safe —
it just won't catch a bump that happened between the snapshot and now, which the
next live-fetch success will catch.
"""

from __future__ import annotations

import re
import urllib.error
import urllib.request
from datetime import datetime, timezone

from ..core import Event, ParseError, http_get

URL = "https://www.iso20022.org/iso-20022-message-definitions"

# Wayback Machine latest-snapshot redirect.  The ``2`` timestamp is a magic
# value meaning "most recent capture"; ``id_`` returns the original bytes
# without the Wayback toolbar.  The redirect URL embeds the real timestamp
# (e.g. ``/web/20260513133352id_/…``) and the ``memento-datetime`` header
# carries the capture date.
_WBM_LATEST = f"https://web.archive.org/web/2id_/{URL}"
_WBM_TS_RE = re.compile(r"/web/(\d{14})id_/")

# Reject Wayback snapshots older than this. ISO publishes annually (Feb–May),
# so 6 months covers the worst case while still being useful.
MAX_SNAPSHOT_AGE_DAYS = 180

# camt.053.001.08 -> area camt, message 053, variant 001, version 08.
# `area.message` is the tracking key: camt.003 and camt.053 are unrelated
# messages that version independently, so keying by area alone would collapse
# ~70 messages into one watermark and silence all but the highest.
# The version group is \d{2,}: a fixed \d{2} stops matching at version 100 and
# the message would vanish from the parse entirely, freezing silently forever.
MSG_ID = re.compile(r"\b(pacs|pain|camt|acmt|remt|reda|semt|setr|sese)"
                    r"\.(\d{3})\.(\d{3})\.(\d{2,})\b")

# A WAF interstitial, a maintenance page, a login wall or a redesign all return
# HTTP 200 with a body that is not the catalogue. Those do not raise on fetch,
# so implausibility has to be an error here or state gets overwritten with
# nonsense while the digest reports success. The real page carried 122 ids
# across many areas on the day this was written.
MIN_EXPECTED_IDS = 100
MIN_EXPECTED_AREAS = 3


def _fetch_live() -> str:
    """One attempt against the live site. No retry — retrying recreates the
    request burst that caused the rate-limiting in the first place."""
    return http_get(URL, retries=1, timeout=30)


def _fetch_wayback() -> tuple[str, str]:
    """Fetch the latest Wayback Machine snapshot.

    Returns (html, date_label) on success.
    Raises ConnectionError if no usable snapshot exists or the snapshot is
    older than MAX_SNAPSHOT_AGE_DAYS.
    """
    req = urllib.request.Request(_WBM_LATEST, headers={
        "User-Agent": "shipwatch (+https://github.com/tempoloss/shipwatch)",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            html = resp.read().decode("utf-8", errors="replace")
            final_url = resp.url
    except (urllib.error.URLError, urllib.error.HTTPError,
            TimeoutError, OSError) as exc:
        raise ConnectionError(f"Wayback fetch failed: {exc}") from exc

    # Extract the snapshot timestamp from the redirect URL.
    m = _WBM_TS_RE.search(final_url)
    if not m:
        raise ConnectionError(
            f"Wayback redirect URL has no timestamp: {final_url}"
        )
    timestamp = m.group(1)

    try:
        snap_dt = datetime.strptime(timestamp, "%Y%m%d%H%M%S").replace(
            tzinfo=timezone.utc)
    except ValueError as exc:
        raise ConnectionError(
            f"Wayback returned unparseable timestamp: {timestamp}"
        ) from exc

    age_days = (datetime.now(timezone.utc) - snap_dt).days
    if age_days > MAX_SNAPSHOT_AGE_DAYS:
        raise ConnectionError(
            f"Wayback snapshot is {age_days} days old "
            f"(threshold: {MAX_SNAPSHOT_AGE_DAYS}d, snapshot: {timestamp})"
        )

    label = snap_dt.strftime("%Y-%m-%d")
    return html, label


def fetch() -> tuple[str, str]:
    """Waterfall fetch: live site → Wayback Machine.

    Returns (html, source_label).  source_label is "live" or
    "archive (YYYY-MM-DD)" so the digest shows where the data came from.
    """
    # Try the live site first.
    try:
        html = _fetch_live()
        return html, "live"
    except (ConnectionError, OSError):
        pass  # expected — the site is almost always unreachable

    # Fall back to Wayback Machine.
    html, date_label = _fetch_wayback()
    return html, f"archive ({date_label})"


def parse(html: str, prior: dict, families: list[str],
          issue_repo: str) -> tuple[list[Event], dict]:
    """Pure. Returns (events, {area.message: latest version}).

    Writes are a monotone merge: the result starts from `prior` and only ever
    raises a version. No parse can delete a key or lower a value, so a page that
    renders partially degrades to a no-op instead of dropping a message and
    silently re-adopting it later at whatever version it then shows.
    """
    found = MSG_ID.findall(html)
    areas = {area for area, _, _, _ in found}
    if len(found) < MIN_EXPECTED_IDS or len(areas) < MIN_EXPECTED_AREAS:
        raise ParseError(
            f"implausible page: {len(found)} identifiers across {len(areas)} "
            f"business areas, expected at least {MIN_EXPECTED_IDS} across "
            f"{MIN_EXPECTED_AREAS}. Treating as a failed fetch rather than as "
            f"'the versions vanished'."
        )

    seen: dict[str, int] = {}
    for area, number, _variant, version in found:
        key = f"{area}.{number}"
        value = int(version)
        if value > seen.get(key, 0):
            seen[key] = value

    tracked = {f: seen[f] for f in families if f in seen}
    if not tracked:
        raise ParseError("none of the configured messages appeared on the page")

    state = dict(prior)          # monotone: never delete, never lower
    events: list[Event] = []
    for family, version in sorted(tracked.items()):
        was = prior.get(family)
        if was is None:
            state[family] = version      # adopt on first sight, do not announce
            continue
        if version <= was:
            continue                     # unchanged, or the page changed shape
        state[family] = version
        events.append(Event(
            kind="new_version",
            repo=issue_repo,
            key=f"iso:{family}:{version}",
            title=f"ISO 20022: {family} moved to version {version:02d}",
            body=(
                f"`{family}` is now published at version **{version:02d}** "
                f"({issue_repo.split('/')[-1]} was built against {was:02d}).\n\n"
                f"- [ ] read the change description for {family}\n"
                f"- [ ] decide whether the new version needs parser changes\n"
                f"- [ ] add a real message of the new version to the test corpus\n\n"
                f"_Filed by shipwatch._"
            ),
        ))

    return events, state
