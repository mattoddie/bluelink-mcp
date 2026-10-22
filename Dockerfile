# syntax=docker/dockerfile:1

# The app is pure Python, so the build stage always runs on the build
# machine's own platform. Multi-arch builds don't run uv under emulation.
# Base images are pinned by digest (Dependabot updates them); the tags are for reading.
FROM --platform=$BUILDPLATFORM python:3.14-slim-bookworm@sha256:48b13b003dda20b16f9442b8475aa05fe21bf6579a8c881db92ffb4d8fd20f83 AS build
COPY --from=ghcr.io/astral-sh/uv:0.13@sha256:b9fddcde095a7aeafd499024adb8927df6619c7c5672f5ff7126474715f62ba4 /uv /bin/uv
ENV UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src ./src
COPY tests ./tests
COPY scripts ./scripts
COPY docs ./docs
RUN uv sync --locked && uv run pytest -q && uv run python scripts/gen_tool_docs.py --check
# Release builds pass the version from the Git tag (e.g. 1.2.3).
ARG VERSION
RUN if [ -n "$VERSION" ]; then uv version --frozen "$VERSION"; fi \
 && rm -rf .venv && uv sync --frozen --no-dev --no-editable --compile-bytecode

FROM python:3.14-slim-bookworm@sha256:48b13b003dda20b16f9442b8475aa05fe21bf6579a8c881db92ffb4d8fd20f83
LABEL org.opencontainers.image.title="bluelink-mcp" \
      org.opencontainers.image.description="MCP server for Hyundai Bluelink and Kia Connect" \
      org.opencontainers.image.source="https://github.com/mattoddie/bluelink-mcp" \
      org.opencontainers.image.documentation="https://bluelink-mcp.mattoddie.dev/docs/" \
      org.opencontainers.image.licenses="MIT" \
      io.modelcontextprotocol.server.name="io.github.mattoddie/bluelink-mcp"
RUN useradd --create-home --uid 1000 app && mkdir /data && chown app /data
COPY --from=build /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    MCP_TRANSPORT=http \
    MCP_HTTP_HOST=0.0.0.0 \
    MCP_HTTP_PORT=8080 \
    BLUELINK_TOKEN_FILE=/data/token.json
USER app
VOLUME /data
EXPOSE 8080
# Checks /health on MCP_HTTP_HOST and MCP_HTTP_PORT (loopback for 0.0.0.0); passes in stdio mode.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD ["bluelink-mcp", "healthcheck"]
ENTRYPOINT ["bluelink-mcp"]
