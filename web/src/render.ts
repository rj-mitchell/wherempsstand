import DOMPurify from "dompurify";
import { marked } from "marked";
import { isNoSource, quotations, type Quotation } from "./checked";
import type { Segment } from "./types";

marked.setOptions({ gfm: true, breaks: false });

// Placeholders for the marks, set between invisible separators so that markdown passes them through untouched, then
// replaced by the family's markup once the markdown is HTML (the charity's family/BRIEFINGS.md).
const T = (body: string) => `\u2063\u27e6${body}\u27e7\u2063`;
const TOKEN = /\u2063\u27e6([^\u27e7]*)\u27e7\u2063/g;

interface Mark {
  start: number;
  end: number;
  open: string;
  close: string;
}

/** The marks inside one passage: judgemental words in the briefing's own voice, quotations not found word for word
 * in their sources (offsets from the server), and the record's own words, found word for word. Loaded words are only
 * flagged outside quotations, so the marks never nest. */
function marked_(s: Segment, i: number, quotes: Quotation[]): string {
  const marks: Mark[] = [];
  for (const q of quotes) {
    if (q.seg !== i) continue;
    const src = q.sources.join(",");
    if (q.found) marks.push({ start: q.start, end: q.end, open: T(`R:${i}:${src}`), close: T(`/R:${src}`) });
  }
  for (const f of s.misquotes ?? []) {
    const src = s.sources.join(",");
    marks.push({ start: f.start, end: f.end, open: T(`M:${i}:${src}`), close: T(`/M:${src}`) });
  }
  for (const f of s.loaded ?? []) {
    if (marks.some((m) => f.start < m.end && m.start < f.end)) continue;
    marks.push({ start: f.start, end: f.end, open: T("L"), close: T("/L") });
  }
  let text = s.text;
  for (const f of marks.sort((a, b) => b.start - a.start)) {
    text = text.slice(0, f.start) + f.open + text.slice(f.start, f.end) + f.close + text.slice(f.end);
  }
  return text;
}

/** Each line of a passage with no source wrapped in its mark, keeping a list's or a heading's marker outside it so
 * that the markdown around it survives. */
function noSource(text: string, i: number, why: string, first: { done: boolean }): string {
  let anchored = false; // the passage's first line carries its anchor, for the strip's "Show the first"
  return text
    .split("\n")
    .map((line) => {
      const m = line.match(/^(\s*(?:[-*+]\s+|\d+\.\s+|#{1,6}\s+)?)([\s\S]*?)(\s*)$/);
      if (!m || !m[2]) return line;
      const tag = first.done ? "" : "first";
      const anchor = anchored ? "" : "id";
      first.done = anchored = true;
      return `${m[1]}${T(`N:${i}:${why}:${tag}:${anchor}`)}${m[2]}${T(`/N:${why}:${tag}`)}${m[3]}`;
    })
    .join("\n");
}

/** Markdown for a verified answer, with placeholders for the chips, the quotations and the passages with no
 * source. Two runs next to each other from the same source show one chip: a quotation shown beside its citation
 * (wts-core's display rule) carries the chip, and the words after it, cited to the same source, do not repeat it. */
export function segmentsToMarkdown(segments: Segment[]): string {
  const quotes = quotations(segments);
  const first = { done: false };
  let previous: Segment | undefined;
  return segments
    .map((s, i) => {
      const text = marked_(s, i, quotes);
      const lead = text.match(/^\s*/)?.[0] ?? "";
      const trail = text.slice(lead.length).match(/\s*$/)?.[0] ?? "";
      const core = text.slice(lead.length, text.length - trail.length);
      let out = text;
      if (s.status === "cited") {
        const repeat = previous?.beside && previous.sources.join() === s.sources.join();
        out = repeat || !core ? text : `${lead}${core}${T(`C:${i}:${s.sources.join(",")}`)}${trail}`;
      } else if (isNoSource(s) && core) {
        out = noSource(text, i, s.status === "invalid" ? "removed" : "none", first);
      }
      if (s.text.trim()) previous = s;
      return out;
    })
    .join("");
}

/** The question is the page's h2, so a briefing's own headings start at h3 (keeps the outline in order). */
export function demoteHeadings(html: string): string {
  return html.replace(/<(\/?)h([1-6])\b/g, (_m, close: string, level: string) => `<${close}h${Math.min(Number(level) + 2, 6)}`);
}

/** A line that is only a bold group label ("**Ministers - supportive**") stands on its own above its text: the
 * briefing writes it with a single line break, which markdown would run into the paragraph that follows. */
export function separateLabels(md: string): string {
  return md.replace(/^([ \t]*\*\*[^*\n]+\*\*:?)[ \t]*\n(?=[ \t]*\S)/gm, "$1\n\n");
}

/** How the chips and marks are worded: each source's chip ("Emma Reynolds, Commons, 3 November 2026"), whether its
 * speaker had a registered interest that may relate (docs/DECISIONS.md, 15), and the prefix that keeps this
 * briefing's anchors apart from another's on the same page. */
export interface BriefingWords {
  chip?: (n: number) => string | null;
  interest?: (n: number) => boolean;
  id?: string;
}

const esc = (s: string) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
const inSources = (src: string) => {
  const ns = src.split(",").filter(Boolean);
  return ns.length > 1 ? `sources ${ns.slice(0, -1).join(", ")} and ${ns.at(-1)}` : `source ${ns[0] ?? ""}`;
};

/** A source chip: who said it, where and when, with the number kept inside for printing and the reference list.
 * The button is named by its visible words ("Source 3 Emma Reynolds, Commons, 3 November 2026"), so what a
 * speech-input user says is its name (WCAG 2.5.3); "Source" is there for screen readers only. */
export function chipHtml(n: number, seg: number, words: BriefingWords): string {
  const label = words.chip?.(n) ?? null;
  const interest = words.interest?.(n) ?? false;
  return (
    `<button type="button" class="wts-chip" data-source="${n}" data-seg="${seg}">` +
    `<span class="wts-chip__in"><span class="wts-chip__n"><span class="wts-vh">Source</span> ${n}</span>` +
    (label ? ` <span>${esc(label)}</span>` : "") +
    (interest ? `<span class="chip-interest">Registered interest<span class="wts-vh"> that may relate</span></span>` : "") +
    "</span></button>"
  );
}

/** Render answer markdown to sanitised HTML in the family's markup: chips, record quotations, quotations not found
 * word for word, passages with no source (the first tagged "No source"), and judgemental words. */
export function renderAnswer(md: string, words: BriefingWords = {}): string {
  const id = words.id ?? "";
  let misquote = 0;
  let html = demoteHeadings(marked.parse(separateLabels(md), { async: false }) as string);
  html = html.replace(TOKEN, (_m, body: string) => {
    const [kind, ...rest] = body.split(":");
    switch (kind) {
      case "C":
        return rest[1]
          .split(",")
          .map((n) => chipHtml(Number(n), Number(rest[0]), words))
          .join("");
      case "R":
        return `<span class="wts-quote wts-quote--checked" data-seg="${rest[0]}" data-source="${rest[1]}">`;
      case "/R":
        return (
          `<span class="wts-quote__tick" aria-hidden="true">\u2713 word for word</span>` +
          `<span class="wts-vh"> (quotation, found word for word in ${inSources(rest[0])})</span></span>`
        );
      case "M":
        return `<span class="wts-quote wts-quote--missing" id="${id}misquote-${misquote++}" data-seg="${rest[0]}">`;
      case "/M":
        return (
          `<span class="wts-quote__tick" aria-hidden="true">Not found word for word</span>` +
          `<span class="wts-vh"> (quotation not found word for word in ${inSources(rest[0])})</span></span>`
        );
      case "N":
        return `<span class="wts-nosource"${rest[3] ? ` id="${id}nosource-${rest[0]}"` : ""}>`;
      case "/N": {
        const why = rest[0] === "removed" ? "its citation did not match the source and was removed" : "no source";
        return `${rest[1] ? '<span class="wts-nosource__tag">No source</span>' : ""}<span class="wts-vh"> (${why})</span></span>`;
      }
      case "L":
        return '<span class="loaded" title="A judgemental word in the briefing\'s own voice, not a quotation">';
      case "/L":
        return "</span>";
      default:
        return "";
    }
  });
  return typographic(DOMPurify.sanitize(html, { ADD_ATTR: ["data-source", "data-seg", "aria-label", "aria-hidden"] }));
}

/** A finished briefing as HTML: its segments as the reader is shown them. */
export function renderBriefing(segments: Segment[], words: BriefingWords = {}): string {
  return renderAnswer(segmentsToMarkdown(segments), words);
}

/** Plain streaming text (no citations yet). */
export function renderPlain(md: string): string {
  return typographic(DOMPurify.sanitize(demoteHeadings(marked.parse(separateLabels(md), { async: false }) as string)));
}

/** Printers' quotation marks and apostrophes (“ ” ‘ ’) in place of typewriter ones, in the text only (never inside
 * tags). For display: the briefing's own text, its copies and the quotation checks keep the original characters. */
export function typographic(html: string): string {
  let prev = " "; // the character before the current one, carried across tags
  return html
    .split(/(<[^>]*>)/)
    .map((part) => {
      if (part.startsWith("<")) {
        // a new paragraph, item or heading starts fresh; inline tags (a citation, emphasis) carry on
        if (/^<\/?(p|li|h[1-6]|ul|ol|blockquote|br|div)\b/i.test(part)) prev = " ";
        return part;
      }
      const text = part.replace(/&quot;/g, '"').replace(/&#39;/g, "'");
      let out = "";
      for (const ch of text) {
        const opening = /[\s(\[{\u2014\u2013-]/.test(prev);
        if (ch === '"') out += opening ? "\u201c" : "\u201d";
        else if (ch === "'") out += opening ? "\u2018" : "\u2019";
        else out += ch;
        prev = ch;
      }
      return out;
    })
    .join("");
}

/** Split text into parts, marking exact quotes to highlight. */
export function highlightParts(text: string, quotes: string[]): { text: string; hit: boolean }[] {
  const norm = (s: string) => s.replace(/\s+/g, " ").trim();
  const ranges: [number, number][] = [];
  for (const q of quotes.map(norm).filter((q) => q.length > 3)) {
    const i = text.indexOf(q);
    if (i >= 0) ranges.push([i, i + q.length]);
  }
  ranges.sort((a, b) => a[0] - b[0]);
  const parts: { text: string; hit: boolean }[] = [];
  let pos = 0;
  for (const [s, e] of ranges) {
    if (s < pos) continue;
    if (s > pos) parts.push({ text: text.slice(pos, s), hit: false });
    parts.push({ text: text.slice(s, e), hit: true });
    pos = e;
  }
  if (pos < text.length) parts.push({ text: text.slice(pos), hit: false });
  return parts;
}

export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso + "T00:00:00Z");
  return d.toLocaleDateString("en-GB", { day: "numeric", month: "long", year: "numeric", timeZone: "UTC" });
}

/** Words that read wrongly split across two lines, such as a date ("4 / November 2026"), joined by no-break spaces.
 * For text set straight into the page only: questions and copied text keep ordinary spaces. */
export function keepTogether(s: string): string {
  return s.replace(/ /g, "\u00a0");
}

/** Lists on member pages are shown open; one longer than this starts folded, with its count in the heading. */
export const LONG = 25;

/** A record's heading as shown: the index joins Hansard's headings with a dash, shown here with a colon. */
export const showHeading = (h: string) => h.replace(/ — /g, ": ");

/** A field name from a register in sentence case ("Regularity Of Payment" → "Regularity of payment"), keeping
 *  capitalised abbreviations ("UK", "MP") as they are. */
export function sentenceCase(label: string): string {
  return label
    .split(" ")
    .map((w, i) => (i > 0 && /^[A-Z][a-z]/.test(w) ? w.toLowerCase() : w))
    .join(" ");
}

/** ``text`` cut at a word to at most ``limit`` characters, with an ellipsis if anything was cut (as the server cuts a
 * page's title: hansard/wording.py, shorten). */
export function shorten(text: string, limit: number): string {
  const t = text.split(/\s+/).filter(Boolean).join(" ");
  if (t.length <= limit) return t;
  let cut = t.slice(0, limit - 1);
  // the cut is inside a word: back to the word before it
  if (/[\p{L}\p{N}]/u.test(t[limit - 1]) && cut.includes(" ")) cut = cut.slice(0, cut.lastIndexOf(" "));
  return `${cut.replace(/[\s,;:—–-]+$/, "")}…`;
}
