---
name: wrv-workflow
description: Run a Codex coding task through a Writer -> Reviewer -> Verifier delivery loop. Use when the user asks for one-click WRV, Writer/Reviewer/Verifier, implementation plus review plus acceptance verification, "一写一审一验收", or wants Codex to implement a coding change and then self-review and validate the result against requirements.
---

# WRV Workflow

Use this skill to execute a coding task as three distinct roles in one Codex session:

1. Writer implements.
2. Reviewer audits the diff.
3. Verifier validates behavior against the requirement.

Keep the boundaries explicit. Writer may edit files. Reviewer and Verifier must not edit files.

## Quick Start

When the user asks for WRV or one-click delivery:

1. Restate the requirement and derive acceptance criteria.
2. Run Writer.
3. Run Reviewer.
4. Let Writer fix accepted review findings.
5. Run Verifier.
6. Let Writer fix failed acceptance items.
7. Re-run Verifier until passed, blocked, or the user stops.

If acceptance criteria are missing, derive a short checklist and mark it as assumptions.

## Writer Pass

Act as Writer.

- Read relevant files before editing.
- Identify files to change before editing.
- Provide a 3-6 step plan.
- Keep the diff small and consistent with the repo.
- Follow `AGENTS.md` and existing project style.
- Add or update tests for behavior changes when the project has tests.
- Run the fastest relevant check first.
- Perform a self-review before handing off to Reviewer.

Writer output:

- Summary
- Files changed
- Verification performed
- Self-review findings
- Remaining risks

## Reviewer Pass

Switch to code-review stance. Do not modify files.

- Inspect the current diff first.
- Read surrounding code only as needed.
- Prioritize bugs, regressions, missing tests, type safety, security, performance, and unnecessary edits.
- Findings must lead the response and be ordered by severity.
- If no blocking issues are found, say so and name residual risks.

Finding format:

- Severity: P0/P1/P2/P3
- File and line
- Issue
- Impact
- Suggested fix

After Reviewer reports findings, return to Writer only for accepted fixes.

## Verifier Pass

Switch to acceptance-verification stance. Do not modify files.

- Verify from the user/product perspective, not by re-reviewing code.
- Run the app, tests, browser, simulator, or commands needed to observe behavior.
- Check the requested behavior and nearby regression flows.
- For UI changes, inspect visual output when possible.
- Capture concrete evidence: command output, screenshots, URLs, logs, observed state, or reproduction steps.

Verifier verdicts:

- Passed: acceptance criteria satisfied.
- Failed: one or more criteria not satisfied.
- Questionable: expected behavior is ambiguous.
- Blocked: environment, permission, dependency, or data prevents verification.

Failure format:

- Acceptance item
- Steps to reproduce
- Expected result
- Actual result
- Evidence
- Suggested owner: Writer, Reviewer, or user clarification

## Permissions And Commands

Before non-trivial commands, state the command and rough time estimate.

Use Codex escalation when a command may need:

- Network access
- Simulator/browser/desktop access
- Writes outside the workspace
- Process/cache/environment access outside the sandbox
- Git index/ref updates

Do not run destructive commands unless the user explicitly asks or approves.

## Final Response

End with:

- Summary
- Files changed
- Verification performed
- Reviewer findings
- Verifier verdict
- Remaining risks

Keep the answer concise and in the user's language.
