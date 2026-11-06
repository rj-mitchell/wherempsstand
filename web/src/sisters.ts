/**
 * Where the records meet (the family plan, section 4): under a briefing, a quiet link that asks the same question on
 * a sister site, shown only where that site's record deals with the subject. Where Councils Stand answers from
 * councils' minutes, so it is offered for local services; Where Countries Stand answers from the United Nations'
 * record, so it is offered for foreign affairs. Both take the question in `?q=` on their home page (Councils fills in
 * the question box; Countries asks it at once).
 */
import { sister } from "./family";

interface Rule {
  id: string;
  /** what the sister site's record is, in a few words */
  note: string;
  /** the subjects it deals with */
  test: (question: string) => boolean;
}

const LOCAL =
  /\b(councils?|councillors?|local (authorit(y|ies)|government|services?|elections?|plans?)|council tax|business rates|social care|care homes?|bin collections?|bins|waste collection|recycling|fly-?tipping|potholes?|librar(y|ies)|leisure centres?|planning (permission|applications?)|green belt|housing|homeless(ness)?|temporary accommodation|special educational needs|school transport|children's services|parking|street lighting|high streets?|section 114|mayors?|combined authorit(y|ies)|devolution deals?)\b/i;
const FOREIGN =
  /\b(United Nations|Security Council|General Assembly|foreign (policy|affairs|aid)|overseas aid|aid budget|international (aid|development|law)|sanctions?|Ukrain(e|ian)|Russian?|Gaza|Israeli?|Palestin\w*|West Bank|Iran|China|Chinese|Taiwan|Hong Kong|Syria|Yemen|Sudan|Afghanistan|North Korea|Myanmar|Venezuela|NATO|nuclear (weapons|disarmament)|disarmament|arms (sales|exports|trade)|ceasefire|genocide|peacekeep\w*|refugees?|climate (change|finance))\b/i;

const RULES: Rule[] = [
  {
    id: "councils",
    note: "what councils have decided and said",
    test: (q) => LOCAL.test(q) || /\bSEND\b/.test(q),
  },
  {
    id: "countries",
    note: "what countries have said at the UN",
    test: (q) => FOREIGN.test(q) || /\bUN\b/.test(q),
  },
];

export interface SameQuestionLink {
  id: string;
  name: string;
  href: string;
  spine: string;
  note: string;
}

/** The sister sites that could answer this question from their own record, with the address that asks it there. */
export function sameQuestionLinks(question: string): SameQuestionLink[] {
  const q = question.trim();
  if (q.length < 3) return [];
  return RULES.filter((r) => r.test(q)).flatMap((r) => {
    const s = sister(r.id);
    return s ? [{ id: s.id, name: s.name, spine: s.spine, note: r.note, href: `${s.url}?q=${encodeURIComponent(q)}` }] : [];
  });
}
