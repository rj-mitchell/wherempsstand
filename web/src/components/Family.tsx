import { FAMILY } from "../family";

/* The family kit's partials are fixed files from the kit, written at build time, never anything a reader sent, so
   they are set as HTML. The wrappers match partials/header.html and partials/footer.html. */

/** Under the wordmark: the site's spine and "A Where They Stand project", linking to wheretheystand.uk. */
export function FamilyEndorsement() {
  return <div className="family-endorse" dangerouslySetInnerHTML={{ __html: FAMILY.endorsement }} />;
}

/** At the foot of the footer: the charity, its status, and every project on its shelf. */
export function FamilyShelf() {
  return <div className="family-shelf" dangerouslySetInnerHTML={{ __html: FAMILY.shelf }} />;
}
