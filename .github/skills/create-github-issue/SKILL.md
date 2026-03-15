---
name: create-github-issue
description: "Use when: creating or updating GitHub issues with rich markdown bodies. Handles multiline body quoting correctly by writing to a temp file before calling gh CLI. USE FOR: creating new issues, editing existing issue bodies, adding labels/milestones. DO NOT USE FOR: PR reviews, code comments, git operations."
---

# Create / Update GitHub Issue

## Use When
- Creating a new GitHub issue with a structured markdown body
- Updating an existing issue body with full content (e.g. after a stub was created)
- Adding labels, assignees, or milestone to an issue

## Critical Rule: Never Use `--body` for Multiline Content

Shell quoting of multiline strings is unreliable across terminals. Always write the body to a temp file and pass `--body-file`.

**Wrong (body gets mangled):**
```bash
gh issue create --body "## Goal\nLine 1\nLine 2"
```

**Correct — use Python subprocess + temp file:**
```python
import subprocess, tempfile, os

body = """## Goal
Your markdown here.

## Scope
- Item 1
- Item 2
"""

with tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False, dir='/tmp') as f:
    f.write(body)
    fname = f.name

result = subprocess.run(
    ['gh', 'issue', 'create',
     '--repo', 'OWNER/REPO',
     '--title', 'Issue title here',
     '--body-file', fname],
    capture_output=True, text=True
)
os.unlink(fname)
print(result.stdout, result.stderr)
```

## Workflow

### Create a new issue
1. Draft the full markdown body in a Python string (Goal, Scope, Acceptance Criteria, Status sections)
2. Write to temp file via `tempfile.NamedTemporaryFile`
3. Call `gh issue create --repo OWNER/REPO --title "..." --body-file TMPFILE`
4. Clean up temp file
5. Report the created issue URL

### Update an existing issue body
Same pattern but use `gh issue edit NUMBER --repo OWNER/REPO --body-file TMPFILE`

### Add labels
```python
subprocess.run(['gh', 'issue', 'edit', NUMBER, '--repo', 'OWNER/REPO', '--add-label', 'backlog,system'])
```

## Standard Issue Template

```markdown
## Goal
1–2 sentence description of what this achieves.

## Deliverables
1. **First deliverable** — description
2. **Second deliverable** — description

## Scope
- Task 1
- Task 2
- Task 3

## Out of scope
- Not this

## Acceptance Criteria
- [ ] Criterion 1
- [ ] Criterion 2

## Status
Backlog — not started
```

## Preconditions
- `gh` CLI is authenticated (`gh auth status`)
- Correct `--repo OWNER/REPO` is specified (do not rely on git remote inference when cross-repo)
