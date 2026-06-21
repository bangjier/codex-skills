# Codex Skills

Personal Codex skills repository.

## Skills

- `wrv-workflow`: Writer -> Reviewer -> Verifier delivery loop for coding tasks.

## Install

Install one skill by path:

```bash
python3 ~/.codex/skills/.system/skill-installer/scripts/install-skill-from-github.py \
  --repo yorke/codex-skills \
  --path skills/wrv-workflow
```

Install multiple skills:

```bash
python3 ~/.codex/skills/.system/skill-installer/scripts/install-skill-from-github.py \
  --repo yorke/codex-skills \
  --path skills/wrv-workflow \
  --path skills/another-skill
```

Restart Codex after installing new skills.
