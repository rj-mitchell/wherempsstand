import { fmtDate } from "../render";

export interface Quoted {
  title: string;
  path: string;
  prepared_at: string;
}

/** The topic briefings whose checked citations quote a member, newest first, from the saved briefings' sources (no
 * AI). The server writes the same into the page (plaintext.py, quoted_in_text). */
export function QuotedIn({ name, quoted }: { name: string; quoted: Quoted[] }) {
  return (
    <section aria-labelledby="h-quoted">
      <h2 id="h-quoted">Quoted in</h2>
      <p>Topic briefings, written by AI from the record, whose checked citations quote {name}.</p>
      <ul>
        {quoted.map((q) => (
          <li key={q.path}>
            <a href={q.path}>{q.title}</a>, {fmtDate(q.prepared_at.slice(0, 10))}
          </li>
        ))}
      </ul>
    </section>
  );
}
