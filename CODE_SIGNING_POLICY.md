# Code signing policy

## Current status

Official releases are currently unsigned. The project is preparing an
application to SignPath Foundation; publication of this policy does not mean
that the application has already been accepted.

Required attribution after approval:

> Free code signing provided by SignPath.io, certificate by SignPath Foundation

## Source and build integrity

- Official source repository: https://github.com/luoxu-ai/ai-media-tagger
- Release binaries must be produced by the GitHub Actions workflow stored in
  this repository.
- Build scripts, dependency versions and model files are version controlled.
- Every signing request must correspond to a tagged public release and requires
  manual approval.
- Release pages must publish a SHA-256 checksum for each executable.

## Team roles

- Committer and reviewer: [luoxu-ai](https://github.com/luoxu-ai)
- Signing approver: [luoxu-ai](https://github.com/luoxu-ai)

The maintainer uses multi-factor authentication for repository and signing
access. Contributions from other people must be reviewed before merging.

## Privacy

See [PRIVACY.md](PRIVACY.md). The application will not transfer any information
to other networked systems unless specifically requested by the user operating
it.

