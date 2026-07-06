# Pull Request

<!-- Thanks for contributing! Please fill in the sections below. -->

## What does this PR do?

<!-- A 1-3 sentence summary. The first line will be the commit message subject. -->

## Why?

<!-- What problem does this solve? Reference an issue if there is one. Fixes #123 / Closes #456 -->

## How to test it

<!-- Step-by-step instructions for the reviewer. -->

1.
2.
3.

## Screenshots / screen recording

<!-- If the change is visual, attach before/after screenshots or a screen recording. Drag-and-drop works in this box. -->

## Checklist

<!-- Put an `x` in each box that applies. Delete lines that don't. -->

- [ ] My code follows the project's style (see existing code for conventions)
- [ ] I have added tests that prove my fix / feature works
  - [ ] Unit tests (if backend)
  - [ ] Frontend regression test (if template / JS change)
- [ ] New and existing unit tests pass locally:
  ```bash
  python -m pytest -q
  ```
- [ ] Coverage is at or above 95% (run `pytest --cov=app`)
- [ ] I have updated relevant docs (Readme, CHANGELOG, doc/*, inline comments)
- [ ] I have NOT committed any secrets (Firebase keys, .env files, *.pem, etc.)
- [ ] I have run `detect-secrets scan --baseline .secrets.baseline` and it reports no NEW findings
- [ ] This PR is on a feature branch (not `main`)

## Breaking changes

<!-- List any breaking API / schema / config changes. If none, write "None". -->

## Related issues / PRs

<!-- Link related issues and PRs here. -->
