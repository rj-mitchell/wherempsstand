/**
 * The check strip at the top of every briefing (the family's BRIEFINGS.md): a band in Commons green with the three
 * counts in words, from the answer as it is shown (``checkCounts``, which is wts-core's ``check_counts``). It links to
 * the full note at the end ("How this briefing was checked"), and a count with something to check links to its
 * first instance.
 */
import type { CheckCounts } from "../checked";

/** While a new briefing's checks run up: how far the citations and quotations have got. The count of sentences
 * without a source shows "counted last" until the end, rather than a zero that would later change. */
export interface RunningUp {
  citations: number;
  quotes: number;
}

export function CheckStrip({
  counts,
  id,
  detailsId,
  firstNoSource,
  firstMissing,
  firstRemoved,
  running,
}: {
  counts: CheckCounts;
  id: string;
  detailsId: string;
  firstNoSource: string | null;
  firstMissing: string | null;
  /** the first passage whose every citation failed and was removed, if any is shown */
  firstRemoved: string | null;
  running?: RunningUp | null;
}) {
  const cit = running ? running.citations : counts.citations_matched;
  const qt = running ? running.quotes : counts.quotes_found;
  const citWarn = !running && counts.citations_matched < counts.citations_total;
  const qtWarn = !running && counts.quotes_found < counts.quotes_total;
  const nsWarn = !running && counts.no_source > 0;
  const title = `${id}-title`;
  return (
    <section className="wts-check" aria-labelledby={title}>
      <div className="wts-check__head">
        <h3 className="wts-check__title" id={title}>
          Checked against Hansard
        </h3>
        <a className="wts-check__more" href={`#${detailsId}`}>
          How it was checked
        </a>
      </div>
      {/* while the counts run up they are hidden from screen readers, which hear the result once, at the end */}
      <ul className="wts-check__list" aria-hidden={running ? "true" : undefined}>
        <Item
          warn={citWarn}
          num={cit}
          of={counts.citations_total}
          words="citations matched to the record"
          first={citWarn ? firstRemoved : null}
        />
        <Item
          warn={qtWarn}
          num={qt}
          of={counts.quotes_total}
          words="quotations found word for word"
          first={qtWarn ? firstMissing : null}
        />
        {running ? (
          <li className="wts-check__item">
            <span className="wts-check__count">
              <span className="wts-check__glyph" aria-hidden="true">
                {"…"}
              </span>
              <span className="wts-check__num">{"–"}</span>
            </span>
            <span>sentences without a source: counted last</span>
          </li>
        ) : (
          <Item
            warn={nsWarn}
            num={counts.no_source}
            words={counts.no_source === 1 ? "sentence without a source" : "sentences without a source"}
            first={nsWarn ? firstNoSource : null}
          />
        )}
      </ul>
    </section>
  );
}

function Item({
  warn,
  num,
  of,
  words,
  first,
}: {
  warn: boolean;
  num: number;
  of?: number;
  words: string;
  first: string | null;
}) {
  return (
    <li className="wts-check__item">
      <span className="wts-check__count">
        <span className="wts-check__glyph" aria-hidden="true">
          {warn ? "!" : "✓"}
        </span>
        <span className="wts-check__num">{num}</span>
        {of !== undefined && <span>of {of}</span>}
        {warn && <span className="wts-check__badge">To check</span>}
      </span>
      <span>
        {words}
        {first && (
          <>
            .{" "}
            <a className="wts-check__first" href={`#${first}`}>
              Show the first
            </a>
          </>
        )}
      </span>
    </li>
  );
}
