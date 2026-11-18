import { describe, expect, it } from "vitest";
import { followUpsFor, historyFor } from "../followups";
import type { Answer, Plan, Turn } from "../types";

const plan = (filters: Partial<Plan["filters"]>, extra: Partial<Plan> = {}): Plan =>
  ({
    search_text: "bus services",
    filters: { date_from: null, date_to: null, houses: [], regions: [], parties: [], person_ids: [], offices: [], record_types: [], ...filters },
    portfolios: [],
    wants_votes: false,
    speakers: [],
    interpretation: "",
    ...extra,
  }) as Plan;
const turn = (p: Plan): Turn =>
  ({ id: "1", question: "q", settings: {} as Turn["settings"], sources: [], streamText: "", status: "done", plan: p, answer: { no_records: false } as Answer }) as Turn;

describe("suggested follow-ups", () => {
  it("offer the other House, votes, a recent period and ministers", () => {
    expect(followUpsFor(turn(plan({ houses: ["commons"] })))).toEqual([
      "What have peers said about this?",
      "How have MPs voted on this?",
      "What has been said since the 2024 general election?",
    ]);
  });

  it("offer MPs after a question about peers, and nothing from a recent period already asked for", () => {
    const f = followUpsFor(turn(plan({ houses: ["lords"], date_from: "2025-01-01" })));
    expect(f[0]).toBe("What have MPs said about this?");
    expect(f).not.toContain("What has been said since the 2024 general election?");
  });

  it("offer nothing when there was no briefing", () => {
    expect(followUpsFor({ ...turn(plan({})), answer: null })).toEqual([]);
  });
});

describe("what a follow-up sends", () => {
  it("names earlier briefings by their ids and sends none of their text", () => {
    const earlier: Turn = {
      ...turn(plan({}, { interpretation: "All members." })),
      briefingId: "abcdefghjk",
      answer: { no_records: false, markdown: "Ministers said the scheme would close." } as Answer,
    };
    const sourcesOnly: Turn = { ...turn(plan({})), question: "rail fares", answer: null };
    const sent = historyFor([earlier, sourcesOnly]);
    expect(sent).toEqual([
      { question: "q", topic: "bus services", scope: "All members.", briefing_id: "abcdefghjk" },
      { question: "rail fares", topic: "bus services", scope: "", briefing_id: "" },
    ]);
    expect(JSON.stringify(sent)).not.toContain("scheme would close");
  });
});
