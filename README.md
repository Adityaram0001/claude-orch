# claude-orch

Run Claude Code for hours across many short sessions instead of one long one.

One interactive **manager** session plans the work, spawns headless **worker** sessions (each with its own model, permissions and turn budget), reads their results from markdown files, batches the questions only a human can answer, watches the account's usage limits, and hands over to a fresh manager before its own context fills up. Every piece of state lives in plain markdown under `<project>/orchestration/`, so any session can pick up where another left off.

It is a thin layer over stock Claude Code: a bash script, a system-prompt template, a settings file, and three skills. No daemon, no database, no SDK.

## Why

Long single sessions get compacted repeatedly and lose nuance; they also stop dead when a usage window closes. Splitting work into bounded worker sessions with externalised state fixes both, but doing it by hand (open a terminal, paste a prompt, relay results) is the bottleneck. `claude-orch` automates the mechanical part and keeps two disciplines in the workers' contract: **verify the stated facts before building on them**, and **find the root cause before fixing**.

## Requirements

- Claude Code **2.1.259 or newer** on PATH (`claude --version`), logged in.
- macOS or Linux, bash, python3.
- A project directory (git optional but recommended).

## Install

```bash
git clone https://github.com/Adityaram0001/claude-orch
cd claude-orch && ./install.sh
```

This symlinks `orch` and `orch-statusline` into `~/.local/bin`, the skills into `~/.claude/skills/`, and, only if you have none configured, sets your Claude Code statusline to `orch-statusline` so usage limits and context percentage can be read by `orch limits` / `orch brief`. Open a new Claude Code session afterwards so the statusline starts reporting.

## Quick start

In your project, open Claude Code (Opus recommended for the manager) and type:

```
/orch-manager
```

then describe the task. The manager will:

1. `orch init` the `orchestration/` directory.
2. Plan a phase: explore workers first (verify facts, find root causes), build workers after.
3. Spawn workers in the background with `orch spawn`, choosing a model and turn budget per unit.
4. Wake up when each worker exits, read its `result.md`, and act on the outcome word.
5. Ask you only when a worker is blocked on something only you can give, and ask everything pending at once.
6. Refresh `handoff.md` after every event, archive completed phases, and tell you when to start a fresh manager with `/orch-manager resume`.

Check on a run any time with `/orch-status` or `orch brief --project .`.

## How it works

```
manager (interactive Claude Code session)
  │  orch spawn --id p1-01-… --role explore --model opus   (Bash, run in background)
  ├────────────► worker: claude -p … --permission-mode dontAsk --max-turns N
  │                 writes orchestration/workers/p1-01-…/{log.md, friction.md, result.md}
  │◄──── process exits → manager is woken; reads result.md only
  │  (mid-run, a worker may SendMessage the manager once: PLAN_CHANGE)
  └─ updates progress.md / plan.md / questions.md / handoff.md (manager-only files)
```

### Roles

| role | tools | writes |
|---|---|---|
| `explore` | Read, Grep, Glob, WebFetch, read-only shell (`ls`, `cat`, `git log`, …) | only its own worker directory |
| `build` | explore set + Edit, Write, `git add/commit/rm/mv`, test runners, `python3`, `node`, scripts in its own `scratch/` | the project |

Extra permissions for a project go in `orchestration/allow.txt` (all workers), `allow.build.txt`, `allow.explore.txt`, one rule per line, e.g. `Bash(.venv/bin/pytest:*)`. A short never-grantable deny list (recursive delete, push, history rewrite, sudo, kill, writing `~/.claude` or the manager's files) is applied to every worker.

### The worker contract

Every worker gets `templates/worker_protocol.md` as an appended system prompt. It must end by writing `result.md` (≤150 lines) whose first line is one of:

| status | meaning | manager's response |
|---|---|---|
| `DONE` | finished, with evidence | record summary and cost, distil friction |
| `BLOCKED` | needs a permission or a human decision | grant and resume, or batch the question |
| `FAILED` | cannot be done; more permissions would not help | split, change model, or re-scope |
| `PLAN_CHANGE` | a stated fact was false in a way that changes the plan | re-plan; the worker also messaged the manager |

`result.md` has mandatory sections: claims verified / falsified / unverified, root cause, what was done, evidence, open items, needs-from-human, and the shortcut not taken.

### Outcomes the spawn script adds

`orch spawn` prints one line when the worker exits: `WORKER <id> FINISHED outcome=<word> …` plus an `ACTION:` hint. Beyond the four statuses above: `MAX_TURNS` (resume with a small budget to write the result), `RATE_LIMITED` (the 5-hour window closed; reset time is parsed from the error), `API_ERROR` (resume unchanged), `EXITED_NO_RESULT`.

### Usage limits and context

`orch-statusline` records the 5-hour / 7-day usage percentages, reset times and the session's context percentage that Claude Code passes to statusline commands. `orch limits` and `orch context --project .` read them. The manager skill uses them: below 70% used it spawns freely; at 85% or on any `RATE_LIMITED` outcome it stops spawning, writes the handoff, and either schedules its own wake-up after the reset (if its context is still small) or asks you to start a fresh manager after the reset. At 60% context it stops starting new phases; at 75% it hands over.

### State files (all markdown, in `orchestration/`)

| file | owner | purpose |
|---|---|---|
| `plan.md` | manager | objective, verified facts, current phase's units, dated re-plans |
| `progress.md` | manager | current phase only: status line, worker table, completed-phase one-liners |
| `friction.md` | manager | curated one-line lessons (≤ ~80 lines) |
| `questions.md` | manager | batched human questions and answers |
| `handoff.md` | manager | refreshed after every event; what a fresh manager does first |
| `allow*.txt` | you / manager | extra permission rules |
| `archive/` | manager | completed phases' detail |
| `workers/<id>/` | that worker | `prompt.md`, `log.md`, `friction.md`, `result.md`, `run.json`, `scratch/` |

No two sessions ever write the same file.

## Commands

```
orch init    --project <dir>
orch spawn   --project <dir> --id <id> --role explore|build --model <m> --manager <session-name>
             [--max-turns N] [--allow "<rule>"]... [--resume] [--note "<text>"] [--prompt-file <f>]
orch status  --project <dir>
orch brief   --project <dir>      # limits, context, workers, open questions, handoff — one read
orch report  --project <dir>      # per-phase cost, turns, turn-cap rate, limit hits, denials by shape
orch limits                       # 5h / 7d usage as last reported by the statusline
orch context --project <dir>      # context % of the newest interactive session in that directory
```

Skills: `/orch-manager [resume]`, `/orch-status`, `/orch-new-skill <name> — <purpose>`.

## Costs

Workers load the project's CLAUDE.md and skills like any session, so even a trivial worker costs a few cents. In a real multi-day run, sessions averaged a few dollars each, about one in five build sessions needed a short resume to write its result, and roughly 70% of permission denials were compound commands where one part lacked an allow rule. `orch report` gives you these numbers for your own runs so you can tune `allow.txt`, prompts and turn budgets.

## Limitations

- Local machine only. Workers are processes on the same host as the manager.
- Workers share one working tree. The manager must not run two build workers on the same files; the protocol tells workers to commit with explicit pathspecs and never amend.
- The manager is an interactive session and needs its terminal left open; scheduled self-wake-ups fire only while it is alive.
- Permission checking is by command shape: environment-variable prefixes, shell loops and `git -C` are always denied in the workers' mode. The protocol explains the allowed shapes and gives build workers a scratch-script escape hatch.

## Contributing

Issues and pull requests are welcome. Keep the toolkit thin: prefer a line in the protocol or the manager skill over new machinery, and measure before changing permission rules (see `docs/design.md` for how the current rules were derived).

## License

MIT. See `LICENSE`.
