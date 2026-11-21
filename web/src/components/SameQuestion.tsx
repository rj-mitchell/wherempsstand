import { sameQuestionLinks } from "../sisters";
import type { Plan } from "../types";

/** Under a briefing: the same question on a sister site, by name and with its spine, where its record covers it
 * (judged from the plan the site made when it read the question: sisters.ts). */
export function SameQuestion({ question, plan }: { question: string; plan: Plan | null | undefined }) {
  const links = sameQuestionLinks(question, plan);
  if (links.length === 0) return null;
  return (
    <div className="same-question">
      {links.map((l) => (
        <p key={l.id} className="small">
          {/* the sister site's spine, from the family kit's shelf: a fixed drawing, not anything a reader sent */}
          <span className="same-question-spine" dangerouslySetInnerHTML={{ __html: l.spine }} />
          <a href={l.href}>Ask the same question on {l.name}</a>
          <span className="muted">, {l.note}</span>
        </p>
      ))}
    </div>
  );
}
