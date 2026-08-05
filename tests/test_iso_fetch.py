"""Tests for the ISO 20022 multi-source waterfall fetch.

These cover the fetch-layer logic: waterfall order, Wayback redirect parsing,
staleness guard, and source labelling. The parse tests live in test_watchers.py
and are unchanged — parse() is pure and source-agnostic.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone, timedelta
from unittest import mock

from shipwatch.watchers import iso


def _fake_wayback_response(timestamp: str, body: bytes = b"<html>snapshot</html>"):
    """Return a context-manager mock imitating the Wayback redirect response."""
    url = f"https://web.archive.org/web/{timestamp}id_/https://www.iso20022.org/iso-20022-message-definitions"
    resp = mock.MagicMock()
    resp.read.return_value = body
    resp.url = url
    resp.__enter__ = mock.MagicMock(return_value=resp)
    resp.__exit__ = mock.MagicMock(return_value=False)
    return resp


class FetchWaterfall(unittest.TestCase):
    """fetch() tries live first, then Wayback."""

    @mock.patch("shipwatch.watchers.iso._fetch_live")
    def test_live_success_skips_wayback(self, mock_live):
        mock_live.return_value = "<html>live page</html>"
        html, source = iso.fetch()
        self.assertEqual(source, "live")
        self.assertIn("live page", html)

    @mock.patch("shipwatch.watchers.iso._fetch_wayback")
    @mock.patch("shipwatch.watchers.iso._fetch_live")
    def test_live_failure_falls_through_to_wayback(self, mock_live, mock_wb):
        mock_live.side_effect = ConnectionError("timeout")
        mock_wb.return_value = ("<html>archived</html>", "2026-05-13")
        html, source = iso.fetch()
        self.assertEqual(source, "archive (2026-05-13)")
        self.assertIn("archived", html)

    @mock.patch("shipwatch.watchers.iso._fetch_wayback")
    @mock.patch("shipwatch.watchers.iso._fetch_live")
    def test_both_fail_raises(self, mock_live, mock_wb):
        mock_live.side_effect = ConnectionError("live down")
        mock_wb.side_effect = ConnectionError("wayback down")
        with self.assertRaises(ConnectionError):
            iso.fetch()

    @mock.patch("shipwatch.watchers.iso._fetch_live")
    def test_oserror_from_live_triggers_fallback(self, mock_live):
        """OSError (socket-level) must also trigger the fallback."""
        mock_live.side_effect = OSError("network unreachable")
        with mock.patch("shipwatch.watchers.iso._fetch_wayback") as mock_wb:
            mock_wb.return_value = ("<html>wb</html>", "2026-01-01")
            html, source = iso.fetch()
            self.assertEqual(source, "archive (2026-01-01)")


class WaybackRedirect(unittest.TestCase):
    """_fetch_wayback redirect-URL parsing and staleness guard."""

    def test_fresh_snapshot_succeeds(self):
        now = datetime.now(timezone.utc)
        ts = now.strftime("%Y%m%d%H%M%S")

        with mock.patch("urllib.request.urlopen",
                        return_value=_fake_wayback_response(ts)):
            html, label = iso._fetch_wayback()
            self.assertIn("snapshot", html)
            self.assertEqual(label, now.strftime("%Y-%m-%d"))

    def test_stale_snapshot_raises(self):
        old = datetime.now(timezone.utc) - timedelta(days=200)
        ts = old.strftime("%Y%m%d%H%M%S")

        with mock.patch("urllib.request.urlopen",
                        return_value=_fake_wayback_response(ts)):
            with self.assertRaises(ConnectionError) as ctx:
                iso._fetch_wayback()
            self.assertIn("200 days old", str(ctx.exception))

    def test_network_error_raises(self):
        with mock.patch("urllib.request.urlopen", side_effect=OSError("dns")):
            with self.assertRaises(ConnectionError):
                iso._fetch_wayback()

    def test_missing_timestamp_in_url_raises(self):
        resp = mock.MagicMock()
        resp.read.return_value = b"<html>something</html>"
        resp.url = "https://web.archive.org/web/something-unexpected"
        resp.__enter__ = mock.MagicMock(return_value=resp)
        resp.__exit__ = mock.MagicMock(return_value=False)

        with mock.patch("urllib.request.urlopen", return_value=resp):
            with self.assertRaises(ConnectionError) as ctx:
                iso._fetch_wayback()
            self.assertIn("no timestamp", str(ctx.exception))

    def test_snapshot_exactly_at_threshold_is_accepted(self):
        boundary = datetime.now(timezone.utc) - timedelta(days=180)
        ts = boundary.strftime("%Y%m%d%H%M%S")

        with mock.patch("urllib.request.urlopen",
                        return_value=_fake_wayback_response(ts)):
            html, label = iso._fetch_wayback()
            self.assertIn("snapshot", html)

    def test_snapshot_one_day_over_threshold_is_rejected(self):
        over = datetime.now(timezone.utc) - timedelta(days=181)
        ts = over.strftime("%Y%m%d%H%M%S")

        with mock.patch("urllib.request.urlopen",
                        return_value=_fake_wayback_response(ts)):
            with self.assertRaises(ConnectionError) as ctx:
                iso._fetch_wayback()
            self.assertIn("181 days old", str(ctx.exception))


class SourceInOutcome(unittest.TestCase):
    """run_iso reports the fetch source in the outcome string."""

    @mock.patch("shipwatch.watchers.iso.fetch")
    @mock.patch("shipwatch.core.load_state", return_value={})
    def test_outcome_includes_source_label(self, _load, mock_fetch):
        from tests.test_watchers import iso_page
        page = iso_page({"camt.053": 8, "pacs.008": 12, "pain.001": 11})
        mock_fetch.return_value = (page, "archive (2026-05-13)")

        from shipwatch.main import run_iso
        cfg = {
            "iso_families": ["camt.053", "pacs.008", "pain.001"],
            "iso_issue_repo": "t/q",
        }
        failures: list[str] = []
        outcomes: dict = {}
        run_iso(cfg, "", failures, outcomes)
        self.assertIn("archive (2026-05-13)", outcomes["iso20022.org"])
        self.assertIn("3/3", outcomes["iso20022.org"])


if __name__ == "__main__":
    unittest.main()
