# Task: find the root cause of the intermittent timeout in the export job

## Context
- Entry point: src/jobs/export.ts. Scheduler config: config/jobs.yaml.
- A previous worker verified the job runs every 15 minutes: read orchestration/workers/p1-01-verify-schedule/result.md first.

## Stated facts to verify
1. The timeout is 30 seconds and set in config/jobs.yaml.
2. The job only fails when more than 10,000 rows are exported.
3. There is no retry logic around the export call.

## What to do
Find the root cause of the timeout. Explain why the code is the way it is. Do not fix anything; you are an explore worker.

## What NOT to do
Do not run the job against any real database. Do not modify files outside your worker directory.

## Definition of done
result.md with each stated fact marked VERIFIED / FALSIFIED / UNVERIFIED with evidence, a Root cause section naming file and line, and a proposed build unit (one paragraph) for the fix.
