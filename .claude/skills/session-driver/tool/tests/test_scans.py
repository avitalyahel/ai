"""Run: python3 -I ~/Claude/sessions-watch/tests/test_scans.py

The Scans Hub (design section 3), on temp folders, without GitHub or the app:
- The refresh goes to the Scans Hub only while its feed is alive, and only one refresh waits at a
  time.
- A refresh the hub has not finished for 15 minutes goes back to the CLI session (exit 3).
- While the hub has a refresh, sd does not run collect (scans-busy).
- The two refresh prompts are cut from prompt.md: the classifier has no publish step, the publisher
  no transcript step.
- Events of the Scans Hub never reach the Trackers Hub, and the other way round. Event ids are
  unique.
- The hub status line shows the Scans Hub only when it is in use.
- A scan is queued when a scheduled time passes: first sight only recorded, one event at a time, no
  run at midnight.
- A scan with a missing prompt file is retried until the file is there, and shows on the input
  check line.
- Scan entries in config.json are checked, and bad ones are ignored.
- A report line is kept per scan or refresh, cut to one clean line, and trimmed after 14 days.
- A failed run closes its event, writes a FAILED line and notifies you, and the next time queues
  the scan again.
- A down Scans Hub, or a stuck event of it, notifies you once.
- The hooks of the subagents allow only the fixed commands and files, and block on any error.
- scans-setup writes the subagent definitions, only from the sd folder, never over a file that sd
  did not write.
- The hub's wait counts from its oldest waiting event or its last acknowledgement, so a refresh
  behind a long scan is not given up early, and a hub stuck on a scan is.
- A failed refresh sends the next round to the CLI session once. Notices of failed runs come at
  most every 30 minutes.
- A scan cannot be named refresh.
- A hook command exits 2 when watch.py crashes before it can answer, for example on a broken
  config.json.
- Notices keep their line in the round log, and a notice with a backslash in it is still shown.
- The commands take their arguments as documented, and main() calls the right command with them.
- The sd loop starts no CLI session when the Scans Hub takes the refresh, and starts one when it
  does not.
"""

import contextlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock
from datetime import (
    datetime,
    timedelta,
    timezone,
)

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("watch", os.path.join(HERE, "..", "watch.py"))
watch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watch)

PATHS = (
    "HUB_DIR",
    "HUB_QUEUE",
    "HUB_DELIVERED",
    "HUB_LOCK",
    "HUB_HEARTBEAT",
    "SCANS_HEARTBEAT",
    "SCANS_DIR",
    "SCANS_SLOTS",
    "AGENTS_DIR",
    "CONFIG",
    "DIR",
    "notify",
)


def read_text(path: str) -> str:
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def write_text(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)


def moment(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(
        year=2026,
        month=11,
        day=day,
        hour=hour,
        minute=minute,
    )


class TempHub(unittest.TestCase):
    """The hub files in a temp folder."""

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp()
        self.saved = {name: getattr(watch, name) for name in PATHS}
        watch.HUB_DIR = self.tmp
        watch.HUB_QUEUE = os.path.join(self.tmp, "queue.jsonl")
        watch.HUB_DELIVERED = os.path.join(self.tmp, "delivered.log")
        watch.HUB_LOCK = os.path.join(self.tmp, ".lock")
        watch.HUB_HEARTBEAT = os.path.join(self.tmp, "heartbeat")
        watch.SCANS_HEARTBEAT = os.path.join(self.tmp, "heartbeat-scans")
        watch.SCANS_DIR = os.path.join(self.tmp, "scans")
        watch.SCANS_SLOTS = os.path.join(watch.SCANS_DIR, "slots.json")
        watch.AGENTS_DIR = os.path.join(self.tmp, "agents")
        watch.CONFIG = {}
        self.notices: list = []
        watch.notify = lambda title, text: self.notices.append(text)
        self.addCleanup(self.restore)

    def restore(self) -> None:
        for name, value in self.saved.items():
            setattr(watch, name, value)
        shutil.rmtree(self.tmp, True)

    def beat(self, seconds_ago: int = 0) -> None:
        with open(watch.SCANS_HEARTBEAT, "a"):
            pass
        stamp = time.time() - seconds_ago
        os.utime(watch.SCANS_HEARTBEAT, (stamp, stamp))

    def queue_event(
        self,
        event_id: str,
        kind: str,
        to: str,
        minutes_ago: int = 0,
        title: str = "t",
    ) -> None:
        queued = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
        event = {
            "id": event_id,
            "at": queued.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "to": to,
            "kind": kind,
            "title": title,
            "message": "m",
        }
        with open(watch.HUB_QUEUE, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(event) + "\n")

    def queue_scan(self, event_id: str, minutes_ago: int = 0, title: str = "t") -> None:
        self.queue_event(
            event_id=event_id,
            kind="scan",
            to=watch.SCANS_TO,
            minutes_ago=minutes_ago,
            title=title,
        )

    def queue_refresh_event(self, event_id: str, minutes_ago: int = 0, title: str = "t") -> None:
        self.queue_event(
            event_id=event_id,
            kind="refresh",
            to=watch.SCANS_TO,
            minutes_ago=minutes_ago,
            title=title,
        )

    def queue_session_event(self, event_id: str, minutes_ago: int = 0, title: str = "t") -> None:
        self.queue_event(
            event_id=event_id,
            kind="pr",
            to="local_abc",
            minutes_ago=minutes_ago,
            title=title,
        )

    def capture(self, function, *args) -> tuple:
        """Run a command and return its result and what it printed."""
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = function(*args)
        return result, output.getvalue()

    def with_stdin(self, text: str, function, *args) -> tuple:
        with mock.patch("sys.stdin", io.StringIO(text)):
            return self.capture(function, *args)


class RefreshQueue(TempHub):
    def queue_refresh(self, changed: str = "4") -> tuple:
        return self.capture(watch.cmd_scans_queue, ["w", "scans-queue", "refresh", changed])

    def test_never_started_uses_the_cli_session(self) -> None:
        result, _ = self.queue_refresh()
        self.assertEqual(result, 3)
        self.assertEqual(watch.hub_pending("scans"), [])

    def test_quiet_for_3_minutes_uses_the_cli_session(self) -> None:
        self.beat(seconds_ago=180)
        result, _ = self.queue_refresh()
        self.assertEqual(result, 3)

    def test_running_queues_one_refresh_with_the_instruction_paths(self) -> None:
        self.beat(seconds_ago=5)
        result, _ = self.queue_refresh()
        events = watch.hub_pending("scans")
        self.assertEqual(result, 0)
        kinds = [(event["kind"], event["to"]) for event in events]
        self.assertEqual(kinds, [("refresh", watch.SCANS_TO)])
        self.assertIn("4 changed session(s)", events[0]["message"])
        for part in ("classify", "publish"):
            path = os.path.join(watch.SCANS_DIR, f"refresh-{part}.md")
            self.assertIn(path, events[0]["message"])

    def test_the_classify_prompt_has_no_publish_step(self) -> None:
        self.beat()
        self.queue_refresh()
        classify = read_text(os.path.join(watch.SCANS_DIR, "refresh-classify.md"))
        self.assertIn("todo.json", classify)
        self.assertNotIn("watch.py apply", classify)
        self.assertNotIn("watch.py collect", classify)
        self.assertNotIn("{{DIR}}", classify)
        self.assertIn(watch.DIR, classify)

    def test_the_publish_prompt_has_no_transcript_step(self) -> None:
        self.beat()
        self.queue_refresh()
        publish = read_text(os.path.join(watch.SCANS_DIR, "refresh-publish.md"))
        self.assertIn("watch.py apply", publish)
        self.assertNotIn("todo.json", publish)
        self.assertNotIn("watch.py collect", publish)
        self.assertNotIn("{{DIR}}", publish)

    def test_a_waiting_scan_does_not_stop_the_refresh(self) -> None:
        self.beat()
        self.queue_scan("scan-1", minutes_ago=3)
        result, _ = self.queue_refresh()
        self.assertEqual(result, 0)
        kinds = sorted(event["kind"] for event in watch.hub_pending("scans"))
        self.assertEqual(kinds, ["refresh", "scan"])

    def test_a_second_round_does_not_queue_another(self) -> None:
        self.beat()
        self.queue_refresh()
        result, out = self.queue_refresh("6")
        self.assertEqual(result, 0)
        self.assertIn("already queued", out)
        self.assertEqual(len(watch.hub_pending("scans")), 1)

    def test_a_refresh_not_finished_for_15_minutes_goes_to_the_cli_session(self) -> None:
        self.beat()
        self.queue_refresh_event("old-refresh", minutes_ago=20)
        result, out = self.queue_refresh()
        self.assertEqual(result, 3)
        self.assertIn("has not finished its events for 20 min", out)

    def test_bad_arguments_are_refused(self) -> None:
        self.assertEqual(self.capture(watch.cmd_scans_queue, ["w", "scans-queue"])[0], 1)
        self.assertEqual(self.capture(watch.cmd_scans_queue, ["w", "scans-queue", "scan"])[0], 1)


class ScansBusy(TempHub):
    def test_busy_while_a_recent_refresh_waits_and_the_hub_runs(self) -> None:
        self.beat()
        self.queue_refresh_event("refresh-1", minutes_ago=3)
        self.assertEqual(watch.cmd_scans_busy(), 0)

    def test_not_busy_without_a_waiting_refresh(self) -> None:
        self.beat()
        self.assertEqual(watch.cmd_scans_busy(), 1)

    def test_not_busy_when_only_a_scan_waits(self) -> None:
        self.beat()
        self.queue_scan("scan-1", minutes_ago=3)
        self.assertEqual(watch.cmd_scans_busy(), 1)

    def test_not_busy_when_the_hub_is_not_running(self) -> None:
        self.queue_refresh_event("refresh-1", minutes_ago=3)
        self.assertEqual(watch.cmd_scans_busy(), 1)

    def test_not_busy_after_15_minutes_so_the_cli_session_takes_over(self) -> None:
        self.beat()
        self.queue_refresh_event("refresh-1", minutes_ago=16)
        self.assertEqual(watch.cmd_scans_busy(), 1)


class HubRoles(TempHub):
    def test_each_hub_sees_only_its_own_events(self) -> None:
        self.queue_session_event("to-session")
        self.queue_scan("to-scans")
        self.assertEqual([event["id"] for event in watch.hub_pending()], ["to-session"])
        self.assertEqual([event["id"] for event in watch.hub_pending("scans")], ["to-scans"])

    def test_events_queued_in_the_same_moment_get_different_ids(self) -> None:
        ids = {
            watch.hub_enqueue(
                sid="scans",
                kind="scan",
                title="t",
                msg="m",
                to=watch.SCANS_TO,
            )[0]
            for _ in range(20)
        }
        self.assertEqual(len(ids), 20)

    def test_the_status_line_has_no_scans_hub_when_it_is_not_in_use(self) -> None:
        self.assertNotIn("Scans Hub", watch.hub_line_text())

    def test_the_status_line_shows_the_scans_hub_when_it_is_in_use(self) -> None:
        self.beat(seconds_ago=5)
        self.queue_scan("to-scans")
        self.assertIn("Scans Hub: alive, 1 event waiting.", watch.hub_line_text())

    def test_hub_status_lists_both_hubs(self) -> None:
        self.beat()
        _, out = self.capture(watch.cmd_hub_status)
        self.assertIn("Trackers Hub: never started", out)
        self.assertRegex(out, r"Scans Hub: alive \(heartbeat \d+ s ago\)")


class ScanTimes(TempHub):
    PROMPT = "prompt.md"

    def config(self, times: tuple = ("09:00", "13:00", "17:00")) -> None:
        path = os.path.join(self.tmp, self.PROMPT)
        write_text(path, "Archive mail.")
        watch.CONFIG = {
            "scans": [
                {
                    "name": "inbox",
                    "times": list(times),
                    "prompt": path,
                    "agent": "sw-scan-gmail",
                },
            ],
        }

    def due(self, hour: int, minute: int = 0, day: int = 9) -> str:
        return self.capture(watch.cmd_scans_due, moment(day, hour, minute))[1]

    def test_first_sight_only_records(self) -> None:
        self.config()
        self.assertIn("first seen, recorded", self.due(10))
        self.assertEqual(watch.hub_pending("scans"), [])

    def test_nothing_happens_inside_the_same_slot(self) -> None:
        self.config()
        self.due(10)
        self.assertEqual(self.due(11), "")

    def test_a_new_time_queues_one_event_with_the_agent_and_the_file(self) -> None:
        self.config()
        self.due(10)
        self.assertIn("queued for the Scans Hub", self.due(13, 5))
        events = watch.hub_pending("scans")
        kinds = [(event["kind"], event["title"]) for event in events]
        self.assertEqual(kinds, [("scan", "inbox")])
        self.assertIn("sw-scan-gmail", events[0]["message"])
        self.assertIn(os.path.join(self.tmp, self.PROMPT), events[0]["message"])

    def test_one_event_waits_at_a_time(self) -> None:
        self.config()
        self.due(10)
        self.due(13, 5)
        self.assertIn("already waiting", self.due(17, 5))
        self.assertEqual(len(watch.hub_pending("scans")), 1)

    def test_two_scans_due_in_one_round_are_both_queued(self) -> None:
        self.config()
        second = dict(watch.CONFIG["scans"][0], name="other")
        watch.CONFIG["scans"].append(second)
        self.due(10)
        self.due(13, 5)
        events = watch.hub_pending("scans")
        self.assertEqual(sorted(event["title"] for event in events), ["inbox", "other"])
        self.assertEqual(len({event["id"] for event in events}), 2)

    def test_no_run_at_midnight(self) -> None:
        self.config()
        self.due(23, 50)
        self.assertEqual(self.due(0, 5, day=10), "")
        self.assertEqual(watch.hub_pending("scans"), [])

    def test_the_first_time_of_the_next_day_queues_the_scan(self) -> None:
        self.config()
        self.due(23, 50)
        self.assertIn("queued", self.due(9, 5, day=10))

    def test_a_missing_prompt_file_is_named_every_round(self) -> None:
        self.config()
        os.remove(os.path.join(self.tmp, self.PROMPT))
        self.due(10)
        self.assertIn("the prompt file is missing", self.due(13, 5))
        self.assertIn("the prompt file is missing", self.due(13, 20))

    def test_the_scan_is_queued_as_soon_as_the_missing_file_is_there(self) -> None:
        self.config()
        path = os.path.join(self.tmp, self.PROMPT)
        os.remove(path)
        self.due(10)
        self.due(13, 5)
        write_text(path, "Archive mail.")
        self.assertIn("queued for the Scans Hub", self.due(13, 35))

    def test_a_scan_that_finished_is_not_queued_again_in_the_same_slot(self) -> None:
        self.config()
        self.due(10)
        self.due(13, 5)
        self.capture(watch.cmd_hub_ack, watch.hub_pending("scans")[0]["id"])
        self.assertEqual(self.due(13, 20), "")
        self.assertEqual(watch.hub_pending("scans"), [])

    def test_a_relative_prompt_path_starts_in_the_sw_folder(self) -> None:
        saved = watch.DIR
        self.addCleanup(setattr, watch, "DIR", saved)
        watch.DIR = os.path.join(self.tmp, "sd")
        scan = {
            "name": "inbox",
            "times": ["09:00"],
            "prompt": "scans/x.md",
            "agent": "a",
        }
        expected = os.path.join(self.tmp, "sd", "scans", "x.md")
        self.assertEqual(watch.scan_prompt_path(scan), expected)

    def test_a_tilde_in_the_prompt_path_is_expanded(self) -> None:
        scan = {
            "name": "inbox",
            "times": ["09:00"],
            "prompt": "~/scans/x.md",
            "agent": "a",
        }
        self.assertEqual(watch.scan_prompt_path(scan), os.path.expanduser("~/scans/x.md"))


class ScanEntries(TempHub):
    GOOD = {
        "name": "inbox",
        "times": ["09:00"],
        "prompt": "p.md",
        "agent": "sw-scan-gmail",
    }

    def test_a_good_entry_has_no_error(self) -> None:
        self.assertIsNone(watch.scan_error(self.GOOD))

    def test_bad_entries_name_the_wrong_part(self) -> None:
        cases = [
            ("not an object", "inbox", "not an object"),
            ("bad name", dict(self.GOOD, name="Inbox!"), "name"),
            ("no times", dict(self.GOOD, times=[]), "times"),
            ("bad time", dict(self.GOOD, times=["9am"]), "times"),
            ("hour 24", dict(self.GOOD, times=["24:00"]), "times"),
            ("no prompt", dict(self.GOOD, prompt=""), "prompt"),
            ("bad agent", dict(self.GOOD, agent="a b"), "agent"),
        ]
        for name, entry, part in cases:
            with self.subTest(name):
                self.assertTrue((watch.scan_error(entry) or "").startswith(part))

    def test_bad_entries_are_ignored_and_a_repeated_name_counts_once(self) -> None:
        watch.CONFIG = {"scans": [self.GOOD, dict(self.GOOD), {"name": "BAD"}, "x"]}
        self.assertEqual(watch.scans_config(), [self.GOOD])

    def test_bad_entries_are_named_in_the_round_log(self) -> None:
        watch.CONFIG = {"scans": [self.GOOD, {"name": "BAD"}]}
        _, out = self.capture(watch.cmd_scans_due, moment(9, 10))
        self.assertIn("Scan BAD: ignored", out)


class RunReports(TempHub):
    def report(self, event_id: str, text: str) -> None:
        self.with_stdin(text, watch.main, ["w", "hub-ack", event_id, "--report", "-"])

    def test_the_line_is_kept_in_the_log_of_its_scan(self) -> None:
        self.queue_scan("scan-1", title="Inbox Archive")
        self.report("scan-1", "archived 12 of 12 threads")
        line = read_text(os.path.join(watch.SCANS_DIR, "inbox-archive.log"))
        self.assertTrue(line.endswith("archived 12 of 12 threads\n"), line)

    def test_the_refresh_has_its_own_log(self) -> None:
        self.queue_refresh_event("refresh-1", title="Sessions Watch refresh")
        self.report("refresh-1", "Home: 1 new blocked session")
        log = read_text(os.path.join(watch.SCANS_DIR, "refresh.log"))
        self.assertIn("Home: 1 new blocked session", log)

    def test_the_event_is_closed(self) -> None:
        self.queue_scan("scan-1", title="inbox")
        self.report("scan-1", "ok")
        self.assertEqual(watch.hub_pending("scans"), [])

    def test_only_the_first_line_is_kept(self) -> None:
        self.queue_scan("scan-1", title="inbox")
        self.report("scan-1", "archived 2\nsecond line\nSW_REPORT")
        log = read_text(os.path.join(watch.SCANS_DIR, "inbox.log"))
        self.assertTrue(log.endswith("archived 2\n"))

    def test_control_characters_are_removed_and_shell_syntax_stays_text(self) -> None:
        report = watch.clean_report("a $(touch /tmp/x) \x1b[31mred\x07")
        self.assertEqual(report, "a $(touch /tmp/x) [31mred")

    def test_a_long_line_is_cut(self) -> None:
        self.assertEqual(len(watch.clean_report("x" * 1000)), watch.REPORT_CHARS)

    def test_an_empty_report_is_an_empty_line(self) -> None:
        self.assertEqual(watch.clean_report(""), "")

    def test_lines_older_than_14_days_are_removed(self) -> None:
        self.queue_scan("scan-1", title="inbox")
        os.makedirs(watch.SCANS_DIR)
        log = os.path.join(watch.SCANS_DIR, "inbox.log")
        old = (datetime.now() - timedelta(days=15)).strftime("%Y-%m-%d %H:%M")
        recent = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d %H:%M")
        write_text(log, f"{old} old run\n{recent} recent run\n")
        self.report("scan-1", "new run")
        lines = read_text(log).splitlines()
        self.assertEqual([line[17:] for line in lines], ["recent run", "new run"])

    def test_an_event_for_a_session_saves_no_report(self) -> None:
        self.queue_session_event("to-session")
        self.report("to-session", "text")
        self.assertFalse(os.path.exists(watch.SCANS_DIR))

    def test_an_unknown_event_saves_no_report(self) -> None:
        self.report("nope", "text")
        self.assertFalse(os.path.exists(watch.SCANS_DIR))

    def test_the_report_must_come_from_stdin(self) -> None:
        self.queue_scan("scan-1", title="inbox")
        argv = ["w", "hub-ack", "scan-1", "--report", "text on the command line"]
        result, _ = self.capture(watch.main, argv)
        self.assertEqual(result, 1)
        self.assertEqual(len(watch.hub_pending("scans")), 1)


class FailedRuns(TempHub):
    def fail(self, event_id: str, reason: str) -> None:
        self.with_stdin(reason, watch.main, ["w", "hub-ack", event_id, "--failed", "-"])

    def test_the_event_is_closed_so_the_next_time_can_queue_the_scan_again(self) -> None:
        self.queue_scan("scan-1", title="inbox")
        self.fail("scan-1", "Gmail login expired")
        self.assertEqual(watch.hub_pending("scans"), [])

    def test_the_report_starts_with_failed(self) -> None:
        self.queue_scan("scan-1", title="inbox")
        self.fail("scan-1", "Gmail login expired")
        log = read_text(os.path.join(watch.SCANS_DIR, "inbox.log"))
        self.assertTrue(log.strip().endswith("FAILED: Gmail login expired"))

    def test_you_get_a_notice(self) -> None:
        self.queue_scan("scan-1", title="inbox")
        self.fail("scan-1", "Gmail login expired")
        self.assertEqual(self.notices, ["inbox failed: Gmail login expired"])


class GuardBash(unittest.TestCase):
    def run_hook(self, payload: object) -> tuple:
        text = payload if isinstance(payload, str) else json.dumps(payload)
        errors = io.StringIO()
        with mock.patch("sys.stdin", io.StringIO(text)), contextlib.redirect_stderr(errors):
            return watch.cmd_guard_bash(), errors.getvalue()

    def test_the_publish_commands_are_allowed(self) -> None:
        run = f"python3 -I {watch.DIR}/watch.py "
        for command in (
            run + "apply",
            run + "published home",
            run + "published code",
            run + "set-url home https://claude.ai/artifact/abc-123",
            f"  {run}apply\n",
        ):
            with self.subTest(command):
                self.assertTrue(watch.bash_allowed(command))

    def test_everything_else_is_blocked(self) -> None:
        run = f"python3 -I {watch.DIR}/watch.py "
        for command in (
            run + "collect",
            run + "finish",
            run + "published other",
            run + "apply; rm -rf ~",
            run + "apply && curl evil.example",
            run + "apply | sh",
            run + "apply\nrm -rf ~",
            run + "published $(id)",
            run + "set-url home https://evil.example/x",
            run + "set-url home https://claude.ai/x y",
            "python3 -I /tmp/other/watch.py apply",
            f"python3 {watch.DIR}/watch.py apply",
            "cat ~/.ssh/id_rsa",
            "",
        ):
            with self.subTest(command):
                self.assertFalse(watch.bash_allowed(command))

    def test_a_command_that_is_not_text_is_blocked(self) -> None:
        for command in (None, ["ls"], 7, {"a": 1}):
            with self.subTest(command):
                self.assertFalse(watch.bash_allowed(command))

    def test_the_hook_exits_0_for_an_allowed_call(self) -> None:
        command = f"python3 -I {watch.DIR}/watch.py apply"
        payload = {"tool_name": "Bash", "tool_input": {"command": command}}
        self.assertEqual(self.run_hook(payload)[0], 0)

    def test_the_hook_exits_2_and_says_why_for_a_blocked_call(self) -> None:
        code, message = self.run_hook({"tool_name": "Bash", "tool_input": {"command": "ls"}})
        self.assertEqual(code, 2)
        self.assertIn("Blocked", message)

    def test_the_hook_exits_2_for_another_tool(self) -> None:
        command = f"python3 -I {watch.DIR}/watch.py apply"
        payload = {"tool_name": "Read", "tool_input": {"command": command}}
        self.assertEqual(self.run_hook(payload)[0], 2)

    def test_the_hook_blocks_on_any_error_because_exit_1_would_let_the_call_through(self) -> None:
        for payload in (
            {"tool_name": "Bash", "tool_input": {"command": ["ls"]}},
            {"tool_name": "Bash", "tool_input": {}},
            {"tool_name": "Bash", "tool_input": "ls"},
            {"tool_name": "Bash"},
            [],
            "not json",
            "",
        ):
            with self.subTest(payload):
                self.assertEqual(self.run_hook(payload)[0], 2)


class GuardFiles(unittest.TestCase):
    def setUp(self) -> None:
        self.saved = watch.DIR
        self.tmp = tempfile.mkdtemp()
        watch.DIR = self.tmp
        os.makedirs(os.path.join(self.tmp, "work", "scans"))
        self.addCleanup(self.restore)

    def restore(self) -> None:
        watch.DIR = self.saved
        shutil.rmtree(self.tmp, True)

    def path(self, relative: str) -> str:
        return os.path.join(self.tmp, relative)

    def test_the_classifier_reads_its_two_files(self) -> None:
        for relative in ("work/todo.json", "work/scans/refresh-classify.md"):
            with self.subTest(relative):
                self.assertTrue(watch.file_allowed("Read", self.path(relative)))

    def test_the_classifier_writes_its_one_file(self) -> None:
        self.assertTrue(watch.file_allowed("Write", self.path("work/classified.json")))

    def test_the_classifier_reads_no_other_file(self) -> None:
        for path in (
            self.path("work/collected.json"),
            self.path("state.json"),
            self.path("work/scans/refresh-publish.md"),
            os.path.expanduser("~/.ssh/id_rsa"),
            "/etc/hosts",
        ):
            with self.subTest(path):
                self.assertFalse(watch.file_allowed("Read", path))

    def test_the_classifier_writes_no_other_file(self) -> None:
        for path in (
            self.path("work/todo.json"),
            self.path("state.json"),
            os.path.expanduser("~/.claude/agents/sw-publish.md"),
        ):
            with self.subTest(path):
                self.assertFalse(watch.file_allowed("Write", path))

    def test_the_classifier_has_no_other_tool(self) -> None:
        self.assertFalse(watch.file_allowed("Edit", self.path("work/classified.json")))
        self.assertFalse(watch.file_allowed(None, self.path("work/classified.json")))

    def test_a_path_that_climbs_out_and_back_is_the_same_file(self) -> None:
        self.assertTrue(watch.file_allowed("Read", self.path("work/scans/../todo.json")))

    def test_a_link_to_another_file_is_blocked(self) -> None:
        link = self.path("work/todo.json")
        os.symlink("/etc/hosts", link)
        self.assertFalse(watch.file_allowed("Read", link))

    def test_a_path_that_is_not_text_is_blocked(self) -> None:
        for path in (None, "", ["work/todo.json"], 7):
            with self.subTest(path):
                self.assertFalse(watch.file_allowed("Read", path))

    def run_hook(self, payload: object) -> int:
        text = payload if isinstance(payload, str) else json.dumps(payload)
        with mock.patch("sys.stdin", io.StringIO(text)), contextlib.redirect_stderr(io.StringIO()):
            return watch.cmd_guard_files()

    def test_the_hook_blocks_on_any_error(self) -> None:
        for payload in ("not json", "[]", '{"tool_name": "Read", "tool_input": []}'):
            with self.subTest(payload):
                self.assertEqual(self.run_hook(payload), 2)

    def test_the_hook_exit_code_follows_the_tool_and_the_file(self) -> None:
        cases = [
            ("Read", "work/todo.json", 0),
            ("Write", "work/classified.json", 0),
            ("Write", "work/todo.json", 2),
            ("Read", "/etc/hosts", 2),
            ("Edit", "work/classified.json", 2),
        ]
        for tool, relative, expected in cases:
            with self.subTest(f"{tool} {relative}"):
                path = relative if relative.startswith("/") else self.path(relative)
                payload = {"tool_name": tool, "tool_input": {"file_path": path}}
                self.assertEqual(self.run_hook(payload), expected)


class ScansSetup(TempHub):
    def setUp(self) -> None:
        super().setUp()
        watch.DIR = watch.SW_HOME

    def read(self, name: str) -> str:
        return read_text(os.path.join(watch.AGENTS_DIR, name))

    def run_setup(self) -> tuple:
        return self.capture(watch.cmd_scans_setup)

    def test_it_refuses_to_run_from_another_folder(self) -> None:
        watch.DIR = "/some/clone/tool"
        result, out = self.run_setup()
        self.assertEqual(result, 1)
        self.assertIn(f"python3 -I {watch.SW_HOME}/watch.py scans-setup", out)
        self.assertFalse(os.path.exists(watch.AGENTS_DIR))

    def test_it_writes_the_classifier_and_the_publisher(self) -> None:
        self.run_setup()
        self.assertEqual(sorted(os.listdir(watch.AGENTS_DIR)), ["sw-classify.md", "sw-publish.md"])

    def test_it_skips_the_mail_agent_without_a_server_name(self) -> None:
        _, out = self.run_setup()
        self.assertIn("Skipped sw-scan-gmail.md", out)

    def test_the_publisher_gets_the_bash_hook_with_this_folder(self) -> None:
        self.run_setup()
        publish = self.read("sw-publish.md")
        hook = f"command: 'python3 -I {watch.SW_HOME}/watch.py guard-bash || exit 2'"
        self.assertIn(hook, publish)
        self.assertNotIn("{{", publish)

    def test_the_classifier_gets_the_file_hook_and_two_tools(self) -> None:
        self.run_setup()
        classify = self.read("sw-classify.md")
        self.assertIn("tools: Read, Write", classify)
        self.assertIn('matcher: "Read|Write"', classify)
        hook = f"command: 'python3 -I {watch.SW_HOME}/watch.py guard-files || exit 2'"
        self.assertIn(hook, classify)

    def test_a_quote_in_the_folder_name_is_doubled_for_yaml(self) -> None:
        self.assertEqual(watch.render_agent("'{{DIR}}'", None), f"'{watch.DIR}'")
        watch.DIR = "/home/o'brien/Claude/sessions-watch"
        rendered = watch.render_agent("'{{DIR}}'", None)
        self.assertEqual(rendered, "'/home/o''brien/Claude/sessions-watch'")

    def test_the_mail_agent_has_four_gmail_tools_and_tool_search(self) -> None:
        watch.CONFIG = {"scansGmailServer": "mcp__abc-123"}
        self.run_setup()
        text = self.read("sw-scan-gmail.md")
        tools = text.split("tools:\n", 1)[1].split("disallowedTools:", 1)[0]
        expected = ["- ToolSearch"] + [
            f"- mcp__abc-123__{name}"
            for name in ("search_threads", "get_thread", "list_labels", "unlabel_thread")
        ]
        self.assertEqual([line.strip() for line in tools.splitlines()], expected)
        self.assertNotIn("{{", text)

    def test_the_mail_agent_denies_every_tool_that_sends_drafts_trashes_or_deletes(self) -> None:
        watch.CONFIG = {"scansGmailServer": "mcp__abc-123"}
        self.run_setup()
        text = self.read("sw-scan-gmail.md")
        tools, denied = text.split("disallowedTools:", 1)
        for name in (
            "send_message",
            "reply",
            "forward",
            "trash_thread",
            "mark_thread_spam",
            "delete_label",
        ):
            with self.subTest(name):
                self.assertNotIn(f"- mcp__abc-123__{name}\n", tools)
                self.assertIn(f"- mcp__abc-123__{name}\n", denied)

    def test_a_server_name_that_is_not_a_plain_name_is_refused(self) -> None:
        for bad in ("abc", "mcp__a b", "mcp__x; rm -rf ~", "mcp__", 7):
            with self.subTest(bad):
                watch.CONFIG = {"scansGmailServer": bad}
                _, out = self.run_setup()
                self.assertIn("Skipped sw-scan-gmail.md", out)
                agent = os.path.join(watch.AGENTS_DIR, "sw-scan-gmail.md")
                self.assertFalse(os.path.exists(agent))

    def test_a_file_sw_did_not_write_is_kept(self) -> None:
        os.makedirs(watch.AGENTS_DIR)
        mine = os.path.join(watch.AGENTS_DIR, "sw-classify.md")
        write_text(mine, "my own agent\n")
        _, out = self.run_setup()
        self.assertEqual(read_text(mine), "my own agent\n")
        self.assertIn("left alone", out)

    def test_a_file_sw_wrote_is_updated(self) -> None:
        os.makedirs(watch.AGENTS_DIR)
        path = os.path.join(watch.AGENTS_DIR, "sw-publish.md")
        write_text(path, f"{watch.AGENT_MARK}\nold copy\n")
        self.run_setup()
        self.assertNotIn("old copy", self.read("sw-publish.md"))
        self.assertIn(watch.AGENT_MARK, self.read("sw-publish.md"))


class CommandArguments(TempHub):
    def test_a_plain_ack_closes_a_session_event(self) -> None:
        self.queue_session_event("to-session")
        self.assertEqual(self.capture(watch.main, ["w", "hub-ack", "to-session"])[0], 0)
        self.assertEqual(watch.hub_pending(), [])

    def test_scans_busy_and_scans_due_are_commands(self) -> None:
        self.assertEqual(self.capture(watch.main, ["w", "scans-busy"])[0], 1)
        self.assertEqual(self.capture(watch.main, ["w", "scans-due"])[0], 0)

    def test_hub_line_prints_the_status_line(self) -> None:
        _, out = self.capture(watch.main, ["w", "hub-line"])
        self.assertIn("Trackers Hub: never started, 0 messages waiting.", out)


class HubStateText(TempHub):
    def test_the_status_line_says_never_started_for_a_scans_hub_with_events(self) -> None:
        self.queue_scan("to-scans")
        self.assertIn("Scans Hub: never started, 1 event waiting.", watch.hub_line_text())

    def test_the_status_line_says_how_long_the_scans_hub_was_quiet(self) -> None:
        self.beat(seconds_ago=7 * 60)
        self.assertIn("Scans Hub: quiet for 7 min, 0 events waiting.", watch.hub_line_text())

    def test_a_heartbeat_file_alone_puts_the_scans_hub_in_use(self) -> None:
        self.assertFalse(watch.scans_in_use())
        self.beat()
        self.assertTrue(watch.scans_in_use())

    def test_the_trackers_hub_state_comes_from_its_own_heartbeat(self) -> None:
        with open(watch.HUB_HEARTBEAT, "a"):
            pass
        self.assertEqual(watch.hub_state("delivery"), "alive")
        self.assertEqual(watch.hub_state("scans"), "never started")

    def test_hub_status_lists_the_events_of_each_hub(self) -> None:
        self.beat()
        self.queue_scan("to-scans", title="inbox")
        self.queue_session_event("to-session", title="AX-1")
        _, out = self.capture(watch.cmd_hub_status)
        self.assertIn("scan  inbox", out)
        self.assertIn("pr  AX-1", out)


class ScanSlots(unittest.TestCase):
    TIMES = ["09:00", "13:00", "17:00"]

    def test_a_scan_is_due_at_the_exact_minute(self) -> None:
        now = moment(9, 13)
        self.assertEqual(watch.scan_slot(self.TIMES, now), "2026-11-09 13:00")

    def test_a_scan_is_not_due_one_minute_early(self) -> None:
        now = moment(9, 12, 59)
        self.assertEqual(watch.scan_slot(self.TIMES, now), "2026-11-09 09:00")

    def test_times_in_any_order_give_the_latest_one(self) -> None:
        now = moment(9, 18)
        self.assertEqual(watch.scan_slot(["17:00", "09:00"], now), "2026-11-09 17:00")

    def test_before_the_first_time_the_slot_is_yesterdays_last(self) -> None:
        now = moment(9, 8)
        self.assertEqual(watch.scan_slot(self.TIMES, now), "2026-11-08 17:00")


class CatchUp(TempHub):
    def test_two_missed_times_give_one_catch_up_run(self) -> None:
        path = os.path.join(self.tmp, "p.md")
        write_text(path, "x")
        watch.CONFIG = {
            "scans": [
                {
                    "name": "inbox",
                    "times": ["09:00", "13:00", "17:00"],
                    "prompt": path,
                    "agent": "a",
                },
            ],
        }
        self.capture(watch.cmd_scans_due, moment(9, 9, 5))
        self.capture(watch.cmd_scans_due, moment(9, 17, 5))
        self.assertEqual([event["title"] for event in watch.hub_pending("scans")], ["inbox"])

    def test_no_scans_configured_creates_nothing(self) -> None:
        result, out = self.capture(watch.cmd_scans_due, moment(9, 10))
        self.assertEqual((result, out), (0, ""))
        self.assertFalse(os.path.exists(watch.SCANS_DIR))

    def test_a_tilde_in_the_prompt_is_expanded_from_the_home_folder(self) -> None:
        saved = os.environ["HOME"]
        self.addCleanup(os.environ.__setitem__, "HOME", saved)
        os.environ["HOME"] = self.tmp
        write_text(os.path.join(self.tmp, "p.md"), "x")
        watch.CONFIG = {
            "scans": [
                {
                    "name": "inbox",
                    "times": ["09:00"],
                    "prompt": "~/p.md",
                    "agent": "a",
                },
            ],
        }
        self.capture(watch.cmd_scans_due, moment(9, 8))
        _, out = self.capture(watch.cmd_scans_due, moment(10, 9, 5))
        self.assertIn("queued for the Scans Hub", out)


class AckWithoutReport(TempHub):
    def test_a_plain_ack_prints_one_line_and_writes_no_report(self) -> None:
        self.queue_session_event("to-session")
        _, out = self.capture(watch.cmd_hub_ack, "to-session")
        self.assertEqual(len(out.strip().splitlines()), 1)
        self.assertFalse(os.path.exists(watch.SCANS_DIR))


class HubFeed(TempHub):
    def printed(self, role: str) -> str:
        """Run the feed for one loop and return what it printed."""
        output = io.StringIO()
        sleep = mock.patch("time.sleep", side_effect=KeyboardInterrupt)
        with sleep, contextlib.redirect_stdout(output):
            try:
                watch.cmd_hub_feed(role)
            except KeyboardInterrupt:
                pass
        return output.getvalue()

    def test_the_scans_feed_touches_only_its_own_heartbeat(self) -> None:
        self.printed("scans")
        self.assertTrue(os.path.exists(watch.SCANS_HEARTBEAT))
        self.assertFalse(os.path.exists(watch.HUB_HEARTBEAT))

    def test_the_trackers_feed_touches_only_its_own_heartbeat(self) -> None:
        self.printed("delivery")
        self.assertTrue(os.path.exists(watch.HUB_HEARTBEAT))
        self.assertFalse(os.path.exists(watch.SCANS_HEARTBEAT))

    def test_the_scans_feed_prints_only_the_scans_events(self) -> None:
        self.queue_session_event("to-session")
        self.queue_scan("to-scans")
        printed = self.printed("scans")
        self.assertIn('"id": "to-scans"', printed)
        self.assertNotIn("to-session", printed)

    def test_the_trackers_feed_prints_only_the_session_events(self) -> None:
        self.queue_session_event("to-session")
        self.queue_scan("to-scans")
        printed = self.printed("delivery")
        self.assertIn('"id": "to-session"', printed)
        self.assertNotIn("to-scans", printed)


class Notices(TempHub):
    def check(self, db: dict) -> list:
        lines: list = []
        watch.hub_notify_if_down(db, lines)
        return lines

    def test_a_down_scans_hub_with_events_waiting_notifies_once(self) -> None:
        self.queue_scan("to-scans")
        db: dict = {}
        self.check(db)
        lines = self.check(db)
        self.assertEqual(
            self.notices,
            ["Scans Hub is down — 1 event(s) waiting. Open it and say re-arm."],
        )
        self.assertIn("Scans Hub: never started; 1 event(s) waiting.", lines)

    def test_a_stuck_event_notifies_while_the_feed_is_alive(self) -> None:
        self.beat()
        self.queue_scan("to-scans", minutes_ago=20)
        self.check({})
        self.assertEqual(
            self.notices,
            ["Scans Hub has not finished an event in 15 minutes. Open it and look."],
        )

    def test_a_recent_event_with_a_live_feed_does_not_notify(self) -> None:
        self.beat()
        self.queue_scan("to-scans", minutes_ago=2)
        self.check({})
        self.assertEqual(self.notices, [])

    def test_a_stuck_scans_event_does_not_make_the_trackers_hub_look_down(self) -> None:
        with open(watch.HUB_HEARTBEAT, "a"):
            pass
        self.beat()
        self.queue_scan("to-scans", minutes_ago=20)
        self.queue_session_event("to-session", minutes_ago=1)
        self.check({})
        self.assertEqual(
            self.notices,
            ["Scans Hub has not finished an event in 15 minutes. Open it and look."],
        )

    def test_an_old_event_of_the_trackers_hub_with_a_live_feed_gives_no_notice(self) -> None:
        with open(watch.HUB_HEARTBEAT, "a"):
            pass
        self.queue_session_event("to-session", minutes_ago=20)
        self.check({})
        self.assertEqual(self.notices, [])

    def test_a_quiet_scans_hub_for_5_minutes_gives_no_notice(self) -> None:
        self.beat(seconds_ago=5 * 60)
        self.queue_scan("to-scans")
        self.check({})
        self.assertEqual(self.notices, [])

    def test_a_quiet_scans_hub_for_20_minutes_gives_a_notice(self) -> None:
        self.beat(seconds_ago=20 * 60)
        self.queue_scan("to-scans")
        self.check({})
        self.assertEqual(
            self.notices,
            ["Scans Hub is down — 1 event(s) waiting. Open it and say re-arm."],
        )

    def test_both_hubs_down_give_one_notice_each(self) -> None:
        self.queue_scan("to-scans")
        self.queue_session_event("to-session")
        self.check({})
        self.assertEqual(
            sorted(self.notices),
            [
                "Scans Hub is down — 1 event(s) waiting. Open it and say re-arm.",
                "Trackers Hub is down — 1 event(s) waiting. Open it and say re-arm.",
            ],
        )

    def test_a_second_check_within_30_minutes_gives_no_new_notice(self) -> None:
        self.queue_session_event("to-session")
        db: dict = {}
        self.check(db)
        self.check(db)
        self.assertEqual(len(self.notices), 1)

    def test_each_hub_has_its_own_notice_clock(self) -> None:
        self.queue_scan("to-scans")
        db = {"hubNotified": time.time()}
        self.check(db)
        self.assertEqual(len(self.notices), 1)


class MainDispatch(TempHub):
    NAMES = (
        "cmd_hub_ack",
        "cmd_hub_feed",
        "cmd_scans_queue",
        "cmd_scans_busy",
        "cmd_scans_due",
        "cmd_scans_setup",
        "cmd_guard_bash",
        "cmd_guard_files",
    )

    def setUp(self) -> None:
        super().setUp()
        self.calls: list = []
        for name in self.NAMES:
            self.addCleanup(setattr, watch, name, getattr(watch, name))
            setattr(watch, name, self.recorder(name))

    def recorder(self, name: str):
        def record(*args, **kwargs) -> int:
            self.calls.append((name, args, kwargs))
            return 0

        return record

    def dispatch(self, argv: list, stdin: str = "") -> int:
        return self.with_stdin(stdin, watch.main, ["w"] + argv)[0]

    def test_each_command_calls_its_function_with_its_arguments(self) -> None:
        cases = [
            (["hub-feed"], "", ("cmd_hub_feed", ("delivery",), {})),
            (["hub-feed", "--role", "scans"], "", ("cmd_hub_feed", ("scans",), {})),
            (["hub-ack", "id1"], "", ("cmd_hub_ack", ("id1",), {})),
            (
                ["hub-ack", "id1", "--report", "-"],
                "archived 3 threads",
                ("cmd_hub_ack", ("id1", "archived 3 threads"), {"failed": False}),
            ),
            (
                ["hub-ack", "id1", "--failed", "-"],
                "login expired",
                ("cmd_hub_ack", ("id1", "login expired"), {"failed": True}),
            ),
            (["scans-busy"], "", ("cmd_scans_busy", (), {})),
            (["scans-due"], "", ("cmd_scans_due", (), {})),
            (["scans-setup"], "", ("cmd_scans_setup", (), {})),
            (["guard-bash"], "", ("cmd_guard_bash", (), {})),
            (["guard-files"], "", ("cmd_guard_files", (), {})),
        ]
        for argv, stdin, expected in cases:
            with self.subTest(argv):
                self.calls.clear()
                self.assertEqual(self.dispatch(argv, stdin), 0)
                self.assertEqual(self.calls, [expected])

    def test_scans_queue_gets_the_whole_argument_list(self) -> None:
        self.dispatch(["scans-queue", "refresh", "4"])
        argv = ["w", "scans-queue", "refresh", "4"]
        self.assertEqual(self.calls, [("cmd_scans_queue", (argv,), {})])

    def test_bad_arguments_return_1_and_call_nothing(self) -> None:
        for argv in (
            ["hub-ack", "id1", "--report"],
            ["hub-ack", "id1", "--reprot", "x"],
            ["hub-ack", "id1", "--report", "text on the command line"],
            ["hub-ack", "id1", "--report", "-", "extra"],
            ["hub-feed", "--role", "x"],
            ["hub-feed", "--role", "scans", "more"],
            ["hub-feed", "extra"],
        ):
            with self.subTest(argv):
                self.assertEqual(self.dispatch(argv), 1)
                self.assertEqual(self.calls, [])


class RealScansQueueExit(TempHub):
    def test_main_returns_3_when_the_scans_hub_is_not_running(self) -> None:
        self.assertEqual(self.capture(watch.main, ["w", "scans-queue", "refresh", "4"])[0], 3)

    def test_main_returns_0_when_the_refresh_is_queued(self) -> None:
        self.beat()
        self.assertEqual(self.capture(watch.main, ["w", "scans-queue", "refresh", "4"])[0], 0)


class HubWait(TempHub):
    def touch_last_ack(self, minutes_ago: int) -> None:
        os.makedirs(watch.SCANS_DIR, exist_ok=True)
        path = os.path.join(watch.SCANS_DIR, "last-ack")
        write_text(path, "x")
        stamp = time.time() - minutes_ago * 60
        os.utime(path, (stamp, stamp))

    def test_a_refresh_behind_a_recent_scan_is_still_busy(self) -> None:
        self.beat()
        self.queue_scan("scan-1", minutes_ago=5)
        self.queue_refresh_event("refresh-1", minutes_ago=3)
        self.assertEqual(watch.cmd_scans_busy(), 0)

    def test_the_time_restarts_when_the_scan_ends(self) -> None:
        self.beat()
        self.touch_last_ack(minutes_ago=2)
        self.queue_refresh_event("refresh-1", minutes_ago=20)
        self.assertEqual(watch.cmd_scans_busy(), 0)

    def test_a_refresh_queued_20_minutes_ago_with_no_acknowledgement_is_not_busy(self) -> None:
        self.beat()
        self.queue_refresh_event("refresh-1", minutes_ago=20)
        self.assertEqual(watch.cmd_scans_busy(), 1)

    def test_a_scan_stuck_for_20_minutes_makes_the_hub_late_for_the_refresh_behind_it(
        self,
    ) -> None:
        self.beat()
        self.queue_scan("scan-1", minutes_ago=20)
        self.queue_refresh_event("refresh-1", minutes_ago=3)
        self.assertEqual(watch.cmd_scans_busy(), 1)
        result, _ = self.capture(watch.cmd_scans_queue, ["w", "scans-queue", "refresh", "4"])
        self.assertEqual(result, 3)

    def test_nothing_waiting_is_no_wait(self) -> None:
        self.assertEqual(watch.scans_hub_wait_seconds(), 0.0)

    def test_acknowledging_a_scans_event_records_the_time(self) -> None:
        self.queue_scan("scan-1")
        self.capture(watch.cmd_hub_ack, "scan-1")
        self.assertTrue(os.path.exists(os.path.join(watch.SCANS_DIR, "last-ack")))

    def test_acknowledging_a_message_for_a_session_does_not(self) -> None:
        self.queue_session_event("to-session")
        self.capture(watch.cmd_hub_ack, "to-session")
        self.assertFalse(os.path.exists(os.path.join(watch.SCANS_DIR, "last-ack")))


class FailedRefresh(TempHub):
    def fail(self, event_id: str, reason: str = "Artifact tool missing") -> None:
        self.with_stdin(reason, watch.main, ["w", "hub-ack", event_id, "--failed", "-"])

    def queue(self) -> int:
        return self.capture(watch.cmd_scans_queue, ["w", "scans-queue", "refresh", "4"])[0]

    def test_after_a_failed_refresh_the_next_round_uses_the_cli_session(self) -> None:
        self.beat()
        self.queue_refresh_event("refresh-1", title="Sessions Watch refresh")
        self.fail("refresh-1")
        self.assertEqual(self.queue(), 3)

    def test_the_round_after_that_queues_for_the_scans_hub_again(self) -> None:
        self.beat()
        self.queue_refresh_event("refresh-1", title="Sessions Watch refresh")
        self.fail("refresh-1")
        self.queue()
        self.assertEqual(self.queue(), 0)
        self.assertEqual(len(watch.hub_pending("scans")), 1)

    def test_a_failed_scan_does_not_send_the_refresh_to_the_cli_session(self) -> None:
        self.beat()
        self.queue_scan("scan-1", title="inbox")
        self.fail("scan-1")
        self.assertEqual(self.queue(), 0)

    def test_a_failed_ack_of_an_unknown_event_gives_no_notice(self) -> None:
        result, _ = self.with_stdin("boom", watch.main, ["w", "hub-ack", "nope", "--failed", "-"])
        self.assertEqual(result, 0)
        self.assertEqual(self.notices, [])

    def test_failed_runs_notify_at_most_every_30_minutes(self) -> None:
        for number in (1, 2, 3):
            self.queue_event(
                event_id=f"scan-{number}",
                kind="scan",
                to=watch.SCANS_TO,
                title="inbox",
            )
            self.fail(f"scan-{number}")
        self.assertEqual(self.notices, ["inbox failed: Artifact tool missing"])

    def test_the_notice_comes_again_after_30_minutes(self) -> None:
        self.queue_scan("scan-1", title="inbox")
        self.fail("scan-1")
        notice = os.path.join(watch.SCANS_DIR, "failed-notice")
        old = time.time() - 31 * 60
        os.utime(notice, (old, old))
        self.queue_scan("scan-2", title="inbox")
        self.fail("scan-2")
        self.assertEqual(len(self.notices), 2)


class ReservedName(unittest.TestCase):
    def test_a_scan_cannot_be_named_refresh(self) -> None:
        scan = {
            "name": "refresh",
            "times": ["09:00"],
            "prompt": "p.md",
            "agent": "a",
        }
        self.assertTrue((watch.scan_error(scan) or "").startswith("name: refresh is reserved"))


class WrongAckCall(TempHub):
    def test_an_old_style_call_gets_a_one_line_error_and_does_not_close_the_event(self) -> None:
        self.queue_scan("scan-1")
        argv = ["w", "hub-ack", "scan-1", "--report", "archived 3"]
        result, out = self.capture(watch.main, argv)
        self.assertEqual(result, 1)
        self.assertEqual(len(out.strip().splitlines()), 1)
        self.assertEqual(len(watch.hub_pending("scans")), 1)


@unittest.skipUnless(shutil.which("sh"), "needs sh")
class HookShellSafety(unittest.TestCase):
    """The hook command in the generated agent files, run as Claude Code runs it, in a shell."""

    def hook_command(self, template: str, folder: str) -> str:
        with open(os.path.join(HERE, "..", "agents", template), encoding="utf-8") as handle:
            text = handle.read()
        line = next(line for line in text.splitlines() if line.strip().startswith("command: "))
        return line.split("command: ", 1)[1].strip().strip("'").replace("{{DIR}}", folder)

    def run_hook(self, command: str, payload: dict) -> int:
        return subprocess.run(
            args=["sh", "-c", command],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        ).returncode

    def folder_with_broken_config(self) -> str:
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, True)
        shutil.copy(os.path.join(HERE, "..", "watch.py"), folder)
        write_text(os.path.join(folder, "config.json"), "[]")
        return folder

    def test_both_commands_end_with_exit_2(self) -> None:
        for template in ("sw-classify.md", "sw-publish.md"):
            with self.subTest(template):
                self.assertTrue(self.hook_command(template, "/x").endswith("|| exit 2"))

    def test_a_crash_of_watch_py_blocks_the_bash_call(self) -> None:
        folder = self.folder_with_broken_config()
        command = self.hook_command("sw-publish.md", folder)
        payload = {"tool_name": "Bash", "tool_input": {"command": "id"}}
        self.assertEqual(self.run_hook(command, payload), 2)

    def test_a_crash_of_watch_py_blocks_the_file_call(self) -> None:
        folder = self.folder_with_broken_config()
        command = self.hook_command("sw-classify.md", folder)
        payload = {"tool_name": "Read", "tool_input": {"file_path": "/etc/hosts"}}
        self.assertEqual(self.run_hook(command, payload), 2)

    def test_a_missing_watch_py_blocks_the_call(self) -> None:
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, True)
        command = self.hook_command("sw-publish.md", folder)
        payload = {"tool_name": "Bash", "tool_input": {"command": "id"}}
        self.assertEqual(self.run_hook(command, payload), 2)


class NotifyText(unittest.TestCase):
    def setUp(self) -> None:
        self.calls: list = []
        self.saved = (watch.run, watch.SYSTEM)
        watch.run = lambda command, cwd: self.calls.append(command) or (0, "", "")
        watch.SYSTEM = "Darwin"
        self.addCleanup(self.restore)

    def restore(self) -> None:
        watch.run, watch.SYSTEM = self.saved

    def test_a_backslash_and_a_quote_cannot_break_the_script(self) -> None:
        watch.notify("Sessions Watch", 'it broke \\ "badly" \\')
        script = self.calls[0][2]
        self.assertNotIn("\\", script)
        self.assertEqual(script.count('"'), 4)


@unittest.skipUnless(shutil.which("bash"), "needs bash")
class SwLoop(unittest.TestCase):
    """One round of the real `sd` script in a temp HOME, with a stub watch.py and a stub claude."""

    SW = os.path.join(HERE, "..", "sd")
    PROMPT = os.path.join(HERE, "..", "prompt.md")
    WATCH_STUB = (
        "import os, sys\n"
        "with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'calls.log'), "
        "'a') as f:\n"
        "    f.write(sys.argv[1] + '\\n')\n"
        "if sys.argv[1] == 'collect':\n"
        "    print('TODO 2')\n"
        "sys.exit(int(os.environ.get('STUB_' + sys.argv[1].upper().replace('-', '_'), '0')))\n"
    )

    def setUp(self) -> None:
        self.home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.home, True)
        self.tool = os.path.join(self.home, "Claude", "sessions-watch")
        bin_dir = os.path.join(self.home, ".local", "bin")
        os.makedirs(os.path.join(self.tool, "work"))
        os.makedirs(bin_dir)
        shutil.copy(self.SW, os.path.join(self.tool, "sd"))
        shutil.copy(self.PROMPT, os.path.join(self.tool, "prompt.md"))
        write_text(os.path.join(self.tool, "watch.py"), self.WATCH_STUB)
        claude = os.path.join(bin_dir, "claude")
        write_text(claude, f"#!/bin/sh\necho started >> {self.home}/claude-ran\n")
        os.chmod(claude, 0o755)

    def run_round(self, **stub_exit_codes: int) -> list:
        env = {"HOME": self.home, "PATH": os.environ["PATH"]}
        env.update({f"STUB_{name.upper()}": str(code) for name, code in stub_exit_codes.items()})
        result = subprocess.run(
            args=["bash", os.path.join(self.tool, "sd")],
            env=env,
            capture_output=True,
            timeout=120,
            check=False,
        )
        self.exit_code = result.returncode
        return read_text(os.path.join(self.tool, "calls.log")).split()

    def claude_started(self) -> bool:
        return os.path.exists(os.path.join(self.home, "claude-ran"))

    def test_the_scans_hub_takes_the_refresh_so_no_cli_session_starts(self) -> None:
        calls = self.run_round(scans_busy=1, scans_queue=0)
        self.assertIn("scans-queue", calls)
        self.assertFalse(self.claude_started())

    def test_exit_3_from_scans_queue_starts_the_cli_session(self) -> None:
        self.run_round(scans_busy=1, scans_queue=3)
        self.assertTrue(self.claude_started())

    def test_any_other_failure_from_scans_queue_starts_the_cli_session_too(self) -> None:
        self.run_round(scans_busy=1, scans_queue=1)
        self.assertTrue(self.claude_started())

    def test_a_refresh_still_with_the_scans_hub_skips_collect_for_the_round(self) -> None:
        calls = self.run_round(scans_busy=0)
        self.assertNotIn("collect", calls)
        self.assertNotIn("scans-queue", calls)
        self.assertFalse(self.claude_started())

    def test_a_round_that_skips_collect_still_runs_the_rest_and_exits_0(self) -> None:
        calls = self.run_round(scans_busy=0)
        for command in ("prwatch", "scans-due", "hub-line"):
            with self.subTest(command):
                self.assertIn(command, calls)
        self.assertEqual(self.exit_code, 0)

    def test_scans_are_queued_and_prs_are_watched_every_round(self) -> None:
        calls = self.run_round(scans_busy=1, scans_queue=0)
        self.assertIn("scans-due", calls)
        self.assertIn("prwatch", calls)


if __name__ == "__main__":
    unittest.main()
