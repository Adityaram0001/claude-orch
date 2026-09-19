# Design notes

These are the decisions behind claude-orch and the evidence for them. Most were made before the first real run; the last five came from reviewing a multi-day run of about a hundred workers across three manager sessions.

## 1. Headless CLI workers, interactive manager

Workers are `claude -p` processes. They load the project's CLAUDE.md, skills and hooks exactly like a human-opened session, return a JSON result with session id and cost, and can be resumed with `--resume` so a blocked worker continues with its context intact and wider permissions. The Agent SDK adds a runtime for nothing this design needs; tmux would give interactive workers, which only a human can drive.

The manager is an ordinary interactive session because it is the only thing allowed to ask the human anything.

Do not pass `--bare` to workers: it skips settings discovery and breaks keychain authentication.

## 2. Completion is an exit, not a poll

The manager starts `orch spawn` with Claude Code's background Bash option. The spawn script blocks until the worker exits, so the manager is re-invoked when the process ends. No cron, no sleeping. Workers signal mid-run only for `PLAN_CHANGE`, via cross-session messaging, and only once.

## 3. Files have exactly one writer

Workers write only inside their own `orchestration/workers/<id>/`. The manager alone writes the shared files. Parallel workers therefore cannot race on orchestration state. They do share the project's working tree, which is why the protocol carries git rules (pathspec-only commits, no amend, one new test file per concurrent unit).

## 4. The manager's context grows with the number of workers, not the work

It reads `result.md` (capped at 150 lines) and nothing else on the happy path. It refreshes `handoff.md` after every event so neither compaction nor a usage limit loses the plan. Thresholds from the statusline's context percentage: no new phase above 60%, hand over above 75%.

## 5. Deny-unknown permissions with per-role allowlists

Workers run with `--permission-mode dontAsk --permission-prompts none`, so nothing ever hangs waiting for a prompt. A denied tool tells the model "denied, continue". The protocol turns that into a `BLOCKED` result listing the exact rule needed. The deny list applied to all workers holds only things a manager should never grant, because deny rules beat allow rules and anything on that list could never be granted on resume.

Measured on a 12-command matrix (Claude Code 2.1.270): pipes, chains and redirects are allowed when every part matches an allow rule; environment-variable prefixes, shell loops, `git -C` and any chain containing one unallowed command are denied as a whole; the glob rule form `Bash(bash some/dir/*)` matches a script path, the prefix form `Bash(bash some/dir/:*)` does not.

## 6. Usage limits come from the workers' own streams

In `--output-format stream-json` every run emits a `rate_limit_event` whose `unifiedWindows` carry `five_hour` / `seven_day` `{utilization, resetsAt}`. Workers therefore run in stream mode; `orch spawn` keeps the full stream in the worker's directory, extracts the final `result` message into `run.json`, and records the latest limit event to `~/.claude/orch/limits.json`. Nothing has to be polled: every worker exit refreshes the numbers. `orch limits --refresh` runs a one-turn Haiku probe for the same event when no worker has run recently.

Caveat: `rate_limit_event` is observed behaviour (Claude Code 2.1.270), not a documented message type; the documented stream events carry token usage only. If a future version stops emitting it, `orch limits` degrades to the statusline source (terminal sessions; set `statusLine.refreshInterval` for periodic updates) and reports STALE rather than guessing.

The statusline is a second source in terminal sessions and the only source of the manager's own context percentage; IDE extensions never run it (verified: four days of an extension session wrote nothing), so a `PreCompact` hook records compactions as the fallback signal for `orch context`. `/usage` is display-only, and the OAuth credentials in the keychain were deliberately not used: an undocumented endpoint and a token in a script are the wrong price for a number the CLI already prints.

Headless workers that hit the window die with HTTP 429 and the text "You've hit your session limit · resets HH:MM"; `orch spawn` classifies that as `RATE_LIMITED` and prints the reset time. Interactive sessions on 2.1.234+ auto-continue at the reset if a limit interrupts them mid-turn; the manager relies on that for itself, never for workers.

## 7. Every exit is an outcome word

Managers in the first real run had to learn by experience that an `api_error` with no denials means "resume unchanged" and a `max_turns` exit means "resume with a small budget to write the result". Now the spawn script prints the outcome and the action. Twenty percent of sessions in that run ended on the turn cap, so this path is normal, not exceptional.

## 8. Permissions live in project files

In the first real run managers typed eight `--allow` flags per spawn. `orchestration/allow*.txt` replaced them. A permission the manager could grant is a manager action, not a human question; only irreversible or outside-project actions go to the human, batched.

## 9. Shared state stays small; history goes to `archive/`

The first run's curated-lessons file reached 1,246 lines because managers pasted worker friction verbatim, and every fresh manager read all of it. Templates now hold the current phase only; completed phases are moved (never deleted) to `archive/`; friction is distilled into one-liners with the raw text preserved in the worker's directory and the archive.

## 10. Verify-first and root-cause-first are enforced by the result format

`result.md` must contain "Claims verified / falsified / unverified" and "Root cause" sections. The manager rejects results that skip them. In the first real run a meaningful fraction of stated facts were falsified by explore workers before anything was built on them, which is the whole point.

## What was measured in the first real run

- 100 units, 123 sessions, about 7,900 turns over roughly 29 hours, three manager sessions with written handoffs.
- 1.23 sessions per unit; 20% of sessions ended on the turn cap; three sessions died on the 5-hour limit and were resumed unchanged.
- About 190 permission denials, 70% of them compound commands where one part lacked an allow rule.
- Ten results exceeded the 150-line cap; three units never wrote a result (turn cap or limit) and were resumed.

`orch report --project <dir>` produces these numbers for any run.
