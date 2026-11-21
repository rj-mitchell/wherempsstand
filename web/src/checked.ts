/**
 * A checked briefing as the Where They Stand family shows it (the charity's family/BRIEFINGS.md; the kit's
 * public/family/briefing.css): the check strip's counts, the record's own words in a briefing, the chips that name
 * each source, the record beside the briefing, and the cards of the records found while a briefing is prepared.
 * Everything here is read from the answer and the sources as the server sent them: nothing is invented, and the
 * rules are the server's (wts-core's ``check_counts`` and the verifier's reading of a quotation).
 */
import { chipsFor } from "./components/PlanChips";
import { fmtDate, showHeading } from "./render";
import type { Answer, Meta, Plan, Segment, SourceCard } from "./types";

/** The check strip's three counts, as wts-core's ``check_counts`` gives them for the answer as shown (after the
 * display rule, ``quotes_beside``): citations matched of those made, quotations found word for word of those
 * checked, and the passages shown as having no source. tests/test_checked.py holds the two to the same answers. */
export interface CheckCounts {
  citations_matched: number;
  citations_total: number;
  quotes_found: number;
  quotes_total: number;
  no_source: number;
}

export function checkCounts(shown: Answer): CheckCounts {
  const total = shown.citations_total ?? 0;
  const quotes = shown.quotes_total ?? 0;
  return {
    citations_matched: total - (shown.citations_invalid ?? 0),
    citations_total: total,
    quotes_found: quotes - (shown.quotes_unverified ?? 0),
    quotes_total: quotes,
    no_source: (shown.segments ?? []).filter((s) => isNoSource(s)).length,
  };
}

export const isNoSource = (s: Segment) => s.status === "unsupported" || s.status === "invalid";

/** The counts in words, as the strip and the share card say them. */
export function countsInWords(c: CheckCounts): string {
  return (
    `${c.citations_matched} of ${c.citations_total} citations matched to the record, ` +
    `${c.quotes_found} of ${c.quotes_total} quotations found word for word, ` +
    `${c.no_source} ${c.no_source === 1 ? "sentence" : "sentences"} without a source`
  );
}

// A quotation, as the verifier reads one (wts-core checks/verify.py, _QUOTE): straight or curly double quotes, or
// curly single ones, around six characters or more on one line.
const QUOTE = /["“]([^"“”\n]{6,}?)["”]|‘([^‘’\n]{6,}?)’(?![a-z])/g;
const WORDY = /[\p{L}\p{N}]/u;

/** A quotation in a cited passage: where it is in its segment, its words, and the sources cited for it. */
export interface Quotation {
  seg: number;
  start: number;
  end: number;
  words: string;
  sources: number[];
  found: boolean;
}

/**
 * The quotations of the cited passages, in order. As the verifier reads them, the text of the whole answer is read
 * at once and a word or two in quotes is a term, not a quotation. A quotation counts as the record's own words when
 * it lies in a cited passage and the verifier recorded no misquote over it; one with a misquote is "not found word
 * for word". Quotations in text with no source are left to that text's mark.
 */
export function quotations(segments: Segment[]): Quotation[] {
  const starts: number[] = [];
  let pos = 0;
  for (const s of segments) {
    starts.push(pos);
    pos += s.text.length;
  }
  const full = segments.map((s) => s.text).join("");
  const out: Quotation[] = [];
  for (const m of full.matchAll(QUOTE)) {
    const words = m[1] ?? m[2] ?? "";
    if (words.split(/\s+/).filter((w) => WORDY.test(w)).length < 3) continue;
    const a = m.index ?? 0;
    const b = a + m[0].length;
    let i = starts.length - 1;
    while (i > 0 && (starts[i] > a || !segments[i].text)) i--;
    const seg = segments[i];
    if (!seg || seg.status !== "cited" || b > starts[i] + seg.text.length) continue; // only whole, in a cited passage
    const start = a - starts[i];
    const end = b - starts[i];
    const missed = (seg.misquotes ?? []).some((q) => q.start < end && start < q.end);
    out.push({ seg: i, start, end, words, sources: seg.sources, found: !missed });
  }
  return out;
}

const ISO_DAY = /^\d{4}-\d{2}-\d{2}/;
const fullDate = (iso: string | null | undefined) => (iso && ISO_DAY.test(iso) ? fmtDate(iso.slice(0, 10)) : "");

/** The devolved parliaments, as a chip names them. */
function parliament(s: SourceCard): string {
  if (s.legislature === "senedd" || /senedd/i.test(s.chamber)) return "Senedd";
  if (s.legislature === "scottish-parliament" || /scottish/i.test(s.chamber)) return "Scottish Parliament";
  if (s.legislature === "ni-assembly" || /northern ireland/i.test(s.chamber)) return "Northern Ireland Assembly";
  return s.chamber;
}

/** Who a source is from, as its chip and card name them. */
export function sourceName(s: SourceCard): string {
  if (s.kind === "division") return `Division ${s.number ?? ""}`.trim();
  if (s.kind === "edm") return s.uin ? `Early day motion ${s.uin}` : "Early day motion";
  if (s.kind === "devolved") return s.member?.name ?? "A member";
  if (s.kind === "evidence") return s.witness?.name ?? "A witness";
  return s.speaker?.name ?? s.speaker_name_raw ?? "Unattributed";
}

/** Where a source was said, as its chip names it: the House, the devolved parliament, or committee evidence. */
export function sourceHouse(s: SourceCard): string {
  if (s.kind === "devolved") return parliament(s);
  if (s.kind === "evidence") return "Committee evidence";
  if (/lords/i.test(s.chamber)) return "Lords";
  if (s.speaker?.house === "lords" && !/commons|westminster hall/i.test(s.chamber)) return "Lords";
  return "Commons";
}

/** A source chip's words: who, where and when, separated by commas, the date in full ("Emma Reynolds, Commons,
 * 3 November 2026"). */
export function chipLabel(s: SourceCard): string {
  return [sourceName(s), sourceHouse(s), fullDate(s.date)].filter(Boolean).join(", ");
}

/** A record found, as a card in the pile while a briefing is prepared: a Hansard column (the house and the date,
 * the column, the speaker in bold), with its real first words. */
export function cardOf(s: SourceCard): { top: string; ref: string; who: string; line: string } {
  const ref = s.kind === "division" ? `Division ${s.number ?? ""}` : s.kind === "edm" ? (s.uin ? `EDM ${s.uin}` : "")
    : s.colnum ? `col. ${s.colnum}` : s.uin ? `question ${s.uin}` : "";
  const house = s.kind === "evidence" ? "Committee evidence" : s.kind === "edm" ? "Early day motions"
    : /westminster hall|public bill committee/i.test(s.chamber) ? s.chamber : sourceHouse(s);
  return { top: [house, fullDate(s.date)].filter(Boolean).join(", "), ref, who: sourceName(s), line: firstWords(s) };
}

/** The words of a source that can be quoted: a speech, a member's contribution, a witness's answer, a motion. */
export function passageText(s: SourceCard): string {
  if (s.kind === "edm") return s.motion_text ?? "";
  if (s.kind === "devolved" || s.kind === "evidence") return s.text.split("\n\n").slice(1).join("\n\n") || s.text;
  return s.text ?? "";
}

function firstWords(s: SourceCard, limit = 150): string {
  const t = passageText(s).replace(/\s+/g, " ").trim();
  if (t.length <= limit) return t;
  const cut = t.slice(0, limit);
  return `${cut.slice(0, cut.lastIndexOf(" ") > 0 ? cut.lastIndexOf(" ") : limit)}…`;
}

/** The slip that shows a source beside the briefing: the record's own form (a Hansard column: the House, the date
 * and the column; the speaker in bold with their party and seat; the debate's heading), and where to read it. */
export interface Slip {
  house: string;
  when: string;
  who: string | null;
  tail: string;
  heading: string | null;
  href: string | null;
  label: string;
}

export function slipOf(s: SourceCard): Slip {
  const date = fullDate(s.date);
  const official = s.official_url ?? null;
  if (s.kind === "evidence") {
    const w = s.witness;
    return {
      house: "Oral evidence", when: date, who: w?.name ?? null,
      tail: [w?.role, w?.organisation].filter(Boolean).join(", "),
      heading: [s.committee, s.inquiry].filter(Boolean).join(": ") || null,
      href: official ?? s.url, label: official ? "Read the session on Parliament's website" : "Read the transcript",
    };
  }
  if (s.kind === "devolved") {
    const m = s.member;
    return {
      house: s.chamber, when: date, who: m?.name ?? null, tail: [m?.party, m?.seat].filter(Boolean).join(", "),
      heading: s.heading ? showHeading(s.heading) : null, href: official ?? s.url,
      label: official ? "Read the official report" : "Read the sitting",
    };
  }
  if (s.kind === "edm") {
    return {
      house: "Early day motions", when: [date, s.uin ? `EDM ${s.uin}` : ""].filter(Boolean).join(", "),
      who: s.sponsor?.name ?? null, tail: s.sponsor?.party ?? "", heading: s.edm_title ?? null,
      href: official ?? s.url, label: official ? "Read it on Parliament's early day motions site" : "Read the motion",
    };
  }
  if (s.kind === "division") {
    return {
      house: s.chamber, when: [date, s.number ? `Division ${s.number}` : ""].filter(Boolean).join(", "), who: null,
      tail: "", heading: [s.major_heading, s.minor_heading].filter(Boolean).join(": ") || null,
      href: official ?? s.url, label: "Read the division in Hansard",
    };
  }
  const sp = s.speaker;
  const where = s.colnum ? `column ${s.colnum}` : s.uin ? `question ${s.uin}` : "";
  const seat = sp?.house === "lords" ? "" : (sp?.constituency ?? "");
  return {
    house: s.chamber, when: [date, where].filter(Boolean).join(", "), who: sp?.name ?? s.speaker_name_raw ?? null,
    tail: [sp?.party, seat].filter(Boolean).join(", "), heading: s.heading ? showHeading(s.heading) : null,
    href: official ?? s.twfy_url ?? s.url,
    label: official ? (s.colnum ? "Read it in Hansard" : "Read it on Parliament's website") : "Read it on TheyWorkForYou",
  };
}

/** A passage with the record's own words in it marked: each quoted run found in it, the ones the reader chose
 * ``now``. Matching ignores case, the kind of quotation marks and dashes, and spacing, as the verifier does. */
export function passageParts(text: string, runs: string[], now: string[] = []): { text: string; mark: boolean; now: boolean }[] {
  const keep: number[] = []; // index in text of each character of the folded text
  let folded = "";
  let space = true;
  for (let i = 0; i < text.length; i++) {
    let ch = text[i].toLowerCase();
    if (/\s/.test(ch)) {
      if (space) continue;
      ch = " ";
      space = true;
    } else space = false;
    ch = ch.replace(/[‘’]/, "'").replace(/[“”]/, '"').replace(/[–—]/, "-");
    folded += ch;
    keep.push(i);
  }
  const fold = (q: string) =>
    q.toLowerCase().replace(/[‘’]/g, "'").replace(/[“”]/g, '"').replace(/[–—]/g, "-")
      .replace(/\s+/g, " ").trim();
  const nowFolded = new Set(now.flatMap(pieces).map(fold));
  const spans: [number, number, boolean][] = [];
  for (const run of runs) {
    for (const piece of pieces(run)) {
      const f = fold(piece);
      if (f.length < 4) continue;
      const at = folded.indexOf(f);
      if (at < 0) continue;
      const a = keep[at];
      const b = keep[at + f.length - 1] + 1;
      if (spans.some(([x, y]) => a < y && x < b)) continue;
      spans.push([a, b, nowFolded.has(f)]);
    }
  }
  spans.sort((x, y) => x[0] - y[0]);
  const out: { text: string; mark: boolean; now: boolean }[] = [];
  let at = 0;
  for (const [a, b, isNow] of spans) {
    if (a > at) out.push({ text: text.slice(at, a), mark: false, now: false });
    out.push({ text: text.slice(a, b), mark: true, now: isNow });
    at = b;
  }
  if (at < text.length) out.push({ text: text.slice(at), mark: false, now: false });
  return out;
}

/** A quotation's words without the ellipses that join its parts, each part trimmed of end punctuation. */
function pieces(q: string): string[] {
  return q
    .split(/\.\.\.|…|\[…\]/)
    .map((p) => p.trim().replace(/^[\s,.;:]+|[\s,.;:!?]+$/g, ""))
    .filter(Boolean);
}

/** The sources a briefing cites, in their own order: the ones the record beside the briefing steps through. */
export function citedSources(segments: Segment[], sources: SourceCard[]): SourceCard[] {
  const used = new Set(segments.filter((s) => s.status === "cited").flatMap((s) => s.sources));
  return sources.filter((s) => used.has(s.n));
}

/** The records found, in the words of the wait's summary: "34 records from 31 speakers: 21 from Hansard, 7 from
 * the devolved parliaments and 6 from committee evidence". */
export function foundSummary(sources: SourceCard[]): string {
  const n = sources.length;
  const who = new Set(
    sources.filter((s) => s.kind !== "division").map((s) => s.speaker?.person_id ?? `${sourceName(s)}|${s.kind}`),
  ).size;
  const hansard = sources.filter((s) => s.kind === "record" || s.kind === "division").length;
  const parts = [
    [hansard, "from Hansard"],
    [sources.filter((s) => s.kind === "edm").length, "from early day motions"],
    [sources.filter((s) => s.kind === "devolved").length, "from the devolved parliaments"],
    [sources.filter((s) => s.kind === "evidence").length, "from committee evidence"],
  ].filter(([k]) => (k as number) > 0) as [number, string][];
  const head = `${n} ${n === 1 ? "record" : "records"}${who ? ` from ${who} ${who === 1 ? "speaker" : "speakers"}` : ""}`;
  if (parts.length === 1) return `${head}, ${n === 1 ? "" : "all "}${parts[0][1]}`;
  const list = parts.map(([k, w]) => `${k} ${w}`);
  return `${head}: ${list.slice(0, -1).join(", ")} and ${list.at(-1)}`;
}

/** The question as read, as the wait's large chips say it: the Houses, the dates, the parties, then any other
 * filter the plan holds ("Commons and Lords", "since 17 November 2025", "all parties"). */
export function planWords(plan: Plan, meta: Meta | null): string[] {
  const f = plan.filters;
  const houses = f.houses.length === 1 ? (f.houses[0] === "lords" ? "Lords" : "Commons") : "Commons and Lords";
  const dates = f.date_from && f.date_to ? `${fullDate(f.date_from)} to ${fullDate(f.date_to)}`
    : f.date_from ? `since ${fullDate(f.date_from)}` : f.date_to ? `up to ${fullDate(f.date_to)}` : "all dates";
  const rest = chipsFor(plan, meta).filter((c) => !/^(from|to|h-|b-)/.test(c.key)).map((c) => c.label);
  const parties = f.parties.length ? [] : ["all parties"];
  return [houses, dates, ...parties, ...rest].slice(0, 6);
}
