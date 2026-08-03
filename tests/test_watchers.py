"""Tests for the pure halves of both watchers.

The bias is deliberate: most of these defend against recording a bad parse as
state, because that failure does not raise, does not log, and permanently stops
all future notifications while continuing to report success.
"""

from __future__ import annotations

import json
import unittest

from shipwatch.core import ParseError
from shipwatch.watchers import iso, tags

FAMILIES = ["camt.053", "pacs.008", "pain.001"]


def iso_page(versions: dict[str, int], filler: int = 120) -> str:
    """A page with the given messages plus enough filler ids, across enough
    business areas, to clear the plausibility floor."""
    parts = [f"{msg}.001.{ver:02d}" for msg, ver in versions.items()]
    areas = ("acmt", "remt", "reda", "semt")
    parts += [f"{areas[i % len(areas)]}.{i:03d}.001.01" for i in range(filler)]
    return "<html>" + " ".join(parts) + "</html>"


def tag_body(names: list[str]) -> str:
    return json.dumps([{"name": n} for n in names])


class TagsWatcher(unittest.TestCase):
    def test_first_sight_adopts_without_announcing(self):
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

    def test_state_is_monotone_a_dropped_tag_is_remembered(self):
        """The API returns one page. A tag falling out of the window must stay in
        state, or it gets announced a second time when it reappears."""
        events, state = tags.parse("r/x", tag_body(["v3.0.0"]), ["v1.0.0", "v2.0.0"])
        self.assertEqual(len(events), 1)
        self.assertEqual(sorted(state), ["v1.0.0", "v2.0.0", "v3.0.0"])

    def test_a_tag_is_never_announced_twice(self):
        _, state = tags.parse("r/x", tag_body(["v2.0.0", "v1.0.0"]), ["v1.0.0"])
        events, _ = tags.parse("r/x", tag_body(["v2.0.0", "v1.0.0"]), state)
        self.assertEqual(events, [])

    def test_quackiso_gets_the_registry_bump_item(self):
        events, _ = tags.parse("t/quackiso", tag_body(["v1.3.0"]), ["v1.2.0"])
        self.assertIn("community-extensions", events[0].body)

    def test_other_repos_do_not_get_it(self):
        events, _ = tags.parse("t/moxy", tag_body(["v0.2.0"]), ["v0.1.0"])
        self.assertNotIn("community-extensions", events[0].body)

    def test_rate_limit_body_raises_instead_of_adopting_empty(self):
        with self.assertRaises(ParseError):
            tags.parse("r/x", json.dumps({"message": "API rate limit exceeded"}), ["v1.0.0"])

    def test_malformed_entry_raises(self):
        with self.assertRaises(ParseError):
            tags.parse("r/x", json.dumps([{"sha": "abc"}]), ["v1.0.0"])

    def test_non_json_raises(self):
        with self.assertRaises(ParseError):
            tags.parse("r/x", "<html>502 Bad Gateway</html>", ["v1.0.0"])


class IsoWatcher(unittest.TestCase):
    def test_first_sight_adopts_without_announcing(self):
        page = iso_page({"camt.053": 8, "pacs.008": 12, "pain.001": 11})
        events, state = iso.parse(page, {}, FAMILIES, "t/q")
        self.assertEqual(events, [])
        self.assertEqual(state, {"camt.053": 8, "pacs.008": 12, "pain.001": 11})

    def test_version_bump_emits_event(self):
        page = iso_page({"camt.053": 9, "pacs.008": 12, "pain.001": 11})
        prior = {"camt.053": 8, "pacs.008": 12, "pain.001": 11}
        events, state = iso.parse(page, prior, FAMILIES, "t/q")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].key, "iso:camt.053:9")
        self.assertEqual(state["camt.053"], 9)

    def test_messages_in_one_business_area_are_tracked_independently(self):
        """camt.003 and camt.053 are unrelated messages. Keying by business area
        would collapse every camt message into one watermark and silence the rest."""
        page = iso_page({"camt.003": 30, "camt.053": 8, "pacs.008": 12, "pain.001": 11})
        prior = {"camt.053": 8, "pacs.008": 12, "pain.001": 11}
        events, state = iso.parse(page, prior, FAMILIES, "t/q")
        self.assertEqual(events, [], "camt.003 at v30 must not affect camt.053")
        self.assertEqual(state["camt.053"], 8)

    def test_three_digit_version_is_parsed(self):
        """A fixed \\d{2} stops matching at v100 and the message vanishes from
        the parse entirely, freezing it silently forever."""
        page = iso_page({"camt.053": 8, "pacs.008": 12, "pain.001": 11}) + " camt.053.001.100 "
        events, state = iso.parse(page, {"camt.053": 8, "pacs.008": 12,
                                         "pain.001": 11}, FAMILIES, "t/q")
        self.assertEqual(state["camt.053"], 100)
        self.assertEqual(len(events), 1)

    def test_state_is_monotone_a_missing_message_keeps_its_version(self):
        """A partially rendered page must not delete a key. Deleting it would
        make the next run adopt it silently and miss any bump in between."""
        page = iso_page({"pacs.008": 12, "pain.001": 11})   # camt.053 absent
        prior = {"camt.053": 8, "pacs.008": 12, "pain.001": 11}
        events, state = iso.parse(page, prior, FAMILIES, "t/q")
        self.assertEqual(events, [])
        self.assertEqual(state["camt.053"], 8, "key must survive a partial page")

    def test_thin_page_raises(self):
        with self.assertRaises(ParseError) as ctx:
            iso.parse("<html>camt.053.001.08</html>", {"camt.053": 8}, FAMILIES, "t/q")
        self.assertIn("implausible page", str(ctx.exception))

    def test_single_area_page_raises_even_when_long(self):
        """A rate-limit notice or a redesign can still be long. Requiring several
        business areas catches a body that is not the catalogue."""
        page = " ".join(f"camt.{i:03d}.001.01" for i in range(150))
        with self.assertRaises(ParseError):
            iso.parse(page, {"camt.053": 8}, FAMILIES, "t/q")

    def test_version_regression_keeps_the_higher_value_silently(self):
        page = iso_page({"camt.053": 7, "pacs.008": 12, "pain.001": 11})
        prior = {"camt.053": 9, "pacs.008": 12, "pain.001": 11}
        events, state = iso.parse(page, prior, FAMILIES, "t/q")
        self.assertEqual(events, [])
        self.assertEqual(state["camt.053"], 9)

    def test_newly_tracked_message_is_adopted_not_announced(self):
        page = iso_page({"camt.053": 8, "pacs.008": 12, "pain.001": 11})
        events, state = iso.parse(page, {"camt.053": 8}, FAMILIES, "t/q")
        self.assertEqual(events, [])
        self.assertEqual(state["pacs.008"], 12)

    def test_page_without_any_tracked_message_raises(self):
        page = " ".join(f"acmt.{i:03d}.001.01" for i in range(60)) + " " + \
               " ".join(f"remt.{i:03d}.001.01" for i in range(60)) + " reda.001.001.01"
        with self.assertRaises(ParseError):
            iso.parse(page, {"camt.053": 8}, FAMILIES, "t/q")

    def test_issue_is_filed_against_the_configured_repo(self):
        page = iso_page({"camt.053": 9, "pacs.008": 12, "pain.001": 11})
        events, _ = iso.parse(page, {"camt.053": 8, "pacs.008": 12, "pain.001": 11},
                              FAMILIES, "tempoloss/quackiso")
        self.assertEqual(events[0].repo, "tempoloss/quackiso")


if __name__ == "__main__":
    unittest.main()
