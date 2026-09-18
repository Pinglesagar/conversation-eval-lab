# Security

## Reporting

Open a GitHub issue. This is a portfolio repository, not a service — there is
nothing deployed, nothing listening, and no user data anywhere in it.

## The threat this repository actually has

One: **a committed credential.** Everything else here is offline arithmetic over
files. So the controls are all pointed at that.

| Control | Where |
| --- | --- |
| `.env` is gitignored, and has never appeared in any commit | `.gitignore`, verifiable with `git log --all --name-only \| grep '^\.env$'` |
| `.env.example` carries key *names* only, never values | `.env.example` |
| A `detect-private-key` hook runs before every commit | `.pre-commit-config.yaml` |
| No CI step has a `secrets:` block, and CI asserts no `LAB_LIVE_*` is set | `.github/workflows/ci.yml` |
| Every provider call is behind an opt-in variable that refuses rather than replaying | ten `LAB_LIVE_*` flags |
| Recorded fixtures are checked for anything credential-shaped before commit | the recording scripts |

## If a key is exposed anyway

Rotate it at the provider first, then remove it from history. Rotating is what
actually ends the exposure; rewriting history without rotating is theatre,
because the old value is already in somebody's clone.

## What is deliberately *not* secret

The recorded traces, the audio, the transcripts and the scorecards are all
synthetic. No real person, customer, adviser or firm appears anywhere in this
repository. The advisory firm in the fixtures does not exist.
