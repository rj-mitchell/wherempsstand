/**
 * The wait for a briefing (the family's BRIEFINGS.md, "The wait"): the stages drawn from the stream's real events, in
 * order. Before the first event, the family's spines; on the plan event, the question as read; on the sources event,
 * each record found as a card in the record's own form, into a pile; as the text is written, each source it cites is
 * marked "Cited"; on the final event, the checks' results. Nothing is invented and nothing waits for an animation.
 */
import { forwardRef } from "react";
import { cardOf, countsInWords, checkCounts, foundSummary, planWords } from "../checked";
import type { Meta, SourceCard, Turn } from "../types";

export const STAGES = [
  "Reading the question",
  "Searching the record",
  "Writing from these records only",
  "Checking every citation and quotation",
];

/** The stages, the one under way marked "Now". "Sources only (no AI)" has the first two. */
export function Stages({ at, sourcesOnly }: { at: number; sourcesOnly?: boolean }) {
  const names = sourcesOnly ? STAGES.slice(0, 2) : STAGES;
  return (
    <ol className="wts-stages" aria-label="Preparing the briefing">
      {names.map((name, i) => {
        const state = i < at ? "done" : i === at ? "now" : "next";
        return (
          <li key={name} className="wts-stage" data-state={state} aria-current={state === "now" ? "step" : undefined}>
            <span className="wts-stage__state">{state === "done" ? "✓ Done" : state === "now" ? "Now" : "Next"}</span>
            <span>{name}</span>
          </li>
        );
      })}
    </ol>
  );
}

const CLOTHS = ["mps", "councils", "landlords", "countries", "lives"];
const HEIGHTS = [64, 56, 70, 60, 52];

/** The family's book spines, in the five cloths, before the first records arrive. */
export function Spines() {
  return (
    <div className="wts-spines" aria-hidden="true">
      {CLOTHS.map((c, i) => (
        <span
          key={c}
          style={{ "--c": `var(--wts-cloth-${c})`, "--i": i, "--h": `${HEIGHTS[i]}px` } as React.CSSProperties}
        />
      ))}
    </div>
  );
}

/** The question as read, in large chips, with the site's own reading of it under them. */
export function QuestionAsRead({ plan, meta }: { plan: NonNullable<Turn["plan"]>; meta: Meta | null }) {
  return (
    <div className="wait-plan">
      <ul className="wts-plan" aria-label="The question as read">
        {planWords(plan, meta).map((w) => (
          <li key={w}>{w}</li>
        ))}
      </ul>
      {plan.interpretation && <p className="wait-interp">{plan.interpretation}</p>}
    </div>
  );
}

/** The running count of records found, then the summary; once writing begins, what the AI may use. */
export function Found({ sources, dealt, writing }: { sources: SourceCard[]; dealt: number; writing: boolean }) {
  if (dealt < sources.length) {
    return (
      <p className="wts-found" aria-hidden="true">
        <strong>{dealt}</strong> {dealt === 1 ? "record" : "records"} found so far
      </p>
    );
  }
  const [count, ...rest] = foundSummary(sources).split(" ");
  return (
    <p className="wts-found">
      <strong>{count}</strong> {rest.join(" ")}.
      {writing && (
        <>
          {" "}
          {sources.length === 1
            ? "Writing from this one record only. The AI may use it and nothing else."
            : `Writing from these ${sources.length} records only. The AI may use these and nothing else.`}
        </>
      )}
    </p>
  );
}

/** The records found, as cards in the record's own form (a Hansard column), each with its real first words. Down
 * the side on wide screens, along a shelf on phones; it scrolls, so it is reachable by keyboard. */
export const Pile = forwardRef<HTMLDivElement, { sources: SourceCard[]; dealt: number; cited: Set<number> }>(
  function Pile({ sources, dealt, cited }, ref) {
    return (
      <div className="wts-pile" tabIndex={0} aria-label="The records found" ref={ref}>
        <ul>
          {sources.slice(0, dealt).map((s, i) => {
            const c = cardOf(s);
            return (
              <li
                key={s.n}
                className={cited.has(s.n) ? "wts-card is-cited" : "wts-card"}
                data-n={s.n}
                style={{ "--wts-r": `${((i % 5) - 2) * 1.2}deg` } as React.CSSProperties}
              >
                <div className="wts-card__top">
                  <span>{c.top}</span>
                  {c.ref && <span>{c.ref}</span>}
                </div>
                <div className="wts-card__who">
                  {c.who}
                  <span className="wts-card__cited"> Cited</span>
                </div>
                <div className="wts-card__line">{c.line}</div>
              </li>
            );
          })}
        </ul>
      </div>
    );
  },
);

/** What the one status region says for a turn: each stage once, as it begins, and at the end the result in words
 * (the moving counts are hidden from screen readers). */
export function announcement(t: Turn): string {
  const records = t.sources.length;
  switch (t.status) {
    case "planning":
      return "Reading the question.";
    case "searching":
      return "Searching the record.";
    case "answering":
      if (!records) return "Searching the record.";
      return t.sourcesOnly
        ? `Found ${foundSummary(t.sources)}.`
        : `Found ${foundSummary(t.sources)}. Writing the briefing from these records only.`;
    case "verifying":
      return "Checking every citation and quotation.";
    case "done":
      if (t.answer?.no_records) return "No records found.";
      if (t.answer) return `Briefing ready. Checked against Hansard: ${countsInWords(checkCounts(t.answer))}.`;
      return records ? `Sources ready: ${foundSummary(t.sources)}.` : "Sources ready.";
    default:
      return "";
  }
}
