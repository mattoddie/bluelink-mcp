/**
 * Copies the repository's Markdown docs into Starlight's content folder,
 * so the website and GitHub always show the same text. The repo's docs/
 * stay the source of truth, and nothing under src/content/docs/ is
 * committed.
 *
 * For each file it:
 *   - turns the first "# Heading" into Starlight frontmatter (title,
 *     description, editUrl)
 *   - drops hand-written "Contents" link lists (Starlight has its own ToC)
 *   - rewrites links between .md files to site URLs, and links to other
 *     repo files to GitHub
 *   - converts GitHub alerts (> [!NOTE]) to Starlight asides (:::note)
 *
 * A page that doesn't exist (yet) is skipped with a warning instead of
 * failing the build: links to it point at GitHub, and astro.config.mjs
 * leaves it out of the sidebar.
 */
import { copyFileSync, existsSync, mkdirSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join, normalize, relative } from "node:path";
import { fileURLToPath } from "node:url";

const site = dirname(dirname(fileURLToPath(import.meta.url)));
const repo = dirname(site);
const out = join(site, "src/content/docs/docs");
const GITHUB = "https://github.com/mattoddie/bluelink-mcp";

/** Repo file → URL slug under /docs/. Anything not listed links to GitHub. */
const wanted = new Map([
  ["docs/README.md", ""],
  ...readdirSync(join(repo, "docs"))
    .filter((f) => f.endsWith(".md") && f !== "README.md")
    .map((f) => [`docs/${f}`, f.replace(/\.md$/, "")]),
  ["CONTRIBUTING.md", "contributing"],
  ["CHANGELOG.md", "changelog"],
  ["SECURITY.md", "security-policy"],
]);
const pages = new Map();
for (const [file, slug] of wanted) {
  if (existsSync(join(repo, file))) pages.set(file, slug);
  else console.warn(`sync-docs: warning: ${file} not found, skipping /docs/${slug ? `${slug}/` : ""}`);
}

const ALERTS = {
  NOTE: "note",
  TIP: "tip",
  IMPORTANT: "note[Important]",
  WARNING: "caution",
  CAUTION: "danger",
};

function plain(md) {
  return md
    .replace(/!\[[^\]]*\]\([^)]*\)/g, "")
    .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
    .replace(/[*_`>]/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

function rewriteLink(target, from) {
  if (/^([a-z]+:|#|\/)/i.test(target)) return target;
  const [path, hash] = target.split("#");
  const resolved = normalize(join(dirname(from), path)).replace(/\\/g, "/");
  const anchor = hash ? `#${hash}` : "";
  if (pages.has(resolved)) {
    const slug = pages.get(resolved);
    return `/docs/${slug ? `${slug}/` : ""}${anchor}`;
  }
  if (resolved.startsWith("..")) throw new Error(`${from}: link escapes the repo: ${target}`);
  const kind = resolved.includes(".") ? "blob" : "tree";
  return `${GITHUB}/${kind}/main/${resolved}${anchor}`;
}

function convert(file, slug) {
  let md = readFileSync(join(repo, file), "utf8").replace(/\r\n/g, "\n");

  // The docs index opens with a centred logo block; the site has its own header.
  md = md.replace(/^<div[\s\S]*?<\/div>\n+/, "");

  // Title: the first H1, either "# Title" or inside the removed block.
  let title;
  md = md.replace(/^# (.+)\n+/m, (_, t) => {
    title = t.trim();
    return "";
  });
  if (file === "docs/README.md") title = "Documentation";
  if (!title) throw new Error(`${file}: no "# Title" heading`);

  // Hand-written tables of contents: a run of "- [Text](#anchor)" lines.
  md = md.replace(/^(?:- \[[^\]]+\]\(#[^)]+\)\n)+\n?/m, "");

  // GitHub alerts → Starlight asides.
  md = md.replace(/^> \[!(\w+)\]\n((?:>.*\n?)*)/gm, (_, kind, body) => {
    const type = ALERTS[kind.toUpperCase()] ?? "note";
    const text = body.replace(/^> ?/gm, "").trimEnd();
    return `:::${type}\n${text}\n:::\n`;
  });

  // Links and images, outside fenced code blocks.
  md = md
    .split(/(^```[\s\S]*?^```)/m)
    .map((part, i) =>
      i % 2 === 1
        ? part
        : part.replace(/(!?)\[([^\]]*)\]\(([^)\s]+)\)/g, (_, bang, text, target) => `${bang}[${text}](${rewriteLink(target, file)})`),
    )
    .join("");

  const firstParagraph = md.split("\n\n").find((p) => p.trim() && !/^[#|<:`-]/.test(p.trim()));
  let description = firstParagraph ? plain(firstParagraph) : "";
  if (description.length > 160) description = `${description.slice(0, 157).replace(/\s+\S*$/, "")}…`;

  const frontmatter = [
    "---",
    `title: ${JSON.stringify(title)}`,
    description && `description: ${JSON.stringify(description)}`,
    `editUrl: ${JSON.stringify(`${GITHUB}/edit/main/${file}`)}`,
    "---",
    `<!-- Generated from ${file} by scripts/sync-docs.mjs. Edit that file instead. -->`,
    "",
  ].filter((l) => l !== "");
  return { path: join(out, `${slug || "index"}.md`), text: `${frontmatter.join("\n")}\n\n${md.trim()}\n` };
}

rmSync(out, { recursive: true, force: true });
mkdirSync(out, { recursive: true });
for (const [file, slug] of pages) {
  const { path, text } = convert(file, slug);
  writeFileSync(path, text);
}

// Brand assets come from the repo too.
mkdirSync(join(site, "src/assets"), { recursive: true });
copyFileSync(join(repo, "docs/assets/logo.svg"), join(site, "src/assets/logo.svg"));
mkdirSync(join(site, "public"), { recursive: true });
copyFileSync(join(repo, "docs/assets/logo.svg"), join(site, "public/favicon.svg"));

console.log(`sync-docs: wrote ${pages.size} pages to ${relative(site, out)}`);
