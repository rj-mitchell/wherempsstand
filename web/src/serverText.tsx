/** The readable copy of a page that the server writes into its root (src/hansard/api/plaintext.py), for readers and
 * search engines that do not run scripts. The app draws the page over it once its data arrives; until then, and if
 * the data cannot be fetched (a search engine's renderer may be refused it, or the network may fail), the copy stays
 * on the page, so it is never left with only an error where its heading and content were. */
import type { ReactNode } from "react";

/** The server's copy in a page's root, taken before the app draws over it; null when the root holds only the
 * placeholder ("Fetching the bill's stages and votes."), as it does on a page the server did not fill. */
export function serverText(root: Element | null): string | null {
  return root?.querySelector("h1") ? root.innerHTML : null;
}

/** What shows while the page's data is on its way: the server's copy if there is one, otherwise ``children`` (the
 * page's own "Fetching …" line). */
export function Pending({ text, children }: { text: string | null; children: ReactNode }) {
  // the copy is the site's own, every value escaped as it was written (plaintext.py)
  return text ? <div className="server-text" dangerouslySetInnerHTML={{ __html: text }} /> : <>{children}</>;
}

export const NOT_UPDATED = "Part of this page did not load, so a plain version is shown. Reload the page to try again.";

/** What shows when the page's data could not be fetched: the server's copy under a short notice, or, with no copy
 * (an address with nothing at it), the page's own message in ``children``. */
export function Failed({ text, children }: { text: string | null; children: ReactNode }) {
  if (!text) return <p role="alert">{children}</p>;
  return (
    <>
      <p className="shared-note" role="alert">
        {NOT_UPDATED}
      </p>
      <div className="server-text" dangerouslySetInnerHTML={{ __html: text }} />
    </>
  );
}
