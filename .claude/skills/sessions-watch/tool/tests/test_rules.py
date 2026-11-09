"""Run: python3 -I ~/Claude/sessions-watch/tests/test_rules.py

The status, Done, label and message rules (design sections 6, 7 and 10), without GitHub or the app:
each test feeds the rule the data sw would have collected.

- Done rules: when all branches and PRs are merged, parked, approved or closed, and when something is still open.
- DO_NOT_MERGE spellings and the Self/Peer CR and Review labels.
- Branch ownership, fork history and the PRs a session acted on.
- PR messages: what triggers one, and that they carry metadata only.
- Hub queue: enqueue, delivery, acknowledgement, retention and the size limit.
- Scheduled runs: a run from config.json "scheduledRuns" never shows as Waiting. With a report as its last message
  it is Done, and without one it is Partial. Other statuses stay. The title prefix is matched without case.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("watch", os.path.join(HERE, "..", "watch.py"))
watch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watch)


def br(state, mine=True, parked=False, name="AX-1-x"):
    return {"name": name, "state": state, "repo": "cortex", "mine": mine, "parked": parked}


def pr(state, mine=False, parked=False, ref="Axonius/cortex#100"):
    return {"pr": ref, "state": state, "mine": mine, "parked": parked}


class DoneRules(unittest.TestCase):
    """A Code session is Done by git/GitHub only when nothing it owns is still open."""

    def test_all_branches_merged_is_done(self):
        self.assertTrue(watch.all_merged({"branchInfo": [br("merged"), br("merged", name="b2")]}))

    def test_one_open_branch_keeps_it_open(self):
        self.assertFalse(watch.all_merged({"branchInfo": [br("merged"), br("open", name="b2")]}))
        self.assertEqual(watch.open_work({"branchInfo": [br("merged"), br("open", name="b2")]}), ["b2"])

    def test_parked_draft_counts_as_done(self):
        self.assertTrue(watch.all_merged({"branchInfo": [br("open", parked=True)]}))
        self.assertEqual(watch.open_work({"branchInfo": [br("open", parked=True)]}), [])

    def test_approved_peer_pr_closed_pr_and_merged_pr_are_done(self):
        for st in ("approved", "closed", "merged"):
            self.assertTrue(watch.all_merged({"prInfo": [pr(st)]}), st)

    def test_open_peer_pr_keeps_it_open(self):
        self.assertFalse(watch.all_merged({"prInfo": [pr("open")]}))
        self.assertEqual(watch.open_work({"prInfo": [pr("open")]}), ["PR Axonius/cortex#100"])

    def test_branches_without_a_pr_are_ignored(self):
        m = {"branchInfo": [br("none", name="claude/wt"), br("merged")]}
        self.assertTrue(watch.all_merged(m))
        self.assertFalse(watch.all_merged({"branchInfo": [br("none")]}), "nothing to judge by: not Done")

    def test_no_branches_or_prs_is_not_done(self):
        self.assertFalse(watch.all_merged({}))

    def test_closed_branch_is_not_done(self):
        # A branch whose PR was closed without merging: the work was not delivered.
        self.assertFalse(watch.all_merged({"branchInfo": [br("closed")]}))


class DoNotMergeLabel(unittest.TestCase):
    def test_spellings(self):
        for name in ("DO_NOT_MERGE", "DO NOT MERGE", "do-not-merge", "Do not merge (patch)", "DONT MERGE",
                     "Don't merge", "DNM"):
            self.assertTrue(watch.is_dnm_label(name), name)

    def test_not_dnm(self):
        for name in ("merge-queue", "needs merge", "", None, "dnmx", "ready to merge"):
            self.assertFalse(watch.is_dnm_label(name), name)

    def test_has_dnm(self):
        self.assertTrue(watch.has_dnm({"labels": [{"name": "bug"}, {"name": "do not merge"}]}))
        self.assertFalse(watch.has_dnm({"labels": []}))


class WithScheduledRuns(unittest.TestCase):
    """A test with config.json "scheduledRuns" set to one prefix."""

    PREFIX = "inbox"

    def setUp(self) -> None:
        self.addCleanup(setattr, watch, "SCHEDULED_RUNS", watch.SCHEDULED_RUNS)
        watch.SCHEDULED_RUNS = (self.PREFIX,)


class ScheduledRuns(WithScheduledRuns):
    """A recurring run (config.json "scheduledRuns") has nobody to answer it.

    It never shows as Waiting (design section 10).
    """

    PREFIX = "inbox auto-archive"
    TITLE = "Inbox auto-archive (GitHub PRs only)"

    def run_merge(
        self,
        status: str,
        title: str | None = None,
        ends_with_report: bool = True,
        running: bool = False,
        page: str = "home",
    ) -> dict:
        state = {"home": {"sessions": {}}, "code": {"sessions": {}}}
        session = {
            "title": title or self.TITLE,
            "metaTitle": title or self.TITLE,
            "lastTs": "2026-11-09T07:00:00Z",
            "running": running,
            "endsWithReport": ends_with_report,
        }
        classified = [
            {
                "page": page,
                "id": "k1",
                "status": status,
                "ask": "Archive mail",
                "progress": "Archived 12 threads.",
                "blocker": "Anything else?",
                "next": "Answer",
            },
        ]
        collected = {
            "home": None,
            "code": None,
            page: {"k1": session},
        }
        return watch.merge(state, collected, classified)[page]["sessions"]["k1"]

    def test_waiting_with_a_report_as_last_message_is_done(self) -> None:
        session = self.run_merge("waiting")
        self.assertEqual((session["status"], session["outcome"]), ("done", "Archived 12 threads."))

    def test_done_clears_the_question_and_the_next_step(self) -> None:
        session = self.run_merge("waiting")
        self.assertEqual((session["ask"], session["blocker"], session["next"]), ("", "", ""))

    def test_waiting_without_a_report_is_partial_never_waiting(self) -> None:
        session = self.run_merge("waiting", ends_with_report=False)
        self.assertEqual(session["status"], "partial")
        self.assertIn("stopped before its report", session["blocker"])

    def test_other_statuses_are_kept(self) -> None:
        for status in ("blocked", "partial", "done"):
            with self.subTest(status):
                self.assertEqual(self.run_merge(status)["status"], status)

    def test_a_running_run_stays_running(self) -> None:
        self.assertEqual(self.run_merge("waiting", running=True)["status"], "running")

    def test_a_title_not_on_the_list_keeps_waiting(self) -> None:
        self.assertEqual(self.run_merge("waiting", title="Review PR 100")["status"], "waiting")

    def test_code_sessions_follow_the_same_rule(self) -> None:
        self.assertEqual(self.run_merge("waiting", page="code")["status"], "done")

    def test_a_title_with_other_case_and_leading_space_matches(self) -> None:
        self.assertTrue(watch.scheduled_run("  INBOX auto-archive - run 9:00"))

    def test_a_title_that_only_contains_the_name_does_not_match(self) -> None:
        self.assertFalse(watch.scheduled_run("Not the inbox auto-archive"))

    def test_no_title_does_not_match(self) -> None:
        self.assertFalse(watch.scheduled_run(None))

    def test_nothing_matches_when_the_list_is_empty(self) -> None:
        watch.SCHEDULED_RUNS = ()
        self.assertFalse(watch.scheduled_run(self.TITLE))

    def test_prefixes_are_stripped_and_lower_cased_and_other_items_dropped(self) -> None:
        self.assertEqual(watch.title_prefixes([" Inbox ", "", 7, None]), ("inbox",))

    def test_a_string_instead_of_a_list_gives_no_prefix(self) -> None:
        self.assertEqual(watch.title_prefixes("Inbox"), ())

    def test_no_value_gives_no_prefix(self) -> None:
        self.assertEqual(watch.title_prefixes(None), ())

    def test_assistant_text_as_last_message_is_a_report(self) -> None:
        for tail in (["user: archive", "assistant: Archived 12 threads."], ["assistant: done"]):
            with self.subTest(tail):
                self.assertTrue(watch.ends_with_report(tail))

    def test_anything_else_as_last_message_is_not_a_report(self) -> None:
        for tail in (
            ["assistant: Archived 12 threads.", "user: thanks"],
            ["assistant: (called Gmail: unlabel_thread)"],
            [],
            None,
        ):
            with self.subTest(tail):
                self.assertFalse(watch.ends_with_report(tail))


class ScheduledRunsWithoutNewClassification(WithScheduledRuns):
    """The normal path: the state already says Waiting, and nothing new was classified."""

    def merge_waiting(self, ends_with_report: bool, progress: str = "") -> dict:
        waiting = {
            "title": "Inbox run",
            "status": "waiting",
            "progress": progress,
            "ask": "Anything else?",
            "blocker": "Anything else?",
            "next": "Answer",
        }
        state = {"home": {"sessions": {"k1": waiting}}, "code": {"sessions": {}}}
        collected = {
            "home": {
                "k1": {
                    "title": "Inbox run",
                    "metaTitle": "Inbox run",
                    "lastTs": "2026-11-09T07:00:00Z",
                    "running": False,
                    "endsWithReport": ends_with_report,
                },
            },
            "code": None,
        }
        return watch.merge(state, collected, [])["home"]["sessions"]["k1"]

    def test_a_waiting_run_with_a_report_becomes_done_with_the_default_outcome(self) -> None:
        session = self.merge_waiting(True)
        self.assertEqual(session["status"], "done")
        self.assertEqual(
            session["outcome"],
            "Scheduled run finished; its last message is the report.",
        )

    def test_the_outcome_is_the_summary_when_there_is_one(self) -> None:
        session = self.merge_waiting(True, progress="Archived 3 threads.")
        self.assertEqual(session["outcome"], "Archived 3 threads.")

    def test_a_waiting_run_without_a_report_becomes_partial_with_a_next_step(self) -> None:
        session = self.merge_waiting(False)
        self.assertEqual(session["status"], "partial")
        self.assertEqual(session["next"], "Open the session and read its last message.")


class LastMessageHasToolCall(unittest.TestCase):
    """ends_with_report reads the raw message, not the cut text of the tail."""

    def test_the_flag_makes_a_message_with_a_tool_call_no_report(self) -> None:
        self.assertFalse(watch.ends_with_report(["assistant: Archiving now."], last_tool_use=True))

    def test_a_report_that_says_called_off_is_a_report(self) -> None:
        tail = ["assistant: Archived 3 threads. Skipped: Meeting (called off)."]
        self.assertTrue(watch.ends_with_report(tail))

    def test_a_long_text_with_a_tool_call_is_no_report_even_when_the_tail_cut_the_call_away(
        self,
    ) -> None:
        tail = ["assistant: " + "x" * 600]
        self.assertFalse(watch.ends_with_report(tail, last_tool_use=True))

    def test_the_transcript_parser_sets_the_flag_from_the_raw_blocks(self) -> None:
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, True)
        path = os.path.join(folder, "session.jsonl")
        lines = [
            {
                "type": "user",
                "timestamp": "2026-11-09T10:00:00Z",
                "message": {"role": "user", "content": "archive"},
            },
            {
                "type": "assistant",
                "timestamp": "2026-11-09T10:00:05Z",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "Archiving now."},
                        {
                            "type": "tool_use",
                            "name": "Bash",
                            "input": {"command": "ls"},
                        },
                    ],
                },
            },
        ]
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(json.dumps(line) for line in lines))
        self.assertTrue(watch.parse_transcript(path)["lastToolUse"])

    def test_the_transcript_parser_clears_the_flag_for_a_plain_text_message(self) -> None:
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, True)
        path = os.path.join(folder, "session.jsonl")
        lines = [
            {
                "type": "user",
                "timestamp": "2026-11-09T10:00:00Z",
                "message": {"role": "user", "content": "archive"},
            },
            {
                "type": "assistant",
                "timestamp": "2026-11-09T10:00:05Z",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "Archived 3 threads."}],
                },
            },
        ]
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(json.dumps(line) for line in lines))
        self.assertFalse(watch.parse_transcript(path)["lastToolUse"])


class RoundLogLines(unittest.TestCase):
    def test_a_queued_message_is_kept(self) -> None:
        self.assertTrue(watch.shown_in_round_log("r#2: QUEUED for 's2' - 1 new comment(s)."))

    def test_a_notice_of_either_hub_is_kept(self) -> None:
        for line in ("Trackers Hub: notified you (x).", "Scans Hub: notified you (x)."):
            with self.subTest(line):
                self.assertTrue(watch.shown_in_round_log(line))

    def test_any_other_line_is_not(self) -> None:
        self.assertFalse(watch.shown_in_round_log("Hub: never started; 0 event(s) waiting."))


class HubTitles(unittest.TestCase):
    """The hub sessions are not listed on the pages."""

    def test_the_scans_hub_is_excluded(self) -> None:
        self.assertTrue(watch.excluded({"title": "Scans Hub"}))

    def test_the_trackers_hub_is_excluded(self) -> None:
        self.assertTrue(watch.excluded({"title": "Trackers Hub"}))

    def test_another_session_is_not_excluded(self) -> None:
        self.assertFalse(watch.excluded({"title": "Review PR 100"}))


class CollectKeepsEndsWithReport(unittest.TestCase):
    """cmd_collect writes endsWithReport to work/collected.json for both pages.

    Merge reads it there.
    """

    PATCHED = (
        "WORK",
        "STATE_PATH",
        "CACHE_PATH",
        "MERGED_PATH",
        "ARCHIVED_REPORT",
        "SIDEBAR_REPORT",
        "BRANCHES_REPORT",
        "HUB_DIR",
        "HUB_QUEUE",
        "find_archived",
        "collect_code",
        "collect_home",
        "check_inputs",
        "compare_with_app",
        "analyze_branches",
        "attach_tracking",
        "remove_merged_worktrees",
        "reclaim_merged",
        "session_kinds",
    )

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        for name in self.PATCHED:
            self.addCleanup(setattr, watch, name, getattr(watch, name))
        watch.WORK = self.tmp
        for name, base in (
            ("STATE_PATH", "state.json"),
            ("CACHE_PATH", "cache.json"),
            ("MERGED_PATH", "prs.json"),
            ("ARCHIVED_REPORT", "archived.txt"),
            ("SIDEBAR_REPORT", "sidebar.txt"),
            ("BRANCHES_REPORT", "branches.txt"),
        ):
            setattr(watch, name, os.path.join(self.tmp, base))
        watch.HUB_DIR = os.path.join(self.tmp, "hub")
        watch.HUB_QUEUE = os.path.join(watch.HUB_DIR, "queue.jsonl")
        watch.find_archived = lambda: (set(), frozenset(), "Archived: none")
        watch.check_inputs = lambda used, code, home: []
        watch.compare_with_app = lambda code, lines: None
        watch.analyze_branches = lambda code, prs, lines: set()
        watch.attach_tracking = lambda code, home: None
        watch.remove_merged_worktrees = lambda repos, code, prs, lines: None
        watch.reclaim_merged = lambda code, lines, state=None: (False, 0)
        watch.session_kinds = lambda session: []
        watch.collect_code = lambda cache, used, archived=frozenset(): (
            {
                "c1": {"title": "Scan", "tail": ["user: go", "assistant: Archived 3 threads."]},
                "c2": {"title": "Work", "tail": ["assistant: (called Bash: ls)"]},
            },
            "code diag",
        )
        watch.collect_home = lambda cache, used, archived_keys=frozenset(): (
            {"h1": {"title": "Inbox", "tail": ["assistant: Done."]}},
            "home diag",
        )

    def collected(self) -> dict:
        with contextlib.redirect_stdout(io.StringIO()):
            watch.cmd_collect()
        with open(os.path.join(self.tmp, "collected.json"), encoding="utf-8") as handle:
            return json.load(handle)

    def test_code_sessions_keep_the_flag(self) -> None:
        flags = {key: value["endsWithReport"] for key, value in self.collected()["code"].items()}
        self.assertEqual(flags, {"c1": True, "c2": False})

    def test_home_sessions_keep_the_flag(self) -> None:
        flags = {key: value["endsWithReport"] for key, value in self.collected()["home"].items()}
        self.assertEqual(flags, {"h1": True})

    def test_the_tail_itself_is_not_kept(self) -> None:
        self.assertNotIn("tail", self.collected()["home"]["h1"])


class Labels(unittest.TestCase):
    def test_self_and_peer_cr_from_pr_authors(self):
        self.assertEqual(watch.session_kinds({"branchInfo": [br("open", mine=True)]}), ["Self CR"])
        self.assertEqual(watch.session_kinds({"prInfo": [pr("open", mine=False)]}), ["Peer CR"])
        self.assertEqual(watch.session_kinds({"branchInfo": [br("open", mine=True)], "prInfo": [pr("open")]}),
                         ["Self CR", "Peer CR"])

    def test_branch_without_pr_gives_no_label(self):
        self.assertEqual(watch.session_kinds({"branchInfo": [br("none", mine=True)]}), [])

    def test_review_labels_from_page_roles(self):
        self.assertEqual(watch.session_kinds({"pageRoles": ["edit", "comment"]}), ["Self Review"])
        self.assertEqual(watch.session_kinds({"pageRoles": ["comment"]}), ["Peer Review"])


class BranchOwnership(unittest.TestCase):
    """Branches count only if the session created or pushed them, or ran in its worktree."""

    def test_branches_found_in_commands(self):
        self.assertEqual(watch.branches_in_command("git worktree add ../wt -b AX-1-fix origin/develop"), {"AX-1-fix"})
        self.assertEqual(watch.branches_in_command("git checkout -b AX-2-new"), {"AX-2-new"})
        self.assertEqual(watch.branches_in_command("git push -u origin AX-3-x"), {"AX-3-x"})
        self.assertEqual(watch.branches_in_command("gh pr create --head AX-4-y --fill"), {"AX-4-y"})

    def test_base_branches_and_reads_are_ignored(self):
        self.assertEqual(watch.branches_in_command("git push origin develop"), set())
        self.assertEqual(watch.branches_in_command("git checkout AX-5-other && git log"), set())

    def test_main_checkout_vs_worktree(self):
        tmp = tempfile.mkdtemp()
        try:
            main = os.path.join(tmp, "repo")
            wt = os.path.join(tmp, "wt")
            os.makedirs(os.path.join(main, ".git"))
            os.makedirs(wt)
            with open(os.path.join(wt, ".git"), "w", encoding="utf-8") as f:
                f.write("gitdir: %s/.git/worktrees/wt\n" % main)
            self.assertTrue(watch.in_main_checkout(os.path.join(main, "src")))
            self.assertFalse(watch.in_main_checkout(wt))
        finally:
            shutil.rmtree(tmp)

    def test_prs_acted_on(self):
        self.assertEqual(watch.prs_in_command("gh pr review 100384 --approve"), {"#100384"})
        self.assertEqual(watch.prs_in_command("gh pr comment 12 --repo Axonius/cortex -b hi"), {"Axonius/cortex#12"})
        self.assertEqual(watch.prs_in_command("gh pr view 12"), set(), "reading a PR is not acting on it")


def transcript(tmp, rows):
    p = os.path.join(tmp, "s.jsonl")
    with open(p, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    return p


def user(text, ts="2026-11-08T10:00:00Z", **kw):
    return dict({"type": "user", "timestamp": ts, "message": {"role": "user", "content": text}}, **kw)


def bash(cmd, ts="2026-11-08T10:01:00Z"):
    return {"type": "assistant", "timestamp": ts, "message": {"role": "assistant", "content": [
        {"type": "text", "text": "running"}, {"type": "tool_use", "name": "Bash", "input": {"command": cmd}}]}}


class ForkHistory(unittest.TestCase):
    """A fork starts with a copy of its parent's history; the parent's branches and PRs are not the fork's."""

    def test_fork_drops_parent_branches(self):
        tmp = tempfile.mkdtemp()
        try:
            p = transcript(tmp, [
                user("build the feature"),
                bash("git push -u origin AX-1-parent"),
                user("The user forked this session", ts="2026-11-08T11:00:00Z"),
                bash("git push -u origin AX-2-fork", ts="2026-11-08T11:01:00Z"),
            ])
            meta = watch.parse_transcript(p)
            self.assertEqual(meta["branches"], ["AX-2-fork"])
            self.assertTrue(meta["forkOf"])
        finally:
            shutil.rmtree(tmp)

    def test_no_fork_keeps_branches(self):
        tmp = tempfile.mkdtemp()
        try:
            meta = watch.parse_transcript(transcript(tmp, [user("go"), bash("git push origin AX-1-a")]))
            self.assertEqual(meta["branches"], ["AX-1-a"])
            self.assertIsNone(meta["forkOf"])
        finally:
            shutil.rmtree(tmp)


def snap(**kw):
    base = {"state": "open", "mergeable": "MERGEABLE", "reviews": [], "comments": 0, "inline": 0, "checks": "passed",
            "latest": {}, "commitsOthers": 0, "labelsOthers": []}
    base.update(kw)
    return base


class PrMessages(unittest.TestCase):
    """What changes by others send a session a message (design section 7)."""

    def test_no_change_no_message(self):
        self.assertEqual(watch.pr_changes(snap(), snap()), [])

    def test_merge_and_close(self):
        self.assertEqual(watch.pr_changes(snap(), snap(state="merged")), ["The PR was merged."])
        self.assertEqual(watch.pr_changes(snap(), snap(state="closed")), ["The PR was closed without merging."])

    def test_new_review_and_comments(self):
        ch = watch.pr_changes(snap(), snap(reviews=["bob|CHANGES_REQUESTED|t1"], comments=2, inline=3))
        self.assertIn("New review from bob: changes requested.", ch)
        self.assertIn("2 new comment(s) on the PR.", ch)
        self.assertIn("3 new review comment(s) on the code.", ch)

    def test_dismissed_approval(self):
        ch = watch.pr_changes(snap(latest={"bob": "APPROVED"}), snap(latest={"bob": "DISMISSED"}))
        self.assertTrue(any("approval was dismissed" in c for c in ch))

    def test_ci_result_only_when_finished(self):
        self.assertEqual(watch.pr_changes(snap(checks="passed"), snap(checks="running")), [])
        self.assertEqual(watch.pr_changes(snap(checks="running"), snap(checks="failed: lint")), ["CI finished: failed: lint."])

    def test_commits_labels_conflicts_by_others(self):
        ch = watch.pr_changes(snap(), snap(commitsOthers=2, labelsOthers=[["ci-bot", "labeled", "needs-rebase", "t"]],
                                           mergeable="CONFLICTING"))
        self.assertTrue(any("2 new commit(s) pushed by others" in c for c in ch))
        self.assertTrue(any("added needs-rebase by ci-bot" in c for c in ch))
        self.assertIn("The PR now has merge conflicts with its base branch.", ch)

    def test_messages_carry_metadata_not_comment_text(self):
        # A snapshot holds counts and logins only, so no text written by others can reach a message.
        ch = watch.pr_changes(snap(), snap(reviews=["bob|COMMENTED|t1"], comments=1))
        self.assertTrue(all(len(c) < 120 for c in ch))


class HubQueue(unittest.TestCase):
    """Queue, delivery and retention (design sections 7, 8 and 11)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.saved = {k: getattr(watch, k) for k in ("HUB_DIR", "HUB_QUEUE", "HUB_DELIVERED", "HUB_LOCK")}
        watch.HUB_DIR = self.tmp
        watch.HUB_QUEUE = os.path.join(self.tmp, "queue.jsonl")
        watch.HUB_DELIVERED = os.path.join(self.tmp, "delivered.log")
        watch.HUB_LOCK = os.path.join(self.tmp, ".lock")

    def tearDown(self):
        for k, v in self.saved.items():
            setattr(watch, k, v)
        shutil.rmtree(self.tmp)

    def add(self, eid, at, delivered=False, pad=0):
        with open(watch.HUB_QUEUE, "a", encoding="utf-8") as f:
            f.write(json.dumps({"id": eid, "at": at, "to": "local_x", "message": "m" + "x" * pad}) + "\n")
        if delivered:
            with open(watch.HUB_DELIVERED, "a", encoding="utf-8") as f:
                f.write(eid + "\n")

    def ids(self):
        with open(watch.HUB_QUEUE, encoding="utf-8") as f:
            return [json.loads(ln)["id"] for ln in f]

    def test_enqueue_pending_ack(self):
        ev, err = watch.hub_enqueue("abcdef12-0000", "pr", "T", "hello", to="local_abc")
        self.assertIsNone(err)
        self.assertEqual([e["id"] for e in watch.hub_pending()], [ev])
        self.assertTrue(watch.hub_pending()[0]["message"].endswith("hello"))
        watch.cmd_hub_ack(ev)
        self.assertEqual(watch.hub_pending(), [])

    def test_no_app_id_is_not_queued(self):
        orig = watch.app_id_of
        watch.app_id_of = lambda sid: None
        try:
            ev, err = watch.hub_enqueue("abcdef12-0000", "pr", "T", "hello")
        finally:
            watch.app_id_of = orig
        self.assertIsNone(ev)
        self.assertIn("no app session id", err)

    def test_retention_keeps_undelivered_and_recent(self):
        self.add("old-delivered", "2026-01-01T00:00:00Z", delivered=True)
        self.add("old-waiting", "2026-01-01T00:00:00Z")
        self.add("new-delivered", watch.now_iso(), delivered=True)
        notes = watch.hub_trim(keep_days=14)
        self.assertEqual(self.ids(), ["old-waiting", "new-delivered"])
        with open(watch.HUB_DELIVERED, encoding="utf-8") as f:
            self.assertEqual(f.read().split(), ["new-delivered"])
        self.assertTrue(any("trimmed 1" in n for n in notes))
        self.assertEqual([e["id"] for e in watch.hub_pending()], ["old-waiting"])

    def test_size_limit_drops_oldest_delivered_first(self):
        now = watch.now_iso()
        self.add("a-delivered", now, delivered=True, pad=500)
        self.add("b-waiting", now, pad=500)
        self.add("c-delivered", now, delivered=True, pad=500)
        self.add("d-waiting", now, pad=500)
        watch.hub_trim(keep_days=14, max_bytes=1500)
        self.assertEqual(self.ids(), ["b-waiting", "d-waiting"])

    def test_size_limit_then_drops_oldest_undelivered(self):
        now = watch.now_iso()
        for i in range(4):
            self.add("w%d" % i, now, pad=500)
        notes = watch.hub_trim(keep_days=14, max_bytes=1200)
        self.assertEqual(self.ids(), ["w2", "w3"])
        self.assertTrue(any("2 undelivered" in n for n in notes))

    def test_nothing_to_trim_leaves_files_alone(self):
        self.add("x", watch.now_iso(), delivered=True)
        before = os.path.getmtime(watch.HUB_QUEUE)
        self.assertEqual(watch.hub_trim(), [])
        self.assertEqual(os.path.getmtime(watch.HUB_QUEUE), before)


if __name__ == "__main__":
    unittest.main(verbosity=1)
