# orchestration/progress.md — manager only. Single source of truth for state. KEEP IT SHORT: current phase only.

**Status:** (one line: phase, what is running, what is next)
Last updated: (date, manager session name)

## Session start protocol (any session, including a fresh manager)
1. `orch brief --project .` (limits, context, workers, open questions, handoff — all in one)
2. Read this file, then `friction.md` (curated rules only), then `plan.md` current-phase section.
3. Older phases live in `archive/` — read only if a result points there.

## Current phase workers
| worker id | status | cost usd | result summary (≤ 1 line, ≤ 200 chars) |
|---|---|---|---|

## Completed phases (one line each; details in archive/progress_pN.md)
| phase | units | cost usd | outcome |
|---|---|---|---|

## Skills created
