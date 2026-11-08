import type { APIRoute } from "astro";
import { docs, markdown, NAME } from "../lib/llms";

/** /llms-full.txt: every docs page in one Markdown file, for assistants that want it all at once. */
export const GET: APIRoute = async ({ site }) => {
  const pages = (await docs()).map(
    (entry) => `# ${entry.data.title}\n\nSource: ${new URL(entry.id + "/", site)}\n\n${markdown(entry)}`,
  );
  return new Response([`# ${NAME} documentation`, ...pages].join("\n\n---\n\n") + "\n", {
    headers: { "Content-Type": "text/plain; charset=utf-8" },
  });
};
