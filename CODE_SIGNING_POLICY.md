# Code signing policy

## Current status

Official releases are currently unsigned. An application for the SignPath
Foundation open-source program was submitted on 2026-07-30 and is awaiting
review. Publication of this policy does not mean that the application has
already been accepted.

Required attribution after approval:

> Free code signing provided by SignPath.io, certificate by SignPath Foundation

## Source and build integrity

- Official source repository: https://github.com/luoxu-ai/ai-media-tagger
- Release binaries must be produced by the public
  [GitHub Actions workflow](.github/workflows/build-windows.yml) stored in this
  repository, using GitHub-hosted Windows runners.
- Build scripts, dependency versions and model files are version controlled.
- Every signing request must correspond to a tagged public release and requires
  manual approval.
- Release pages must publish a SHA-256 checksum for each executable.
- After approval, signing requests will use SignPath's official GitHub action
  and the workflow artifact ID. SignPath organization, project and policy
  identifiers will only be added after they are provisioned by SignPath.

## Team roles

- Committer and reviewer: [luoxu-ai](https://github.com/luoxu-ai)
- Signing approver: [luoxu-ai](https://github.com/luoxu-ai)

The maintainer uses multi-factor authentication for repository and signing
access. Contributions from other people must be reviewed before merging.

## Privacy

See [PRIVACY.md](PRIVACY.md). The application will not transfer any information
to other networked systems unless specifically requested by the user operating
it.
