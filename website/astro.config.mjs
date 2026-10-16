// @ts-check
import starlight from "@astrojs/starlight";
import { existsSync, readFileSync } from "node:fs";
import { defineConfig } from "astro/config";

const SITE = "https://bluelink-mcp.mattoddie.dev";
const GITHUB = "https://github.com/mattoddie/bluelink-mcp";
const toolCount = (readFileSync(new URL("../docs/tools.md", import.meta.url), "utf8").match(/^### [a-z]/gm) ?? []).length;

/**
 * Sidebar entries for pages that scripts/sync-docs.mjs actually wrote, so a
 * doc that doesn't exist yet drops out of the sidebar instead of breaking the build.
 */
const docs = (/** @type {string[]} */ ...slugs) =>
  slugs.filter((slug) => existsSync(new URL(`./src/content/docs/${slug}.md`, import.meta.url)));

export default defineConfig({
  site: SITE,
  trailingSlash: "always",
  integrations: [
    starlight({
      title: "bluelink-mcp",
      description:
        "An open-source MCP server for Hyundai Bluelink and Kia Connect. Check and control your car from Claude and other AI assistants.",
      logo: { src: "./src/assets/logo.svg" },
      favicon: "/favicon.svg",
      social: [{ icon: "github", label: "GitHub", href: GITHUB }],
      customCss: ["./src/styles/docs.css"],
      lastUpdated: false,
      credits: false,
      head: [
        { tag: "link", attrs: { rel: "preconnect", href: "https://fonts.googleapis.com" } },
        { tag: "link", attrs: { rel: "preconnect", href: "https://fonts.gstatic.com", crossorigin: true } },
        {
          tag: "link",
          attrs: { rel: "stylesheet", href: "https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" },
        },
        { tag: "meta", attrs: { property: "og:image", content: `${SITE}/og.png` } },
        { tag: "meta", attrs: { name: "twitter:card", content: "summary_large_image" } },
      ],
      sidebar: [
        { label: "Overview", link: "/docs/" },
        {
          label: "Start here",
          items: docs("docs/getting-started", "docs/clients", "docs/configuration"),
        },
        {
          label: "Using bluelink-mcp",
          items: [...docs("docs/usage"), { label: "Tool reference", link: "/docs/tools/", badge: { text: String(toolCount), variant: "note" } }],
        },
        {
          label: "Running it well",
          items: docs("docs/deployment", "docs/security", "docs/compatibility", "docs/troubleshooting"),
        },
        {
          label: "Project",
          items: [...docs("docs/contributing", "docs/development", "docs/releasing", "docs/security-policy", "docs/changelog"), { label: "All MCP servers", link: "https://mcp.mattoddie.dev/" }],
        },
      ].filter((group) => !("items" in group) || group.items.length > 0),
    }),
  ],
});
