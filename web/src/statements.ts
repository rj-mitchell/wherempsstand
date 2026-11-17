/** Statements beside items: the right of reply (the charity's impartiality policy, section 7.2). A member, a peer,
 * an organisation or someone acting for them can add a short statement in their own words beside what the site
 * says about them. It is shown beside the item and on the member's page, headed "Statement from [name], [date]",
 * followed by "In their own words. Not checked or endorsed by Where MPs Stand.", and styled apart from the site's own
 * words. Statements are fetched on their own from /api/statements: they are never part of a briefing, the record or
 * the search, and never reach the AI. The React pages use components/Statements.tsx, which draws the same markup. */
import { fmtDate } from "./render";

export interface Statement {
  id: number;
  name: string;
  role: string;
  item: string; // the path on this site it sits beside, e.g. /people/10669 or /b/abcdefghjk
  person: number | null; // the member it is about
  text: string;
  link: string | null;
  date: string; // YYYY-MM-DD: published, or last updated
}

export const MAX_WORDS = 150;
export const DISCLAIMER = "In their own words. Not checked or endorsed by Where MPs Stand.";
export const NAMED_PAGE = "/named";

/** ?kind= chooses the report form's option, so a link can start the right route: the values the server keeps
 * (correction, complaint, takedown, general), and the routes' short names (wrong, unfair). A statement has its own
 * form, on If you're named. ?page= fills in the address of the page the report is about. */
export const KIND_ALIASES: Record<string, string> = {
  correction: "correction",
  wrong: "correction",
  complaint: "complaint",
  unfair: "complaint",
  takedown: "takedown",
  privacy: "takedown",
  general: "general",
};

/** What is wrong with a statement before it is sent, in words for the person sending it (the server checks again). */
export function statementProblem(f: { name: string; role: string; item: string; text: string; email: string; link: string }) {
  if (f.name.trim().length < 2) return "Please give the name the statement is from.";
  if (f.role.trim().length < 2) return "Please give your role, for example \"MP for Leeds Central\".";
  if (!f.item.trim()) return "Please give the address of the page the statement is about.";
  const words = wordCount(f.text);
  if (words < 5) return "Please write the statement: a sentence or more.";
  if (words > MAX_WORDS) return `A statement can be up to ${MAX_WORDS} words. This one is ${words}.`;
  if (f.link.trim() && !/^https?:\/\/\S+$/i.test(f.link.trim())) return "The link to the record should be a full web address, starting https://.";
  if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(f.email.trim())) return "Please give an email address, so we can confirm it's you.";
  return null;
}

export const heading = (s: Pick<Statement, "name" | "date">) => `Statement from ${s.name}, ${fmtDate(s.date)}`;

export const wordCount = (text: string) => (text.trim() ? text.trim().split(/\s+/).length : 0);

/** The statement's words as paragraphs (blank lines between them), shown as plain text, never as HTML. */
export const paragraphs = (text: string) =>
  text
    .split(/\n\s*\n/)
    .map((p) => p.trim())
    .filter(Boolean);

/** Only an ordinary web address is ever made a link. */
export const safeLink = (link: string | null) => (link && /^https?:\/\/[^\s"'<>]+$/i.test(link) ? link : null);

/** What the statement sits beside, in words, for when it is shown away from it (on a member's page). */
export function itemLabel(path: string): string {
  const kinds: [RegExp, string][] = [
    [/^\/people\//, "their page on this site"],
    [/^\/b\//, "a briefing"],
    [/^\/topics\//, "a topic briefing"],
    [/^\/debates\//, "a debate"],
    [/^\/bills\//, "a bill"],
    [/^\/groups\//, "a group's page"],
    [/^\/edms\//, "an early day motion"],
    [/^\/committees\//, "a committee"],
    [/^\/inquiries\//, "a committee inquiry"],
    [/^\/evidence\//, "evidence to a committee"],
    [/^\/devolved\//, "a day in a devolved parliament"],
  ];
  return kinds.find(([re]) => re.test(path))?.[1] ?? "a page on this site";
}

export interface StatementQuery {
  paths?: string[]; // items, e.g. a briefing's /b/<id> and the topic address it is shown at
  person?: number | string; // a member
}

const asked = new Map<string, Promise<Statement[]>>();

/** Published statements for some items or a member. Asked once per page for each query (the briefing's block and
 * its source list share one answer); an error shows nothing, and the page works without them. */
export function fetchStatements(query: StatementQuery): Promise<Statement[]> {
  const params = new URLSearchParams();
  for (const p of query.paths ?? []) params.append("path", p);
  if (query.person != null) params.set("person", String(query.person));
  const key = params.toString();
  if (!key) return Promise.resolve([]);
  if (!asked.has(key))
    asked.set(
      key,
      fetch(`/api/statements?${key}`)
        .then(async (resp) => (resp.ok ? (((await resp.json()) as { statements: Statement[] }).statements ?? []) : []))
        .catch(() => []),
    );
  return asked.get(key)!;
}

let uid = 0;

/** One statement, as an element (for the pages not drawn with React). */
export function statementElement(s: Statement, level: 2 | 3 | 4 = 3, about = false): HTMLElement {
  const box = document.createElement("aside");
  box.className = "statement";
  const id = `statement-${s.id}-${++uid}`;
  box.setAttribute("aria-labelledby", id);
  const h = document.createElement(`h${level}`);
  h.id = id;
  h.className = "statement-head";
  h.textContent = heading(s);
  const role = document.createElement("p");
  role.className = "statement-role";
  role.textContent = s.role;
  const quote = document.createElement("blockquote");
  quote.className = "statement-text";
  for (const para of paragraphs(s.text)) {
    const p = document.createElement("p");
    p.textContent = para;
    quote.append(p);
  }
  box.append(h, role, quote);
  const link = safeLink(s.link);
  if (link) {
    const p = document.createElement("p");
    p.className = "statement-link";
    const a = document.createElement("a");
    a.href = link;
    a.rel = "nofollow ugc noopener noreferrer";
    a.textContent = "The record they point to";
    p.append(a);
    box.append(p);
  }
  if (about) {
    const p = document.createElement("p");
    p.className = "statement-about";
    const a = document.createElement("a");
    a.href = s.item;
    a.textContent = itemLabel(s.item);
    p.append("About ", a);
    box.append(p);
  }
  const note = document.createElement("p");
  note.className = "statement-note";
  note.textContent = DISCLAIMER;
  box.append(note);
  return box;
}

/** The address of the item a page shows, as statements are filed: a member's page without the name after it. */
export function pagePath(pathname: string): string {
  const path = pathname.replace(/\/+$/, "").replace(/\.html$/, "") || "/";
  return path.match(/^\/people\/\d+/)?.[0] ?? path;
}

/** Item pages drawn without React's help for statements (debates, bills, groups and the like): any published
 * statements about the page are shown at the end of its main content. The home page (briefings) and members' pages
 * place theirs themselves, beside the sources and in their own section. */
export async function showStatementsForPage(main: HTMLElement | null, pathname = window.location.pathname) {
  const path = pagePath(pathname);
  // only addresses with an item in them (/debates/…, /groups/…/…); not /, /people/<n> or /b/<id>
  if (!main || path.split("/").length < 3 || /^\/(people|b|topics)\//.test(path)) return;
  const found = await fetchStatements({ paths: [path] });
  if (!found.length) return;
  const section = document.createElement("section");
  section.className = "statements";
  section.setAttribute("aria-labelledby", "h-statements");
  const h = document.createElement("h2");
  h.id = "h-statements";
  h.textContent = found.length === 1 ? "A statement about this page" : "Statements about this page";
  const intro = document.createElement("p");
  intro.className = "muted small";
  intro.append("Added by the people or organisations named here, in their own words. ");
  const a = document.createElement("a");
  a.href = NAMED_PAGE;
  a.textContent = "If you're named on this site";
  intro.append(a, ".");
  section.append(h, intro, ...found.map((s) => statementElement(s, 3)));
  main.append(section);
}
