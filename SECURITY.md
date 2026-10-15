# Security policy

bluelink-mcp can locate, lock and unlock real cars, so security reports are taken seriously and handled as a priority.

## Supported versions

Security fixes go into the latest release. Please upgrade to the newest version before reporting.

| Version | Supported |
|---|---|
| Latest release | ✅ |
| Older releases | ❌ |

## Reporting a vulnerability

**Please don't report security vulnerabilities in public issues, discussions or pull requests.**

Report them privately through GitHub's **[private vulnerability reporting](https://github.com/mattoddie/bluelink-mcp/security/advisories/new)**: go to the repository's **Security** tab and click **Report a vulnerability**.

Please include:

- A description of the issue and its impact
- Steps to reproduce, or a proof of concept
- The affected version(s) and configuration, such as transport and write/unlock settings
- Any suggested fix, if you have one

## What to expect

- An acknowledgement within **3 working days**.
- An initial assessment within **7 days**, including whether the report is accepted and a rough timeline.
- A fix released as soon as practical, with a GitHub security advisory and credit to you, unless you'd rather stay anonymous.

Please give us a reasonable amount of time to fix the issue before you disclose it publicly.

## Scope

In scope:

- Bypassing `MCP_AUTH_TOKEN`, `MCP_ALLOWED_HOSTS`, or the write and unlock gating, such as sending a command or unlocking when it's disabled
- Leaking the Bluelink password, PIN or session tokens through tool output, logs or files with the wrong permissions
- Getting around the rate limit on waking the car
- Vulnerabilities in the Docker image or the release pipeline

Out of scope:

- Issues that need an attacker who already holds a valid `MCP_AUTH_TOKEN` and uses only the tools that configuration allows. That's intended behaviour.
- Prompt injection that leads the model to call tools the configuration allows. This is a known limitation of AI assistants. [docs/security.md](docs/security.md) explains how to limit the impact.
- Vulnerabilities in Bluelink itself or in `hyundai_kia_connect_api`. Report those to Hyundai or to the [library's maintainers](https://github.com/Hyundai-Kia-Connect/hyundai_kia_connect_api).

For guidance on deploying bluelink-mcp securely, see [docs/security.md](docs/security.md).
