---
name: releasing
description: How to publish a release, protect the credentials that publish or deploy, and handle a security vulnerability. Read it before you do any of these.
---

# Releasing

## Publish a release

Publish a release when the maintainer asks, or when a goal's milestone is done and the project publishes releases.

1. Check that the latest run of `check` on main passed, with `gh run list --branch main --limit 1`.
2. Choose the version from the pull requests since the last release: a major version for a title with `!`, a minor one for a `feat`, and a patch otherwise.
3. Run `gh release create <version> --target <sha> --generate-notes`, with the full SHA you checked.

## Protect the credentials that publish and deploy

- Build in a job that can only read the repository. Give a credential or a token that can write only to a separate job that uploads what the build made.
- Publish and deploy from a job in the `release` environment.
- Where the registry or host accepts GitHub's OpenID Connect login, such as PyPI's trusted publishing, use it. Otherwise, ask the maintainer to store the token as a secret of the `release` environment.

## Handle a security vulnerability

A vulnerability gets a security advisory only when it's serious and in a release that people use. Fix anything else in an ordinary pull request.

For an advisory, ask the maintainer first, because the advisory, its fix and its publication are theirs to decide. Until a release fixes it, keep the problem out of issues, pull requests and commit messages, and describe the fix only by what it changes.
