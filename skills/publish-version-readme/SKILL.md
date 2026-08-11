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

1. Resolve the Git root. Read every applicable `AGENTS.md` and local instruction file before acting. Follow stricter repository instructions; stop on an incompatible instruction.
2. Read [configuration.md](references/configuration.md) if `.release-readme.yaml` exists or automatic detection needs explanation.
3. Run:

```bash
python3 "$SCRIPT" inspect --repo "$REPO" --mode preview
```

Use `--mode publish` for full publish so remote and Git-state preflight checks run. The JSON output is authoritative for provider, version/build, source files, exclusion boundary, commits, status groups, cumulative diff, README path, and suggested checks. Stop when `ok` is false; do not replace a failed detector or boundary with a guess.

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

If push fails, state that the local commit is preserved and include only sanitized error output. If `post_commit_mismatch` occurs, state that the local commit is preserved, the push was not attempted, and hook-created changes require manual review.

## Mandatory Stops

Never bypass helper failures. Stop for sensitive files/content anywhere in commits that would be pushed, conflict entries, merge/rebase/revert/cherry-pick state, detached HEAD, ambiguous versions or boundaries, unconfigured multi-module/target selection, failing static checks, missing remote, workspace mutation during checks, staged-path mismatch, stale release plans, or hook-created post-commit mismatches.

Never force push, pull, merge, rebase, resolve conflicts, amend, rewrite history, add ignored files, or expose `.env` values, tokens, key/certificate contents, signing material, or credential-bearing remote URLs.
