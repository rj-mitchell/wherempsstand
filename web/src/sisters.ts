/**
 * Under a briefing, a link that asks the same question on a sister site, where that site's record deals with the
 * subject (docs/DECISIONS.md, 43). Where Councils Stand answers from councils' minutes and named votes, so it is offered
 * for local services; Where Countries Stand answers from the United Nations' record, so it is offered for foreign
 * affairs. Both take the question in `?q=` on their home page.
 *
 * The judgement is made from the policy areas (`portfolios`) in the plan the site already makes when it reads the
 * question, with no further AI. Some areas are wholly local services, or wholly foreign affairs. Others are run partly
 * by councils and partly nationally (health, schools and universities, transport, the environment, tax): for those,
 * Where Councils Stand is offered only when the question as read names the part councils run (social care, schools,
 * buses, bins and so on), so that a question on hospital waiting lists or tuition fees does not send readers to
 * councils' minutes.
 */
import { sister } from "./family";
import type { Plan } from "./types";

/** Areas that are local services wherever they come up: housing, planning, communities and local government. */
export const LOCAL_PORTFOLIOS = ["housing_communities"];

/** Areas run partly by councils: offered only when the question names the part councils run. */
export const PARTLY_LOCAL: Record<string, RegExp[]> = {
  health: [/\b(social care|adult care|care homes?|home care|domiciliary care|care workers?|carers?|public health)\b/i],
  education: [
    /\b(schools?|pupils?|teachers?|special educational needs|EHCPs?|early years|nurser(y|ies)|childcare|children's services|free school meals|academ(y|ies))\b/i,
    /\bSEND\b/, // in capitals: "send" is not special educational needs
  ],
  transport: [
    /\b(bus(es)?|local (roads?|transport)|potholes?|road (maintenance|repairs)|parking|cycle lanes|cycling|trams?|concessionary (fares|travel)|school transport|low traffic neighbourhoods?|20 ?mph)\b/i,
  ],
  environment: [/\b(waste|bins?|bin collections?|recycling|fly-?tipping|litter|street cleaning|parks)\b/i],
  treasury: [/\b(council tax|business rates|local government (funding|finance)|section 114)\b/i],
};

/** Areas that are foreign affairs: the Foreign Office, international development, defence and international trade. */
export const FOREIGN_PORTFOLIOS = ["foreign", "international_development", "defence", "trade"];

/** Whether the question, as the site read it, touches local services. */
export function touchesLocalServices(plan: Pick<Plan, "portfolios" | "search_text">, question = ""): boolean {
  const words = `${plan.search_text ?? ""} ${question}`;
  return (plan.portfolios ?? []).some(
    (p) => LOCAL_PORTFOLIOS.includes(p) || (PARTLY_LOCAL[p] ?? []).some((re) => re.test(words)),
  );
}

/** Whether the question, as the site read it, touches foreign affairs. */
export function touchesForeignAffairs(plan: Pick<Plan, "portfolios">): boolean {
  return (plan.portfolios ?? []).some((p) => FOREIGN_PORTFOLIOS.includes(p));
}

const RULES: { id: string; note: string; test: (plan: Plan, question: string) => boolean }[] = [
  { id: "councils", note: "from councils' minutes and named votes", test: touchesLocalServices },
  { id: "countries", note: "from the United Nations' records", test: (plan) => touchesForeignAffairs(plan) },
];

export interface SameQuestionLink {
  id: string;
  name: string;
  href: string;
  spine: string;
  note: string;
}

/** The sister sites whose record deals with this question, with the address that asks it there. Nothing without the
 * plan the site made when it read the question. */
export function sameQuestionLinks(question: string, plan: Plan | null | undefined): SameQuestionLink[] {
  const q = question.trim();
  if (q.length < 3 || !plan) return [];
  return RULES.filter((r) => r.test(plan, q)).flatMap((r) => {
    const s = sister(r.id);
    if (!s) return [];
    // the sister sites keep the first 500 characters of a question
    return [{ id: s.id, name: s.name, spine: s.spine, note: r.note, href: `${s.url}?q=${encodeURIComponent(q.slice(0, 500))}` }];
  });
}
