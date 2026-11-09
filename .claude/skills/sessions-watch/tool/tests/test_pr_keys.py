"""Run: python3 -I ~/Claude/sessions-watch/tests/test_pr_keys.py

One PR is watched under one key, so a change is sent to its session once (it was sent twice: once as
'owner/repo#N' from the session's PR list and once as 'repo#N' from its branch list).
"""
import importlib.util
import os

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("watch", os.path.join(HERE, "..", "watch.py"))
watch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watch)

REPO_PATH = "/repos/cortex"


def _state(*sessions):
    return {"code": {"sessions": dict(sessions)}}


def _session(last_ts, pr_info=(), branch_info=()):
    return {"cwd": REPO_PATH, "title": "t-" + last_ts, "lastTs": last_ts,
            "prInfo": list(pr_info), "branchInfo": list(branch_info)}


PRS = {REPO_PATH: {"AX-1-branch": {"prs": [100384]}}}
PR_INFO = {"pr": "Axonius/cortex#100384", "state": "open"}
BRANCH_INFO = {"name": "AX-1-branch", "repo": "cortex", "state": "open"}


def test_a_pr_in_the_pr_list_and_the_branch_list_is_one_key_with_its_owner():
    targets = watch.watched_prs(_state(("s1", _session("2026-11-08", [PR_INFO], [BRANCH_INFO]))), {}, PRS)

    assert list(targets) == ["cortex#100384"]
    assert (targets["cortex#100384"]["full"], targets["cortex#100384"]["num"]) == ("Axonius/cortex", "100384")


def test_the_owner_is_kept_when_a_newer_session_has_the_pr_only_in_its_branch_list():
    state = _state(("old", _session("2026-11-07", [PR_INFO])), ("new", _session("2026-11-08", [], [BRANCH_INFO])))

    targets = watch.watched_prs(state, {}, PRS)

    assert list(targets) == ["cortex#100384"]
    assert (targets["cortex#100384"]["sid"], targets["cortex#100384"]["full"]) == ("new", "Axonius/cortex")


def test_old_owner_keys_merge_into_the_short_key_and_keep_the_marks_already_sent():
    db = {"prs": {
        "Axonius/cortex#100384": {"fp": {"v": "old"}, "reminded": "2026-11-08 17:00", "nudged": 5,
                                  "target": {"full": "Axonius/cortex"}},
        "cortex#100384": {"fp": {"v": "short"}, "target": {"full": None}},
        "Axonius/cortex#100385": {"fp": {"v": "only-long"}},
    }, "wakes": {}}

    watch.merge_pr_keys(db)

    assert db["prs"] == {
        "cortex#100384": {"fp": {"v": "short"}, "reminded": "2026-11-08 17:00", "nudged": 5,
                          "target": {"full": "Axonius/cortex"}},
        "cortex#100385": {"fp": {"v": "only-long"}},
    }



def test_an_approved_peer_pr_stays_watched():
    approved = {"pr": "Axonius/cortex#100384", "state": "approved"}

    targets = watch.watched_prs(_state(("s1", _session("2026-11-08", [approved]))), {}, PRS)

    assert list(targets) == ["cortex#100384"]


def test_merged_closed_and_parked_prs_are_not_watched():
    cases = [
        {"pr": "Axonius/cortex#1", "state": "merged"},
        {"pr": "Axonius/cortex#2", "state": "closed"},
        {"pr": "Axonius/cortex#3", "state": "open", "parked": True},
    ]
    for pr_info in cases:
        state = _state(("s1", _session("2026-11-08", [pr_info])))
        assert watch.watched_prs(state, {}, PRS) == {}, pr_info


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
