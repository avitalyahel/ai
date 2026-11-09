"""Run: python3 -I ~/Claude/sessions-watch/tests/test_safety.py

The paths that delete files or message sessions, on temp folders and local git repos only
(no network, GitHub or app):
- Space reclaim deletes the resources of finished, idle sessions.
- Space reclaim keeps the resources of dry runs, open, running and recently active sessions.
- Space reclaim never deletes a transcript or a link target.
- Worktree removal removes a clean, merged worktree of idle sessions.
- Worktree removal keeps dirty, locked, busy, parked, shared and never-active worktrees.
- A kept merged worktree gets a log line that says why.
- The hub gets a message when the cooldown is over and on a merge, for at most 3 sessions a round.
- The hub gets no message for a running session or during the 30-minute cooldown.
- purge_old.py deletes only old done sessions.
- purge_old.py never globs with an id that is not a UUID.
- purge_old.py deletes nothing without state.json.
- purge_old.py removes a clean, merged worktree and its branch.
- purge_old.py keeps a dirty, newer-session or unmerged worktree and its branch.
"""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.dirname(HERE)
spec = importlib.util.spec_from_file_location("watch", os.path.join(TOOL, "watch.py"))
watch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watch)

MERGED = [{"name": "AX-1-x", "state": "merged"}]
OPEN = [{"name": "AX-1-x", "state": "open"}]
PARKED = [{"name": "AX-1-x", "state": "open", "parked": True}]
GIT_CONFIG = ["-c", "user.email=t@example.com", "-c", "user.name=t", "-c", "commit.gpgsign=false"]


def ago(**delta):
    return (datetime.now(timezone.utc) - timedelta(**delta)).strftime("%Y-%m-%dT%H:%M:%SZ")


def git(cwd, *args):
    result = subprocess.run(
        ["git", "-C", cwd, *GIT_CONFIG, *args], check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


def new_repo(root):
    repo = os.path.join(root, "repo")
    os.makedirs(repo)
    git(repo, "init", "-q", "-b", "develop")
    git(repo, "commit", "-q", "--allow-empty", "-m", "init")
    return repo


def touch(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8"):
        pass


class Patched(unittest.TestCase):
    """Saves the watch attributes a test changes and restores them after it."""

    def patch(self, **values):
        for name, value in values.items():
            self.addCleanup(setattr, watch, name, getattr(watch, name))
            setattr(watch, name, value)

    def temp_dir(self, **kwargs):
        path = tempfile.mkdtemp(**kwargs)
        self.addCleanup(shutil.rmtree, path, True)
        return path


class Reclaim(Patched):
    ALL = {"history", "env", "todo", "scratch"}

    def test_reclaim_deletes_eligible_session_resources(self):
        cases = [
            ("merged", MERGED, False, {"days": 4}, "done", self.ALL),
            ("done scratch", OPEN, False, {"hours": 2}, "done", {"scratch"}),
        ]
        for name, branches, running, idle, status, expected in cases:
            with self.subTest(name):
                self.assertEqual(self.reclaim("on", branches, running, idle, status), expected)

    def test_reclaim_keeps_ineligible_session_resources(self):
        cases = [
            ("dry run", None, MERGED, False, {"days": 4}, "done"),
            ("open branch", "on", OPEN, False, {"days": 4}, "partial"),
            ("running", "on", MERGED, True, {"days": 4}, "done"),
            ("idle too short", "on", MERGED, False, {"days": 1}, "partial"),
            ("done but running", "on", OPEN, True, {"hours": 2}, "done"),
        ]
        for name, mode, branches, running, idle, status in cases:
            with self.subTest(name):
                self.assertEqual(self.reclaim(mode, branches, running, idle, status), set())

    def reclaim(self, mode, branches, running, idle, status):
        home, work, outside = self.temp_dir(), self.temp_dir(), self.temp_dir()
        scratch_root = self.temp_dir(prefix="claude-", dir="/tmp")
        sid = str(uuid.uuid4())
        claude = os.path.join(home, ".claude")
        paths = {
            "history": os.path.join(claude, "file-history", sid, "f"),
            "env": os.path.join(claude, "session-env", sid),
            "todo": os.path.join(claude, "todos", sid + "-agent.json"),
            "scratch": os.path.join(scratch_root, "proj", sid, "f"),
            "transcript": os.path.join(claude, "projects", "p", sid + ".jsonl"),
        }
        touch(os.path.join(outside, "keep.txt"))
        for key, path in paths.items():
            if key == "env":
                os.makedirs(os.path.dirname(path), exist_ok=True)
                os.symlink(outside, path)
            else:
                touch(path)
        self.patch(
            HOME=home,
            RECLAIM_MODE=os.path.join(work, "reclaim.mode"),
            RECLAIM_REPORT=os.path.join(work, "report.txt"),
            RECLAIM_LOG=os.path.join(work, "reclaim.log"),
        )
        if mode:
            with open(watch.RECLAIM_MODE, "w", encoding="utf-8") as mode_file:
                mode_file.write(mode)
        code = {
            sid: {"branchInfo": branches, "running": running, "lastTs": ago(**idle), "title": "t"}
        }
        watch.reclaim_merged(code, [], {"code": {"sessions": {sid: {"status": status}}}})
        self.assertTrue(os.path.exists(paths["transcript"]))
        self.assertTrue(os.path.exists(os.path.join(outside, "keep.txt")))
        folders = {"history", "scratch"}
        return {
            key
            for key, path in paths.items()
            if not os.path.exists(os.path.dirname(path) if key in folders else path)
        }


class WorktreeRemoval(Patched):
    IDLE = ago(hours=2)

    def setUp(self):
        real_run = watch.run

        def no_gh(args, cwd, timeout=30):
            self.assertNotEqual(args[0], "gh")
            return real_run(args, cwd, timeout)

        self.patch(run=no_gh)

    def test_merged_worktree_of_idle_sessions_is_removed(self):
        removed, lines = self.remove("merged", MERGED)
        self.assertTrue(removed)
        self.assertTrue(any(line.startswith("REMOVED") for line in lines), lines)

    def test_unsafe_worktrees_are_kept(self):
        cases = [
            ("untracked file", "merged", MERGED, {"dirty": True}),
            ("locked", "merged", MERGED, {"locked": True}),
            ("running", "merged", MERGED, {"running": True}),
            ("active 5 minutes ago", "merged", MERGED, {"last": ago(minutes=5)}),
            ("no last activity time", "merged", MERGED, {"last": None}),
            ("open branch", "open", OPEN, {}),
            ("parked branch", "open", PARKED, {}),
            ("merged, another unfinished session", "merged", MERGED, {"other": OPEN}),
        ]
        for name, pr_state, branches, options in cases:
            with self.subTest(name):
                removed, _ = self.remove(pr_state, branches, **options)
                self.assertFalse(removed)

    def test_kept_merged_worktree_logs_why(self):
        cases = [
            ("busy", {"last": ago(minutes=5)}, "busy"),
            ("another unfinished session", {"other": OPEN}, "not finished"),
            ("locked", {"locked": True}, "locked"),
            ("untracked file", {"dirty": True}, "uncommitted"),
        ]
        for name, options, reason in cases:
            with self.subTest(name):
                _, lines = self.remove("merged", MERGED, **options)
                self.assertTrue(
                    any(line.startswith("KEPT") and reason in line for line in lines), lines
                )

    def remove(
        self, pr_state, branches, dirty=False, locked=False, running=False, last=IDLE, other=None
    ):
        root = self.temp_dir()
        repo = new_repo(root)
        worktree = os.path.join(repo, ".claude", "worktrees", "w1")
        git(repo, "worktree", "add", "-q", "-b", "AX-1-x", worktree)
        if dirty:
            touch(os.path.join(worktree, "new.txt"))
        if locked:
            git(repo, "worktree", "lock", worktree)
        repo_real = os.path.realpath(repo)
        pr_cache = {
            "state": pr_state,
            "parked": branches[0].get("parked", False),
            "draft": False,
            "checked": 9e12,
        }
        prs = {repo_real: {"AX-1-x": pr_cache}}
        code = {
            "s1": {"cwd": worktree, "running": running, "lastTs": last, "branchInfo": branches}
        }
        if other:
            other_branches = [{"name": "AX-2-y", "state": "open"}]
            code["s2"] = {
                "cwd": worktree,
                "running": False,
                "lastTs": self.IDLE,
                "branchInfo": other_branches,
            }
        self.patch(WORKTREE_LOG=os.path.join(root, "worktrees.log"))
        lines = []
        watch.remove_merged_worktrees({repo_real}, code, prs, lines)
        self.assertTrue(os.path.isdir(os.path.join(repo, ".git")))
        return not os.path.isdir(worktree), lines


def snapshot(state="open", comments=1):
    return {
        "state": state,
        "mergeable": "",
        "reviews": [],
        "comments": comments,
        "inline": 0,
        "checks": "passed",
        "url": "https://github.com/o/r/pull/1",
        "latest": {},
        "own": False,
        "draft": False,
        "decision": "",
        "lastOthers": "",
        "commitsOthers": 0,
        "labelsOthers": [],
        "pending": [],
    }


class WakeGuardRails(Patched):
    PATHS = {
        "WORK": "",
        "STATE_PATH": "state.json",
        "MERGED_PATH": "prs.json",
        "PRWATCH_PATH": "prwatch.json",
        "PRWATCH_REPORT": "prwatch.txt",
        "HUB_DIR": "hub",
        "HUB_QUEUE": "hub/queue.jsonl",
        "HUB_DELIVERED": "hub/delivered.log",
        "CACHE_PATH": "cache.json",
    }

    def test_hub_gets_messages_within_the_limits(self):
        cases = [
            ("cooldown over", [(False, 1900, "open")], 1),
            ("merge in cooldown", [(False, 600, "merged")], 1),
            ("round limit", [(False, None, "open")] * 5, 3),
        ]
        for name, sessions, expected in cases:
            with self.subTest(name):
                self.assertEqual(self.queued(sessions), expected)

    def test_hub_gets_no_message_for_busy_or_recent_sessions(self):
        cases = [
            ("running", [(True, None, "open")]),
            ("in cooldown", [(False, 600, "open")]),
            ("merge while running", [(True, None, "merged")]),
        ]
        for name, sessions in cases:
            with self.subTest(name):
                self.assertEqual(self.queued(sessions), 0)

    def queued(self, sessions):
        work = self.temp_dir()
        self.patch(**{name: os.path.join(work, path) for name, path in self.PATHS.items()})
        self.patch(
            my_login=lambda prs: "me",
            app_id_of=lambda sid: "local_" + sid,
            coordinator_wakes=lambda *args: 0,
            atlassian_auth=lambda: (None, "off"),
            docs_wakes=lambda *args, **kwargs: 0,
            hub_notify_if_down=lambda *args: None,
        )
        state, db, collected, pr_states = (
            {"code": {"sessions": {}}},
            {"prs": {}, "wakes": {}},
            {"code": {}},
            {},
        )
        for index, (running, woken_ago, pr_state) in enumerate(sessions):
            sid, key = f"s{index}", f"o/r#{index}"
            state["code"]["sessions"][sid] = {
                "cwd": work,
                "title": sid,
                "lastTs": ago(hours=1),
                "prInfo": [{"pr": key, "state": "open"}],
            }
            db["prs"][key] = {"fp": snapshot(comments=0), "target": None}
            if woken_ago is not None:
                db["wakes"][sid] = {"at": time.time() - woken_ago}
            collected["code"][sid] = {"running": running}
            pr_states[index] = pr_state
        watch.save_json(os.path.join(work, "state.json"), state)
        watch.save_json(os.path.join(work, "collected.json"), collected)
        watch.save_json(os.path.join(work, "prwatch.json"), db)
        self.patch(pr_snapshot=lambda target, me: snapshot(state=pr_states[int(target["num"])]))
        watch.cmd_prwatch()
        queue = os.path.join(work, "hub", "queue.jsonl")
        if not os.path.exists(queue):
            return 0
        with open(queue, encoding="utf-8") as queue_file:
            return sum(1 for line in queue_file if line.strip())


class PurgeOld(unittest.TestCase):
    """purge_old.py runs as a script, on a copy, with a temp HOME."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)
        self.home = os.path.join(self.root, "home")
        self.tool = os.path.join(self.root, "tool")
        os.makedirs(self.tool)
        shutil.copy(os.path.join(TOOL, "purge_old.py"), self.tool)
        self.repo = new_repo(self.root)

    def run_purge(self, state, *args):
        if state is not None:
            with open(os.path.join(self.tool, "state.json"), "w", encoding="utf-8") as state_file:
                json.dump(state, state_file)
        env = {"HOME": self.home, "PATH": os.environ["PATH"], "GIT_CONFIG_NOSYSTEM": "1"}
        command = [sys.executable, "-I", os.path.join(self.tool, "purge_old.py"), *args]
        return subprocess.run(command, env=env, capture_output=True, text=True)

    def session_files(self, sid):
        claude = os.path.join(self.home, ".claude")
        paths = [
            os.path.join(claude, "projects", "p", sid + ".jsonl"),
            os.path.join(claude, "projects", "p", sid, "sub.jsonl"),
            os.path.join(claude, "file-history", sid, "f"),
            os.path.join(claude, "todos", sid + "-agent.json"),
            os.path.join(claude, "session-env", sid, "e"),
        ]
        for path in paths:
            touch(path)
        return paths

    @staticmethod
    def code_state(sessions):
        return {"home": {"sessions": {}}, "code": {"sessions": sessions}}

    def test_purge_old_deletes_only_old_done_sessions(self):
        for apply in (False, True):
            with self.subTest(apply=apply):
                sessions, files = {}, {}
                for status, days, purged in (
                    ("done", 20, True),
                    ("done", 3, False),
                    ("partial", 20, False),
                ):
                    sid = str(uuid.uuid4())
                    files[sid] = (self.session_files(sid), purged and apply)
                    sessions[sid] = {
                        "status": status,
                        "lastTs": ago(days=days),
                        "cwd": self.repo,
                        "branch": "develop",
                        "title": sid,
                    }
                result = self.run_purge(self.code_state(sessions), *(["--apply"] if apply else []))
                self.assertEqual(result.returncode, 0, result.stderr)
                for paths, gone in files.values():
                    for path in paths:
                        self.assertNotEqual(os.path.exists(path), gone, path)

    def test_purge_old_never_globs_with_a_bad_session_id(self):
        keep = self.session_files(str(uuid.uuid4()))
        for bad_id in ("", "*", "../x"):
            with self.subTest(bad_id=bad_id):
                sessions = {
                    bad_id: {"status": "done", "lastTs": "2020-01-01T00:00:00Z", "title": "bad"}
                }
                result = self.run_purge(self.code_state(sessions), "--apply")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("SKIPPED: not a session id", result.stdout)
                for path in keep:
                    self.assertTrue(os.path.exists(path), path)

    def test_purge_old_without_state_deletes_nothing(self):
        result = self.run_purge(None, "--apply")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Nothing was deleted", result.stdout)
        self.assertNotIn("Traceback", result.stderr)

    def test_purge_old_removes_clean_merged_worktree_and_branch(self):
        worktree, result = self.purge_worktree(dirty=False, newer=False, merged=True)
        self.assertFalse(os.path.isdir(worktree), result.stdout)
        self.assertFalse(self.branch_exists(), result.stdout)

    def test_purge_old_keeps_unsafe_worktrees_and_branches(self):
        cases = [
            ("untracked file", True, False, True),
            ("newer session", False, True, True),
            ("not merged", False, False, False),
        ]
        for name, dirty, newer, merged in cases:
            with self.subTest(name):
                self.setUp()
                worktree, result = self.purge_worktree(dirty, newer, merged)
                self.assertTrue(os.path.isdir(worktree), result.stdout)
                self.assertTrue(self.branch_exists(), result.stdout)

    def purge_worktree(self, dirty, newer, merged):
        worktree = os.path.join(self.repo, ".claude", "worktrees", "w1")
        git(self.repo, "worktree", "add", "-q", "-b", "AX-1-x", worktree)
        git(worktree, "commit", "-q", "--allow-empty", "-m", "work")
        target = git(worktree, "rev-parse", "HEAD") if merged else "develop"
        git(self.repo, "update-ref", "refs/remotes/origin/develop", target)
        if dirty:
            touch(os.path.join(worktree, "new.txt"))
        old = {
            "status": "done",
            "lastTs": ago(days=20),
            "cwd": worktree,
            "branch": "AX-1-x",
            "title": "old",
        }
        sessions = {str(uuid.uuid4()): old}
        if newer:
            new = {
                "status": "running",
                "lastTs": ago(minutes=1),
                "cwd": worktree,
                "branch": "AX-1-x",
                "title": "new",
            }
            sessions[str(uuid.uuid4())] = new
        result = self.run_purge(self.code_state(sessions), "--apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        return worktree, result

    def branch_exists(self):
        command = ["git", "-C", self.repo, "show-ref", "--verify", "--quiet", "refs/heads/AX-1-x"]
        return subprocess.run(command).returncode == 0


if __name__ == "__main__":
    unittest.main()
