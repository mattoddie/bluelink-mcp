import type { APIRoute } from "astro";
import { DESCRIPTION, docs, GITHUB, NAME, PROJECT, slugOf, toolSummary } from "../lib/llms";

/** /llms.txt: what the project is, and a link to every docs page with its description. */
export const GET: APIRoute = async ({ site }) => {
  const pages = await docs();
  const link = (entry: (typeof pages)[number]) =>
    `- [${entry.data.title}](${new URL(entry.id + "/", site)})${entry.data.description ? `: ${entry.data.description}` : ""}`;

  const text = [
    `# ${NAME}`,
    `> ${DESCRIPTION}`,
    [`${NAME} ${toolSummary() ?? ""}`.trim(), `Source code, issues and releases: ${GITHUB}`].join("\n\n"),
    "## Docs",
    [`- [${NAME} home page](${site}): an overview, example questions and a quick start`, ...pages.filter((entry) => !PROJECT.has(slugOf(entry))).map(link)].join("\n"),
    "## Optional",
    [
      `- [Complete documentation](${new URL("/llms-full.txt", site)}): every docs page above in one Markdown file`,
      ...pages.filter((entry) => PROJECT.has(slugOf(entry))).map(link),
      "- [All MCP servers by Matt Oddie](https://mcp.mattoddie.dev/)",
    ].join("\n"),
  ].join("\n\n");

  return new Response(text + "\n", { headers: { "Content-Type": "text/plain; charset=utf-8" } });
};
