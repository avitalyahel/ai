# Sessions Watch tools — code review & modularity notes

## Overview

`watch.py` is a single ~4,000-line script that handles collection, parsing, state merge, rendering, PR/branch analysis, worktree cleanup, space reclaim, hub queue, scans, and more. It works, but the size and mixed concerns make it hard to change safely.

`sd` (bash) is a thin, readable dispatcher. `purge_old.py` is focused and well-scoped.

## Main issues

### 1. Monolithic `watch.py`

One file owns:
- Platform helpers (paths, keyring, notifications)
- Transcript parsing
- Code / Home session collection
- State merge and Done-rule logic
- HTML page rendering
- Branch / PR / worktree analysis
- Cleanup and reclaim
- Hub queue, docs, Slack, scans
- Input checks and health reporting

**Risk:** a change in one area (e.g. rendering) can break another (e.g. PR watch). Tests cover rules well, but the lack of clear boundaries makes navigation slow.

### 2. Long functions with multiple jobs

Examples from the outline:
- `parse_transcript` — tool summaries, branch/PR extraction, doc/Slack refs, and more
- `merge` — status decisions, scheduled-run handling, continued sessions
- `analyze_branches` / `remove_merged_worktrees` — git + GitHub + ownership rules

These are hard to unit-test in isolation and hard to read.

### 3. Implicit global state

Paths (`DIR`, `WORK`, `STATE_PATH`, etc.) and helpers are module-level. This is fine for a script, but it couples every function to the layout and makes reuse or testing with different roots harder.

### 4. Mixed I/O and logic

Many functions both decide and write (JSON, logs, HTML). Separating pure decision functions from side-effecting ones would improve testability.

## Suggested modularity (incremental)

Split `watch.py` into a package without changing behavior:

```
sessions-drive/
  watch.py              # thin CLI entry point
  sw_lib/
    __init__.py
    paths.py            # DIR, WORK, STATE, platform helpers
    parse.py            # transcript parsing, tool/branch/PR/doc extraction
    collect.py          # collect_code, collect_home, archived
    state.py            # load/save, merge, Done rules, scheduled runs
    render.py           # HTML page rendering
    prs.py              # branch/PR analysis, worktree cleanup
    reclaim.py          # space reclaim
    hub.py              # queue, feed, ack, docs, Slack
    scans.py            # scans queue, setup, prompts
    checks.py           # input / requirements checks
```

**Migration steps (safe):**
1. Extract pure helpers (`posix`, `now_iso`, `load_json`, `save_json`) into `paths.py` / `util.py`.
2. Move transcript parsing into `parse.py`.
3. Move rendering into `render.py`.
4. Move hub/scans into their own modules.
5. Keep the public CLI commands unchanged so `sd` and the hubs do not need updates.

## Wording improvements applied

- Removed remaining “correlation” team-specific references from skill cross-links and install paths.
- Tightened peer-review tracking rules in `README.md` (shorter sentences, consistent terms, active voice).
- Skill files (`pr-review`, `loop-self-review`, `loop-peer-reviewer`, `sessions-drive`) now use the generic name `pr-review` and generic path examples.

## Other small notes

- `purge_old.py` is already modular enough for its purpose; keep it separate (it deletes transcripts).
- The bash `sd` help text is clear; consider generating the long help from a shared source if the README and `--help` drift.
- Tests are well-structured by concern (`test_rules`, `test_safety`, `test_scans`, etc.). Keep that pattern when splitting modules.

## Next concrete steps (optional)

1. Extract `paths.py` + `parse.py` and update imports in `watch.py` (no behavior change).
2. Add a short “Architecture” section at the top of `README.md` pointing at the new modules.
3. Run the existing test suite after each extraction.
