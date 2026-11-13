/**
 * Under the question box on the home page: one line that is always shown, and a fold-out answering "Why use this
 * rather than a chatbot?". The wording is the family's (the charity's family/GLOSSARY.md, "Why use this rather than
 * a chatbot?"), with this site's own record in it; each claim is checked against the code it describes, and the
 * About and methodology pages say the same at more length (#chatbot on each). A plain <details>: it opens without
 * scripts, and nothing counts how often it is opened.
 */
export function WhyChatbot() {
  return (
    <div className="why">
      <p className="why-line">Answers only from the official record, every point linked and checked.</p>
      <details className="why-chatbot">
        <summary>Why use this rather than a chatbot?</summary>
        <ul>
          <li>
            <strong>Only the record.</strong> The AI that writes a briefing is given only the Hansard and other
            parliamentary records found for your question, and may use nothing else.
          </li>
          <li>
            <strong>Every point linked and checked.</strong> Each point links to its source. Citations are checked
            against the source text, quotations word for word, and any statement no source supports is flagged.
          </li>
          <li>
            <strong>Up to date.</strong> Hansard is read in twice a day.
          </li>
          <li>
            <strong>It says when there is nothing.</strong> If the record holds nothing relevant, you see &ldquo;No
            records found&rdquo;, not a guess.
          </li>
          <li>
            <strong>Balanced by design.</strong> Evidence is chosen across ministers, opposition and backbenchers,
            not only by relevance.
          </li>
        </ul>
        <p>
          Briefings here are written by AI too. The difference is what the AI is given and how it is checked.{" "}
          <a href="/methodology#chatbot">How it works</a>
        </p>
      </details>
    </div>
  );
}
