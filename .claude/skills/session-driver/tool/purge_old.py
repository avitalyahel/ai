#!/usr/bin/env python3
"""Purge resources of sessions the Watch pages mark "done" with no activity for 14+ days.

    python3 -I ~/Claude/sessions-watch/purge_old.py            dry run: report only, delete nothing
    python3 -I ~/Claude/sessions-watch/purge_old.py --apply    delete, then write the report

This script DELETES TRANSCRIPTS. It is separate from `sd`, which never touches transcripts.

Code sessions: transcript (<id>.jsonl and <id>/), file-history, todos and session-env entries.
               Only sessions whose id is a full UUID.
Worktrees:     removed only if clean, fully pushed or merged, and no newer session uses them.
Branches:      deleted only if not checked out, not used by a newer session, and merged into
               origin/develop or fully pushed to their upstream.
Home sessions and artifacts are only listed: delete those in the Claude app / via Claude.
Report: work/purge-report.txt
"""

from __future__ import annotations

import glob
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

DAYS = 14
HOME = os.path.expanduser("~")
DIR = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(DIR, "state.json")
REPORT = os.path.join(DIR, "work", "purge-report.txt")
CLAUDE = os.path.join(HOME, ".claude")
WORKTREES = "/.claude/worktrees/"
ARTIFACT = "claude.ai/artifact/"
KEEP_BRANCHES = {"develop", "master", "main", "HEAD", ""}
FULL_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


@dataclass
class Report:
    """Collects the report lines and prints each one."""

    apply: bool
    lines: list[str] = field(default_factory=list)

    def say(self, text: str = "") -> None:
        """Print one report line and keep it for the report file."""
        print(text)
        self.lines.append(text)


def git(repo: str, *args: str) -> tuple[int, str, str]:
    """Run git in repo; return the exit code, stdout and stderr."""
    result = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True)
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def repo_root(path: str) -> str | None:
    """Main repo of a cwd (worktrees resolve to their main checkout)."""
    while path and not os.path.isdir(path):
        path = os.path.dirname(path)
    if not path:
        return None
    rc, common, _ = git(path, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if rc != 0:
        return None
    return os.path.dirname(common.rstrip("/")) if common.endswith(".git") else None


def remove(path: str, apply: bool) -> None:
    """Delete a file, link or folder, only with --apply. A link is removed, never its target."""
    if not apply:
        return
    if os.path.isdir(path) and not os.path.islink(path):
        shutil.rmtree(path)
    else:
        os.remove(path)


def short(path: str) -> str:
    """The path with the home folder written as ~."""
    return path.replace(HOME, "~")


def load_state(path: str) -> dict | None:
    """The Sessions Watch state, or None when the file is missing or not JSON."""
    try:
        with open(path, encoding="utf-8") as state_file:
            return json.load(state_file)
    except (OSError, ValueError):
        return None


def old_sessions(state: dict, page: str, cutoff: str) -> dict:
    """Sessions of a page that are done and have no activity since cutoff."""
    sessions = state.get(page, {}).get("sessions", {})
    return {
        sid: session
        for sid, session in sessions.items()
        if session.get("status") == "done" and (session.get("lastTs") or "") < cutoff
    }


def session_paths(sid: str) -> list[str]:
    """Local files of one Code session.

    Empty for an id that is not a full UUID, so a bad id never globs a whole folder.
    """
    if not FULL_UUID.match(sid):
        return []
    patterns = [
        os.path.join(CLAUDE, "projects", "*", sid + ".jsonl"),
        os.path.join(CLAUDE, "projects", "*", sid),
        os.path.join(CLAUDE, "file-history", sid),
        os.path.join(CLAUDE, "todos", sid + "*"),
        os.path.join(CLAUDE, "session-env", sid),
    ]
    return [path for pattern in patterns for path in glob.glob(pattern)]


def artifacts_in(path: str, title: str, found: dict) -> None:
    """Add the artifact links that a transcript mentions to found, with the session title."""
    try:
        with open(path, encoding="utf-8", errors="replace") as transcript:
            for line in transcript:
                start = line.find(ARTIFACT)
                while start != -1:
                    artifact_id = line[start + len(ARTIFACT) : start + len(ARTIFACT) + 22]
                    if artifact_id.isalnum():
                        found.setdefault("https://" + ARTIFACT + artifact_id, set()).add(title)
                    start = line.find(ARTIFACT, start + 1)
    except OSError:
        pass


def size_of(paths: list[str]) -> int:
    """Total size in bytes of the files and folders."""
    size = 0
    for path in paths:
        if os.path.isdir(path):
            for root, _, files in os.walk(path):
                size += sum(
                    os.path.getsize(os.path.join(root, name))
                    for name in files
                    if os.path.exists(os.path.join(root, name))
                )
        elif os.path.exists(path):
            size += os.path.getsize(path)
    return size


def purge_code_sessions(report: Report, old_code: dict, artifact_urls: dict) -> None:
    """Delete the local files of old done Code sessions (transcripts too)."""
    report.say(f"== Code sessions: {len(old_code)} ==")
    freed = 0
    for sid, session in sorted(old_code.items(), key=lambda item: item[1].get("lastTs", "")):
        paths = session_paths(sid)
        if not paths and not FULL_UUID.match(sid):
            report.say(f"- [{sid}] SKIPPED: not a session id")
            continue
        for path in paths:
            if path.endswith(".jsonl"):
                artifacts_in(path, session.get("title"), artifact_urls)
        size = size_of(paths)
        freed += size
        report.say(
            f"- {session.get('lastTs', '')[:10]}  {session.get('title')}  [{sid[:8]}]  "
            f"{len(paths)} item(s), {size / 1e6:.1f} MB"
        )
        for path in paths:
            remove(path, report.apply)
    report.say(
        f"  Total: {freed / 1e6:.1f} MB {'deleted' if report.apply else 'would be deleted'}"
    )
    report.say()


def worktree_of(cwd: str) -> str | None:
    """The .claude/worktrees/<name> folder that holds cwd, or None."""
    if WORKTREES not in cwd:
        return None
    head, tail = cwd.split(WORKTREES, 1)
    return head + WORKTREES + tail.split("/")[0]


def purge_worktrees(report: Report, old_code: dict, newer_code: dict) -> None:
    """Remove worktrees of old sessions: clean, pushed or merged, and unused by newer sessions."""
    report.say("== Worktrees ==")
    newer_cwds = {os.path.realpath(session.get("cwd") or "") for session in newer_code.values()}
    seen = set()
    for session in old_code.values():
        worktree = worktree_of(session.get("cwd") or "")
        if not worktree or worktree in seen:
            continue
        seen.add(worktree)
        if not os.path.isdir(worktree):
            report.say(f"- {short(worktree)}: already gone")
            continue
        real = os.path.realpath(worktree)
        why = []
        if any(cwd == real or cwd.startswith(real + "/") for cwd in newer_cwds):
            why.append("used by a newer session")
        rc, out, _ = git(worktree, "status", "--porcelain")
        if rc != 0 or out:
            why.append("has uncommitted changes")
        rc, ahead, _ = git(worktree, "rev-list", "--count", "@{u}..HEAD")
        rc_merged, _, _ = git(worktree, "merge-base", "--is-ancestor", "HEAD", "origin/develop")
        if rc_merged != 0 and (rc != 0 or ahead != "0"):
            why.append("has commits not pushed or merged")
        if why:
            report.say(f"- {short(worktree)}: KEPT ({'; '.join(why)})")
            continue
        if report.apply:
            rc, _, err = git(repo_root(worktree), "worktree", "remove", worktree)
            report.say(f"- {short(worktree)}: {'removed' if rc == 0 else 'FAILED: ' + err}")
        else:
            report.say(f"- {short(worktree)}: would be removed")
    if not seen:
        report.say("- none")
    report.say()


def purge_branches(report: Report, old_code: dict, newer_code: dict) -> None:
    """Delete local branches of old sessions when merged or pushed, unused and not checked out."""
    report.say("== Local branches ==")
    newer_branches = {session.get("branch") for session in newer_code.values()}
    candidates = set()
    for session in old_code.values():
        branch = session.get("branch") or ""
        root = repo_root(session.get("cwd") or "")
        if branch not in KEEP_BRANCHES and root:
            candidates.add((root, branch))
    for root, branch in sorted(candidates):
        label = f"{branch} ({os.path.basename(root)})"
        if git(root, "show-ref", "--verify", "--quiet", "refs/heads/" + branch)[0] != 0:
            report.say(f"- {label}: already gone")
            continue
        why = []
        if branch in newer_branches:
            why.append("used by a newer session")
        _, worktree_list, _ = git(root, "worktree", "list", "--porcelain")
        if ("branch refs/heads/" + branch) in worktree_list.splitlines():
            why.append("checked out in a worktree")
        merged = git(root, "merge-base", "--is-ancestor", branch, "origin/develop")[0] == 0
        rc, ahead, _ = git(root, "rev-list", "--count", branch + "@{u}.." + branch)
        pushed = rc == 0 and ahead == "0"
        if not merged and not pushed:
            why.append(
                "not merged and has unpushed commits"
                if rc == 0
                else "not merged and has no upstream"
            )
        if why:
            report.say(f"- {label}: KEPT ({'; '.join(why)})")
            continue
        reason = "merged" if merged else "pushed"
        if report.apply:
            rc, _, err = git(root, "branch", "-D", branch)
            report.say(
                f"- {label}: deleted ({reason})" if rc == 0 else f"- {label}: FAILED: {err}"
            )
        else:
            report.say(f"- {label}: would be deleted ({reason})")
    if not candidates:
        report.say("- none")
    report.say()


def list_remaining(report: Report, old_home: dict, artifact_urls: dict) -> None:
    """List what this script does not delete: Home sessions and mentioned artifacts."""
    report.say(f"== Home (Cowork) sessions: {len(old_home)} — delete these in the Claude app ==")
    for sid, session in sorted(old_home.items(), key=lambda item: item[1].get("lastTs", "")):
        report.say(f"- {session.get('lastTs', '')[:10]}  {session.get('title')}  [{sid}]")
    report.say()
    report.say("== Artifacts mentioned in the purged Code transcripts ==")
    for url, titles in sorted(artifact_urls.items()):
        report.say(f"- {url}  ({'; '.join(sorted(title for title in titles if title))})")
    if not artifact_urls:
        report.say("- none")


def main(argv: list[str]) -> int:
    """Run the purge; dry run unless argv has --apply. Return the exit code."""
    state = load_state(STATE)
    if state is None:
        print(f"No readable {short(STATE)} yet: run `sd` once first. Nothing was deleted.")
        return 1
    report = Report("--apply" in argv)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=DAYS)).strftime("%Y-%m-%dT%H:%M:%S")
    old_code = old_sessions(state, "code", cutoff)
    newer_code = {
        sid: session
        for sid, session in state.get("code", {}).get("sessions", {}).items()
        if sid not in old_code
    }
    report.say(
        f"Sessions purge {'APPLIED' if report.apply else 'DRY RUN'} — "
        f"{datetime.now().strftime('%Y-%m-%d %H:%M')} (done, no activity since {cutoff[:16]} UTC)"
    )
    report.say()
    artifact_urls: dict = {}
    purge_code_sessions(report, old_code, artifact_urls)
    purge_worktrees(report, old_code, newer_code)
    purge_branches(report, old_code, newer_code)
    list_remaining(report, old_sessions(state, "home", cutoff), artifact_urls)
    os.makedirs(os.path.dirname(REPORT), exist_ok=True)
    with open(REPORT, "w", encoding="utf-8") as report_file:
        report_file.write("\n".join(report.lines) + "\n")
    print(f"\nReport written to {short(REPORT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
