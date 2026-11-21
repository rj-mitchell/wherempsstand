import proof from "../../partials/proof.html?raw";

/**
 * Just under the question box on the home page: the line saying briefings are written by AI, and a plain fold-out of
 * how they are checked, in the family's words (the charity's family/GLOSSARY.md, "AI briefings"). The About and
 * methodology pages say the same at more length (#ai-briefings on each).
 */
export function WhyChatbot() {
  return (
    <div className="why">
      <p className="why-line">Briefings are written by AI from the official record only, and every citation is checked.</p>
      <details className="why-chatbot">
        <summary>How the briefings are checked</summary>
        <ul>
          <li>
            The AI is given only the Hansard and other parliamentary records found for your question, and may use
            nothing else. Early day motions, committee evidence and the devolved parliaments' reports are given too,
            when they bear on the question.
          </li>
          <li>
            Each point links to its source. Citations are checked against the source text, quotations are checked
            word for word, and sentences that no source supports are flagged.
          </li>
          <li>Hansard is read in twice a day.</li>
          <li>If the record holds nothing relevant, the site says &ldquo;No records found&rdquo;.</li>
          <li>Evidence is drawn from across ministers, the opposition and backbenchers.</li>
        </ul>
        <p>
          The <a href="/methodology#ai-briefings">methodology</a> sets out each check in full.
        </p>
      </details>
    </div>
  );
}

/** The checking, shown: the kit's proof block, from the same file the page's HTML includes for readers without
 * scripts (web/partials/proof.html), so the two cannot differ. */
export function Proof() {
  // the site's own markup, written by hand: no reader's or model's words
  return <div className="proof-holder" dangerouslySetInnerHTML={{ __html: proof.replace(/<!--[\s\S]*?-->/g, "") }} />;
}
