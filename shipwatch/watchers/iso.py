"""Watch ISO 20022 for new message-definition versions.

quackiso implements a fixed set of messages. ISO publishes maintenance releases
annually and versions increment, so a message moving from .08 to .09 is real
work arriving.

The source rate-limits: measured 2026-08-03 it answered once in 1.7s with
1,490,514 bytes and 122 identifiers, then timed out on three consecutive
retries. Everything here assumes the fetch usually fails.
"""

from __future__ import annotations

import re

from ..core import Event, ParseError, http_get

URL = "https://www.iso20022.org/iso-20022-message-definitions"

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


def fetch() -> str:
    # One attempt. The poll is weekly against a source that publishes annually,
    # so retrying inside a run buys nothing that waiting until next week gives
    # for free, and it recreates the request burst that caused the rate limiting.
    return http_get(URL, retries=1, timeout=30)


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
                f"(quackiso was built against {was:02d}).\n\n"
                f"- [ ] read the change description for {family}\n"
                f"- [ ] decide whether the new version needs parser changes\n"
                f"- [ ] add a real message of the new version to the test corpus\n\n"
                f"_Filed by shipwatch._"
            ),
        ))

    return events, state
