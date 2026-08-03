"""Watch ISO 20022 for new message-definition versions.

quackiso implements a fixed set of message families. ISO publishes maintenance
releases annually and versions increment, so a family moving from .08 to .09 is
real work arriving.

The source rate-limits: measured 2026-08-03, it answered once in 1.7s with
1,490,514 bytes and 122 identifiers, then timed out on three consecutive
retries. Everything here assumes the fetch usually fails.
"""

from __future__ import annotations

import re

from ..core import Event, ParseError, http_get

URL = "https://www.iso20022.org/iso-20022-message-definitions"

# e.g. camt.053.001.08 -> family camt.053, version 8
MSG_ID = re.compile(r"\b(pacs|pain|camt|acmt|remt)\.(\d{3})\.(\d{3})\.(\d{2})\b")

# A page redesign, an error page, or a partial response would yield very few
# identifiers. Treating that as "every version disappeared" would rewrite state
# to nonsense and permanently silence the watcher, so demand a floor. The real
# page carried 122 on the day this was written.
MIN_EXPECTED_IDS = 50


def fetch() -> str:
    return http_get(URL)


def parse(html: str, prior: dict, families: list[str],
          issue_repo: str) -> tuple[list[Event], dict]:
    """Pure. Returns (events, {family: latest version int}).

    Only tracks the families passed in. An empty `prior` seeds silently.
    """
    found = MSG_ID.findall(html)
    if len(found) < MIN_EXPECTED_IDS:
        raise ParseError(
            f"only {len(found)} message identifiers found, expected at least "
            f"{MIN_EXPECTED_IDS} — treating as a failed fetch rather than as "
            f"'the versions vanished'"
        )

    latest: dict[str, int] = {}
    for domain, number, _variant, version in found:
        family = f"{domain}.{number}"
        value = int(version)
        if value > latest.get(family, 0):
            latest[family] = value

    wanted = {f: latest[f] for f in families if f in latest}
    if not wanted:
        raise ParseError("none of the configured families appeared on the page")

    if not prior:
        return [], wanted

    events = []
    for family, version in sorted(wanted.items()):
        was = prior.get(family)
        if was is None:
            # Newly tracked family: record it, do not announce it as a change.
            continue
        if version > was:
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
        # A version going backwards means the page changed shape, not that ISO
        # withdrew a release. Keep the higher number we already trust.
        elif version < was:
            wanted[family] = was

    return events, wanted
