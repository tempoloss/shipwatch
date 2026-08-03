"""Runner. One schedule drives every watcher; each decides if it is due.

Ordering rule, and it is deliberate: events are delivered BEFORE state is
written. If delivery fails, state stays unchanged and the event is retried on
the next run — duplicates are prevented by the issue marker and are harmless on
Telegram. The reverse order would lose a notification permanently whenever
delivery failed, and a lost notification is unrecoverable.
"""

from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

from .core import Event, ParseError, due, env, load_state, now, save_state
from .sinks import issue as issue_sink
from .sinks import telegram as telegram_sink
from .watchers import iso as iso_watcher
from .watchers import tags as tags_watcher

ROOT = Path(__file__).resolve().parent.parent


def load_config() -> dict:
    return json.loads((ROOT / "config.json").read_text(encoding="utf-8"))


def deliver(events: list[Event], gh_token: str, tg_token: str, chat_id: str,
            failures: list[str]) -> int:
    """Send every event to both sinks. Returns how many were delivered."""
    delivered = 0
    for event in events:
        try:
            url = ""
            if event.repo and not issue_sink.already_filed(event, gh_token):
                url = issue_sink.send(event, gh_token)
            text = f"*{event.title}*"
            if url:
                text += f"\n{url}"
            telegram_sink.send(tg_token, chat_id, text)
            delivered += 1
        except Exception as exc:  # noqa: BLE001 - one bad event must not stop the rest
            failures.append(f"deliver {event.key}: {exc}")
    return delivered


def run_tags(cfg: dict, gh_token: str, failures: list[str]) -> tuple[list[Event], dict | None]:
    """Per-repo isolation: one failing repo must not block or reseed the others."""
    prior = load_state("tags")
    merged = dict(prior)
    events: list[Event] = []
    any_ok = False
    for repo in cfg["repos"]:
        try:
            text = tags_watcher.fetch(repo, gh_token)
            repo_events, current = tags_watcher.parse(repo, text, prior.get(repo))
        except (ParseError, ConnectionError, OSError) as exc:
            failures.append(f"tags {repo}: {exc}")
            continue  # leave this repo's prior state untouched
        events.extend(repo_events)
        merged[repo] = current
        any_ok = True
    return events, (merged if any_ok else None)


def run_iso(cfg: dict, failures: list[str]) -> tuple[list[Event], dict | None]:
    prior = load_state("iso")
    try:
        html = iso_watcher.fetch()
        events, state = iso_watcher.parse(
            html, prior, cfg["iso_families"], cfg["iso_issue_repo"])
    except (ParseError, ConnectionError, OSError) as exc:
        # Expected regularly: this source rate-limits. Not an alarm.
        failures.append(f"iso: {exc}")
        return [], None
    return events, state


def main() -> int:
    cfg = load_config()
    # Two GitHub tokens, for a reason that is easy to get wrong: GITHUB_TOKEN is
    # scoped to the repository the workflow runs in, so it can READ public repos
    # but CANNOT open an issue in tempoloss/quackiso from tempoloss/shipwatch.
    # Cross-repo issue creation needs a fine-grained PAT with issues:write on the
    # watched repos.
    read_token = env("GITHUB_TOKEN")
    write_token = env("GH_PAT")
    tg_token = env("TELEGRAM_TOKEN")
    chat_id = env("TELEGRAM_CHAT_ID")

    failures: list[str] = []
    heartbeat = load_state("heartbeat")
    last_success = dict(heartbeat.get("last_success", {}))
    ran: list[str] = []
    delivered_total = 0

    for name, runner in (("tags", run_tags), ("iso", run_iso)):
        interval = cfg["intervals_hours"][name]
        if not due(name, interval):
            continue
        ran.append(name)
        args = (cfg, read_token, failures) if name == "tags" else (cfg, failures)
        events, state = runner(*args)
        if state is None:
            continue  # nothing succeeded; state deliberately not written
        delivered_total += deliver(events, write_token, tg_token, chat_id, failures)
        save_state(name, state)
        last_success[name] = now().isoformat()

    # The heartbeat is written unconditionally. It proves the runner is alive
    # even when every watcher failed, and its commit is repository activity.
    counters = heartbeat.get("counters", {"runs": 0, "delivered": 0, "failures": 0})
    counters["runs"] = counters.get("runs", 0) + 1
    counters["delivered"] = counters.get("delivered", 0) + delivered_total
    counters["failures"] = counters.get("failures", 0) + len(failures)
    save_state("heartbeat", {
        "last_run": now().isoformat(),
        "last_success": last_success,
        "ran": ran,
        "last_failures": failures[-10:],
        "counters": counters,
    })

    # Weekly digest rides the iso cadence. Silence then means broken, not quiet.
    if "iso" in ran:
        try:
            telegram_sink.send(tg_token, chat_id, (
                f"*shipwatch digest*\n"
                f"runs: {counters['runs']}\n"
                f"delivered: {counters['delivered']}\n"
                f"failures: {counters['failures']}\n"
                f"this run: {', '.join(ran)}"
                + (f"\nrecent: {failures[-1]}" if failures else "")
            ))
        except Exception as exc:  # noqa: BLE001
            print(f"digest failed: {exc}", file=sys.stderr)

    for line in failures:
        print(f"FAIL {line}", file=sys.stderr)

    # A watcher failing is normal for the rate-limited source and must not turn
    # the run red, or the failure notification becomes noise and gets muted.
    # Only an unexpected crash (below, in __main__) should fail the job.
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        raise SystemExit(1)
