# Releasing

Images are built and published by GitHub Actions (`.github/workflows/release.yml`) whenever a version tag is pushed.
Nobody pushes images by hand.

## Cut a release

1. Set the version in `app/version.py`, e.g. `VERSION = "1.2.0"` ([Semantic Versioning](https://semver.org/):
   breaking change → major, new feature → minor, fix → patch).
2. Move the entries under `## [Unreleased]` in `CHANGELOG.md` to `## [1.2.0] - YYYY-MM-DD`.
3. Commit, merge to `main`, then tag and push:

   ```bash
   git tag -a v1.2.0 -m "RelayRoster 1.2.0"
   git push origin v1.2.0
   ```

4. The workflow checks that the tag matches `app/version.py`, runs all tests, builds for `linux/amd64` and
   `linux/arm64` and pushes `ghcr.io/detmud/relayroster` with the tags `1.2.0`, `1.2`, `1` and `latest`
   (plus SBOM and provenance). Follow it under *Actions* on GitHub.
5. Create a GitHub release for the tag with the changelog entry as release notes.

A tag like `v1.3.0-rc.1` publishes only `1.3.0-rc.1`; `latest`, `1` and `1.3` stay on the last regular release.

## Once, after the very first release

GitHub creates the package as **private**. Make it public, otherwise nobody can pull it:

1. GitHub → your profile → *Packages* → `relayroster` → *Package settings*
2. *Danger Zone* → *Change visibility* → **Public**
3. In the same settings, check that the package is linked to the `RelayRoster` repository (the workflow sets the
   `org.opencontainers.image.source` label, so this normally happens automatically).

## If a release fails

- *Tag does not match*: fix `app/version.py`, delete the tag (`git tag -d v1.2.0 && git push origin :v1.2.0`), tag again.
- *Tests fail*: nothing was published. Fix on `main`, then move the tag as above.
- Never re-tag a version that was already published: people may have pulled it. Publish a fixed patch version instead.
