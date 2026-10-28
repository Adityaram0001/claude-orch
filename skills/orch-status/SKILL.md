---
name: orch-status
description: Human-readable status of a multi-session orchestration run in the current project: usage limits, manager context, phase, each worker's state and cost, open human questions, and what is running right now. Use `/orch-status`.
---

1. Run `orch brief --project .` and read its output (limits, context, worker table, OPEN questions, handoff).
2. Report in plain language, at most 15 lines: Claude's 5-hour and 7-day usage with reset times, the Grok weekly line and its budget word if present, current phase and objective, workers running / done / blocked / rate-limited / failed with one line each (and which engine), total cost so far, and any OPEN questions verbatim.
3. Do not read worker logs. Do not start or resume anything; this skill is read-only.
