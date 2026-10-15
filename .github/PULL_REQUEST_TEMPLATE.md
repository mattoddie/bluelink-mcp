## What does this change?

<!-- A short description of the change and why it's needed. Link any related issue: "Fixes #123". -->

## How was it tested?

<!-- e.g. new tests against the fake vehicle manager, `uv run pytest`, and/or tried against a real car (model, year, region). -->

## Checklist

- [ ] `uv run pytest` and `uv run ruff check .` pass
- [ ] New or changed tools have tests, and `uv run python scripts/gen_tool_docs.py` has been run
- [ ] Command tools are registered only when writes are enabled, with the right annotations
- [ ] Docs updated where behaviour changed (README / `docs/`)
- [ ] `CHANGELOG.md` has an entry under **Unreleased** for user-facing changes
- [ ] No passwords, PINs, tokens, VINs or locations in code, tests, logs or screenshots
