## Linked issue

- GitHub issue: <!-- required -->
- Issue branch: <!-- e.g. issue/123-notebook-workflow -->

## Acceptance criteria

- [ ] Criterion 1:
- [ ] Criterion 2:
- [ ] Criterion 3:

## Notebook -> artifact mapping (required for notebook-driven work)

- Primary notebook path:
- Notebook lifecycle status (`WIP` / `Extraction complete` / `DONE` / `Reference` / `Archived`):
- Extracted implementation files (`src/...`):
- Extracted test files (`tests/...`):
- Extracted docs (`README.md` / `docs/...`):
- Intentionally retained notebook-only content (and why):

## Verification performed

- [ ] Start-of-work cleanliness check recorded (`git status --porcelain`)
- [ ] Pre-PR-close cleanliness check recorded (`git status --porcelain`)
- [ ] Relevant automated tests passed
- [ ] Notebook checkpoint passed (clean-kernel rerun or CI notebook check)

Commands run:

```bash
# paste commands used for verification
```

## System-of-record sync

- [ ] Issue status updated
- [ ] PR description reflects current implementation status
- [ ] Temporary `/tickets/` markdown removed or explicitly retained as durable documentation
