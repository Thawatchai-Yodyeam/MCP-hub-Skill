---
name: conventional-commits
category: git-workflow
description: Semantic commit messages, changelog automation, and pull request slicing strategies.
tags: [git, commits, changelog, workflow, standards]
---

# Conventional Commits & Clean Git Workflow

Standards for writing structured commit messages that automate semantic versioning and changelog generation.

## Checklist
- [ ] Format: `<type>(<optional scope>): <subject>` (e.g., `feat(auth): add OAuth2 refresh token handling`).
- [ ] Primary Types:
  - `feat`: A new feature for the user
  - `fix`: A bug fix for the user
  - `docs`: Documentation changes only
  - `refactor`: Code change that neither fixes a bug nor adds a feature
  - `test`: Adding missing tests or correcting existing tests
  - `chore`: Build process, auxiliary tools, or dependency updates
- [ ] Use imperative mood in the subject ("add" not "added" or "adds").
- [ ] Do not capitalize the first letter of the subject, and no ending period.
- [ ] Keep PRs focused to single logical concerns for easier code review.
