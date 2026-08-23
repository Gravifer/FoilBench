# Web release and deployment

FoilBench publishes the static browser lab as a versioned GitHub Release and
deploys the same build to GitHub Pages. Ordinary branch pushes and pull
requests validate the release packager but never publish or deploy anything.

The target project Pages URL is `https://gravifer.github.io/FoilBench/`. Before
the first release, repository **Settings → Pages → Build and deployment** must
use **GitHub Actions** as its source. The repository `GITHUB_TOKEN` can deploy
an enabled Pages site, but it cannot enable Pages on the owner's behalf.

## Starting a release

The release workflow accepts versions of the form
`vMAJOR.MINOR.PATCH[-PRERELEASE]`, including `v0.2.0`, `v0.2.0-rc1`, and
`v0.2.0-rc.1`. A suffix marks the GitHub Release as a prerelease. Build
metadata introduced with `+` is intentionally unsupported.

There are two equivalent entry points.

### GitHub Actions interface

1. Open **Actions → Release web lab**.
2. Select **Run workflow** and choose `main`.
3. Enter the new version.
4. Run the workflow.

The workflow refuses manual runs from any other branch and refuses a version
whose tag already exists. It creates the release tag at the tested `main`
commit only after the identified artifact is ready to publish.

The same operation can be requested through GitHub CLI:

```shell
gh workflow run release.yml --ref main -f version=v0.2.0
```

### Pushed tag

An annotated or lightweight matching tag also starts the workflow:

```shell
git tag -a v0.2.0-rc.1 -m "FoilBench v0.2.0-rc.1"
git push origin v0.2.0-rc.1
```

The workflow fetches the full repository history and requires the tagged
commit to be reachable from `origin/main`. A tag on an unmerged feature commit
therefore cannot publish or deploy the lab. A known-good older commit on the
`main` history remains eligible.

## Build and publication transaction

The workflow performs one identified production build with the fixed Pages
base `/FoilBench/`. Its generated `release.json` records:

- the release version;
- the complete Git commit SHA;
- the UTC build time;
- the Pages base path;
- the accepted contract identifier and revision.

After static checks, unit tests, and the production Chromium smoke test pass,
that single build feeds both publication paths:

1. `foilbench-web-vX.Y.Z.zip` packages the static site.
2. `SHA256SUMS` identifies the exact archive bytes.
3. A draft GitHub Release receives both assets and is then published.
4. Only after publication succeeds is the already-built Pages artifact
   deployed.

GitHub adds its tag-based source ZIP and tarball automatically. FoilBench does
not publish a separate Rust/WASM archive or use GitHub Packages; the WASM files
needed by the lab are already contained in the static web archive.

Prereleases use the same Pages destination. Publishing an RC therefore makes
that RC the live lab while retaining GitHub's prerelease classification.

To verify a downloaded release archive on a Unix-like system:

```shell
sha256sum --check SHA256SUMS
```

On PowerShell, compare `Get-FileHash -Algorithm SHA256` with the value recorded
in `SHA256SUMS`.

## Failure and retry behavior

- A build or test failure creates no release and performs no deployment.
- A pushed tag remains in Git even if its release checks fail; correct the
  problem with a new version rather than moving a published release tag.
- A release-publication failure leaves Pages on its previous release. A draft
  may remain available for inspection or removal.
- A Pages failure after publication leaves the GitHub Release intact and the
  previous Pages deployment live. Rerun the failed deployment job; rebuilding
  or recreating the release is unnecessary.

GitHub Releases and Pages are separate services and cannot be updated as one
atomic transaction. Publishing the release before deployment guarantees that
every successful Pages update corresponds to an already-visible release. The
site's `release.json`, downloadable archive, and checksum make that
relationship auditable.
