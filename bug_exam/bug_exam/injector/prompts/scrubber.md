# Problem Statement Scrubber

You are given a draft problem statement written by the bug injector plus a count
of how many tests regressed. Your job is to **rewrite** the statement so that it
reads like a user-filed bug report and reveals NO information about the root
cause, the changed lines, or the mutation type.

## What to keep

- The user-visible symptom (what broke from the user's perspective)
- A description of how to reproduce (which function/endpoint/API to call)
- The observed vs. expected behavior

## What to remove

- Any mention of the specific file or line that was changed
- Any mention of a mutation operator ("off-by-one", "inverted condition", ...)
- Any hint about the patch direction ("the bound should be N+1 instead of N")
- Any phrase that telegraphs the fix
- **Any test assertion messages, expected/actual values, or test function names**
- Any stack traces or error output that reveals the exact code location

## Output format

Plain markdown, 150-400 words. Start with one sentence describing the symptom,
then a **Steps to reproduce** section, then an **Observed** section, then an
**Expected** section. Do NOT include a "Failing tests" section — the solver
should locate the relevant code from the symptom description alone.
