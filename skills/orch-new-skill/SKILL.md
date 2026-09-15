---
name: orch-new-skill
description: Capture a repeatable, project-specific procedure (how to run this project's tests, how to verify a deploy, how to regenerate fixtures) as a skill under the project's .claude/skills/ so later workers and sessions reuse it instead of rediscovering it. Use `/orch-new-skill <name> — <what it does>`.
---

1. Confirm the procedure was actually performed successfully at least once in this project (point to the worker result or log that did it). Do not write skills for procedures nobody has run.
2. Create `.claude/skills/<kebab-name>/SKILL.md` with frontmatter `name` and a `description` that says when to use it, then numbered steps with exact commands and the expected successful output, and a short "if it fails" section with the known failure modes from `orchestration/friction.md`.
3. Keep it under 60 lines. Point to files rather than pasting them.
4. Append a line to `orchestration/progress.md` under a `## Skills created` heading: name, date, one-line purpose.
5. Tell the manager (or user) the skill name so it can be referenced in future worker prompts.
