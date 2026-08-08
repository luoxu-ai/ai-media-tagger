# Code signing policy

## Current status

The SignPath Foundation application submitted on 2026-07-30 was not approved because the project has not yet established sufficient public adoption and visibility. Official releases are therefore currently unsigned. The project may reapply after it gains broader public recognition.

## Source and build provenance

- Official source repository: <https://github.com/luoxu-ai/ai-media-tagger>
- Official releases: <https://github.com/luoxu-ai/ai-media-tagger/releases>
- Release binaries are built by the repository's public GitHub Actions workflow from a tagged commit.
- The workflow runs automated tests before producing the Windows application and installer.
- Release artifacts must match the product name and version declared by the tagged source revision.

## Team roles

- Committer and reviewer: [Xu Luo](https://github.com/luoxu-ai)
- Signing approver: [Xu Luo](https://github.com/luoxu-ai)

The sole maintainer reviews dependency, build-script and release changes before approving a signing request. If additional maintainers join, no author may approve their own security-sensitive build changes without another maintainer's review.

## Privacy

This program will not transfer any information to other networked systems unless specifically requested by the user or the person installing or operating it. Version checks and user-requested updates access GitHub. See [PRIVACY.md](PRIVACY.md) for details.

## Release verification

- Every release publishes a SHA-256 digest for the installer.
- The application verifies the GitHub Release asset digest before installation.
- A valid Windows Authenticode signature is accepted automatically. When an official installer is unsigned, the application displays an explicit warning and requires a second user confirmation before installation.
- Invalid, damaged or unverifiable signatures are rejected.
- Draft and prerelease builds are not offered through the normal in-app update channel.
