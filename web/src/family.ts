/**
 * The Where They Stand family kit, for the parts React draws. The kit's files are in public/family/, copied in by the
 * kit's sync.mjs and never edited here; the static pages include the same partials at build time (vite.config.ts),
 * so the home page and the static pages show exactly the same endorsement and footer shelf. See docs/DESIGN.md,
 * "The family binding".
 */
import endorsement from "../public/family/endorsement.html?raw";
import shelf from "../public/family/shelf.html?raw";

export const FAMILY = { endorsement, shelf };

/** A sister site as the footer shelf shows it: its name, address and spine (an SVG in its cloth). */
export interface Sister {
  id: string;
  name: string;
  url: string;
  spine: string;
}

const BOOK =
  /<a class="wts-book" href="([^"]+)"[^>]*>(<svg class="wts-spine wts-cloth--([\w-]+)"[\s\S]*?<\/svg>)<span class="wts-book__name">([^<]+)<\/span>/g;

/** Every project on the shelf, in the shelf's order (this site included). */
export const PROJECTS: Sister[] = [...shelf.matchAll(BOOK)].map((m) => ({
  url: m[1],
  spine: m[2],
  id: m[3],
  name: m[4].replace(/&amp;/g, "&"),
}));

export function sister(id: string): Sister | undefined {
  return PROJECTS.find((p) => p.id === id);
}
