---
name: publish-version-readme
description: Prepare and publish a version release in any Git project by detecting Flutter, iOS, Android, or configured custom versions; locating a trustworthy version boundary; updating README release notes from real commits and local changes; committing every non-ignored local change; and pushing the current branch. Use when the user invokes $publish-version-readme for a full publish or $publish-version-readme preview/预览 for a README-only preview.
---

# Publish Version README

Run the deterministic helper for discovery and Git mutation. Use judgment only to summarize verified changes and preserve the README's existing structure and language.

Set `SCRIPT` to this Skill's `scripts/publish_version_readme.py`. Resolve it from the Skill directory; do not assume the caller's working directory is the Skill directory.

## Select Mode

Treat arguments containing `preview` or `预览` as preview mode. Any other explicit invocation is full publish mode. A full invocation authorizes the helper to stage, commit, and push; do not request routine confirmation.

## Inspect

1. Resolve the Git root and read applicable repository instructions. Resolve apparent conflicts by instruction authority, scope, and the user's existing authorization, rather than automatically selecting the strictest wording. A full invocation already authorizes the documented stage, commit, and push operations. If a material conflict remains unresolved, pause only the affected mutation and explain the exact conflicting clauses while completing independent read-only preparation.
2. Read [configuration.md](references/configuration.md) if `.release-readme.yaml` exists or automatic detection needs explanation.
3. Run:

```bash
python3 "$SCRIPT" inspect --repo "$REPO" --mode preview
```

Use `--mode publish` for full publish so remote and Git-state preflight checks run. The JSON output is authoritative for provider, version/build, source files, exclusion boundary, commits, status groups, cumulative diff, README path, and suggested checks. When `ok` is false, pause the dependent release steps, inspect the reported cause, and follow the recovery rules below. Do not replace a failed detector or boundary with a guess.

Show the current branch, concise `git status`, current version/build, boundary commit, boundary rationale, committed changes, and modified/staged/untracked/deleted summary. Never print secret values or sensitive file contents.

Compare `static_checks` with applicable repository instructions and actual project tooling. If project configuration declares `checks.commands`, use it. Otherwise choose the fastest existing relevant static check; retain the detected command when correct or pass the better command to `stage` with `--check-command`. Do not invent a check when the project has none.

## Draft Release Notes

Read the current README, commits, and cumulative diff returned by `inspect`. Produce a notes JSON file outside the repository:

```json
{
  "version": "1.2.3",
  "date": "YYYY-MM-DD",
  "section_heading": "版本更新记录",
  "items": ["用户可理解、由提交或 diff 直接支持的变化"]
}
```

Match the README's language, heading hierarchy, bullet style, and wording. Prefer an existing release-notes/changelog region. Base every item on specific evidence; consolidate low-quality commit subjects, omit internal churn that has no user-facing meaning, and do not claim unverified tests or fixes. Avoid duplicates. When the current version exists, include its useful existing items so updating does not discard them.

When `diff_truncated` is true, inspect every path in `changed_paths_since_boundary` with focused Git diffs or file reads before drafting notes. Do not proceed from the truncated patch alone.

Generate the proposed diff without writing:

```bash
python3 "$SCRIPT" render --repo "$REPO" --notes-file "$NOTES" --preview
```

Review the diff for unsupported claims, duplicate bullets, damaged README structure, wrong version/date, or unrelated edits. Revise the notes and rerun until clean.

In preview mode, return the inspection summary and final proposed README diff. Do not write the README, stage, commit, or push. Stop here.

## Publish

For full mode only:

1. Apply only the reviewed README result:

```bash
python3 "$SCRIPT" render --repo "$REPO" --notes-file "$NOTES"
```

2. Stage the complete workspace and run checks:

```bash
python3 "$SCRIPT" stage --repo "$REPO" --expected-version "$VERSION"
```

Add `--check-command "$CHECK"` when repository inspection found a better check and `.release-readme.yaml` does not declare `checks.commands`. Repeat the option only when multiple checks are explicitly required.

The helper reruns preflight and sensitive checks, executes the selected checks, verifies they did not mutate the workspace, runs `git add -A`, and compares staged paths to the complete expected non-ignored workspace set.

3. Show `staged_paths`, `staged_summary`, and checks from the stage result. This display must happen before commit. Do not request confirmation; the full invocation already authorizes commit and push.

4. Immediately finalize with the returned token:

```bash
python3 "$SCRIPT" publish --repo "$REPO" --plan-token "$PLAN_TOKEN"
```

The token binds branch, HEAD, version, remote, commits to push, staged paths, staged tree, and commit message. The helper stops if state drifted. It commits as `release: <version>` (or the configured template), verifies hooks did not change the commit tree or leave the workspace dirty, then pushes without force. It pushes only `HEAD` to the existing upstream branch, independent of `push.default`; without an upstream it uses the configured remote or an unambiguous `origin` with `git push -u <remote> HEAD`.

When there are neither file changes nor unpushed commits, `stage` returns a `no-op` action and `publish` returns `no_changes` without invoking Git push. Treat that as a successful normal exit.

If push fails, inspect the remote state through an authorized read-only check before any retry, since the push may have reached the remote despite a lost response. Preserve the local commit, report sanitized evidence, and retry only the same authorized destination after the cause is resolved and a fresh plan passes. Never create a duplicate release commit or force push to recover. If `post_commit_mismatch` occurs, preserve the local commit, report that push was not attempted, and review the hook-created changes; do not amend, rewrite, or push that commit automatically.

## Release gates and recovery

Never bypass helper failures or proceed with a failed release plan. Sensitive files/content in commits to push, unresolved conflicts, an in-progress Git operation, detached HEAD, ambiguous versions or boundaries, unconfigured target selection, failing checks, a missing remote, workspace mutation during checks, staged-path mismatch, stale plans, and post-commit mismatches block the dependent release mutation.

- Diagnose with read-only inspection first. Correct ordinary setup or formatting problems within the authorized scope. Fix check failures only when the underlying edit is already authorized or necessary to make the requested change correct; ask before expanding into unrelated behavior changes.
- Use the repository's existing configuration and explicit user choices to resolve targets, versions, and remotes. Ask a focused question only when the answer remains ambiguous; do not invent a destination or exclusion boundary.
- After any correction or state drift, rerun inspection and README review. Preview mode still uses `render --preview` without staging or publishing; full mode reruns `stage` and uses a fresh plan token. Do not repeat the same failure indefinitely without new evidence.
- Do not disable tests, remove safeguards, discard user edits, or omit non-ignored files to make a full release pass. Sensitive-history and post-commit problems require a separately authorized remediation before publication.
- If work still requires user action or additional authorization, finish independent safe preparation and report what is complete, what is blocked, and the minimum next action. A preview is complete at its reviewed diff; a full publish is complete only after the authorized push is verified or the helper confirms `no_changes`.

Never force push, pull, merge, rebase, resolve conflicts, amend, rewrite history, add ignored files, or expose `.env` values, tokens, key/certificate contents, signing material, or credential-bearing remote URLs.
