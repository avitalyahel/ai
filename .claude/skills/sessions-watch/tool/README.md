# sw — Sessions Watch

`sw` keeps the two pinned pages, **Home Sessions Watch** and **Code Sessions Watch**, current. It also cleans up after finished Code sessions and, through the **Trackers Hub**, messages Code sessions when their PRs, design pages or child sessions need them.

## Usage

| Command | What it does |
|---|---|
| `sw` | One round now, then exit. |
| `sw --loop [INTERVAL]` | A round now, then one every `INTERVAL` while the window stays open. `Ctrl+C` stops it. `INTERVAL` is a number plus `s`, `m`, `h` or `d`; the default is `15m` and the shortest is `1m`. |
| `sw --check` | Step 1 only (collect, no Claude): does anything need refreshing? It does not collect while a refresh is with the Scans Hub. |
| `sw --status` | What the pages hold now. |
| `sw --last` | How the last Claude refresh ended. |
| `sw --trackers` | Code sessions that may still run their own PR trackers. |
| `sw --hub` | Are the Trackers Hub and the Scans Hub alive, and which events are waiting for them. |
| `sw --watch-list` | What's on the opt-in watch list. |
| `sw --help`, `sw -h` | Short help. |

## Platforms, settings and input checks

- **Platforms:** `watch.py` runs on macOS, Linux and Windows. It finds the Claude app's folder, the system keyring (macOS Keychain, Linux `secret-tool`, the Python `keyring` module elsewhere) and desktop notifications for the OS it's on. `sw` is bash, so on Windows use Git Bash or WSL. Starting at login is out of scope.
- **Install and requirements:** see "Install" in the `sessions-watch` skill (`../SKILL.md`): a symlink to a separate `agent-skills` clone that stays on `main`, so updates are a `git pull`. Every round checks the requirements and reports anything missing on the "⚠ Input check" line.
- **Per-user settings:** `config.json` holds the two page URLs (filled in on the first refresh), `hideSessions`, `userName` and `slackUserId`. Three more keys are optional: `scheduledRuns` (see [Scheduled runs](#scheduled-runs)), `scans` and `scansGmailServer` (see [Scans Hub](#scans-hub)). Copy `config.example.json` to start. It's never shared.
- **Input checks:** every round verifies the shape of each file `sw` reads: session histories, the app's metadata, the Cowork folder layout, `config.json`, `atlassian.conf`, `sw`'s own state files, the hub's doc snapshots and queue, and GitHub lookups. Warnings go to the round log, `work/health.txt`, and a "⚠ Input check" line on both pages.

## Each round

1. **Collect, on this Mac, without Claude** (`watch.py collect`)
   - Code sessions: `~/.claude/projects`, last 30 days, newest 60.
   - Home (Cowork) sessions: last 60 days, newest 80.
   - Archive flags: from the Claude app's metadata.
   - Branches and PRs: from git and GitHub (`gh`).
   - Worktree cleanup and space reclaim (see [Cleanup](#cleanup)).
2. **Nothing changed:** no Claude at all.
3. **Something changed:** the refresh runs, and it reads only the changed sessions, rebuilds both pages and publishes them.
   - **With the [Scans Hub](#scans-hub) running:** `sw` queues one refresh for it. The Scans Hub starts two Haiku subagents, one to classify and one to publish. No new session starts.
   - **Without it,** or when it hasn't finished the refresh for 15 minutes: a Claude Code session starts, as before.
     - Haiku at its default effort, in auto permission mode.
     - Tools: `Read`, `Write`, `ToolSearch`, `Artifact` and the `watch.py` commands only.
     - It closes itself when done.
4. **PR watch** (see [PR watch](#pr-watch)).
5. **Scans:** a scan in `config.json` whose time has passed is queued for the Scans Hub.
6. **With `--loop`:** wait `INTERVAL`, then repeat. The loop is plain bash and doesn't rely on Claude to schedule anything.

## Statuses

| Status | Meaning |
|---|---|
| Running | Activity in the last 2 minutes. |
| Blocked | Stopped on an obstacle, or interrupted. |
| Waiting on you | Ended with a question for you. |
| Partial | Part of the ask is undone. |
| Done | See [Done rules](#done-rules). |

### How Claude reads a session

- The original ask and the last 12 messages, including tool details: commands, files, plan steps.
- A session is read again only when it changes: new, new activity, stopped running, unarchived, or a branch or PR changed state.
- Running rows must name the concrete current action and the next step.

## Done rules

The first rule that matches wins.

### Both pages

- **Running:** never Done, except when archived.
- **Archived in the app:** Done, tagged *Archived*. An unarchived session is read again.
- **Marked done by you** (`watch.py done home|code <id or title>`): Done until the session has new activity.

### Code

- **Continued sessions:** transcripts with the same title in the same repo count as one session, and the newest part decides. Archiving any part archives the whole session.
- **Branches tied to a session**
  - the branch it ran on;
  - branches it created or pushed: `git worktree add -b`, `checkout`/`switch -b`, `push origin`, `gh pr create --head`;
  - in any repo it worked in (`cd`, `git -C`).
- **PRs tied to a session:** those it reviewed, commented on, edited, marked ready, merged or closed.
- **Ignored**
  - branches with no PR;
  - draft-PR branches, when the session has several branches (unless all of them are drafts).
- **Everything finished → Done** without asking Claude, tagged *Merged*, *Approved* or *Closed*:
  - every branch merged, and
  - every PR merged, closed, or (someone else's PR) approved by you.
- **Parked → Done:** a draft PR labelled `DO_NOT_MERGE` (also `do-not-merge`, `DNM`) counts as finished; if that's all that's left, the session is Done, tagged *Parked*, and the PR isn't watched.
- **Anything still open → never Done.** It shows as *Partial* with "Not merged yet: …". Your own done mark still wins.

### Home

- Claude's reading decides, as on Code: a finished session is Done.
- Your own done mark and archiving still count.
- No branch or PR rules.

### Scheduled runs

A recurring run, such as a scheduled task, has nobody to answer it. Without a rule, its last message (the report) can read as a question, and it shows as *Waiting*.

- Add the title prefixes of such runs to `scheduledRuns` in `config.json`, for example `["Inbox auto-archive"]`. The match ignores case and leading spaces, but a dash is not a space: copy the start of the title from the page.
- A session whose title starts with one of them **never shows as Waiting**.
- When Claude reads it as *Waiting*, the session is not running and its last message is the assistant's own text (the report), it shows as **Done**. Its outcome is the summary of that report.
- When its last message is not a report, it shows as *Partial* ("The scheduled run stopped before its report"), so it isn't hidden. A final message that has a tool call in it is not a report. If you reply in such a session, your reply is the last message, and it shows as *Partial* too.
- *Blocked*, *Partial*, *Done* and *Running* stay as they are. A failed run still shows.
- It applies to both pages. The runs that the Scans Hub starts as subagents create no session at all.

### Never shown

- `sw`'s own sessions, including the *Trackers Hub* and the *Scans Hub*.
- The *Session Watches Controller* session.

## Cleanup

Code sessions only, on this Mac.

### Worktrees

- **Removed** with `git worktree remove`, never forced, when
  - its branch's PR is merged, or
  - every session working in it has all its branches and PRs merged.
- **Kept** when
  - it has uncommitted or untracked files;
  - it is locked;
  - a session worked in it in the last 30 minutes (a long command does not change the transcript);
  - another unfinished session works in it, even when its own branch is merged;
  - its branch is parked (a draft PR labelled `DO_NOT_MERGE`).
- Log: `work/worktrees-removed.log`.

### Purge of old sessions (manual, deletes transcripts)

`purge_old.py` is separate from `sw`, and `sw` never runs it. Unlike everything above, it **deletes transcripts**.

- **What:** Code sessions marked Done with no activity for 14 days: the transcript (`<id>.jsonl` and `<id>/`), file-history, todos and session-env. Only ids that are full UUIDs. Their clean, pushed or merged worktrees and branches, unless a newer session uses them.
- **Listed only:** Home (Cowork) sessions and the artifacts the purged transcripts mention.
- **Run:** `python3 -I ~/Claude/sessions-watch/purge_old.py` is a dry run; add `--apply` to delete. It needs `state.json`, so run `sw` once first.
- **Report:** `work/purge-report.txt`.

### Space reclaim

- **Switch:** only when `work/reclaim.mode` contains `on`; otherwise it's a dry run.
- **Merged sessions,** idle 3+ days and not running: temp folder, file-history, todos, session-env.
- **Done sessions,** idle 1+ hour and not running: temp folder (scratchpad and task output).
- **Never** the transcript.
- **Only** paths named with the full session ID, under
  - `/private/tmp/claude-*/`
  - `~/.claude/file-history`, `~/.claude/session-env`, `~/.claude/todos`
- A link is removed as a link only; what it points to is left alone.
- Report: `work/reclaim.txt`. Log: `work/reclaimed.log`.

## Trackers Hub

`sw` never resumes or starts sessions itself. It decides **what** a session needs to hear and **when**, and puts that message in a queue. The **Trackers Hub**, one Code session you keep open in the app, delivers each message to its session through the app's session messaging. The session gets it as a normal message, in the app.

### Setting up the hub

1. Open a new Code session in the app, in `~/Claude/sessions-watch`, and name it **Trackers Hub**.
2. Paste the contents of `HUB.md` as its first message.
3. It starts one feed, `watch.py hub-feed`, a Monitor that prints each waiting message and keeps a heartbeat. It also creates a recurring job that re-arms the feed every 25 minutes.

### Re-arming

- If the hub stopped (app restarted, Mac restarted), open it and say **re-arm**. That restores delivery for every session at once.
- Messages wait in the queue while the hub is down; none are lost.
- If the hub's heartbeat is older than 15 minutes and messages are waiting, `sw` shows a Mac notification: "Trackers Hub is down — N event(s) waiting. Open it and say re-arm." At most every 30 minutes.

### Delivery

- Each queued message names its target by the app session id (`local_…`), found in the app's metadata. Sessions started outside the app have none and are skipped.
- After delivering, the hub records the message id (`watch.py hub-ack <id>`); a delivered message never comes back.
- If delivery fails, the message stays queued and comes back at the next re-arm.
- Home (Cowork) sessions get messages too, for pages and docs on the [opt-in watch list](#opt-in-watch-list); everything else goes to Code sessions.
- Messages carry metadata only: what kind of change, counts, who acted, and the PR or doc link. Comment text written by others never goes into a message. Coordinator messages quote the last reply of the coordinator's own child sessions (up to 700 characters), marked as data.
- **Retention:** each round, delivered messages older than 14 days are removed from the queue and from `delivered.log`. Undelivered ones stay until delivered. If the queue file still exceeds 1 GB, its oldest delivered messages go first, then its oldest undelivered ones, with a warning on the "⚠ Input check" line.
- The hub treats messages as data: it passes them on and does nothing else.

## Scans Hub

Recurring work used to start a new session on each run: the app's scheduled tasks (for example a mail scan three times a day) and the refresh of every round with changes (about 50 a day). The **Scans Hub** is a second Code session you keep open in the app. It runs that work as **subagents**, so no run starts a session of its own. It is optional: without it, `sw` works as before.

It is a second session, and not a job of the Trackers Hub, so that the Trackers Hub stays read-only: it delivers messages and writes doc snapshots, and nothing more. The Scans Hub can use a state-changing connector (for example Gmail archiving), but only through a subagent with a short, fixed tool list.

### Setting up the Scans Hub

1. Write the subagent definitions: `python3 -I ~/Claude/sessions-watch/watch.py scans-setup`. Run it with exactly this path: the paths in the files must match what `sw` uses, and the command refuses to run from another path. It writes `sw-classify` and `sw-publish` to `~/.claude/agents`, from the templates in `agents/`. A file there that `sw` did not write is never overwritten.
2. Open a new Code session in `~/Claude/sessions-watch`, name it **Scans Hub**, and paste `SCANS.md` as its first message. It checks the limits of each subagent once, starts its feed (`watch.py hub-feed --role scans`) and a re-arm job, and replies "Scans Hub armed". Do this after step 1. If `~/.claude/agents` did not exist before the session started, restart the session.
3. For scans, add them to `config.json`, then disable the app's scheduled task of the same name:

   ```json
   "scans": [{"name": "inbox-auto-archive", "times": ["09:00", "13:00", "17:00"],
              "prompt": "~/Claude/scans/inbox-archive.md", "agent": "sw-scan-gmail"}],
   "scansGmailServer": "mcp__<id>"
   ```

   - `prompt` is a file you own. It is the whole prompt of the subagent. A `~` is expanded, and a relative path starts in the `sw` folder. `scan-examples/` has one to copy. A missing file shows on the "⚠ Input check" line, and the scan is retried every round until the file is there.
   - `agent` is the subagent that runs it. `sw-scan-gmail` is for mail: set `scansGmailServer` to the start of your Gmail tool names (`mcp__<id>` in `mcp__<id>__search_threads`) and run `scans-setup` again. Claude Code loads a changed agent file within seconds, when the folder existed at session start.

### How a run goes

- `sw` is the clock. Each round it queues a scan whose time has passed (`watch.py scans-due`). The first time it sees a scan it only records it; it runs at the next time. Only one event per scan waits at a time. A time missed while the Mac slept gives one catch-up run.
- **The refresh:** after `collect` finds changes, `sw` queues one `refresh` event (`watch.py scans-queue refresh`). The Scans Hub starts `sw-classify` (tools `Read` and `Write`: the only subagent that reads raw session text written by others), then `sw-publish` (tools `Read`, `Bash`, `Artifact`, `ToolSearch`). A hook limits the Bash of `sw-publish` to `apply`, `published` and `set-url`, and a hook limits `sw-classify` to its three files. Each hook command ends with `|| exit 2`, so a crash of `watch.py` or a missing `python3` blocks the call too. The "changed sessions only" rule is unchanged: `collect` still decides before any event is queued.
- **No collect during a refresh:** `collect` rewrites `work/todo.json` and deletes `work/classified.json`, which a running refresh reads and writes. So `sw` skips `collect` (`watch.py scans-busy`) while a refresh waits at the Scans Hub, for at most 15 minutes.
- **A scan:** the Scans Hub reads the prompt file and starts the named subagent with it. The subagent answers with one line. The Scans Hub records it with `hub-ack <id> --report -` and passes the line on stdin, in a single-quoted heredoc, never on the command line: the line can hold mail subjects or session titles written by others. `watch.py` keeps only its first line, without control characters, cut to 300 characters.
- **A failed run:** the Scans Hub records it with `hub-ack <id> --failed -`. The event is closed, the report line starts with `FAILED:`, you get a Mac notice, and the next scheduled time queues the scan again.
- Subagents run in the foreground, one at a time. The Scans Hub keeps one line per run, so nothing builds up in its context.

### Limits of the Scans Hub

- **Mail agent:** `sw-scan-gmail` has four Gmail tools (`search_threads`, `get_thread`, `list_labels`, `unlabel_thread`) and `ToolSearch`, which only loads those tools. It has no send, reply, forward, draft, trash, spam or label-delete tool. Archive means removing the INBOX label, which is reversible: the mail stays in All Mail.
- **Mail text is data.** The subagent ignores any request in it. The prompt file should fix the query and cap the threads per run (the example does). In the subagent, the worst case is a wrong archive.
- **The Scans Hub itself** reads no mail, session text or page. It sees one line per run, and treats it as data. Its own Bash is not limited by a hook: it follows `SCANS.md`, and the report line only ever goes through stdin.
- **Files:** `sw-classify` can read `work/todo.json` and its instruction file, and write `work/classified.json`. `sw-publish` can read any file, as the CLI refresh session could, and the Artifact tool is as open as before.
- **Run reports:** `work/scans/<name>.log` and `work/scans/refresh.log` hold one line per run (counts and subjects only). Lines older than 14 days are removed.
- **What the docs do not promise:** Claude Code's documentation does not say that a subagent hook also runs when the subagent is in the background, and it says a hook is not a hard permission rule. So the Scans Hub starts subagents in the foreground, and `SCANS.md` has it check each subagent's tool list once. A subagent file takes bare tool names only: a Bash command pattern can't be written in `tools`, which is why the hook exists.

### When the Scans Hub is down

- **Not running:** while its feed's heartbeat is older than 2 minutes, `sw` starts the Claude session for the refresh, as before. Scan events wait in the queue; the next run archives everything that matches, and no mail is lost.
- **Running but not finishing the refresh:** after 15 minutes `sw` starts the Claude session too. The 15 minutes count from the queue time, or from the end of the Scans Hub's previous event when that is later, because the Scans Hub handles one event at a time.
- **A failed refresh:** the Scans Hub closes it with `hub-ack <id> --failed -`. The next round starts the Claude session once, so the pages do not wait for a Scans Hub that keeps failing. Notices of failed runs come at most every 30 minutes.
- **Notice:** with events waiting while the feed is quiet for 15 minutes, `sw` shows a Mac notification: "Scans Hub is down — N event(s) waiting. Open it and say re-arm." It also notifies when an event has waited 15 minutes while the feed is alive: a stuck scan would otherwise block the next one. At most every 30 minutes.
- **Re-arm:** open the Scans Hub and say **re-arm**. If its context fills up, the app compacts it. Nothing is lost, because every run is one subagent and all state is in files.
- After updating `sw`, paste the new `SCANS.md` into the Scans Hub again and run `scans-setup` again: an older `SCANS.md` calls `hub-ack` with the report on the command line, which `sw` now refuses with a one-line error. Re-arm the Trackers Hub too. An old feed keeps its old code in memory and would hand it the Scans Hub's events (`to: scans-hub`). `HUB.md` tells the Trackers Hub to skip those events and never acknowledge them.

## PR watch

Runs after every round.

### What is watched

- Each open PR tied to a Code session: your own PRs, and reviews you haven't approved.
- Until it's merged or closed.

### What sends the session a message

Only activity by others. Your own activity and the session's own replies and pushes never count.

- a new review;
- new comments on the PR or on the code;
- CI finished, passed or failed;
- new commits pushed by others (for example, the author of a PR you review);
- label changes by others, bots included (for example `CI_READY`);
- new merge conflicts;
- an approval dismissed by new commits, so the PR needs re-approval;
- merged or closed.

On your own PRs (open, not drafts, not yet approved), on workdays only (Sunday to Thursday):

- **Reviewer reminders: off.** `REMINDER_TIMES` in `watch.py` is empty, so sessions are not asked to remind reviewers. Put times back (for example `("09:00", "17:00")`) to turn them on.
- **No review for 1 day:** when nobody is asked to review and nothing happened for a day, the session re-requests review. At most once a day per PR.

### How it's delivered

- `sw` queues a message for the newest session tied to that PR, with what changed and the PR link.
- The Trackers Hub delivers it (see [Trackers Hub](#trackers-hub)).

### What the session is told

- Continue the PR work by its earlier instructions: fix comments, fix or rerun CI, commit, push.
- After pushing fixes for review comments, reply on each thread with what changed, and re-request review from everyone who requested changes or commented (`gh pr edit <N> --add-reviewer <login>`).
- If the PR is waiting for review or re-approval, re-request review from its reviewers on GitHub. No Slack messages to them unless you ask.
- Merge only if you told it to merge after approval.
- Never force-push.
- Never touch other PRs or branches; start no new work.
- Reply in one line if there's nothing to do.
- On merge or close, do the wrap-up you asked for.

### Peer-review tracking rules

These apply when a session tracks a **peer review** (someone else's PR). They come from the `pr-review` skill (step 7, "Track the user's manual comments") and the `loop-peer-reviewer-responses` skill ("Open the gap doc first"). `sw` and the hub only deliver messages; the session applies the rules.

#### Rule 1 — Open the gap doc first

- **When:** a review tracking loop starts.
- **Before anything else:** before drafting or posting any comment, the session creates the PR's gap doc (a Claude Docs page titled "PR <number> review gaps") and opens it. If it already exists, the session opens it.
- **Scope:** the gap doc is the tracking list for the whole loop. Update it on every new head of the PR.

#### Rule 2 — Track manual comments

- **What's tracked:** the session tracks both its own comments and the PR comments the user wrote by hand (same GitHub login, not posted by the session).
- **In the gap doc:** each manual comment gets its own row and is checked on every re-check, like the session's own.
- **Duplicates:** if a manual comment asks for the same thing as a session comment (same file and lines, same ask), merge it into the session comment.
  - Not posted yet: edit the draft before posting.
  - Already posted: PATCH the posted comment.
  - If they conflict, the user's wording wins.
  - The manual row's Status becomes **Dropped**, with "Merged into Gx".
- **Never touched:** the session never edits, resolves, or deletes the user's manual comment.

**Example:** a manual comment row G20 that matches session comment G4 becomes "Dropped — Merged into G4".

### Guard rails

- Never messages a running session; the change waits, and is re-checked every round until it can be sent. Changes that pile up meanwhile go out together in one message. Nothing held back by a guard rail is dropped.
- At most one message per session every 30 minutes. A merge or close is always sent.
- At most 3 messages per round.
- The 1-day re-request (and reminders, when turned on): your own open, non-draft, not-yet-approved PRs only; never on Friday or Saturday; one reminder per time slot; one re-request a day.
- The first time a PR is seen it's only recorded; no message.
- A session whose folder is gone gets no message.

### Replacing in-session trackers

Sessions used to track PRs themselves: 5-minute background watchers, Monitor tasks, `/loop` and cron jobs (for example, the reviewer reminders in `loop-self-review-response`), and the app's PR monitor. They stop when the session closes or hits a time limit.

- `sw --trackers` lists Code sessions active in the last 7 days that show signs of a tracker or have an open PR.
- New runs of your skills (`loop-self-review-response`, `loop-peer-reviewer-responses`) still start their own watchers and cron jobs until the skills are told to rely on `sw` instead.

### Coordinator sessions

A coordinator session starts other sessions (for example, a session that starts one review session per PR) and waits for them. The app tells the coordinator when a child finishes a turn, but only while the coordinator is alive.

- **Who's a coordinator:** a Code session whose history shows sessions it started ("you started (local_…)") or messaged.
- **Who its children are:** found through the app's metadata, which links each app session (`local_…`) to its Claude Code session.
- **Message:** when a child finished a turn the coordinator hasn't seen (the child's last message is newer than the coordinator's), `sw` queues a message for the coordinator with each child's title and last reply.
- **Guard rails**
  - waits 2 minutes after the child's turn, so the app can deliver it first;
  - never messages a running coordinator;
  - at most one message every 10 minutes per coordinator, batching all pending children;
  - only coordinators active in the last 7 days, not archived;
  - the first time a child is seen it's only recorded.

### Confluence design pages

- **Which pages:** pages a Code session worked on: Confluence tool calls with a page id, and page links in its replies. Only sessions active in the last 14 days, not archived; the newest session working on a page owns it.
- **What sends the session a message:** new comments or replies by others (page and inline), and edits to the page by someone else.
- **What it's told:** read and answer the new comments, update the page if asked, resolve the threads it addressed, touch no other pages.
- **Guard rails:** the same as for PRs (running, 30-minute cooldown, 3 messages per round, first sight only recorded).
- **Access:** Confluence's REST API, read-only, with TLS verification on.
  - `atlassian.conf` holds the site and your email (no secrets).
  - The API token lives only in your system keyring, under the service `sw-atlassian` and your email as the account. Create a token at <https://id.atlassian.com/manage-profile/security/api-tokens> and store it as "Install" in `../SKILL.md` says; it's never written to a file or shown.
  - Without a token, the page watch is off and `work/prwatch.txt` says so.

### Google Docs and Claude Docs

- **Which docs:** docs a Code session worked on (Google Docs or Claude Docs tool calls, and Google Docs links in its replies), from sessions active in the last 14 days. The newest session working on a doc owns it. The two Sessions Watch pages are never watched.
- **Who reads them:** the Trackers Hub, every 10 minutes, with its connectors. `sw` can't read these itself. The hub writes only comment IDs and authors to `work/hub/docsnap/`.
- **Confluence:** read the same way when there's no API token in the keyring. With a token, `sw` reads Confluence directly.
- **What sends the session a message:** new comments or replies by someone other than you. The first check of a doc only records it.
- **Labels:** editing or creating a doc counts as *Self Review*; only commenting counts as *Peer Review*.

### Slack threads

- **Which threads:** Slack messages a Code session posted (a `slack_send_message` result with its `message_link`), the thread of each; at most the 20 newest per session, from sessions active in the last 14 days. The newest session that posted in a thread owns it.
- **Who reads them:** the Trackers Hub, in the same 10-minute docs check, with `slack_read_thread`. It writes only message ts values and sender names to `work/hub/docsnap/`, never message text.
- **What sends the session a message:** new replies by someone other than you. The first check of a thread only records it.
- **Pin a thread** to a session, also when the session is done (for example, waiting for a reply after all PRs merged): `python3 -I ~/Claude/sessions-watch/watch.py slack-watch <channel id> <thread ts> --session <Claude Code session id> [--days 14]`. Remove it with `slack-unwatch <channel id> <thread ts>`. Pins live in `work/hub/slack-pins.json` and expire after 14 days.

### Opt-in watch list

Pages and docs are found automatically only for Code sessions active in the last 14 days. Entries in `work/extra-watch.json` (if present) override automatic ownership for the same page or doc. There is no CLI to add or remove entries. List them with `sw --watch-list`.

- The entries live in `work/extra-watch.json`.
- They override any automatically found owner for the same page or doc.
- There's no 14-day limit, and a Done session still gets its messages. Only archived sessions (Code or Home) are skipped.
- Confluence pages are read directly when an API token is set, otherwise by the hub. Claude and Google Docs are always read by the hub.
- Messages go to the session's app id, so Home sessions get them in the app like Code sessions do.

### Records

- Report: `work/prwatch.txt`.
- Queue: `work/hub/queue.jsonl`; delivered ids: `work/hub/delivered.log`; heartbeat: `work/hub/heartbeat`.

## GitHub

- PR states come from `gh`, which must be logged in.
- *Merged* is final. Other states are looked up again every 30 minutes, and at once when the PR watch sees a change. So when new commits dismiss your approval of someone else's PR, its session goes from Done back to Partial on the next round and gets a message.
- Worktree cleanup decides *merged* from the PR's state on GitHub, not from git history, so squash merges count.

## Files

All in `~/Claude/sessions-watch`.

| File | What it is |
|---|---|
| `sw` | This tool. |
| `watch.py` | Collect, apply, PR watch, cleanup. |
| `prompt.md` | The refresh instructions for Claude. |
| `state.json` | What the pages hold. |
| `out/*.html` | The pages. |
| `work/collected.json`, `work/todo.json` | The last collect, and the sessions waiting to be read. |
| `work/archived.txt`, `work/branches.txt` | Archive and branch/PR reports. |
| `work/prs.json` | Cached PR states. |
| `work/prwatch.json`, `work/prwatch.txt` | Watch state and report. |
| `work/hub/` | The hub's queue, delivered ids and heartbeat. |
| `HUB.md` | The Trackers Hub's instructions (paste into its session once). |
| `SCANS.md` | The Scans Hub's instructions (paste into its session once). |
| `agents/` | Templates of the subagents; `scans-setup` writes them to `~/.claude/agents`. |
| `scan-examples/` | An example prompt file for a scan. |
| `work/scans/` | The refresh prompts for the subagents, the last time slot of each scan, and the run reports. |
| `work/reclaim.mode`, `work/reclaim.txt`, `work/reclaimed.log` | Space reclaim switch, report and log. |
| `work/worktrees-removed.log` | Removed worktrees. |
| `work/appids.json` | App session ids linked to Claude Code session ids (for coordinators). |
| `atlassian.conf` | Confluence site and your email; the token is in the system keyring. |
| `work/health.txt` | The last input and requirements check. |
| `tests/` | Unit tests (see [Tests](#tests)). |

## Tests

Run all of them with `for t in ~/Claude/sessions-watch/tests/test_*.py; do python3 -I "$t"; done`. They need no network, GitHub or app.

- `test_rules.py`: the Done rules (merged, parked, approved, closed, branches without PRs), scheduled runs never Waiting, DO_NOT_MERGE spellings, Self/Peer CR and Review labels, branch ownership (created or pushed, main checkout vs worktree), fork history, PR messages (reviews, comments, dismissed approvals, CI, commits, labels, conflicts) and the hub queue (delivery, acknowledgement, retention, size limit).
- `test_input_checks.py`: every input format check and the per-OS requirements check.
- `test_safety.py`: space reclaim, worktree removal, the message guard rails (running sessions, cooldown, merges, 3 per round) and `purge_old.py`, on temp folders and local git repos.
- `test_scans.py`: the Scans Hub: when the refresh goes to it or to the CLI session, the two refresh prompts, scan times, run reports, the publish subagent's Bash hook and `scans-setup`.
- `test_slack_watch.py`: Slack thread refs, pins and their expiry.
- `test_pr_keys.py`: one key per PR across the PR list and the branch list.
