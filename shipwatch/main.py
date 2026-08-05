"""Runner. One schedule drives every watcher; each decides if it is due.

Two ordering rules, both deliberate:

1. Events are delivered BEFORE state is written, and state is only written when
   every event was accepted by every sink. Delivery is therefore at-least-once:
   a duplicate is harmless and deduplicated by the issue marker, while a lost
   notification is unrecoverable. The reverse order silently drops an event
   whenever a sink fails, and the state file then asserts it succeeded.

2. The heartbeat is the one deliberate exception to the correctness rule. It
   records what HAPPENED, never what was SEEN, so it cannot silence anything,
   and it must be written even when every watcher failed — that is precisely
   when its two jobs are needed.
"""

from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

from .core import Event, ParseError, due, env, env_opt, load_state, now, ping, save_state
from .sinks import issue as issue_sink
from .sinks import telegram as telegram_sink
from .watchers import iso as iso_watcher
from .watchers import tags as tags_watcher

ROOT = Path(__file__).resolve().parent.parent


def load_config() -> dict:
    return json.loads((ROOT / "config.json").read_text(encoding="utf-8"))


def deliver(events: list[Event], gh_token: str, tg_token: str, chat_id: str,
            failures: list[str]) -> tuple[int, int]:
    """Send every event to every sink. Returns (delivered, undelivered).

    A non-zero undelivered count makes the caller skip the state write, so the
    event is retried on the next run rather than being lost.
    """
    delivered = undelivered = 0
    for event in events:
        try:
            url = ""
            if event.repo and not issue_sink.already_filed(event, gh_token):
                url = issue_sink.send(event, gh_token)
            text = f"*{event.title}*" + (f"\n{url}" if url else "")
            telegram_sink.send(tg_token, chat_id, text)
            delivered += 1
        except Exception as exc:  # noqa: BLE001 - one bad event must not stop the rest
            failures.append(f"deliver {event.key}: {exc}")
            undelivered += 1
    return delivered, undelivered


def run_tags(cfg: dict, read_token: str, failures: list[str],
             outcomes: dict) -> tuple[list[Event], dict | None]:
    """Per-repo isolation: one failing repo must not block or reseed the others."""
    prior = load_state("tags")
    merged = dict(prior)
    events: list[Event] = []
    any_ok = False
    for repo in cfg["repos"]:
        try:
            text = tags_watcher.fetch(repo, read_token)
            repo_events, known = tags_watcher.parse(repo, text, prior.get(repo))
        except (ParseError, ConnectionError, OSError) as exc:
            failures.append(f"tags {repo}: {exc}")
            outcomes[repo] = "failed"
            continue  # this repo keeps its previous entry, untouched
        events.extend(repo_events)
        merged[repo] = known
        outcomes[repo] = "ok"
        any_ok = True
    return events, (merged if any_ok else None)


def run_iso(cfg: dict, _read_token: str, failures: list[str],
            outcomes: dict) -> tuple[list[Event], dict | None]:
    prior = load_state("iso")
    try:
        html, source = iso_watcher.fetch()
        events, state = iso_watcher.parse(
            html, prior, cfg["iso_families"], cfg["iso_issue_repo"])
    except (ParseError, ConnectionError, OSError) as exc:
        # Expected regularly: this source rate-limits. Not an alarm.
        failures.append(f"iso: {exc}")
        outcomes["iso20022.org"] = "failed"
        return [], None
    tracked = sum(1 for f in cfg["iso_families"] if f in state)
    outcomes["iso20022.org"] = f"ok, {source} ({tracked}/{len(cfg['iso_families'])} messages found)"
    return events, state


def main() -> int:
    cfg = load_config()

    failures: list[str] = []
    outcomes: dict[str, str] = {}
    last_success = dict(load_state("heartbeat").get("last_success", {}))
    ran: list[str] = []
    delivered_total = 0

    # Configuration is collected WITHOUT raising, which looks lax and is not.
    # Reading it eagerly used to abort main() before the heartbeat below was
    # written, and the heartbeat is both the liveness proof and the thing that
    # resets GitHub's 60-day scheduled-workflow inactivity clock. Losing it on
    # exactly the runs that are broken is how a schedule gets silently disabled
    # - the precise failure this module exists to prevent. A missing secret is
    # still loud: it lands in `failures`, skips every watcher, and exits 1.
    def secret(name: str) -> str:
        try:
            return env(name)
        except RuntimeError as exc:
            failures.append(str(exc))
            return ""

    # Two GitHub tokens, for a reason that is easy to get wrong: GITHUB_TOKEN is
    # scoped to the repository the workflow runs in, so it can READ public repos
    # but CANNOT open an issue in tempoloss/quackiso from tempoloss/shipwatch.
    # Cross-repo issue creation needs a fine-grained PAT with issues:write.
    read_token = secret("GITHUB_TOKEN")
    write_token = secret("GH_PAT")
    tg_token = secret("TELEGRAM_TOKEN")
    chat_id = secret("TELEGRAM_CHAT_ID")
    configured = not failures

    for name, runner in (("tags", run_tags), ("iso", run_iso)):
        # A watcher must not run half-configured: it would fetch, deliver
        # nothing, and leave the event owed with no record of why.
        if not configured:
            break
        if not due(name, cfg["intervals_hours"][name]):
            continue
        ran.append(name)
        events, state = runner(cfg, read_token, failures, outcomes)
        if state is None:
            continue  # nothing succeeded; state deliberately not written
        sent, unsent = deliver(events, write_token, tg_token, chat_id, failures)
        delivered_total += sent
        if unsent:
            # Do not advance state while an event is still owed. It re-fires next
            # run; the issue marker stops a duplicate issue being filed.
            failures.append(f"{name}: {unsent} event(s) undelivered, state not advanced")
            continue
        save_state(name, state)
        last_success[name] = now().isoformat()

    # Written unconditionally. Facts about this run only - no cumulative
    # counters, because those need read-modify-write and would be the one file a
    # lost push corrupts rather than merely stales. The git log is the series.
    save_state("heartbeat", {
        "last_run": now().isoformat(),
        "ran": ran,
        "outcomes": outcomes,
        "delivered": delivered_total,
        "failures": failures,
        "last_success": last_success,
    })

    # Push liveness outward instead of relying on someone noticing an absence.
    # Absence-as-alarm needs exactly the attention this tool exists to replace,
    # and a schedule auto-disabled for inactivity does not self-recover.
    hc = env_opt("HEALTHCHECK_URL")
    if hc:
        try:
            ping(hc)
        except Exception as exc:  # noqa: BLE001
            print(f"healthcheck ping failed: {exc}", file=sys.stderr)

    if "iso" in ran:
        try:
            lines = [f"{k}: {v}" for k, v in sorted(outcomes.items())]
            telegram_sink.send(tg_token, chat_id, (
                "*shipwatch weekly*\n" + "\n".join(lines)
                + f"\ndelivered: {delivered_total}"
                + (f"\nlast failure: {failures[-1]}" if failures else "")
            ))
        except Exception as exc:  # noqa: BLE001
            print(f"digest failed: {exc}", file=sys.stderr)

    for line in failures:
        print(f"FAIL {line}", file=sys.stderr)

    # A watcher failing is normal for the rate-limited source and must not turn
    # the run red, or the failure notification becomes noise and gets muted.
    # Only an unexpected crash fails the job - and a missing secret, which is
    # not a flaky source but a run that cannot ever have worked. It stays red
    # every day until it is fixed, which is the point.
    return 0 if configured else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        raise SystemExit(1)
