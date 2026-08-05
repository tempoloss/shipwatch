"""Runner tests, no network.

These defend one rule that the watcher tests cannot reach: the heartbeat is
written on EVERY run, including runs that were misconfigured from the start.

That rule is load-bearing rather than tidy. The heartbeat commit is the only
liveness proof and it is what resets GitHub's 60-day scheduled-workflow
inactivity clock, so a run that dies before writing it loses both - and a
schedule disabled for inactivity does not self-recover. The original code read
its four secrets eagerly at the top of `main`, which meant one absent secret
raised before the heartbeat existed, `state/` was never created, and the
`if: always()` commit step then died on `git add state/` with a missing
pathspec. The mechanism written to survive a crash was removed by the crash.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from shipwatch import core, main as runner

SECRETS = ("GITHUB_TOKEN", "GH_PAT", "TELEGRAM_TOKEN", "TELEGRAM_CHAT_ID")


class RunnerHeartbeat(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name)
        patcher = mock.patch.object(core, "STATE_DIR", self.state)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)

    def heartbeat(self) -> dict:
        path = self.state / "heartbeat.json"
        self.assertTrue(path.exists(), "heartbeat was not written")
        return json.loads(path.read_text(encoding="utf-8"))

    def test_missing_secret_still_writes_the_heartbeat(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            code = runner.main()
        self.assertEqual(code, 1, "a run that cannot work must be red")
        hb = self.heartbeat()
        self.assertEqual(hb["ran"], [], "no watcher may run half-configured")
        self.assertTrue(hb["last_run"])

    def test_every_missing_secret_is_named_not_just_the_first(self):
        # Naming one at a time turns a single fix into four red runs.
        with mock.patch.dict(os.environ, {}, clear=True):
            runner.main()
        reported = " ".join(self.heartbeat()["failures"])
        for name in SECRETS:
            self.assertIn(name, reported)

    def test_one_missing_secret_is_enough_to_stop_the_run(self):
        env = {k: "x" for k in SECRETS}
        del env["GH_PAT"]
        with mock.patch.dict(os.environ, env, clear=True):
            code = runner.main()
        self.assertEqual(code, 1)
        hb = self.heartbeat()
        self.assertEqual(hb["ran"], [])
        self.assertIn("GH_PAT", " ".join(hb["failures"]))

    def test_a_blank_secret_counts_as_missing(self):
        # An empty repository secret is easy to create by pasting nothing, and
        # would otherwise reach the sinks as a token and fail as a 401.
        env = {k: "x" for k in SECRETS}
        env["GH_PAT"] = "   "
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(runner.main(), 1)
        self.assertIn("GH_PAT", " ".join(self.heartbeat()["failures"]))

    def test_configured_run_with_nothing_due_is_green_and_touches_no_network(self):
        # Proves the guard did not break the ordinary path: every watcher has
        # run inside its interval, so the loop does nothing, the heartbeat is
        # still rewritten, and no fetch happens.
        core.save_state("heartbeat", {
            "last_success": {"tags": core.now().isoformat(),
                             "iso": core.now().isoformat()},
        })
        with mock.patch.dict(os.environ, {k: "x" for k in SECRETS}, clear=True), \
             mock.patch.object(core, "http_get",
                               side_effect=AssertionError("no network expected")):
            code = runner.main()
        self.assertEqual(code, 0)
        hb = self.heartbeat()
        self.assertEqual(hb["ran"], [])
        self.assertEqual(hb["failures"], [])

    def test_prior_successes_survive_a_misconfigured_run(self):
        # The heartbeat carries last_success, which drives `due`. Blanking it on
        # a broken run would make every watcher due again and re-fire work.
        stamp = core.now().isoformat()
        core.save_state("heartbeat", {"last_success": {"tags": stamp}})
        with mock.patch.dict(os.environ, {}, clear=True):
            runner.main()
        self.assertEqual(self.heartbeat()["last_success"]["tags"], stamp)


if __name__ == "__main__":
    unittest.main()
