# Web release and deployment

FoilBench publishes the static browser lab as a versioned GitHub Release and
deploys the same build to GitHub Pages. Ordinary branch pushes and pull
requests validate the release packager but never publish or deploy anything.

The target project Pages URL is `https://gravifer.github.io/FoilBench/`. Before
the first release, repository **Settings → Pages → Build and deployment** must
use **GitHub Actions** as its source. The repository `GITHUB_TOKEN` can deploy
an enabled Pages site, but it cannot enable Pages on the owner's behalf.

The `github-pages` environment must also permit both workflow entry points.
Under **Settings → Environments → github-pages → Deployment branches and
tags**, retain these allowed refs:

- branch `main`, for manual workflow dispatches;
- tag pattern `v*.*.*`, for tag-triggered releases.

An optional environment wait timer is compatible with the workflow; it simply
keeps the deployment job in its expected waiting state before Pages promotion.
Do not configure only `main`: GitHub evaluates a tag-triggered deployment
against the tag ref even though the workflow separately proves that its commit
is reachable from `origin/main`.

## Starting a release

The release workflow accepts versions of the form
`vMAJOR.MINOR.PATCH[-PRERELEASE]`, including `v0.2.0`, `v0.2.0-rc1`, and
`v0.2.0-rc.1`. A suffix marks the GitHub Release as a prerelease. Build
metadata introduced with `+` is intentionally unsupported.

Stable release labels need only be valid and unused; publication itself is not
globally monotonic. For example, publishing the stable backport `v4.2.13`
after `v4.7.1` is allowed without replacing the live lab. A prerelease is
stricter: once `vX.Y.Z` exists, that final tag permanently closes its series
and every later `vX.Y.Z-*` candidate is rejected before build work begins.

There are two equivalent entry points.

### GitHub Actions interface

1. Open **Actions → Release web lab**.
2. Select **Run workflow** and choose `main`.
3. Enter the new version.
4. Run the workflow.

The workflow refuses manual runs from any other branch, refuses a version
whose tag already exists, and refuses a prerelease whose corresponding final
tag exists. It creates the release tag at the tested `main` commit only after
the identified artifact is ready to publish.

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
`main` history remains eligible. Pushed prerelease tags are subject to the same
closed-series rule as manually requested versions.

## Build, publication, and Pages promotion

Release workflow runs share one global concurrency queue. Different version
labels therefore cannot race one another while reading or updating the live
Pages site.

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
3. A GitHub Release receives both assets and is published.
4. The release is published explicitly without changing GitHub's **Latest**
   designation.
5. The workflow compares a stable candidate with the release identity served
   by the live Pages site.
6. Only an eligible stable build is deployed; after successful deployment,
   its GitHub Release is marked **Latest**.

GitHub adds its tag-based source ZIP and tarball automatically. FoilBench does
not publish a separate Rust/WASM archive or use GitHub Packages; the WASM files
needed by the lab are already contained in the static web archive.

Repository releases are immutable after publication. The workflow therefore
builds and verifies the identified artifact before publishing it, and later
failures are recovered by rerunning the failed job rather than editing or
recreating that release.

Prereleases are publish-only and never deploy to Pages. Stable releases use
strict SemVer precedence against the version in the live site's
`release.json`: a candidate deploys only when it is newer. An equal or older
stable release is still published, but Pages and GitHub's **Latest** release
remain unchanged. A missing `release.json` response (HTTP 404) is treated as
the first deployment; malformed, unreachable, or otherwise untrustworthy live
metadata fails closed instead of guessing.

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
- A prerelease or non-newer stable release publishes successfully and records
  a skipped Pages promotion; this is an expected successful outcome.
- An inability to establish the live release version leaves the newly
  published release visible but non-latest and prevents deployment.
- A Pages failure after publication leaves the GitHub Release intact and the
  previous Pages deployment live. Rerun the failed deployment job; rebuilding
  or recreating the release is unnecessary.
- If GitHub reports that a tag is not allowed to deploy to `github-pages`, add
  or correct the `v*.*.*` environment tag rule described above, then rerun only
  **Deploy released Pages artifact**. Its dependent **Mark deployed release as
  Latest** job will follow after a successful deployment.
- If Pages succeeds but updating GitHub's **Latest** designation fails, the
  new site is already live. Rerun only the failed promotion job.

GitHub Releases and Pages are separate services and cannot be updated as one
atomic transaction. Publishing the release before deployment guarantees that
every successful Pages update corresponds to an already-visible release. The
site's `release.json`, downloadable archive, and checksum make that
relationship auditable.

After publication has created a tag, retry failed jobs rather than rerunning
the entire workflow: a new complete manual run correctly rejects the now-used
version label.
