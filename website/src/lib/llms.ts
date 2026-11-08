import { readFileSync } from "node:fs";
import { join } from "node:path";
import { getCollection, type CollectionEntry } from "astro:content";

/**
 * Shared by /llms.txt and /llms-full.txt (https://llmstxt.org). Both are built
 * from the same synced docs as the site, so they can't drift from it.
 */
export const NAME = "bluelink-mcp";
export const GITHUB = `https://github.com/mattoddie/${NAME}`;
// The landing page's meta description, and the summary at the top of /llms.txt.
export const DESCRIPTION =
  "An open-source MCP server for Hyundai Bluelink and Kia Connect. Ask Claude and other AI assistants about your car's charge, range, locks and location, and let them pre-heat the cabin, lock up or start charging when you allow it.";

// The sidebar's order. Pages not listed here follow, in file order.
const ORDER = ["", "getting-started", "clients", "configuration", "usage", "tools", "deployment", "security", "compatibility", "troubleshooting"];
// Pages about working on the project rather than using it, listed under "Optional".
export const PROJECT = new Set(["contributing", "development", "releasing", "security-policy", "changelog"]);

export const slugOf = (entry: CollectionEntry<"docs">) => entry.id.replace(/^docs\/?/, "");

export async function docs() {
  const rank = (entry: CollectionEntry<"docs">) => {
    const i = ORDER.indexOf(slugOf(entry));
    return i === -1 ? ORDER.length : i;
  };
  return (await getCollection("docs")).sort((a, b) => rank(a) - rank(b));
}

/** The page's Markdown without the "Generated from" comment sync-docs adds. */
export const markdown = (entry: CollectionEntry<"docs">) => (entry.body ?? "").replace(/^<!-- Generated from .*-->\n+/, "").trim();

/** The tool summary line from docs/tools.md, e.g. "has 15 tools in 5 groups: 7 read, 7 write and 1 unlock." */
export const toolSummary = () =>
  readFileSync(join(process.cwd(), "../docs/tools.md"), "utf8")
    .match(new RegExp(`^${NAME} (has .*)$`, "m"))?.[1]
    .replaceAll("**", "");
