# Releasing

Releases are driven by **GitHub releases**. Publishing a release with a tag like `v1.2.3` triggers the [Release workflow](../.github/workflows/release.yml). That workflow builds the Docker image for `linux/amd64` and `linux/arm64` and pushes it to `ghcr.io/mattoddie/bluelink-mcp`, then attaches the Claude Desktop extension bundle (`bluelink-mcp-X.Y.Z.mcpb`) to the release. Stable releases are then published to the [MCP Registry](https://registry.modelcontextprotocol.io). Nothing is published by merging to `main` alone.

- [Versioning](#versioning)
- [Release checklist](#release-checklist)
- [Pre-releases](#pre-releases)
- [What the workflow does](#what-the-workflow-does)
- [MCP Registry](#mcp-registry)
- [If something goes wrong](#if-something-goes-wrong)
- [Repository setup](#repository-setup)

## Versioning

The project follows [Semantic Versioning](https://semver.org). Its public API is the set of **tool names, their arguments and the environment variables**:

| Change | Bump | Examples |
|---|---|---|
| Breaking | **major** (`2.0.0`) | Renaming or removing a tool, argument or variable; moving a tool to a less restrictive access level; changing a default so that more is allowed |
| New features | **minor** (`1.3.0`) | New tools or arguments |
| Fixes | **patch** (`1.2.4`) | Bug fixes, compatibility fixes, docs, dependency updates |

While the version is `0.x`, minor bumps may contain breaking changes. Call them out in the release notes.

The version comes **from the tag**. `pyproject.toml` stays at `0.0.0.dev0` in the repository, and release builds stamp the tag's version into the image. The server reports it to MCP clients and prints it on its first log line.

## Release checklist

1. **Check `main` is green** on [CI](https://github.com/mattoddie/bluelink-mcp/actions/workflows/ci.yml).
2. **Update the changelog.** In a small PR titled `chore(release): v1.2.0`, move the **Unreleased** entries in [`CHANGELOG.md`](../CHANGELOG.md) under a new heading, `## [1.2.0] - YYYY-MM-DD`, and update the comparison links at the bottom. Fill in the final tool count from the top of [`docs/tools.md`](tools.md). Merge it.
3. **Publish the release.** Use either the CLI or the web UI.

   With the CLI:

   ```sh
   gh release create v1.2.0 --target main --title "v1.2.0" --generate-notes
   ```

   Or in the web UI: **Releases → Draft a new release → Choose a tag → type `v1.2.0` → Create new tag on publish**, target `main`. Click **Generate release notes**, edit them as needed, then **Publish release**.

4. **Watch the workflow.** Open **Actions → Release**. It takes a few minutes. When it finishes, the release notes gain a *Container image* section with the pull command, digest and tags.
5. **Check the image:** `docker pull ghcr.io/mattoddie/bluelink-mcp:1.2.0`, and that the release has the `.mcpb` bundle attached.
6. **Check the registry entry:** `curl -s 'https://registry.modelcontextprotocol.io/v0/servers?search=io.github.mattoddie/bluelink-mcp' | jq '.servers[].server.version'` lists the new version.

Release notes are generated from merged PR titles and grouped by label. The groups are set in [`.github/release.yml`](../.github/release.yml). Label PRs `breaking`, `enhancement`, `new-tool`, `bug`, `compatibility`, `documentation` or `dependencies` to sort them, or `skip-changelog` to leave a PR out.

## Pre-releases

To test a release candidate, use a pre-release tag and tick **Set as a pre-release**:

```sh
gh release create v1.3.0-rc.1 --target main --prerelease --generate-notes
```

Pre-releases are published only under their exact version (`ghcr.io/mattoddie/bluelink-mcp:1.3.0-rc.1`). They never move `latest`, `1` or `1.3`, and they aren't published to the MCP Registry.

## What the workflow does

| Step | Detail |
|---|---|
| Validate the tag | Must match `vMAJOR.MINOR.PATCH` with an optional `-prerelease` suffix, otherwise the workflow fails before building. |
| Build | `docker buildx` for `linux/amd64` and `linux/arm64`, from scratch with no build cache. The test suite runs inside the build, so failing tests mean no image. |
| Tag | `X.Y.Z` always. For stable releases also `X.Y`, `X` (from `1.0.0` on) and `latest`. |
| Push | To `ghcr.io/mattoddie/bluelink-mcp` using the workflow's `GITHUB_TOKEN`. No personal tokens are needed. |
| Supply chain | Attaches an SBOM and SLSA provenance to the image, and pushes a signed build provenance attestation (`gh attestation verify`). |
| Release notes | Appends the pull command, digest and tags. |
| MCP Bundle | After the image, builds `bluelink-mcp-X.Y.Z.mcpb` with `scripts/mcpb.py pack`, validates its manifest with `@anthropic-ai/mcpb`, attests it, uploads it to the release and adds a *Claude Desktop* section to the notes. |
| MCP Registry | Stable releases only, after the image and the bundle. Stamps the version, image tag and the bundle's SHA-256 into `server.json`, validates it and publishes it with `mcp-publisher`. See [MCP Registry](#mcp-registry). |

## MCP Registry

[`server.json`](../server.json) describes bluelink-mcp for the [MCP Registry](https://registry.modelcontextprotocol.io) as `io.github.mattoddie/bluelink-mcp`. It lists two packages:

- **The Docker image**, `ghcr.io/mattoddie/bluelink-mcp:X.Y.Z`, run over stdio: `MCP_TRANSPORT` is fixed to `stdio`, and a `bluelink-data` volume keeps the login token cache. The registry checks that the image carries the `io.modelcontextprotocol.server.name` label from the [`Dockerfile`](../Dockerfile).
- **The Claude Desktop bundle**, `bluelink-mcp-X.Y.Z.mcpb` from the release, with its SHA-256. The bundle is platform-independent, so there is one.

The environment variables mirror the bundle's settings (`user_config` in [`mcpb/manifest.json`](../mcpb/manifest.json)). CI runs `uv run python scripts/registry.py check`, which fails if they drift apart, and validates `server.json` with `mcp-publisher validate`. Change the two files together.

In the repository, `server.json` holds the `0.0.0-dev` placeholder, like the bundle manifest. The *registry* job in the release workflow:

1. Installs `mcp-publisher` at a pinned version and checks its SHA-256. To upgrade it, change `MCP_PUBLISHER_VERSION` and `MCP_PUBLISHER_SHA256` in both [`release.yml`](../.github/workflows/release.yml) and [`ci.yml`](../.github/workflows/ci.yml), taking the hash of `mcp-publisher_linux_amd64.tar.gz` from the registry release's `registry_<version>_checksums.txt`.
2. Downloads the release's bundle, hashes it and runs `python3 scripts/registry.py stamp X.Y.Z <sha256>`.
3. Logs in with GitHub OIDC (`mcp-publisher login github-oidc`, which needs `id-token: write` on that job only, and no secrets) and runs `mcp-publisher publish`.

A version can be published to the registry only once, and it can't be changed afterwards.

### Re-running the registry step

If only the *registry* job failed, open the run under **Actions → Release** and choose **Re-run failed jobs**. The image and bundle aren't rebuilt. If the version did reach the registry, a re-run fails because the version already exists: there's nothing left to do.

### Publishing by hand

If the job can't be fixed in time, publish from a checkout of the release tag. You need to be an owner of the `mattoddie` GitHub organisation: the registry grants the `io.github.mattoddie` namespace only to owners.

```sh
git checkout v1.2.0
version=1.2.0 dir=$(mktemp -d)
gh release download "v$version" --pattern "bluelink-mcp-$version.mcpb" --dir "$dir"
python3 scripts/registry.py stamp "$version" "$(sha256sum "$dir/bluelink-mcp-$version.mcpb" | cut -d' ' -f1)"
mcp-publisher validate server.json
mcp-publisher login github    # opens a browser to sign in to GitHub
mcp-publisher publish server.json
git checkout server.json
```

Install the same `mcp-publisher` version the workflow pins, from the [registry releases](https://github.com/modelcontextprotocol/registry/releases), and check its hash against the release's checksums file.

## If something goes wrong

- **The workflow failed** for a transient reason: re-run it from the Actions tab. It's safe to re-run, and it pushes the same tags again.
- **The release was wrong** (bad code or wrong tag): don't move or reuse a published tag, because people may already have pulled it. Fix the problem on `main` and publish the next patch version. You can delete the bad GitHub release and mark it as such in the changelog, but leave the image tag alone.
- **Wrong tag format** (e.g. `1.2.0` without the `v`): delete the release and tag (`gh release delete 1.2.0 --cleanup-tag`) and publish again with the right tag.

## Repository setup

### Rulesets

Two rulesets protect the repository. You can view and edit them under **Settings → Rules → Rulesets**, and that's where to create them if you're setting up a fresh copy.

**Protect main** (the default branch):

- Changes land only through pull requests. Nobody can push directly, force-push or delete the branch, and there are no bypasses.
- The CI checks *Test (Python 3.12)*, *Test (Python 3.13)*, *Test (Python 3.14)* and *Docker image* must pass, and the branch must be up to date with `main`.
- Review threads must be resolved before merging. The only merge method is squash.
- No approving review is required yet, because GitHub doesn't let authors approve their own pull requests and the project has a single maintainer. Raise this when there are more maintainers.

**Protect release tags** (`v*`):

- Only repository admins can create, move or delete version tags, so only maintainers can publish releases. Published versions can't be re-pointed.

> [!NOTE]
> Required checks are matched by job name. If you rename a job in [`ci.yml`](../.github/workflows/ci.yml), update the *Protect main* ruleset in the same change. Otherwise pull requests wait forever for a check that never reports.

### Container package visibility

New GHCR packages normally start private, and GitHub doesn't offer an API to change this. A package linked to a public repository usually becomes public automatically. If it starts private, open the package page (**Your profile → Packages → bluelink-mcp → Package settings**) and choose **Change visibility → Public** under **Danger Zone**.

The package links to the repository automatically through the image's `org.opencontainers.image.source` label, which makes it show in the repository sidebar.
