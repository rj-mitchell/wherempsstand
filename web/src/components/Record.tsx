/**
 * The record beside a briefing (the family's BRIEFINGS.md, "The record beside the briefing"): on wide screens a
 * sticky panel open on a source's passage, with the record's own words highlighted; on narrower ones the same slip
 * in a bottom sheet that a chip opens. The slip is the record's own form, a Hansard column.
 */
import { useEffect, useLayoutEffect, useRef } from "react";
import { passageParts, passageText, slipOf } from "../checked";
import type { SourceCard } from "../types";

/** One source as a slip: the House, the date and the column; the speaker in bold, with their party and seat; the
 * debate's heading; the passage with each quoted run highlighted (``now``: the ones the reader chose, outlined). */
export function Slip({
  source,
  runs,
  now,
  near,
  passageId,
  onDetails,
}: {
  source: SourceCard;
  runs: string[];
  now: string[];
  /** words the citation points at, to scroll to when nothing is quoted from the source */
  near?: string;
  passageId?: string;
  onDetails?: () => void;
}) {
  const v = slipOf(source);
  const text = passageText(source);
  const parts = passageParts(text, runs, now);
  const found = parts.some((p) => p.mark);
  const passage = useRef<HTMLParagraphElement>(null);
  // the chosen words in view inside the passage (its own scroll, so the page does not move)
  useLayoutEffect(() => {
    const p = passage.current;
    if (!p) return;
    const m = p.querySelector<HTMLElement>("mark.wts-now") ?? p.querySelector<HTMLElement>("mark");
    if (m) {
      p.scrollTop = Math.max(0, p.scrollTop + m.getBoundingClientRect().top - p.getBoundingClientRect().top - 24);
      return;
    }
    const at = near ? text.indexOf(near.slice(0, 60)) : -1;
    p.scrollTop = at > 0 ? Math.max(0, (at / Math.max(text.length, 1)) * p.scrollHeight - 24) : 0;
  }, [source, runs, now, near, text]);
  return (
    <div className="wts-slip">
      <div className="wts-slip__top">
        <b>{v.house}</b>
        <span>{v.when}</span>
      </div>
      {v.who && (
        <div className="wts-slip__who">
          <strong>{v.who}</strong>
          {v.tail && ` (${v.tail})`}
        </div>
      )}
      {v.heading && <div className="wts-slip__heading">{v.heading}</div>}
      {source.interest && (
        <p className="slip-interest">
          <span className="interest-flag">Registered interest</span> The speaker had an entry in the register made before
          they spoke that may relate to this. Matched by AI: a link to check, not a finding of wrongdoing.
        </p>
      )}
      {text && (
        <p className="wts-passage" id={passageId} tabIndex={0} aria-label={`The passage from source ${source.n}`} ref={passage}>
          {parts.map((part, i) =>
            part.mark ? (
              <mark key={i} className={part.now ? "wts-now" : undefined}>
                {part.text}
              </mark>
            ) : (
              <span key={i}>{part.text}</span>
            ),
          )}
        </p>
      )}
      <div className="wts-slip__foot">
        <span className="wts-slip__found">{found ? "✓ Highlighted words found word for word" : ""}</span>
        {v.href && <a href={v.href}>{v.label}</a>}
        {onDetails && (
          <button type="button" className="link slip-details" onClick={onDetails}>
            {source.interest ? "The registered interest and all the details" : "All the details of this source"}
          </button>
        )}
      </div>
    </div>
  );
}

/** The panel beside the briefing, on wide screens: the chosen source's slip, and a way to step through them. */
export function RecordPanel({
  source,
  index,
  count,
  runs,
  now,
  near,
  settling,
  idPrefix,
  onStep,
  onDetails,
}: {
  source: SourceCard;
  idPrefix: string;
  index: number;
  count: number;
  runs: string[];
  now: string[];
  near?: string;
  settling?: boolean;
  onStep: (by: number) => void;
  onDetails: () => void;
}) {
  return (
    <div className={settling ? "wts-record is-settling" : "wts-record"}>
      <p className="wts-record__title">
        The record{" "}
        <span>
          Source {source.n}, {index + 1} of {count}
        </span>
      </p>
      <Slip source={source} runs={runs} now={now} near={near} passageId={`${idPrefix}record-passage`} onDetails={onDetails} />
      {count > 1 && (
        <div className="wts-record__pager">
          <button type="button" onClick={() => onStep(-1)}>
            Previous source
          </button>
          <button type="button" onClick={() => onStep(1)}>
            Next source
          </button>
        </div>
      )}
    </div>
  );
}

/** On narrower screens: the slip in a bottom sheet. Focus moves to its heading, stays inside it, and goes back to
 * the chip when it closes (Close, Escape, or a tap outside it). */
export function SourceSheet({
  source,
  runs,
  now,
  near,
  onClose,
  onDetails,
}: {
  source: SourceCard;
  runs: string[];
  now: string[];
  near?: string;
  onClose: () => void;
  onDetails: () => void;
}) {
  const sheet = useRef<HTMLDivElement>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const opener = useRef<Element | null>(null);
  useEffect(() => {
    opener.current = document.activeElement;
    heading.current?.focus();
    return () => {
      const back = opener.current;
      if (back instanceof HTMLElement && back.isConnected) back.focus();
    };
  }, []);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
        return;
      }
      if (e.key !== "Tab" || !sheet.current) return;
      const stops = [...sheet.current.querySelectorAll<HTMLElement>("button, a[href], [tabindex='0']")];
      if (!stops.length) return;
      const at = stops.indexOf(document.activeElement as HTMLElement);
      if (e.shiftKey && at <= 0) {
        e.preventDefault();
        stops[stops.length - 1].focus();
      } else if (!e.shiftKey && (at === stops.length - 1 || at === -1)) {
        e.preventDefault();
        stops[0].focus();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <>
      <div className="wts-sheet__back" onClick={onClose} />
      <div className="wts-sheet wts-cloth--mps" role="dialog" aria-modal="true" aria-labelledby="sheet-head" ref={sheet}>
        <div className="wts-sheet__head">
          <h2 id="sheet-head" tabIndex={-1} ref={heading}>
            Source {source.n}: the record
          </h2>
          <button type="button" className="wts-sheet__close" onClick={onClose}>
            Close
          </button>
        </div>
        <Slip source={source} runs={runs} now={now} near={near} passageId="sheet-passage" onDetails={onDetails} />
      </div>
    </>
  );
}
