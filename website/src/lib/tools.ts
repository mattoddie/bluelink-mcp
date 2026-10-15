import { readFileSync } from "node:fs";
import { join } from "node:path";

/**
 * Facts about the tools, read from the generated docs/tools.md at build time,
 * so the landing page's numbers can't drift from the code.
 */
export type Level = "read" | "write" | "unlock";

export interface Group {
  name: string;
  summary: string;
  read: number;
  write: number;
  unlock: number;
  total: number;
}

const LABELS: Record<Level, string> = { read: "🟢 read", write: "🟠 write", unlock: "🔴 unlock" };

// Builds run from website/, and the repo root is its parent.
const md = readFileSync(join(process.cwd(), "../docs/tools.md"), "utf8");

export const groups: Group[] = [];
for (const section of md.split(/^## /m).slice(1)) {
  const [heading, ...rest] = section.split("\n");
  const name = heading.trim();
  if (!/^[a-z]+$/.test(name)) continue;
  const body = rest.join("\n");
  const summary = body.trim().split("\n")[0].trim();
  const rows = body.match(/^\| \[`[a-z0-9_]+`\].*$/gm) ?? [];
  const count = (level: Level) => rows.filter((r) => r.includes(LABELS[level])).length;
  groups.push({ name, summary, read: count("read"), write: count("write"), unlock: count("unlock"), total: rows.length });
}

const sum = (k: Level | "total") => groups.reduce((n, g) => n + g[k], 0);

export const totals = {
  tools: sum("total"),
  read: sum("read"),
  write: sum("write"),
  unlock: sum("unlock"),
  groups: groups.length,
};
