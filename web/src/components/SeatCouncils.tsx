import { sister } from "../family";

export interface SeatCouncil {
  gss: string;
  name: string;
  tier: "unitary" | "district" | "county";
  kind: string | null;
  /** its page on Where Councils Stand, or null where that site has no page for it */
  url: string | null;
}

/** A sitting MP's councils: those whose areas overlap the seat (the ONS lookup of July 2024), each linked to its page
 * on Where Councils Stand under that site's name and spine. The server writes the same into the page for readers
 * without scripts (plaintext.py, councils_text). */
export function SeatCouncils({ seat, councils }: { seat: string; councils: SeatCouncil[] }) {
  const site = sister("councils");
  const linked = councils.filter((c) => c.url);
  const others = councils.filter((c) => !c.url);
  return (
    <section aria-labelledby="h-councils">
      <h2 id="h-councils">Councils</h2>
      <p>The councils whose areas overlap {seat}, from the ONS lookup of wards, constituencies and councils (July 2024).</p>
      {site && linked.length > 0 && (
        <aside className="sister" aria-label={`On ${site.name}`}>
          {/* the sister site's spine, from the family kit's shelf: a fixed drawing */}
          <span className="sister-spine" dangerouslySetInnerHTML={{ __html: site.spine }} />
          <div>
            <p className="sister-name">On {site.name}</p>
            <ul>
              {linked.map((c) => (
                <li key={c.gss}>
                  <a href={c.url!}>{c.name}</a>
                </li>
              ))}
            </ul>
          </div>
        </aside>
      )}
      {others.length > 0 && (
        <p>
          Also overlapping, with no page on {site?.name ?? "Where Councils Stand"}: {others.map((c) => c.name).join(", ")}.
        </p>
      )}
    </section>
  );
}
