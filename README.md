# Codex Skills

Personal Codex skills repository for reusable coding workflows.

This repository is organized as a multi-skill catalog: each skill lives in its own directory under `skills/`, and each skill can be installed independently.

## Available Skills

| Skill | Description |
| --- | --- |
| `wrv-workflow` | Runs a coding task through a Writer -> Reviewer -> Verifier loop: implement, review the diff, then validate behavior against the requirement. |

## Repository Structure

```text
codex-skills/
├── README.md
└── skills/
    └── wrv-workflow/
        ├── SKILL.md
        └── agents/
            └── openai.yaml
```

Every skill directory must contain a `SKILL.md`. Optional UI metadata can live in `agents/openai.yaml`.

## Install A Skill

Install `wrv-workflow`:

```bash
python3 ~/.codex/skills/.system/skill-installer/scripts/install-skill-from-github.py \
  --repo bangjier/codex-skills \
  --path skills/wrv-workflow
```

Restart Codex after installation, or start a new Codex thread.

## Use A Skill

Invoke the skill by name in Codex:

```text
使用 $wrv-workflow 完成这个需求：
<你的需求>
```

For an explicit multi-round loop:

```text
使用 $wrv-workflow 完成这个需求。Reviewer 和 Verifier 发现的问题都要回到 Writer 修复，并复验到通过或阻塞为止：
<你的需求>
```

For a single pass only:

```text
使用 $wrv-workflow 跑一轮 Writer -> Reviewer -> Verifier，不要自动修复：
<你的需求>
```

## Update A Skill

If a skill is already installed, remove the local installed copy first, then reinstall:

```bash
rm -rf ~/.codex/skills/wrv-workflow
python3 ~/.codex/skills/.system/skill-installer/scripts/install-skill-from-github.py \
  --repo bangjier/codex-skills \
  --path skills/wrv-workflow
```

Restart Codex after updating.

## Install Multiple Skills

As this repository grows, install multiple skills by passing multiple `--path` values:

```bash
python3 ~/.codex/skills/.system/skill-installer/scripts/install-skill-from-github.py \
  --repo bangjier/codex-skills \
  --path skills/wrv-workflow \
  --path skills/another-skill
```

## Add A New Skill

Create a new directory under `skills/`:

```text
skills/my-new-skill/
├── SKILL.md
└── agents/
    └── openai.yaml
```

Recommended rules:

- Use lowercase hyphenated skill names.
- Keep each skill self-contained.
- Put trigger conditions in the `description` field of `SKILL.md`.
- Keep `SKILL.md` concise and procedural.
- Add reference files only when the skill needs extra detail.
- Do not include secrets, tokens, private keys, or machine-specific paths.

## Validate A Skill

When available, run Codex's skill validator:

```bash
python3 ~/.codex/skills/.system/skill-creator/scripts/quick_validate.py skills/wrv-workflow
```

If the validator reports missing Python dependencies, inspect `SKILL.md` manually for:

- valid YAML frontmatter
- `name`
- `description`
- matching directory name
- required `SKILL.md` file

## License

No license has been specified yet. Treat this repository as private/personal unless a license is added.
