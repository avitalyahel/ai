"""Run: python3 -I ~/Claude/sessions-watch/tests/test_input_checks.py

Input format checks and requirements (design section 5): every file sd reads but doesn't control is checked,
and a wrong shape becomes a warning instead of quietly wrong pages.

- Session histories, app metadata, the Cowork folder, the state files, the hub's snapshots and queue.
- config.json: its shape, including scheduledRuns and scans, and the prompt file of each scan.
- atlassian.conf, GitHub lookups, the classification and the required programs for the OS.
"""
import importlib.util
import json
import os
import shutil
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("watch", os.path.join(HERE, "..", "watch.py"))
watch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watch)

PATHS = ("CONFIG_PATH", "ATLASSIAN_CONF", "STATE_PATH", "MERGED_PATH", "PRWATCH_PATH", "APP_IDS_PATH",
         "APP_CLI_PATH", "APP_META_PATH", "EXTRA_WATCH", "HUB_DIR", "HUB_DOCSNAP", "HUB_QUEUE", "HEALTH_REPORT")


class InputChecks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.saved = {k: getattr(watch, k) for k in PATHS}
        self.saved["check_requirements"] = watch.check_requirements
        for k in PATHS:
            setattr(watch, k, os.path.join(self.tmp, k.lower()))
        watch.HUB_DOCSNAP = os.path.join(watch.HUB_DIR, "docsnap")
        watch.HUB_QUEUE = os.path.join(watch.HUB_DIR, "queue.jsonl")
        watch.check_requirements = lambda: []
        self.addCleanup(setattr, watch, "CONFIG", watch.CONFIG)
        watch.CONFIG = {}
        # A linked Code session, so the app-metadata check passes unless a test breaks it.
        self.write(watch.APP_CLI_PATH, {"local_1": ["sid-1"]})

    def tearDown(self):
        for k, v in self.saved.items():
            setattr(watch, k, v)
        shutil.rmtree(self.tmp)

    def write(self, path, obj, raw=False):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(obj if raw else json.dumps(obj))

    def check(self, used=None, code=None, home=None):
        return watch.check_inputs(used or {}, {"sid-1": {}} if code is None else code, {} if home is None else home)

    def test_all_good(self):
        self.write(watch.CONFIG_PATH, {"pages": {"home": "https://claude.ai/artifact/x"}, "hideSessions": []})
        self.assertEqual(self.check(), [])
        with open(watch.HEALTH_REPORT, encoding="utf-8") as f:
            self.assertIn("OK all inputs", f.read())

    def test_config_wrong_shape(self):
        self.write(watch.CONFIG_PATH, {"pages": ["not", "a", "dict"]})
        self.assertTrue(any("config_path" in w and "expected format" in w for w in self.check()))
        self.write(watch.CONFIG_PATH, {"pages": {"home": "https://example.com/x"}})
        self.assertTrue(any("expected format" in w for w in self.check()))
        self.write(watch.CONFIG_PATH, "{not json", raw=True)
        self.assertTrue(any("expected format" in w for w in self.check()))

    SCAN = {
        "name": "inbox",
        "times": ["09:00", "17:00"],
        "prompt": "~/p.md",
        "agent": "sw-scan-gmail",
    }

    def test_config_scheduled_runs_and_scans_in_the_right_shape(self) -> None:
        self.write(watch.CONFIG_PATH, {"scheduledRuns": ["Inbox auto-archive"], "scans": [self.SCAN]})
        self.assertFalse(any("expected format" in warning for warning in self.check()))

    def test_config_scheduled_runs_and_scans_in_the_wrong_shape(self) -> None:
        for bad in (
            {"scheduledRuns": "Inbox"},
            {"scheduledRuns": [1]},
            {"scans": "inbox"},
            {"scans": [dict(self.SCAN, times=["9am"])]},
            {"scans": [dict(self.SCAN, name="Bad Name")]},
        ):
            with self.subTest(bad):
                self.write(watch.CONFIG_PATH, bad)
                self.assertTrue(any("expected format" in warning for warning in self.check()))

    def test_a_scan_whose_prompt_file_is_missing_is_a_warning(self) -> None:
        watch.CONFIG = {"scans": [dict(self.SCAN, prompt=os.path.join(self.tmp, "missing.md"))]}
        self.assertTrue(any("the prompt file is missing" in warning for warning in self.check()))

    def test_a_scan_whose_prompt_file_exists_is_no_warning(self) -> None:
        prompt: str = os.path.join(self.tmp, "prompt.md")
        self.write(prompt, "archive", raw=True)
        watch.CONFIG = {"scans": [dict(self.SCAN, prompt=prompt)]}
        self.assertFalse(any("the prompt file is missing" in warning for warning in self.check()))

    def test_atlassian_conf_wrong_shape(self):
        self.write(watch.ATLASSIAN_CONF, {"site": "x.atlassian.net", "email": "no-at-sign"})
        self.assertTrue(any("atlassian_conf" in w for w in self.check()))
        self.write(watch.ATLASSIAN_CONF, {"site": "x.atlassian.net", "email": "a@b.com"})
        self.assertFalse(any("atlassian_conf" in w for w in self.check()))

    def test_own_state_file_unreadable(self):
        self.write(watch.STATE_PATH, "[1, 2", raw=True)
        self.assertTrue(any("unreadable JSON" in w for w in self.check()))

    def test_app_metadata_missing_or_unlinked(self):
        os.remove(watch.APP_CLI_PATH)
        self.assertTrue(any("no app session ids" in w for w in self.check()))
        self.write(watch.APP_CLI_PATH, {"local_1": ["other"]})
        self.assertTrue(any("have no app session id" in w for w in self.check(code={"a": {}, "b": {}, "c": {}})))

    def test_cowork_layout_not_recognized(self):
        self.assertTrue(any("Cowork folder layout" in w for w in watch.check_inputs({}, {"sid-1": {}}, None)))

    def test_history_files_that_give_no_messages(self):
        now = time.time()
        used = {}
        for i in range(10):
            p = os.path.join(self.tmp, "h%d.jsonl" % i)
            self.write(p, "x" * 5000, raw=True)
            used[p] = {"mtime": now, "meta": None}
        self.assertTrue(any("Session histories" in w and "format may have changed" in w for w in self.check(used=used)))

    def test_history_without_timestamps(self):
        used = {"/x.jsonl": {"mtime": time.time(), "meta": {"tail": ["user: hi"], "lastTs": None}}}
        self.assertTrue(any("lack timestamps" in w for w in self.check(used=used)))

    def test_hub_docsnap_shape(self):
        self.write(os.path.join(watch.HUB_DOCSNAP, "good.json"), {"comments": []})
        self.write(os.path.join(watch.HUB_DOCSNAP, "err.json"), {"error": "no connector"})
        self.assertEqual(self.check(), [])
        self.write(os.path.join(watch.HUB_DOCSNAP, "bad.json"), {"comments": "text"})
        self.assertTrue(any("bad.json" in w for w in self.check()))

    def test_hub_queue_malformed_lines(self):
        self.write(watch.HUB_QUEUE, '{"id": "1", "to": "local_x", "message": "m"}\nnot json\n{"id": "2"}\n', raw=True)
        self.assertTrue(any("Hub queue: 2 malformed" in w for w in self.check()))

    def test_gh_lookups_failing(self):
        self.write(watch.MERGED_PATH, {"/repo": {"b%d" % i: {"state": "unknown"} for i in range(6)}})
        self.assertTrue(any("gh auth status" in w for w in self.check()))

    def test_health_line(self):
        self.write(watch.CONFIG_PATH, {"pages": []})
        self.check()
        self.assertTrue(watch.health_line_text().startswith("⚠ Input check: 1 warning"))


class Requirements(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.saved = watch.ATLASSIAN_CONF
        watch.ATLASSIAN_CONF = os.path.join(self.tmp, "atlassian.conf")

    def tearDown(self):
        watch.ATLASSIAN_CONF = self.saved
        shutil.rmtree(self.tmp)

    def test_all_present(self):
        self.assertEqual(watch.check_requirements(which=lambda p: "/bin/" + p, system="Darwin"), [])

    def test_missing_programs(self):
        have = {"git"}
        w = watch.check_requirements(which=lambda p: p in have and "/bin/" + p, system="Linux")
        self.assertTrue(any("`claude`" in x for x in w))
        self.assertTrue(any("`gh`" in x for x in w))
        self.assertFalse(any("`git`" in x for x in w))

    def test_windows_needs_bash(self):
        w = watch.check_requirements(which=lambda p: p != "bash" and "/bin/" + p, system="Windows")
        self.assertTrue(any("Git Bash or WSL" in x for x in w))

    def test_keyring_tool_only_with_atlassian_conf(self):
        which = lambda p: p not in ("secret-tool", "security") and "/bin/" + p
        self.assertEqual(watch.check_requirements(which=which, system="Linux"), [])
        with open(watch.ATLASSIAN_CONF, "w", encoding="utf-8") as f:
            f.write("{}")
        self.assertTrue(any("secret-tool" in x for x in watch.check_requirements(which=which, system="Linux")))
        self.assertTrue(any("Keychain" in x for x in watch.check_requirements(which=which, system="Darwin")))


if __name__ == "__main__":
    unittest.main(verbosity=1)
