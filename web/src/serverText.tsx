/** The readable copy of a page that the server writes into its root (src/hansard/api/plaintext.py), for readers and
 * search engines that do not run scripts. The app draws the page over it once its data arrives; until then, and if
 * the data cannot be fetched (a search engine's renderer may be refused it, or the network may fail), the copy stays
 * on the page, so it is never left with only an error where its heading and content were. */
import type { ReactNode } from "react";

/** The server's copy in a page's root, taken before the app draws over it: a page of the record (with its heading)
 * or an index page's list of links (.server-list). Null when the root holds only the placeholder ("Fetching the
 * bill's stages and votes."), as it does on a page the server did not fill. */
export function serverText(root: Element | null): string | null {
  return root?.querySelector("h1, .server-list") ? root.innerHTML : null;
}

/** For the index pages written without React: when their list cannot be fetched, the server's list stays, under the
 * same notice; with no list from the server, ``message`` takes the placeholder's place. */
export function listFailed(live: HTMLElement, message: string): void {
  const note = document.createElement("p");
  note.setAttribute("role", "alert");
  if (live.querySelector(".server-list")) {
    note.className = "shared-note";
    note.textContent = NOT_UPDATED;
    live.prepend(note);
  } else {
    note.className = "form-error";
    note.textContent = message;
    live.replaceChildren(note);
  }
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
