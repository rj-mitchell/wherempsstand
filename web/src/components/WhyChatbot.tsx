import proof from "../../partials/proof.html?raw";

/** The checking, shown, just under the question box on the home page: the kit's proof block, from the same file the
 * page's HTML includes for readers without scripts (web/partials/proof.html), so the two cannot differ. It holds the
 * family's line saying briefings are written by AI and the plain fold-out of how they are checked, in the family's
 * words (the charity's family/GLOSSARY.md, "AI briefings"); the About and methodology pages say the same at more
 * length (#ai-briefings on each). */
export function Proof() {
  // the site's own markup, written by hand: no reader's or model's words
  return <div className="proof-holder" dangerouslySetInnerHTML={{ __html: proof.replace(/<!--[\s\S]*?-->/g, "") }} />;
}
