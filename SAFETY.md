# SAFETY.md — flag-janitor guardrails

flag-janitor is built to be left running without worry. Its hard rules:

1. **No approval, no changes.** `apply` refuses to run unless `plan.json` contains
   `approved: true` and a named `approved_by`. The agent proposes; a person disposes.
2. **Archive, never delete.** Flags are archived in Flagsmith, never hard-deleted.
   Archiving is reversible from the Flagsmith UI.
3. **Conservative edits only.** The agent only rewrites direct Flagsmith SDK calls
   it can pattern-match with certainty. Anything ambiguous is listed in the PR body
   for a human instead of being touched.
4. **Dry-run by default.** `scan` and `plan` are read-only. They never write to
   Flagsmith, GitHub, or your code (only `PLAN.md` / `plan.json` / `trace.jsonl`
   in the working directory).
5. **Scoped credentials.** It runs with a Flagsmith Management API key you issue
   and a GitHub token scoped to the target repository only. Keys come from
   environment variables, never hard-coded.
6. **Full trail.** Every decision and API action is appended to `trace.jsonl`
   with timestamps, so you can reconstruct exactly what it did and why.
7. **Branch, never main.** Code changes are always pushed to a
   `flag-janitor/cleanup-*` branch and land through a pull request. Direct pushes
   to the base branch are never attempted.
