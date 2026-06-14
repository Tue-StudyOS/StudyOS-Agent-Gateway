---
name: find-skills
description: Discover and optionally install reusable agent skills from the open skills ecosystem. Use when the user asks "how do I do X", "find a skill for X", "is there a skill that can...", wants new capabilities, asks about installable skills, or before creating a new custom skill when an existing package may already cover the workflow.
---

# Find Skills

## Overview

Use this skill to search for existing agent skills before inventing a new
workflow. It is both a discovery helper and, with user approval, an installation
helper.

## Safety Boundary

- Searching is safe to do directly.
- Installing third-party skills changes the runtime. Present the package, source
  link, and install command before installing unless the user explicitly asked
  for immediate installation.
- Prefer checked-in StudyOS skills for cohort-wide behavior and global installs
  for personal/local capabilities.
- Do not install a skill to replace a workflow that should stay project-local
  under `.agents/skills/`.

## Search Workflow

1. Identify the domain and task: framework, tool, file type, platform, or
   workflow.
2. Search with specific keywords:

   ```bash
   npx skills find <query>
   ```

3. If results are broad, try one or two alternate terms.
4. Present the best matches with the skill name, what it appears to do, the
   source link, and install command.
5. If no match is useful, say so and continue with general capabilities or
   `$studyos-skill-expansion` for creating a StudyOS-specific skill.

## Install Workflow

When the user approves installation, install globally with:

```bash
npx skills add <owner/repo@skill> -g -y
```

After installing:

1. List the installed skill path when the CLI reports it.
2. Validate the installed `SKILL.md` if `quick_validate.py` is available.
3. If the skill should become part of the shared StudyOS runtime, copy or adapt
   it into `codex/skills/<skill-name>/` and add `agents/openai.yaml`.

## Useful Queries

| Need | Query |
| --- | --- |
| React or Next.js work | `react performance`, `nextjs`, `typescript` |
| Testing | `testing`, `playwright`, `jest`, `e2e` |
| Deployment | `deploy`, `docker`, `ci-cd`, `kubernetes` |
| Documentation | `docs`, `readme`, `changelog`, `api-docs` |
| Code quality | `review`, `lint`, `refactor`, `best-practices` |
| Design | `ui`, `ux`, `design-system`, `accessibility` |
| Productivity | `workflow`, `automation`, `git` |

## Output Shape

When recommending skills, keep it short:

- Skill/package name.
- Why it may fit.
- Install command.
- Source link.
- Any risk or reason to prefer a custom StudyOS skill instead.
