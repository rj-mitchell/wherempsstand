import { useEffect, useId, useState } from "react";
import type { SourceCard } from "../types";
import {
  DISCLAIMER,
  fetchStatements,
  heading,
  itemLabel,
  paragraphs,
  safeLink,
  type Statement,
  type StatementQuery,
  NAMED_PAGE,
} from "../statements";

/** A statement in someone's own words, beside what the site says about them (the right of reply). Styled apart from
 * the site's own text, and always followed by the words that say it is theirs, not the site's. statements.ts draws
 * the same markup for the pages not drawn with React. */
export function StatementBox({
  s,
  level = 3,
  about = false,
  anchor,
}: {
  s: Statement;
  level?: 2 | 3 | 4;
  about?: boolean;
  anchor?: string; // an id to link to, for the one beside a source card
}) {
  const id = `statement-${s.id}-${useId().replace(/:/g, "")}`;
  const H = `h${level}` as "h2" | "h3" | "h4";
  const link = safeLink(s.link);
  return (
    <aside className="statement" aria-labelledby={id} id={anchor} tabIndex={anchor ? -1 : undefined}>
      <H id={id} className="statement-head">
        {heading(s)}
      </H>
      <p className="statement-role">{s.role}</p>
      <blockquote className="statement-text">
        {paragraphs(s.text).map((p, i) => (
          <p key={i}>{p}</p>
        ))}
      </blockquote>
      {link && (
        <p className="statement-link">
          <a href={link} rel="nofollow ugc noopener noreferrer">
            The record they point to
          </a>
        </p>
      )}
      {about && (
        <p className="statement-about">
          About <a href={s.item}>{itemLabel(s.item)}</a>
        </p>
      )}
      <p className="statement-note">{DISCLAIMER}</p>
    </aside>
  );
}

/** Published statements for an item or a member, fetched on their own (never with a briefing or the record). */
export function useStatements(query: StatementQuery | null): Statement[] {
  const [found, setFound] = useState<Statement[]>([]);
  const key = query ? `${(query.paths ?? []).join(",")}|${query.person ?? ""}` : "";
  useEffect(() => {
    if (!query) return;
    let live = true;
    void fetchStatements(query).then((s) => live && setFound(s));
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- the key stands for the query
  }, [key]);
  return found;
}

/** The member a source card is from, as the number in their page's address (/people/<number>). */
export const memberNumber = (personId: string | null | undefined): number | null => {
  const n = personId?.split("/").pop();
  return n && /^\d+$/.test(n) ? Number(n) : null;
};

/** Which card each statement sits beside: the first card from the member it is about. */
export function besideCards(sources: SourceCard[], statements: Statement[]): Map<number, Statement[]> {
  const out = new Map<number, Statement[]>();
  for (const st of statements) {
    const card = st.person != null ? sources.find((c) => memberNumber(c.speaker?.person_id) === st.person) : undefined;
    if (card) out.set(card.n, [...(out.get(card.n) ?? []), st]);
  }
  return out;
}

export const besideAnchor = (s: Statement) => `statement-beside-${s.id}`;

/** Under a briefing, always in view: statements about it from the people it names. One from a member with a source
 * card is shown beside their first card in the source list, and listed here with a link to it; any other is shown
 * here in full. */
export function BriefingStatements({ paths, sources }: { paths: string[]; sources: SourceCard[] }) {
  const statements = useStatements({ paths });
  const headId = useId().replace(/:/g, "");
  if (!statements.length) return null;
  const beside = new Set([...besideCards(sources, statements).values()].flat().map((s) => s.id));
  const show = (s: Statement) => (e: React.MouseEvent) => {
    const el = document.getElementById(besideAnchor(s));
    if (!el) return;
    e.preventDefault();
    const details = el.closest("details");
    if (details) details.open = true;
    el.scrollIntoView({ block: "start" });
    el.focus({ preventScroll: true });
  };
  return (
    <section className="statements briefing-statements" aria-labelledby={`h-st-${headId}`}>
      <h3 id={`h-st-${headId}`} className="examples-head">
        {statements.length === 1 ? "A statement about this briefing" : "Statements about this briefing"}
      </h3>
      <p className="muted small">
        From people it names, in their own words. <a href={NAMED_PAGE}>If you're named on this site</a>.
      </p>
      {statements.some((s) => beside.has(s.id)) && (
        <ul className="statement-pointers">
          {statements
            .filter((s) => beside.has(s.id))
            .map((s) => (
              <li key={s.id}>
                <a href={`#${besideAnchor(s)}`} onClick={show(s)}>
                  {heading(s)}
                </a>
                , beside their source below
              </li>
            ))}
        </ul>
      )}
      {statements
        .filter((s) => !beside.has(s.id))
        .map((s) => (
          <StatementBox key={s.id} s={s} level={4} />
        ))}
    </section>
  );
}
