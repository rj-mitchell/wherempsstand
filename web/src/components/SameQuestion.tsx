import { sameQuestionLinks } from "../sisters";

/** Under a briefing: the same question on a sister site, by name and with its spine, where its record covers it. */
export function SameQuestion({ question }: { question: string }) {
  const links = sameQuestionLinks(question);
  if (links.length === 0) return null;
  return (
    <p className="small same-question">
      <span className="muted">On a sister site: </span>
      {links.map((l, i) => (
        <span key={l.id}>
          {i > 0 && " · "}
          <a href={l.href}>
            {/* the sister site's spine, from the family kit's shelf: a fixed drawing, not anything a reader sent */}
            <span className="same-question-spine" dangerouslySetInnerHTML={{ __html: l.spine }} />
            Ask {l.name}
          </a>
          <span className="muted"> ({l.note})</span>
        </span>
      ))}
    </p>
  );
}
