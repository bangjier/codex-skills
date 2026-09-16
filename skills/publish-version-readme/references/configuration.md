# Project Configuration

Place optional configuration at the Git root as `.release-readme.yaml`. The helper accepts a strict YAML subset: nested mappings, scalar values, and scalar lists. JSON is also valid. Unsupported YAML features fail explicitly.

```yaml
version:
  provider: custom
  file: config/version.json
  field: release.version
  build_field: release.build
  module: app
  target: Runner
readme:
  file: README.md
  section: 版本更新记录
  start_marker: '<!-- release-notes:start -->'
  end_marker: '<!-- release-notes:end -->'
boundary:
  strategy: version-file-history
checks:
  commands:
    - flutter analyze
  timeout_seconds: 600
git:
  remote: origin
```

## Version

- `provider`: `flutter`, `ios`, `android`, or `custom`. Configuration takes priority over automatic detection.
- `file`: repository-relative version source. Required for `custom`; optional for other providers.
- `field`: dotted field path for custom JSON, TOML, or YAML files.
- `build_field`: optional dotted build-number field.
- `pattern`: custom regular expression with a required named `version` group and optional named `build` group. Use either `field` or `pattern`.
- `module`: Android application-module path or name when multiple application modules exist.
- `target`: iOS application target when multiple targets exist.

Use exactly one of `field` or `pattern`. For a text version file, replace the field settings with:

```yaml
version:
  provider: custom
  file: VERSION.txt
  pattern: 'VERSION=(?P<version>[^\s]+) BUILD=(?P<build>[0-9]+)'
```

For custom files, the extension selects the structured parser: `.json`, `.toml`, `.yaml`, or `.yml`. TOML fields require Python 3.11 or a compatible `tomllib` runtime. Use `pattern` for other formats or when running the helper with an older Python.

## README

- `file`: repository-relative README path. Defaults to root `README.md` with case-insensitive fallback.
- `section`: exact existing Markdown heading text, or the heading to create.
- `start_marker` and `end_marker`: optional exact marker pair delimiting the release-notes region. Configure both or neither.

The notes JSON `items` list is the complete summary for the detected version. Rendering replaces that version's entire section body, including old paragraphs and subsections, while preserving its heading style and the other versions. Consolidate all still-relevant changes and essential context into `items` before rendering. If the version has no section yet, a new section is added and the existing release history is preserved.

## Boundary

- `strategy: version-file-history`: default; derive the exclusion commit from version-source history.
- `strategy: commit`: use `commit` as an explicit exclusion boundary after verifying it is an ancestor of `HEAD`.
- `strategy: tag`: resolve `ref` to a commit and verify it is an ancestor of `HEAD`.

## Checks And Git

- `checks.commands`: ordered shell commands. An explicit empty list disables auto-detection because the project declares no static check.
- `checks.timeout_seconds`: per-command timeout, default 600 seconds.
- Invocation-only `stage --check-command`: supplies a repository-reviewed check when `checks.commands` is absent. Project configuration takes priority.
- `git.remote`: push remote used when the branch has no upstream. Without this, only an existing `origin` is accepted.
- Invocation-only `stage --commit-message`: required when creating a new commit. Supply one concise sentence describing that commit's actual changes, without version/build numbers or a `release:` prefix. The helper uses the text directly and returns it for review before publishing. Push-only and no-op actions do not require a message.
- `git.commit_message`: legacy setting, now ignored. Replace version templates with a fresh `--commit-message` summary on each invocation.
