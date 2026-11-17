import type { GroupEvidence, PartyGroup } from "../types";

/** One line of evidence, linked to where it was published or said ("Signed the Labour Growth Group's letter of
 * 30 July 2024 (LabourList)"). */
function EvidenceLine({ e }: { e: GroupEvidence }) {
  return (
    <li>
      {e.url ? <a href={e.url}>{e.label}</a> : e.label}
      {e.archive_url && (
        <>
          {" "}
          <a href={e.archive_url} className="small">
            (archived copy)
          </a>
        </>
      )}
      {e.note && <span className="muted small"> {e.note}</span>}
    </li>
  );
}

/** A member's party groups, each with its evidence. ``parliaments`` adds which Parliament each piece belongs to (the
 * member page lists every Parliament; a source shows only the one the words were said in). */
export function PartyGroupList({ groups, parliaments = false }: { groups: PartyGroup[]; parliaments?: boolean }) {
  return (
    <ul className="party-groups">
      {groups.map((g) => (
        <li key={g.id}>
          <strong>{g.name}</strong>
          <ul>
            {g.evidence.map((e) => (
              <EvidenceLine key={`${e.kind}-${e.date}-${e.record_id ?? e.url}`} e={parliaments ? { ...e, label: `${e.label} (${e.parliament} Parliament)` } : e} />
            ))}
          </ul>
        </li>
      ))}
    </ul>
  );
}

/** Said wherever groups are shown: membership rests on published evidence, which is uneven. */
export const GROUPS_CAVEAT =
  "On published evidence only: a group's own list, a letter or statement the member signed, or their own words in Parliament. Most groups publish no lists, so no evidence is not evidence of not belonging.";
