---
name: "linked-list-pr-reviews-per-day"
description: "List the PRs the user reviewed on a given day (peer PRs by default, or their own with \"self\") as a ready-to-paste Slack message: one line per PR with its GitHub URL, ticket and title, author, review-notes or design-doc URL, and a :done2: or :work-in-progress: status emoji. Use when they ask which PRs were reviewed on a day, PR reviews for Thursday, yesterday's peer reviews for Slack, or similar."
---

# PR reviews per day → Slack list

Produce a Slack-ready list of the pull requests the user reviewed on one day, from their Claude Code sessions as tracked by the Sessions Watch tool (`sd`).

## Inputs

- **Day.** It may be a weekday ("Thursday"), "yesterday", "today" or a date. Resolve it to a local date `YYYY-MM-DD` in the user's local time zone. A bare weekday means the most recent past one. If no day is given, use yesterday.
- **Kind.** Peer reviews (PRs by others) by default. Use self reviews (their own PRs) if they say "self", "my PRs" or "own".

## Steps

1. **Find `sd`.** This needs the `session-driver` skill installed and `sd` run at least once.
   - Claude Code: `SW=~/Claude/sessions-watch`.
   - Cowork: `~/Claude` is mounted under `/sessions/SESSION/mnt/Claude`, so `SW=/sessions/SESSION/mnt/Claude/sessions-watch`. If it isn't connected, ask for the folder `~/Claude (the folder that holds sessions-watch)` with the folder-request tool.

2. **Get the reviews** (read-only; it only reads `sd`'s files):

   ```
   python3 -I "$SW/watch.py" reviews YYYY-MM-DD          # peer PRs
   python3 -I "$SW/watch.py" reviews YYYY-MM-DD --self   # their own PRs
   ```

   It prints one JSON line per PR: `url`, `number`, `repo`, `title`, `author` (GitHub login), `prState`, `session` (the Code session that worked on it), `sessionStatus`, `done` and `docs` (design-doc URLs: Google Docs or Confluence).
   - The data comes from Code sessions that had activity on that day and the PRs they acted on (reviewed, commented on, approved).
   - Drop lines that clearly don't belong: an empty title and a PR number far outside the range of the others (for example `#30` next to five-digit PRs) is a stray reference.
   - If `title` or `author` is empty (PR details not fetched yet), take the ticket and topic from the `session` title. For example "Peer review #97836 AX-113072 remove 20 rules" gives "AX-113072: remove 20 rules".

3. **Find each PR's review notes:** list the user's artifacts (Artifact tool, `action: "list"`, `limit: 200`) and match titles like "PR NUMBER review gaps" or "REPO PR NUMBER review gaps". Use that artifact's URL. Then add any design-doc URLs from `docs`. If a PR has neither, leave that part out.

4. **Status emoji:** `:done2:` when `done` is true (the review session finished); otherwise `:work-in-progress:`.

5. **Author:** a short first name when known from the login or from the session (for example `jdoe-axonius` → John). Leave it out if unknown.

6. **Write the message** in a code block, exactly in this format: plain URLs (no angle brackets, no `[text](url)`), the emoji at the end of each line, one line per PR, sorted by repo and PR number.

   ```
   *Peer PRs reviewed on Thursday, Nov 5*
   • https://github.com/Axonius/cortex/pull/97836 AX-113072: remove 20 rules (John) · https://claude.ai/artifact/AbCdEf0123456789xyzABC :work-in-progress:
   • https://github.com/Axonius/cortex/pull/98721 AX-113469: users runtime (John) · https://claude.ai/artifact/GhIjKl0123456789xyzDEF :done2:
   ```

   - For self reviews, the header is "*My PRs worked on, Thursday, Nov 5*".
   - The description is the ticket key plus a short title: "AX-113072: remove 20 rules".

7. **After the code block, add two short notes:**
   - Review-notes pages (claude.ai/artifact links) are private until shared from each page's Share menu. If the message is for teammates, share them first or drop those links.
   - The list comes from their Code sessions' activity, not from GitHub. For GitHub's own record they can run `gh search prs --reviewed-by=@me --updated=YYYY-MM-DD --owner=Axonius`.

## Don'ts

- Don't post to Slack; give the text only.
- Don't include customer data or anything beyond PR links, titles, first names and the review-notes or design-doc links.
- If `watch.py reviews` prints nothing, say no PR reviews were found for that day in their Code sessions, and suggest the `gh search` command.
