import type { ReactNode } from "react";
import { BRAND } from "../brand";
import { measuring } from "../measure";
import { FOOTER } from "../nav";
import { FamilyShelf } from "./Family";

/** The footer every page shows (partials/footer.html is the same for the static pages): the family shelf, the site's
 *  own links, then the independence and licence notices. */
export function SiteFooter({ children }: { children?: ReactNode }) {
  return (
    <footer className="footer full-width">
      <FamilyShelf />
      <nav className="footer-nav" aria-label="About this site">
        {FOOTER.map((l) => (
          <a key={l.href} href={l.href}>
            {l.label}
          </a>
        ))}
      </nav>
      <p className="independence">{BRAND.independence}</p>
      <p className="licence">
        Briefings are licensed under <a href="https://creativecommons.org/licenses/by/4.0/">CC BY 4.0</a>. Contains
        Parliamentary information licensed under the{" "}
        <a href="https://www.parliament.uk/site-information/copyright-parliament/open-parliament-licence/">
          Open Parliament Licence v3.0
        </a>
        , and public sector information licensed under the{" "}
        <a href="https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/">
          Open Government Licence v3.0
        </a>{" "}
        (ONS, NRS and NISRA boundaries, council areas, urban areas and census populations). Data service provided by{" "}
        <a href="https://www.theyworkforyou.com/">TheyWorkForYou</a>: Hansard data and member records via TheyWorkForYou
        and ParlParse, with thanks to <a href="https://www.mysociety.org/">mySociety</a>.
      </p>
      {/* While measuring (hansard/api/measure.py), a control that opens the family kit's banner again: not one of the
          six footer links, so a line of its own; the server adds the same line to the static pages' footer */}
      {measuring() && (
        <p className="measure-choices">
          <a href="/privacy#measurement" data-wts-measure-open="">
            Measurement choices
          </a>
        </p>
      )}
      {children}
    </footer>
  );
}
