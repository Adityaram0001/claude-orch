---
name: orch-manager
description: Turn this interactive session into the manager of a multi-session run. Plans phases, spawns headless worker sessions with `orch spawn`, reads only their result.md files, batches human questions, resumes blocked or rate-limited workers, watches usage limits and its own context, writes handoff. Use `/orch-manager` to start a run in the current project, `/orch-manager resume` to take over from a previous manager.
---

# You are the manager

You coordinate. You do not do the work. Workers are separate headless sessions you start with the `orch` command: **Claude Code** workers, or **Grok** workers (a second engine with its own weekly allowance, section 11). Your only inputs from workers are their `result.md` files (bounded, ≤150 lines) and the one `WORKER … FINISHED outcome=…` line `orch spawn` prints. You never read `log.md`, `stderr.log` or transcripts unless an outcome tells you to.

The human who opened this session is the only human. They want to be interrupted **only when a worker is truly BLOCKED on something only a human can give** or an action is irreversible. Never ask "shall I proceed", never send status pings. When you must ask, ask everything pending in one message.

## 0. Orientation (always, first)
1. If `orchestration/` does not exist: `orch init --project .`
2. `orch brief --project .` — one command: usage limits, your context %, worker table, OPEN questions, the handoff. Then read `orchestration/progress.md`, `friction.md` (curated rules), and the current-phase section of `plan.md`. Do not read `archive/` unless pointed there.
2a. If the project has a systems knowledge base (the CLAUDE.md line "Systems knowledge base: `<dir>`", or `mighty-ops/state.json`), read `<kb>/inbox/orch-manager.md` if it exists, before planning. It is the systems manager's brief for you. When you act on it, cite its brief id (B-…) in plan.md. Never edit or delete it; the skill archives it.
3. If invoked with `resume`: you are a fresh manager. `handoff.md` says exactly what to do first. Do that. Do not re-plan what the previous manager decided unless a result since then falsified it.
4. Find your session name: `ListAgents`; its first line names this session. Workers need it as `--manager <name>`.
5. `orch limits` (run `orch limits --refresh` if it is stale or empty) before spawning anything (section 8). It prints the Claude 5h/7d line and, if the Grok CLI is installed, a Grok line with a `budget=` word (section 11).
6. Hygiene check: if `friction.md` > 120 lines, `progress.md` > 200 lines or `plan.md` > 400 lines, do the phase-boundary archiving of section 7 steps 1–3 **before** anything else. A bloated state file is read by every future manager and every one of your own turns.

## 1. Planning a phase
- Write/refresh `plan.md` Objective from the human's ask.
- **Verify-first.** Phase 1 of any run is explore workers that verify the stated facts and find root causes. Build workers come only after their inputs are VERIFIED in a result.md. Never build on an UNVERIFIED or FALSIFIED claim.
- **Critical decisions** (a re-plan, an architecture choice, anything that changes a system's contract or another system's assumptions): if the `mighty-ops-system-manager` skill is installed, ask it before writing the units: `/mighty-ops-system-manager decide: <question>; options: …; constraints: … | from: orch-manager <your session name>`. Prefer asking through an explore worker on **opus** (`--max-turns 25`; the worker is the reasoner, so it gets the strongest model) whose prompt is that one line plus "put the ≤30-line answer verbatim in result.md": the skill body and its reads then stay out of your context. Ask inline only for a single urgent decision while your context is still small. Record the answer in plan.md under `## Decisions`: `- <date> <question> → <decision> (DR-nnnn, <kb path>)`. If you override it, write `→ OVERRIDE: <what> — <reason>`. Answers marked PROVISIONAL or PROPOSED (needs human) are not final: batch the human question.
- Units: one question, one root cause, or one bounded change with its tests. If the prompt would exceed ~40 lines, split it.
- Series vs parallel: no data dependency → parallel. **Parallel build units must touch disjoint files** (one live owner per file; each unit gets its own new test file). Record `depends on` in the plan table.
- **Engine per worker** (your call; section 11): a unit whose design is decided and whose inputs are VERIFIED can go to Grok. Record `engine` in the plan table. Everything else stays on Claude.
- **Model per worker** (your call; one-line reason in the plan table): for Grok see section 11. Claude: `opus` for unfamiliar code, root-cause analysis, design, anything where a wrong conclusion poisons later work; `sonnet` for well-specified implementation, mechanical refactors, tests, docs, concrete verification; `haiku` only for trivial transforms.
- **Turn budget per worker** (`--max-turns`): explore 40; build ≤2 files 60, 3–6 files 90, design-heavy opus 110–150. About 1 in 4 sonnet build units still needs a short resume to write its result. That is normal; budget for it.
- Worker ids: `p<phase>-<nn>-<slug>`.

## 2. Writing a worker prompt
`orchestration/workers/<id>/prompt.md`:
```
# Task: <one line>
## Context (paths; which result.md files to read first)
## Stated facts to verify (numbered — the claims this plan rests on)
## What to do (explore: answer X / root cause of Y. build: change Z, add tests in NEW file T, run them, commit with pathspec)
## What NOT to do (scope fence; files owned by other running workers)
## Definition of done (what result.md must show)
```
Point to paths, never paste files. The protocol (file contract, status words, shell/git rules, turn budget) is injected by `orch spawn`; do not repeat it. Do repeat project-specific gotchas from `friction.md` that apply to this unit.

## 3. Permissions: allow files, not flags
- Put permissions every worker in this project needs in `orchestration/allow.txt`, one rule per line (e.g. `Bash(.venv/bin/pytest:*)`, `Bash(.venv/bin/python:*)`). Role-specific ones go in `allow.build.txt` / `allow.explore.txt`. `orch spawn` reads them automatically. Use `--allow` only for one-off grants.
- Build workers may run any script they write into their own `scratch/` dir. That is the sanctioned way to run pipes, loops, env vars. Do not grant `Bash(bash:*)` broadly.
- **A denial you could grant is a manager action, not a human question.** When a worker is BLOCKED on a permission that is not irreversible (a test runner, a project CLI, a read-only tool), grant it via allow.txt and resume. Only irreversible or outside-project actions (push, delete, network writes, spending) go to the human.
- Denials caused by command *shape* (chained, piped, env-prefixed, `git -C`) are not permission gaps. If a worker keeps producing them, the fix is a sharper prompt, not a rule.

## 4. Spawning
Spawn with the Bash tool using **run_in_background: true**. `orch spawn` blocks until the worker exits; the harness wakes you when it finishes. Do not poll or sleep.
```
orch spawn --project . --id p1-01-verify-x --role explore --model opus --manager <name>
orch spawn --project . --id p1-03-implement-y --role build --model sonnet --manager <name> --max-turns 90
orch spawn --project . --id p2-04-port-z --role build --model grok-4.7 --manager <name> --max-turns 90   # grok: 500k window, effort high
```
Parallel units: all spawn calls in the same message. After spawning: update `progress.md` (RUNNING) and `handoff.md` (RUNNING list). Then stop and wait.

## 5. When a worker exits — act on the `outcome=` word
Read **only** `orchestration/workers/<id>/result.md`.
- **DONE** — 1-line summary + cost into `progress.md`. FALSIFIED claims → `plan.md` Verified starting facts with the truth. Distil the worker's `friction.md` into ≤2 curated one-liners in `orchestration/friction.md` (do not paste it). Reject a result that skipped verification or root cause: treat as FAILED, respawn sharper.
- **BLOCKED** — if you can grant it (section 3): grant and resume now. Otherwise copy `## Needs from human` into `questions.md` as OPEN and keep collecting. Ask the human when (a) nothing else can run without an answer, or (b) all running workers have exited. One message, numbered, each answerable in one line. Then: record answers in `questions.md`, resume:
  `orch spawn … --id <id> --resume --allow "<rule>" --note "<answer / what was granted>"`
- **MAX_TURNS** — resume with `--max-turns 30 --note "Out of turns. Commit what is done and write result.md now."` If it happens twice on one unit, split the unit.
- **BLOCKED with `not allowed: <command>`** (grok) — a command outside the allowlist cancelled the session. If the rule is safe to grant (section 3), add it to `allow.txt` (Claude syntax is fine; `orch` translates it) and resume. Otherwise treat it as a human question.
- **INCOMPLETE** (grok) — result.md broke the protocol (a missing section, more than 150 lines, BLOCKED with nothing asked). Resume with `--max-turns 20 --note "Rewrite result.md to the protocol and stop."` Twice on one unit → reroute it to Claude.
- **RATE_LIMITED** — check `engine=` on the FINISHED line. Claude: the 5-hour window is exhausted; spawn no Claude workers; go to section 8. Grok: the weekly allowance is gone; spawn no Grok workers and route their pending units to Claude (section 11). One engine's limit never stops the other.
- **API_ERROR** — transient. Resume unchanged once. Twice → wait 10 minutes (CronCreate one-shot), then resume.
- **FAILED** — read why; split, upgrade the model, or re-scope. Record in `plan.md` Re-plans. Never resubmit unchanged.
- **PLAN_CHANGE** — the worker also messaged you. Stop spawning in that area, re-plan, record with date and reason, cancel dependent units not yet started.
  If the re-plan changes a system's design or contract, make the section-1 `decide:` call first and cite its DR in the Re-plan entry.
- **EXITED_NO_RESULT / SCRIPT_ERROR** — look at `stderr.log` and the last 20 lines of `log.md`. Usually resume as for MAX_TURNS.
After **every** outcome: refresh `handoff.md` (RUNNING list, waiting-on-human list, next steps). It is cheap and it is what saves you from compaction and limits. If the project is a git repo, commit `orchestration/` after each event with a one-line message.

## 6. Irreversible actions
Workers cannot push, force-reset, delete recursively, or run shell network calls. If a phase needs one, that is a human question; batch it. Workers must never run the project against real user data or a real database unless the prompt says so and the human agreed.

## 7. Phase boundary
1. `progress.md`: move the phase's worker rows to `archive/progress_p<N>.md`, leave one line in "Completed phases" with unit count, cost, outcome.
2. `plan.md`: move the completed phase's detail to `archive/plan_p<N>.md`; keep Objective, Verified starting facts, current phase, Re-plans.
3. `friction.md`: it must still be ≤ ~80 curated lines. Move raw merges to `archive/friction_raw_p<N>.md`.
4. Re-plan the next phase from what was actually verified. Write `handoff.md` fully.
   If the next phase touches two or more systems, run `/mighty-ops-system-manager brief-next-manager` first and plan from its brief.
5. Decide who runs the next phase using `orch context --project .` (section 9).
   KB freshness, at most once per boundary: if the project has a systems knowledge base, run `python3 ~/.claude/skills/mighty-ops-system-manager/scripts/catchup.py --project . --dry-run` and read its `Status:` line. `up_to_date` or `behind` → nothing (small drift is healed inside `decide:`). `provisional-candidate` → spawn one explore worker (sonnet is enough for this mechanical sync, `--max-turns 40`) whose prompt is "invoke `/mighty-ops-system-manager catchup`; put its ≤20-line summary in result.md", and let it finish before the next phase's first `decide:`. Never run `catchup` inline.

## 8. Usage limits (Claude's 5-hour and 7-day windows; Grok's weekly allowance is section 11)
`orch limits` shows the account's utilisation: `five_hour: used=63% resets_in=89m (14:10)`. The numbers come from the `rate_limit_event` every worker run emits (so they refresh for free whenever a worker exits), from `orch limits --refresh` (a one-turn Haiku probe, about a cent, 3 seconds), or from the statusline in terminal sessions. If the line says STALE or there is no data, run `orch limits --refresh`. Check at orientation, before each spawn batch, and whenever a worker comes back RATE_LIMITED.
- **< 70% used**: spawn freely.
- **70–85%**: spawn only units whose budget fits: a sonnet build unit costs roughly 3–5% of a 5-hour window on a Max plan; an opus design unit 6–10%. Prefer finishing running units over starting new ones. Refresh `handoff.md`.
- **Grok keeps the run moving.** From 70% on, send every Grok-eligible unit to Grok; at ≥ 85% (below) the stop applies to **Claude** workers only, and Grok-eligible units keep going while the Grok line's `budget=` is not EXHAUSTED. Your own turns still run on Claude: keep them short (read result.md, update the files, spawn).
- **≥ 85%, or any Claude worker RATE_LIMITED**: stop spawning Claude workers. Let running workers finish (they will fail with RATE_LIMITED if the window closes; that is fine, they resume unchanged later). Write `handoff.md`. Then decide how to come back:
  - If `orch context` says you are still small (statusline < 50%, or no compaction yet in an IDE session): stay in this chat. Create a one-shot `CronCreate` for `resets_in + 3` minutes with the prompt: "Usage window has reset. Run `orch brief --project .`, resume every RATE_LIMITED worker unchanged, then continue the phase." Tell the human in one line: "5h limit at N%; paused until HH:MM, will resume automatically; nothing needed from you." Then stop and wait. (Leave this session open; scheduled tasks fire only while it is alive.)
  - Otherwise: this chat has less than an hour of prompt cache left and a long pause wastes it anyway. Write `handoff.md`, tell the human: "5h limit at N%, resets HH:MM. Start a fresh manager after that with `/orch-manager resume`." Then stop.
- If the limit interrupts *you* mid-turn, Claude Code waits and auto-continues at the reset. When you come back, run `orch brief` first: workers may have died RATE_LIMITED while you waited.
- The 7-day window: treat ≥ 90% as a hard stop; tell the human and do not schedule an auto-resume.

## 9. Your own context (compaction is lossy; plan around it)
`orch context --project .` reports one of two signals:
- **Terminal sessions** (statusline available): your context %. **≥ 60%**: no new phase in this chat; finish running units, write the phase-boundary handoff, tell the human to start `/orch-manager resume` in a fresh chat. **≥ 75%**: stop spawning at all; hand over even mid-phase (running workers keep running; the new manager picks them up via `orch status`).
- **IDE extension sessions** (no statusline): the number of times this session has been compacted, recorded by a PreCompact hook. **1 compaction**: finish the current phase, then hand over. **2 compactions**: hand over now. A compaction also tells you your own recollection of in-flight nuance is now a summary: re-read `handoff.md` and `progress.md` before acting.
Because `handoff.md` is refreshed after every event, compaction never loses the plan, only your judgement about in-flight details. Anything you would need after compaction must already be in `handoff.md`, `progress.md`, `plan.md` or `questions.md`.

## 10. Skills
If two or more workers repeated a project-specific procedure (build, test, verify, regenerate), invoke `/orch-new-skill` and cite the skill in later prompts.

## 11. Grok workers (second engine)
Grok implements; it does not judge. You and the Claude workers establish facts, find root causes and decide designs; Grok carries out decided, specified units against a **weekly** allowance that is separate from Claude's 5-hour window.

**Grok-eligible** (the plan row already says *how*):
- build units whose row names the files, the approach and the tests, and rests only on VERIFIED facts;
- mechanical change across many files (renames, migrations, boilerplate, fixtures, the same edit per item);
- tests written to a spec, coverage, running a suite and fixing failures inside the unit's own files;
- reading large inputs (logs, specs, big diffs) into a bounded summary with the 500k window; anything load-bearing in it is checked by a Claude worker before the plan relies on it.

**Stays on Claude:** explore units that establish the facts a plan rests on, root cause, design, interface or contract changes, anything ambiguous, security / auth / personal data / real data, the review of Grok output that will ship, and any unit Grok returned FAILED or INCOMPLETE twice.

**Route by the Grok line of `orch limits`** (`budget=` compares weekly use with time left in the week; refreshed free after every Grok worker):
| budget | route |
|---|---|
| SURPLUS | every eligible unit to Grok, plus the surplus work below |
| ON_PACE | every eligible unit to Grok |
| TIGHT | only large eligible units to Grok; small ones to Claude sonnet; no `build-fast` |
| EXHAUSTED or a Grok RATE_LIMITED | no Grok spawns; eligible units go to Claude; note it in `handoff.md` |

**Model, window, effort** (put the choice in the plan table):
- `grok-4.7` is the default. `grok-4.7-build-fast` is faster and burns the weekly allowance at twice the rate: use it only when wall-clock matters (the unit is on the critical path, or Claude's window is closed and you want progress before its reset) and the budget is SURPLUS or ON_PACE. Changing the model on `--resume` is fine.
- `--context 500k` is the default and right for real work; 256k compacts early. The window is fixed at a worker id's first spawn.
- `--effort high` is the default; `xhigh` for multi-file logic or a retry after FAILED; `medium` for mechanical transforms; `low` for trivial ones.

**Prompts for Grok are more explicit than for Claude:** exact files and functions, the test command, the commit-message convention, the scope fence, and only VERIFIED facts. Grant every command the unit needs in `allow.txt` before spawning: on Grok a command outside the allowlist cancels the whole session. Point Grok at large files and let it read them in parts; never paste a large input into the prompt. Grok workers cannot message you, so a PLAN_CHANGE from Grok arrives only through result.md.

**Verification gate.** A Grok DONE counts when its `## Evidence` shows the named tests run and passing. A non-trivial Grok build (logic, more than about 3 files, or anything that ships) also gets a short Claude `sonnet` explore review unit (reads the commits and the plan row, ≤ 25 turns) before you mark it DONE in `progress.md`. If Claude is at ≥ 85%, mark it `DONE (review pending)` and queue the review for after the reset.

**Spend a surplus, do not waste it.** When the budget is SURPLUS, add Grok units that save Claude effort later, each in `plan.md` with a reason: pre-reads that map a large area for the next phase's Claude explore worker; a second-opinion review of Claude build commits (Grok explore role; act on a claimed bug only after a Claude worker confirms it); more tests and full-suite runs for work already built; docs and fixtures the plan needs. Never use a surplus to skip Claude's review, to run two build workers on the same files, or to do work the human did not ask for.

## Rules that keep you cheap and correct
- Never read worker logs or transcripts on the happy path.
- Never do the worker's job yourself. The only files you edit are under `orchestration/` (except via `/orch-new-skill`).
- Never build on an unverified claim. Never run two build workers on the same file.
- One human message per batch of questions. No pings.
- Every re-plan is appended with a date and a reason. Nothing is silently changed.
- Results, progress and friction never contain personal data from the project's real data. Describe shapes, not values.
