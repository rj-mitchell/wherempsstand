import { useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import { checkCounts, chipLabel, citedSources, isNoSource, quotations } from "../checked";
import { briefingDocx, downloadName } from "../docx";
import { briefingWithSources, referencesOnly } from "../export";
import { fmtDate, renderBriefing, renderPlain } from "../render";
import { stancesFrom, type Stance } from "../stances";
import type { Answer, Highlight, Meta, Segment, SourceCard, Turn } from "../types";
import { CheckStrip, type RunningUp } from "./CheckStrip";
import { RecordPanel, SourceSheet } from "./Record";
import { ReportProblem } from "./ReportProblem";
import { Checking, Found, Pile, QuestionAsRead, Spines, Stages } from "./Wait";

interface Props {
  turn: Turn;
  meta?: Meta | null;
  /** opens the source panel with every detail of a source (its registered interests, the speaker's role, links) */
  onCite: (h: Highlight) => void;
}

const WIDE = "(min-width: 64rem)";
const still = () => typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

function useWide(): boolean {
  const [wide, setWide] = useState(() => typeof window.matchMedia === "function" && window.matchMedia(WIDE).matches);
  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const mq = window.matchMedia(WIDE);
    const on = () => setWide(mq.matches);
    mq.addEventListener?.("change", on);
    return () => mq.removeEventListener?.("change", on);
  }, []);
  return wide;
}

/** The quoted runs of each source (what its passage highlights), from the cited passages' quotations, and the
 * words each segment's citation points at, for a source nothing is quoted from. */
function quotedRuns(segments: Segment[]) {
  const runs = new Map<number, string[]>();
  const bySeg = new Map<number, string[]>();
  for (const q of quotations(segments)) {
    if (!q.found) continue;
    for (const n of q.sources) runs.set(n, [...(runs.get(n) ?? []), q.words]);
    bySeg.set(q.seg, [...(bySeg.get(q.seg) ?? []), q.words]);
  }
  return { runs, bySeg };
}

type Chosen = { n: number; seg: number | null };

/**
 * A briefing, from the moment the question is sent: the wait's stages and the records found while it is prepared,
 * then the check strip, the text with its record quotations, chips and marks for text with no source, and the
 * record beside it (a panel on wide screens, a bottom sheet on narrower ones). A briefing written while the reader
 * waited has its checks' real results run up when it ends, and the pile of records settles into the record panel.
 */
export function AnswerView({ turn, meta = null, onCite }: Props) {
  const { question, answer, streamText, sources, briefingKey, briefingId, preparedAt, recordsUpTo } = turn;
  const active = !["done", "error"].includes(turn.status);
  const link = briefingId ? `${window.location.origin}/b/${briefingId}` : undefined;
  const uid = useId().replace(/:/g, "");
  const prefix = `b${uid}-`;
  const checkId = `${prefix}check`;
  const wide = useWide();
  const written = !!answer && !answer.no_records;

  // the checks' results running up, for a briefing that has just been written: shown at once, never waited for
  const [phase, setPhase] = useState<"live" | "checking" | "settled">(() =>
    written && !turn.live ? "settled" : "live",
  );
  const [running, setRunning] = useState<RunningUp | null>(null);
  const [settling, setSettling] = useState(false);

  // the records dealt into the pile as they arrive (all at once with reduced motion, or once text is coming)
  const [dealt, setDealt] = useState(0);
  useEffect(() => {
    if (!sources.length || phase === "settled") return;
    if (still() || streamText || answer || turn.status === "done") {
      setDealt(sources.length);
      return;
    }
    const each = Math.max(1, Math.ceil(sources.length / 30));
    const timer = window.setInterval(() => {
      setDealt((d) => {
        const next = Math.min(sources.length, d + each);
        if (next >= sources.length) window.clearInterval(timer);
        return next;
      });
    }, 35);
    return () => window.clearInterval(timer);
  }, [sources.length, streamText, answer, turn.status, phase]);

  const cited = useMemo(() => new Set(turn.cited ?? []), [turn.cited]);
  const pile = useRef<HTMLDivElement>(null);
  // a newly cited record is brought into view in the pile
  const lastCited = turn.cited?.at(-1);
  useEffect(() => {
    if (lastCited === undefined || !pile.current) return;
    const card = pile.current.querySelector<HTMLElement>(`.wts-card[data-n="${lastCited}"]`);
    if (!card) return;
    pile.current.scrollTo?.({
      top: card.offsetTop - 40,
      left: card.offsetLeft - 40,
      behavior: still() ? "auto" : "smooth",
    });
  }, [lastCited]);

  const flagged = useMemo(() => new Set(sources.filter((s) => s.interest).map((s) => s.n)), [sources]);
  const bySource = useMemo(() => new Map(sources.map((s) => [s.n, s])), [sources]);
  // While it is written, the paragraphs already finished are drawn once and kept, and only the one being written
  // is drawn again as each few words arrive, so a phone keeps up with a long briefing.
  const head = streamText.slice(0, Math.max(0, streamText.lastIndexOf("\n\n")));
  const headHtml = useMemo(() => (head ? renderPlain(head) : ""), [head]);
  const html = useMemo(() => {
    if (answer) {
      return renderBriefing(answer.segments, {
        chip: (n) => (bySource.has(n) ? chipLabel(bySource.get(n)!) : null),
        interest: (n) => flagged.has(n),
        id: prefix,
      });
    }
    // the cursor goes once the text is written: the checking line under it says what happens next
    const tail = renderPlain(streamText.slice(head.length));
    return headHtml + (turn.status === "verifying" ? tail : withCursor(tail));
  }, [answer, streamText, bySource, flagged, prefix, head, headHtml, turn.status]);

  const counts = useMemo(() => (answer ? checkCounts(answer) : null), [answer]);
  const { runs, bySeg } = useMemo(() => quotedRuns(answer?.segments ?? []), [answer]);
  const record = useMemo(() => (answer ? citedSources(answer.segments, sources) : []), [answer, sources]);
  const firstNoSource = answer?.segments.findIndex(isNoSource) ?? -1;
  const firstRemoved = answer?.segments.findIndex((s) => s.status === "invalid") ?? -1;
  const firstMissing = answer?.segments.some((s) => s.misquotes?.length) ? `${prefix}misquote-0` : null;

  const body = useRef<HTMLDivElement>(null);
  const grid = useRef<HTMLDivElement>(null);
  const line = useRef<SVGSVGElement>(null);
  const stances = useMemo(() => stancesFrom(answer), [answer]);

  // A briefing just written: its checks' results run up through the real results, citation by citation, each
  // quotation taking its highlight in turn; then the pile settles into the record beside the briefing.
  const began = useRef(false);
  const stopRunUp = useRef<() => void>(() => {});
  useEffect(() => {
    if (!written || phase !== "live" || began.current) return;
    began.current = true;
    const steps = answer!.segments.map((s, i) => [s, i] as const).filter(([s]) => s.status === "cited");
    if (still() || !steps.length) {
      setPhase("settled");
      return;
    }
    setPhase("checking");
    const final = checkCounts(answer!);
    const found = quotations(answer!.segments).filter((q) => q.found);
    const ms = Math.min(110, Math.max(40, 1400 / steps.length));
    let k = 0;
    let cit = 0;
    let qt = 0;
    setRunning({ citations: 0, quotes: 0 });
    const timer = window.setInterval(() => {
      if (k >= steps.length) {
        window.clearInterval(timer);
        setRunning(null);
        setPhase("settled");
        setSettling(true);
        return;
      }
      const [s, i] = steps[k++];
      cit = Math.min(final.citations_matched, cit + s.sources.length);
      qt = Math.min(final.quotes_found, qt + found.filter((q) => q.seg === i).length);
      body.current?.querySelectorAll(`.wts-quote--checked[data-seg="${i}"]`).forEach((q) => q.classList.add("is-checked"));
      for (const n of s.sources) pile.current?.querySelector(`.wts-card[data-n="${n}"]`)?.classList.add("is-checked");
      setRunning({ citations: cit, quotes: qt });
    }, ms);
    stopRunUp.current = () => window.clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- once, when the briefing's answer arrives
  }, [written, answer]);
  useEffect(() => () => stopRunUp.current(), []);
  useEffect(() => {
    if (!settling) return;
    const t = window.setTimeout(() => setSettling(false), 600);
    return () => window.clearTimeout(t);
  }, [settling]);
  // the strip comes into view when the checks begin, unless the reader has scrolled on into the text
  const strip = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (phase !== "checking" || !strip.current) return;
    const r = strip.current.getBoundingClientRect();
    if (r.top < 0 || r.bottom > window.innerHeight)
      strip.current.scrollIntoView?.({ block: "nearest", behavior: still() ? "auto" : "smooth" });
  }, [phase]);

  // The record beside the briefing: open from the start on the first source, following the reader.
  const [chosen, setChosen] = useState<Chosen | null>(null);
  const [sheet, setSheet] = useState<Chosen | null>(null);
  const pinned = useRef(0);
  const settled = phase === "settled" && written;
  // open from the start on the first source the text cites, at its first chip
  const opening = useMemo((): Chosen | null => {
    const i = answer?.segments.findIndex((s) => s.status === "cited" && s.sources.length) ?? -1;
    return i >= 0 ? { n: answer!.segments[i].sources[0], seg: i } : null;
  }, [answer]);
  const current: Chosen | null = settled ? (chosen ?? opening) : null;
  const choose = useCallback((c: Chosen, hold = 4000) => {
    pinned.current = Date.now() + hold;
    setChosen(c);
  }, []);

  const chipFor = useCallback(
    (c: Chosen | null) => {
      if (!c || !body.current) return null;
      const sel = c.seg !== null ? `.wts-chip[data-source="${c.n}"][data-seg="${c.seg}"]` : `.wts-chip[data-source="${c.n}"]`;
      return body.current.querySelector<HTMLElement>(sel);
    },
    [],
  );

  // the chosen chip is marked, for the eye and for screen readers
  useEffect(() => {
    const chip = chipFor(current);
    body.current?.querySelectorAll<HTMLElement>(".wts-chip").forEach((c) => {
      if (c === chip) c.setAttribute("aria-current", "true");
      else c.removeAttribute("aria-current");
    });
  }, [current, chipFor, html]);

  const draw = useCallback(() => {
    const svg = line.current;
    if (!svg) return;
    const chip = wide && settled ? chipFor(current) : null;
    if (!chip) {
      if (svg.firstChild) svg.replaceChildren();
      return; // nothing to join, and no layout to read
    }
    const box = grid.current?.getBoundingClientRect();
    const panel = grid.current?.querySelector<HTMLElement>(".wts-record .wts-passage");
    if (!chip || !box || !panel || !body.current) {
      svg.replaceChildren();
      return;
    }
    const a = chip.getBoundingClientRect();
    if (a.bottom < 0 || a.top > window.innerHeight) {
      svg.replaceChildren();
      return;
    }
    const p = panel.getBoundingClientRect();
    const m = panel.querySelector("mark.wts-now") ?? panel.querySelector("mark");
    const b = m ? m.getBoundingClientRect() : p;
    const x1 = body.current.getBoundingClientRect().right + 10 - box.left;
    const y1 = a.top + a.height / 2 - box.top;
    const x2 = p.left - 6 - box.left;
    const y2 = Math.min(Math.max(b.top + 10, p.top + 8), p.bottom - 8) - box.top;
    const mid = (x1 + x2) / 2;
    svg.setAttribute("viewBox", `0 0 ${box.width} ${box.height}`);
    svg.setAttribute("width", String(box.width));
    svg.setAttribute("height", String(box.height));
    svg.innerHTML =
      `<path d="M${x1} ${y1} C ${mid} ${y1}, ${mid} ${y2}, ${x2} ${y2}"/>` +
      `<circle cx="${x1}" cy="${y1}" r="3"/><circle cx="${x2}" cy="${y2}" r="3"/>`;
  }, [wide, settled, current, chipFor]);
  useLayoutEffect(() => draw());

  // As the reader scrolls, the chip nearest the upper third of the screen leads, unless they have just chosen one.
  useEffect(() => {
    if (!wide || !settled) return;
    let frame = 0;
    const onScroll = () => {
      if (frame) return;
      frame = window.requestAnimationFrame(() => {
        frame = 0;
        if (Date.now() < pinned.current || !body.current) {
          draw();
          return;
        }
        const level = window.innerHeight * 0.38;
        let best: HTMLElement | null = null;
        let gap = Infinity;
        for (const c of body.current.querySelectorAll<HTMLElement>(".wts-chip")) {
          const r = c.getBoundingClientRect();
          if (r.bottom < 0 || r.top > window.innerHeight) continue;
          const d = Math.abs(r.top - level);
          if (d < gap) {
            gap = d;
            best = c;
          }
        }
        if (best && best.getAttribute("aria-current") !== "true")
          setChosen({ n: Number(best.dataset.source), seg: Number(best.dataset.seg) });
        else draw();
      });
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll);
    // the panel beside the briefing, and its passage, scroll on their own: the line follows them too
    const side = grid.current;
    const redraw = () => draw();
    side?.addEventListener("scroll", redraw, { passive: true, capture: true });
    return () => {
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onScroll);
      side?.removeEventListener("scroll", redraw, { capture: true });
      if (frame) window.cancelAnimationFrame(frame);
    };
  }, [wide, settled, draw]);

  const chipOf = (e: React.SyntheticEvent) => (e.target as HTMLElement).closest<HTMLElement>(".wts-chip");
  const asChosen = (chip: HTMLElement): Chosen => ({ n: Number(chip.dataset.source), seg: Number(chip.dataset.seg) });
  function onClick(e: React.MouseEvent<HTMLDivElement>) {
    const chip = chipOf(e);
    if (!chip || !answer) return;
    if (phase === "checking") {
      stopRunUp.current();
      setRunning(null);
      setPhase("settled");
    }
    if (wide) choose(asChosen(chip));
    else setSheet(asChosen(chip));
  }
  function onFocus(e: React.FocusEvent<HTMLDivElement>) {
    const chip = chipOf(e);
    if (chip && wide && settled) choose(asChosen(chip));
  }
  function onOver(e: React.MouseEvent<HTMLDivElement>) {
    const chip = chipOf(e);
    if (!chip || !wide || !settled || chip.getAttribute("aria-current") === "true") return;
    if (window.matchMedia?.("(hover: hover)").matches) choose(asChosen(chip), 2500);
  }
  const step = (by: number) => {
    if (!current || !record.length) return;
    const at = record.findIndex((s) => s.n === current.n);
    const next = record[(at + by + record.length) % record.length];
    const chip = body.current?.querySelector<HTMLElement>(`.wts-chip[data-source="${next.n}"]`);
    choose(chip ? asChosen(chip) : { n: next.n, seg: null });
    chip?.scrollIntoView?.({ block: "center", behavior: still() ? "auto" : "smooth" });
  };
  const details = (n: number) => {
    setSheet(null);
    onCite({ source: n, quotes: runs.get(n) ?? [] });
  };
  const nowOf = (c: Chosen) => (c.seg !== null ? (bySeg.get(c.seg) ?? []) : (runs.get(c.n) ?? []).slice(0, 1));
  const nearOf = (c: Chosen) =>
    c.seg !== null ? answer?.segments[c.seg]?.cited.find((x) => x.source === c.n)?.cited_text : undefined;

  // "Show the first": the passage comes into view, and is focused so that reading goes on from it
  useEffect(() => {
    const strip = body.current?.closest(".answer")?.querySelector(".wts-check");
    if (!strip) return;
    const go = (e: Event) => {
      const a = (e.target as HTMLElement).closest<HTMLAnchorElement>("a.wts-check__first");
      if (!a) return;
      const target = document.getElementById(a.getAttribute("href")!.slice(1));
      if (!target) return;
      e.preventDefault();
      target.tabIndex = -1;
      target.scrollIntoView?.({ block: "center", behavior: still() ? "auto" : "smooth" });
      target.focus({ preventScroll: true });
      target.classList.add("is-shown");
      window.setTimeout(() => target.classList.remove("is-shown"), 1600);
    };
    strip.addEventListener("click", go);
    return () => strip.removeEventListener("click", go);
  }, [counts]);

  const stage = turn.status === "planning" ? 0 : turn.status === "searching" ? 1
    : turn.status === "answering" ? (turn.sourcesOnly || dealt < sources.length ? 1 : 2) : 3;
  const waiting = turn.live !== false && (active || phase === "checking");
  const showPile = (active || phase === "checking") && sources.length > 0 && !turn.sourcesOnly;
  const recordSource = current ? bySource.get(current.n) : undefined;
  const sheetSource = sheet ? bySource.get(sheet.n) : undefined;

  // the stages and what has been found: beside the records found until the text begins, then above the briefing
  const waitBlock = (
        <div className="wait">
          <Stages at={stage} sourcesOnly={turn.sourcesOnly} />
          {!turn.plan && !sources.length && <Spines />}
          {turn.plan && !streamText && !answer && <QuestionAsRead plan={turn.plan} meta={meta} />}
          {turn.status === "searching" && (
            <p className="wait-line">
              {turn.passages
                ? `Searching ${turn.passages.toLocaleString("en-GB")} passages of Hansard by words and by meaning.`
                : "Searching Hansard by words and by meaning."}
            </p>
          )}
          {sources.length > 0 && (
            <Found
              sources={sources}
              dealt={dealt}
              writing={!turn.sourcesOnly && dealt >= sources.length}
              checking={turn.status === "verifying"}
            />
          )}
          {turn.sourcesOnly && sources.length > 0 && (
            <Pile sources={sources} dealt={dealt} cited={cited} ref={pile} />
          )}
        </div>
  );

  if (!active && !answer && !streamText) return null;
  if (answer?.no_records) {
    return (
      <div className="answer wts-cloth--mps">
        <p className="answer-head">
          <span className="answer-label">Briefing written by AI</span>
        </p>
        <Grounding answer={answer} />
      </div>
    );
  }
  if (turn.sourcesOnly && !active) return null;

  return (
    <div className="answer wts-cloth--mps">
      {waiting && (streamText || answer) && waitBlock}
      {(streamText || answer) && (
        <p className="answer-head">
          <span className="answer-label">Briefing written by AI</span>
        </p>
      )}
      {counts && (
        <div ref={strip}>
          <CheckStrip
            counts={counts}
            id={checkId}
            detailsId={`${prefix}details`}
            firstNoSource={firstNoSource >= 0 ? `${prefix}nosource-${firstNoSource}` : null}
            firstRemoved={firstRemoved >= 0 ? `${prefix}nosource-${firstRemoved}` : null}
            firstMissing={firstMissing}
            running={running}
          />
        </div>
      )}
      <div
        className={`briefing-grid${showPile || (settled && wide) ? " has-side" : ""}${streamText || answer ? " has-text" : ""}`}
        ref={grid}
      >
        <div className="briefing-main">
          {waiting && !streamText && !answer && waitBlock}
          {stances.length > 0 && <WhereTheyStand stances={stances} body={body} />}
          {(streamText || answer) && (
            /* sanitised with DOMPurify in render.ts */
            <div
              ref={body}
              className={answer ? (phase === "checking" ? "answer-body is-checking" : "answer-body") : "answer-body streaming"}
              onClick={onClick}
              onFocus={onFocus}
              onMouseOver={onOver}
              dangerouslySetInnerHTML={{ __html: html }}
            />
          )}
          {turn.status === "verifying" && !answer && <Checking />}
          {answer && (
            <>
              <CheckDetails id={`${prefix}details`} answer={answer} records={sources.length} />
              {/* the end of a finished briefing (data-wts-conversion, inert unless measuring: web/src/measure.ts) */}
              <p
                className="ai-note"
                data-wts-conversion="briefing_read"
                {...(briefingId ? { "data-wts-briefing": briefingId } : {})}
              >
                Written by AI (Claude) from the cited Hansard records only. Check the sources before relying on it.
                {preparedAt &&
                  ` Prepared ${fmtDate(preparedAt.slice(0, 10))}${recordsUpTo ? ` from records up to ${fmtDate(recordsUpTo)}` : ""}.`}
              </p>
              <div className="answer-actions">
                {link && <ShareButton url={link} title={briefingTitle(preparedAt)} />}
                <details className="export-menu">
                  <summary className="secondary">Export</summary>
                  <div className="export-list">
                    <CopyButton
                      label="Copy with sources"
                      text={() => briefingWithSources(question, answer, sources, new Date(), link)}
                    />
                    <CopyButton label="Copy references only" text={() => referencesOnly(answer, sources)} />
                    <button
                      type="button"
                      className="secondary"
                      data-wts-conversion="download"
                      onClick={() => downloadWord(question, answer, sources, link ?? window.location.href)}
                    >
                      Download Word
                    </button>
                    <button type="button" className="secondary" data-wts-conversion="download" onClick={() => window.print()}>
                      Print or save as PDF
                    </button>
                    {link && <CopyButton label="Copy embed code" text={() => embedCode(link)} />}
                  </div>
                </details>
                <Feedback question={question} briefingKey={briefingKey} />
              </div>
              <ReportProblem briefingId={briefingId} />
            </>
          )}
        </div>
        {showPile && (
          <aside className="briefing-side" aria-label="The records found">
            <p className="wts-record__title">
              The record for this briefing <span>{dealt} found</span>
            </p>
            <Pile sources={sources} dealt={dealt} cited={cited} ref={pile} />
          </aside>
        )}
        {!showPile && settled && wide && recordSource && current && (
          <aside className="briefing-side" aria-label="The record beside the briefing">
            <RecordPanel
              source={recordSource}
              idPrefix={prefix}
              index={record.indexOf(recordSource)}
              count={record.length}
              runs={runs.get(current.n) ?? []}
              now={nowOf(current)}
              near={nearOf(current)}
              settling={settling}
              onStep={step}
              onDetails={() => details(current.n)}
            />
          </aside>
        )}
        <svg className="wts-connector" ref={line} aria-hidden="true" />
      </div>
      {sheet && sheetSource && !wide && (
        <SourceSheet
          source={sheetSource}
          runs={runs.get(sheet.n) ?? []}
          now={nowOf(sheet)}
          near={nearOf(sheet)}
          onClose={() => setSheet(null)}
          onDetails={() => details(sheet.n)}
        />
      )}
    </div>
  );
}

/** The cursor at the end of the text as it is written. */
function withCursor(html: string): string {
  const cursor = '<span class="wts-cursor" aria-hidden="true"></span>';
  const m = html.match(/(<\/(?:p|li|h\d)>\s*(?:<\/(?:ul|ol)>\s*)?)$/);
  return m ? html.slice(0, m.index) + cursor + m[1] : html + cursor;
}

/** Statements not tied to a cited source: those with no citation ("unsupported"), and those whose every citation
 * failed the check against the source text and was removed ("invalid", counted apart by the server, which counts
 * only the first in `unsupported_segments`). Both are marked alike (render.ts). The verifier's own count, for the
 * note at the end. */
export function uncitedStatements(answer: Answer): number {
  return answer.unsupported_segments + answer.segments.filter((s) => s.status === "invalid").length;
}

/** At the top of a briefing: each group and its position, as the briefing's own section headings put it, with a
 * link to that section. Built from the headings (no extra AI), so it always matches the text below. */
function WhereTheyStand({ stances, body }: { stances: Stance[]; body: React.RefObject<HTMLDivElement | null> }) {
  function go(heading: string) {
    const el = [...(body.current?.querySelectorAll<HTMLElement>("h2, h3, h4, p > strong:only-child") ?? [])].find(
      (e) => e.textContent?.trim() === heading,
    );
    const target = el?.tagName === "STRONG" ? el.parentElement : el;
    if (!target) return;
    target.tabIndex = -1;
    target.scrollIntoView?.({ behavior: "smooth", block: "start" });
    target.focus({ preventScroll: true });
  }
  return (
    <section className="stances" aria-label="Where they stand, in brief">
      <h3 className="stances-head">Where they stand</h3>
      <table>
        <thead>
          <tr>
            <th scope="col">Who</th>
            <th scope="col">Position</th>
            <th scope="col" className="num">
              Sources
            </th>
          </tr>
        </thead>
        <tbody>
          {stances.map((s) => (
            <tr key={s.heading}>
              <th scope="row">
                <button type="button" className="link" onClick={() => go(s.heading)}>
                  {s.group}
                </button>
              </th>
              <td>{s.position}</td>
              <td className="num">
                {s.sources}
                <span className="unit"> source{s.sources === 1 ? "" : "s"}</span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

/**
 * A saved briefing's neutral title, as the server gives its page: "Briefing from Hansard, 6 November 2026". Shared
 * links, embeds and the card never carry the reader's question, which can be worded as an allegation.
 */
export function briefingTitle(preparedAt?: string): string {
  return preparedAt ? `Briefing from Hansard, ${fmtDate(preparedAt.slice(0, 10))}` : "Briefing from Hansard";
}

/** A snippet for a website or blog: the briefing's preview card, linking back to the briefing. */
export function embedCode(link: string): string {
  const esc = (s: string) => s.replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;");
  return (
    `<a href="${esc(link)}" style="display:inline-block;max-width:600px">` +
    `<img src="${esc(link)}/card.png" alt="Where MPs Stand: a briefing written by AI from Hansard" ` +
    `width="600" style="width:100%;height:auto;border:1px solid #ccc"></a>\n` +
    `<p style="font-size:0.9em">From <a href="${esc(link)}">Where MPs Stand</a>, a briefing on the UK ` +
    `Parliament written by AI from the Hansard record (CC BY 4.0).</p>`
  );
}

function downloadWord(question: string, answer: Answer, sources: SourceCard[], link: string) {
  const blob = new Blob([briefingDocx(question, answer, sources, link).buffer as ArrayBuffer], {
    type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = downloadName(question);
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

/** The phone's share sheet where there is one; otherwise copy the link. */
function ShareButton({ url, title }: { url: string; title: string }) {
  const [done, setDone] = useState(false);
  const canShare = typeof navigator !== "undefined" && typeof navigator.share === "function";
  async function share() {
    try {
      if (canShare) await navigator.share({ url, title: `${title} | Where MPs Stand` });
      else {
        await navigator.clipboard.writeText(url);
        setDone(true);
        setTimeout(() => setDone(false), 2000);
      }
    } catch {
      /* cancelled or unavailable */
    }
  }
  return (
    <button type="button" className="primary" onClick={() => void share()}>
      {done ? "Link copied" : canShare ? "Share" : "Copy link"}
    </button>
  );
}

function CopyButton({ label, text }: { label: string; text: () => string }) {
  const [done, setDone] = useState(false);
  async function copy() {
    try {
      await navigator.clipboard.writeText(text());
      setDone(true);
      setTimeout(() => setDone(false), 2000);
    } catch {
      /* clipboard unavailable: nothing to do */
    }
  }
  return (
    <button type="button" className="secondary" onClick={() => void copy()}>
      {done ? "Copied" : label}
    </button>
  );
}

function Feedback({ question, briefingKey }: { question: string; briefingKey?: string }) {
  const [sent, setSent] = useState<boolean | null>(null);
  if (sent !== null) return <p className="feedback muted small">Thank you for the feedback.</p>;
  const send = (useful: boolean) => {
    setSent(useful);
    void api.feedback(question, useful, briefingKey).catch(() => undefined);
  };
  return (
    <p className="feedback">
      <span>Was this briefing useful?</span>
      <button type="button" className="link" onClick={() => send(true)}>
        Yes
      </button>
      <button type="button" className="link" onClick={() => send(false)}>
        No
      </button>
    </p>
  );
}

/** A briefing with no records found: no strip, and the site says so. */
export function Grounding({ answer }: { answer: Answer }) {
  return (
    <p className="grounding neutral">
      {answer.no_records ? "No matching records in the archive for this question and these filters." : ""}
    </p>
  );
}

/** The full note at the end of a briefing, linked from the strip: how it was checked, with the verifier's own
 * counts (the strip counts the passages as they are shown). */
export function CheckDetails({ id, answer, records }: { id: string; answer: Answer; records: number }) {
  const total = answer.citations_total;
  const invalid = answer.citations_invalid ?? 0;
  const quotes = answer.quotes_total ?? 0;
  const missed = answer.quotes_unverified ?? 0;
  const flagged = uncitedStatements(answer);
  const loaded = answer.loaded_words ?? 0;
  const beside = answer.segments.some((s) => s.beside);
  const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;
  return (
    <section className="check-details" id={id} aria-labelledby={`${id}-head`} tabIndex={-1}>
      <h3 id={`${id}-head`}>How this briefing was checked</h3>
      <ul>
        {records > 0 && (
          <li>The AI was given only the {plural(records, "record")} found for the question, and may use nothing else.</li>
        )}
        <li>
          {total === 1 ? "The citation was" : `Each of the ${total} citations was`} checked against the text of its
          source:{" "}
          {invalid
            ? `${plural(invalid, "citation")} did not match and ${invalid === 1 ? "was" : "were"} removed.`
            : total === 1 ? "it matched." : "all matched."}
        </li>
        {quotes > 0 && (
          <li>
            {quotes === 1 ? "The quotation was" : `Each of the ${quotes} quotations was`} looked for word for word in
            its source:{" "}
            {missed
              ? `${plural(missed, "quotation")} could not be found and ${missed === 1 ? "is" : "are"} marked "Not found word for word". Check the source before relying on ${missed === 1 ? "it" : "them"}.`
              : quotes === 1 ? "it was found." : "all were found."}
          </li>
        )}
        <li>
          {flagged
            ? `${flagged === 1 ? "One statement is" : `${flagged} statements are`} not tied to a cited source, and ${flagged === 1 ? "is" : "are"} marked with a dotted underline, the first with "No source": be wary of anything you cannot see cited.`
            : "No statement was found without a source."}{" "}
          Short joining phrases are not counted.
        </li>
        {loaded > 0 && (
          <li>
            {plural(loaded, "judgemental word")} in the briefing's own voice {loaded === 1 ? "is" : "are"} underlined:
            the record itself should be checked.
          </li>
        )}
        {beside && (
          <li>
            A quotation found word for word in the source cited straight after it is shown as that source's quotation.
            The words around it keep their mark.
          </li>
        )}
        <li>
          Some errors can still get through: check the record before relying on a point. The{" "}
          <a href="/methodology#ai-briefings">methodology</a> sets out each check in full.
        </li>
      </ul>
    </section>
  );
}
