# Design notes

These are the decisions behind claude-orch and the evidence for them. Most were made before the first real run; sections 6–10 came from reviewing a multi-day run of about a hundred workers across three manager sessions, and section 11 adds Grok as a second worker engine.

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

## 11. Grok as a second worker engine

Claude's 5-hour window is what stops long runs. A Grok subscription has only a weekly allowance, so Grok workers let decided implementation keep moving while Claude's window is tight, and use an allowance that otherwise expires unspent. Grok implements; facts, root causes, design and the review of shipped Grok work stay on Claude (manager skill, section 11).

Everything below was measured on Grok CLI 1.0.70 with throwaway sessions in a scratch repository:

- **Context window.** `grok-4.7` and `grok-4.7-build-fast` offer 256k and 500k (`contextWindows: [256000, 500000]`) and default to 256k. Headless `grok -p` has no flag for it, and a per-model override exists only in the user's global `config.toml`, which would change every Grok session on the machine. Over the agent protocol, `session/set_model` with `_meta.contextWindow: 500000` (and `reasoningEffort`) is accepted and stored with the session; `grok -p --resume <id>` keeps it, also when `-m` switches the model on resume. So `orch` opens each Grok session over the agent protocol and runs the worker as a headless resume.
- **Rules.** `session/new` `_meta.rules` becomes part of the session's system prompt and persists across resumes; the worker protocol goes there.
- **Project instructions.** A plain headless session in a trusted folder gets `CLAUDE.md` injected at its first prompt. A session opened over the agent protocol and then resumed headless does not: the model reported no project instructions. `orch` therefore embeds the instruction files itself: the project directory and every parent up to, not including, `$HOME`, outermost first, 10,000 characters per file, larger sets listed for the worker to read. That is also what a Claude worker sees, whereas Grok on its own reads only from the git root down. Folder trust (`grok --cwd <dir> --trust inspect`, no model call) is still recorded so project skills and settings load.
- **Claude Code compatibility.** In a trusted folder Grok loads the user's Claude hooks, MCP servers and skills. A project `PreToolUse` hook fired in a Grok session by default and did not fire with `GROK_CLAUDE_HOOKS_ENABLED=0`; `GROK_CLAUDE_MCPS_ENABLED=0` likewise turns the MCP surface off. Workers run with both off. The user-level `PreCompact` hook `install.sh` adds would otherwise count a worker's compaction as the manager's.
- **Shell shapes under `--permission-mode dontAsk`** (one session per shape): allowed when every part is allowed — pipes, `&&` chains, `cd <dir> && git …`, `git -C <dir> …`, `python3 - <<'PY'` heredocs; read-only commands, `mkdir` and `touch` run without any rule. A command that no rule allows — `cp`, `rm`, `curl`, `node`, `git commit` without a grant, output redirection, `VAR=x cmd`, `$(…)`, a `for` loop — **cancels the whole session** (`stopReason: cancelled`, tool text "User cancelled the execution for tool …"). A command that matches an explicit `--deny` rule fails with "Denied by permission policy" and the session continues. Hence the protocol's warning, `orch` reporting a cancel as `BLOCKED` with the command, and a deny list that turns the never-grantable commands into recoverable refusals.
- **Rules syntax.** Grok accepts `Tool(glob)` rules for Bash, Read, Edit, Write, Grep, WebFetch and MCPTool. `orch` translates the Claude worker's allowlist and deny list (`Bash(x:*)` becomes `Bash(x)` and `Bash(x *)`), so both engines share one source; Claude-only tool names are dropped, and every denied git command also gets its `git -C * …` form because Grok runs that shape.
- **Turn cap.** The stream carries `{"type":"max_turns_reached"}` and an `end` event with `stopReason: max_turn_requests`.
- **Weekly allowance.** The agent-protocol extension `_x.ai/billing` (behind Grok's `/usage`) returns `creditUsagePercent` and the current `USAGE_PERIOD_TYPE_WEEKLY` period's start and end, in under a second and without a model call. `orch` reads it after every Grok worker and on `orch limits`, and turns used-versus-elapsed into the budget word. A rate-limit outcome also sets a latch, cleared by the next accepted run or a meter reading below 97%.
- **Cost.** The stream's `end` event carries `total_cost_usd`, an API-equivalent figure; the subscription itself is billed against the weekly meter.

End-to-end check on a scratch repository: Grok explore and build workers returned contract-complete results; the build worker fixed a bug, added tests in a new file, ran them, and committed with a pathspec under the commit-message rule from the embedded `CLAUDE.md`; a worker that ran an ungranted command came back `BLOCKED` with that command, was granted it through `allow.txt` in Claude syntax and resumed on `grok-4.7-build-fast` with its 500k window intact; a 2-turn cap came back `MAX_TURNS` and finished on resume; no Claude hook fired from any Grok worker; a Claude worker in the same run behaved as before.

## What was measured in the first real run

- 100 units, 123 sessions, about 7,900 turns over roughly 29 hours, three manager sessions with written handoffs.
- 1.23 sessions per unit; 20% of sessions ended on the turn cap; three sessions died on the 5-hour limit and were resumed unchanged.
- About 190 permission denials, 70% of them compound commands where one part lacked an allow rule.
- Ten results exceeded the 150-line cap; three units never wrote a result (turn cap or limit) and were resumed.

`orch report --project <dir>` produces these numbers for any run.
