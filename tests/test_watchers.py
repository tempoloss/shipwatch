"""Tests for the pure halves of both watchers.

The bias here is deliberate: most of these defend against silently recording a
bad parse as state, because that failure mode does not raise, does not log, and
permanently stops all future notifications while looking healthy.
"""

from __future__ import annotations

import json
import unittest

from shipwatch.core import ParseError
from shipwatch.watchers import iso, tags

FAMILIES = ["camt.053", "pacs.008", "pain.001"]


def iso_page(versions: dict[str, int], filler: int = 60) -> str:
    """Build a page containing the given families plus enough filler ids to
    clear MIN_EXPECTED_IDS. Filler uses domains the config never tracks."""
    parts = [f"{fam}.001.{ver:02d}" for fam, ver in versions.items()]
    parts += [f"acmt.{i:03d}.001.01" for i in range(filler)]
    return "<html>" + " ".join(parts) + "</html>"


def tag_body(names: list[str]) -> str:
    return json.dumps([{"name": n} for n in names])


class TagsWatcher(unittest.TestCase):
    def test_first_sight_seeds_without_announcing(self):
        events, state = tags.parse("r/x", tag_body(["v1.0.0", "v0.9.0"]), None)
        self.assertEqual(events, [])
        self.assertEqual(state, ["v1.0.0", "v0.9.0"])

    def test_new_tag_emits_one_event(self):
        events, state = tags.parse("r/x", tag_body(["v2.0.0", "v1.0.0"]), ["v1.0.0"])
        self.assertEqual(len(events), 1)
        self.assertIn("v2.0.0", events[0].title)
        self.assertEqual(events[0].key, "tag:r/x:v2.0.0")
        self.assertIn("v2.0.0", state)

    def test_no_change_emits_nothing(self):
        events, _ = tags.parse("r/x", tag_body(["v1.0.0"]), ["v1.0.0"])
        self.assertEqual(events, [])

    def test_quackiso_gets_the_registry_bump_item(self):
        events, _ = tags.parse("t/quackiso", tag_body(["v1.3.0"]), ["v1.2.0"])
        self.assertIn("community-extensions", events[0].body)

    def test_other_repos_do_not_get_it(self):
        events, _ = tags.parse("t/moxy", tag_body(["v0.2.0"]), ["v0.1.0"])
        self.assertNotIn("community-extensions", events[0].body)

    def test_rate_limit_body_raises_instead_of_seeding_empty(self):
        """A GitHub error body is a dict. Accepting it as "no tags" would erase
        state and re-announce every tag on the next successful run."""
        body = json.dumps({"message": "API rate limit exceeded"})
        with self.assertRaises(ParseError):
            tags.parse("r/x", body, ["v1.0.0"])

    def test_malformed_entry_raises(self):
        with self.assertRaises(ParseError):
            tags.parse("r/x", json.dumps([{"sha": "abc"}]), ["v1.0.0"])

    def test_non_json_raises(self):
        with self.assertRaises(ParseError):
            tags.parse("r/x", "<html>502 Bad Gateway</html>", ["v1.0.0"])


class IsoWatcher(unittest.TestCase):
    def test_first_sight_seeds_without_announcing(self):
        page = iso_page({"camt.053": 8, "pacs.008": 12, "pain.001": 11})
        events, state = iso.parse(page, {}, FAMILIES, "t/quackiso")
        self.assertEqual(events, [])
        self.assertEqual(state, {"camt.053": 8, "pacs.008": 12, "pain.001": 11})

    def test_version_bump_emits_event(self):
        page = iso_page({"camt.053": 9, "pacs.008": 12, "pain.001": 11})
        prior = {"camt.053": 8, "pacs.008": 12, "pain.001": 11}
        events, state = iso.parse(page, prior, FAMILIES, "t/quackiso")
        self.assertEqual(len(events), 1)
        self.assertIn("camt.053", events[0].title)
        self.assertEqual(events[0].key, "iso:camt.053:9")
        self.assertEqual(state["camt.053"], 9)

    def test_highest_version_on_page_wins(self):
        page = iso_page({"camt.053": 8}) + " camt.053.001.09 camt.053.001.07 "
        events, state = iso.parse(page, {"camt.053": 8}, ["camt.053"], "t/q")
        self.assertEqual(state["camt.053"], 9)
        self.assertEqual(len(events), 1)

    def test_thin_page_raises_rather_than_wiping_state(self):
        """A redesign or error page yields few ids. Recording that would mark
        every family as unseen-but-current and silence the watcher forever."""
        with self.assertRaises(ParseError) as ctx:
            iso.parse("<html>camt.053.001.08</html>", {"camt.053": 8},
                      FAMILIES, "t/q")
        self.assertIn("expected at least", str(ctx.exception))

    def test_version_regression_keeps_the_higher_number_and_is_silent(self):
        """Versions do not go backwards in reality, so a lower number means the
        page changed shape. Trust the recorded value."""
        page = iso_page({"camt.053": 7, "pacs.008": 12, "pain.001": 11})
        prior = {"camt.053": 9, "pacs.008": 12, "pain.001": 11}
        events, state = iso.parse(page, prior, FAMILIES, "t/quackiso")
        self.assertEqual(events, [])
        self.assertEqual(state["camt.053"], 9)

    def test_newly_tracked_family_is_recorded_not_announced(self):
        page = iso_page({"camt.053": 8, "pacs.008": 12, "pain.001": 11})
        events, state = iso.parse(page, {"camt.053": 8}, FAMILIES, "t/q")
        self.assertEqual(events, [])
        self.assertEqual(state["pacs.008"], 12)

    def test_page_without_any_tracked_family_raises(self):
        page = " ".join(f"acmt.{i:03d}.001.01" for i in range(60))
        with self.assertRaises(ParseError):
            iso.parse(page, {"camt.053": 8}, FAMILIES, "t/q")

    def test_issue_is_filed_against_the_configured_repo(self):
        page = iso_page({"camt.053": 9, "pacs.008": 12, "pain.001": 11})
        events, _ = iso.parse(page, {"camt.053": 8, "pacs.008": 12,
                                     "pain.001": 11}, FAMILIES, "tempoloss/quackiso")
        self.assertEqual(events[0].repo, "tempoloss/quackiso")


if __name__ == "__main__":
    unittest.main()
