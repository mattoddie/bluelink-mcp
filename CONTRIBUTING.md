# Contributing to bluelink-mcp

Thanks for wanting to help! bluelink-mcp is a community project, and every kind of contribution is welcome: bug reports, compatibility reports from your car, documentation fixes, new tools and code review.

By taking part you agree to follow the [code of conduct](CODE_OF_CONDUCT.md).

- [Ways to contribute](#ways-to-contribute)
- [Development setup](#development-setup)
- [Making a change](#making-a-change)
- [Pull request guidelines](#pull-request-guidelines)
- [Commit messages](#commit-messages)
- [Adding or changing tools](#adding-or-changing-tools)
- [Releases](#releases)
- [Getting help](#getting-help)

## Ways to contribute

- **Report a bug.** Use the [bug report form](https://github.com/mattoddie/bluelink-mcp/issues/new?template=bug_report.yml). Include your car, region and the exact error, and strip out passwords, PINs, tokens, VINs and locations.
- **Report compatibility.** Cars, regions and Bluelink generations vary a lot. A [compatibility report](https://github.com/mattoddie/bluelink-mcp/issues/new?template=compatibility.yml) saying *"everything works on a 2023 Ioniq 5 in Germany"* is genuinely useful.
- **Suggest a feature.** Open a [feature request](https://github.com/mattoddie/bluelink-mcp/issues/new?template=feature_request.yml). If [`hyundai_kia_connect_api`](https://github.com/Hyundai-Kia-Connect/hyundai_kia_connect_api) already supports it, link the method. That's often most of the work.
- **Improve the docs.** Typos, unclear steps and missing examples are all fair game. Small docs fixes can go straight to a pull request.
- **Write code.** For anything big, please open an issue first so we can agree on the approach before you invest time.
- **Report a vulnerability privately.** See [SECURITY.md](SECURITY.md). Please don't open a public issue for it.

Fixes to how Bluelink itself is spoken to, such as login changes or new regions, usually belong in the upstream library. bluelink-mcp picks them up when the dependency is updated.

## Development setup

You need **Python 3.12+** and **[uv](https://docs.astral.sh/uv/)**, plus **Docker** to build the image.

```sh
git clone https://github.com/<you>/bluelink-mcp.git   # your fork
cd bluelink-mcp
uv sync
uv run pytest
```

The tests run against a fake vehicle manager in under a second. You don't need a Bluelink account or a car to contribute.

See [docs/development.md](docs/development.md) for the architecture and a walkthrough of adding a tool.

## Making a change

1. **Fork** the repository and create a branch from `main`, for example `git checkout -b feat/charge-schedule`.
2. **Make your change** with tests. Keep it focused: one feature or fix per pull request.
3. **Run the checks** that CI runs:

   ```sh
   uv run pytest
   uv run ruff check . && uv run ruff format --check .
   uv run python scripts/gen_tool_docs.py --check
   uv run python scripts/mcpb.py manifest --check
   uv run python scripts/registry.py check
   docker build .   # optional: the image builds and the tests pass inside it
   ```

4. **Update the docs.** If you changed tools, run `uv run python scripts/gen_tool_docs.py`. Update `README.md` or `docs/` if behaviour changed, and add a line under **Unreleased** in [CHANGELOG.md](CHANGELOG.md) for anything users will notice.
5. **Open a pull request** against `main` with a [Conventional Commits](#commit-messages) title, and fill in the template.

## Pull request guidelines

- CI must be green. It tests on Python 3.12, 3.13 and 3.14, lints, checks the generated docs, builds the image for amd64 and arm64, and smoke-tests the container.
- Match the existing style: type hints, small focused functions, `ruff format`, and comments that explain *why* rather than *what*.
- **No new dependencies without discussion.** The runtime dependencies are `mcp` and `hyundai_kia_connect_api`.
- Never include real passwords, PINs, tokens, VINs, registration numbers or locations in code, tests, fixtures, logs or screenshots.
- Maintainers may push small fixes to your branch or squash commits when merging.
- Label suggestions (`bug`, `enhancement`, `new-tool`, `documentation`, `breaking`, …) are welcome. Labels sort the auto-generated release notes.

## Commit messages

Commits and pull request titles follow [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/). Pull requests are squash-merged, so the PR title becomes the commit on `main`, and a check fails the pull request if its title doesn't match.

- **Format:** `<type>(<optional scope>)<optional !>: <description>`, for example `feat: add charging schedule tool`, `fix: round climate temperatures correctly`, `docs: explain the TLS choice` or `build(deps): bump python from 3.14 to 3.15`.
- **Types:** `feat`, `fix`, `docs`, `refactor`, `perf`, `test`, `build` (dependencies, Dockerfile and toolchain), `ci` (workflows), `chore` (anything else, such as `chore(release): v1.2.0`), `revert` and `style`.
- **Description:** imperative mood, lower-case first word and no trailing full stop. Keep the whole subject to 72 characters or fewer.
- **Breaking changes:** add `!` after the type or scope **and** a `BREAKING CHANGE: …` footer in the body or PR description, and label the pull request `breaking`.
- **Body:** explain **why** when it isn't obvious, and reference issues with `Fixes #123`.

## Adding or changing tools

The full guide is in [docs/development.md](docs/development.md#adding-a-tool). The short version:

- Register anything that sends a command to the car **only when writes are enabled**, and anything with security impact behind its own switch, like unlock. **This gating is the project's main safety feature, so reviewers check it carefully.**
- Use the right annotations: `READ`, `WRITE` or `SENSITIVE`.
- Take an optional `vehicle`, and `wait` for commands.
- Describe every argument, and write descriptions for an AI model: what the tool does, side effects and units.
- Be gentle with the car: don't add tools that wake it without a rate limit.
- Add tests, add the tool to a group in `scripts/gen_tool_docs.py`, and regenerate the docs.

Changing an existing tool's name or arguments in an incompatible way, or moving it to a less restrictive access level, is a **breaking change**. Call it out in the PR and the changelog.

## Releases

Releases are made by maintainers by publishing a GitHub release. That triggers the workflow that builds and pushes the Docker image. The changelog update for a release goes in a small pull request titled `chore(release): vX.Y.Z`. See [docs/releasing.md](docs/releasing.md).

## Getting help

Ask in [GitHub Discussions](https://github.com/mattoddie/bluelink-mcp/discussions), or comment on the issue you're working on. No question is too small.
