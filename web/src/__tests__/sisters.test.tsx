import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { QuotedIn } from "../components/QuotedIn";
import { SameQuestion } from "../components/SameQuestion";
import { SeatCouncils } from "../components/SeatCouncils";
import { sameQuestionLinks } from "../sisters";
import type { Plan } from "../types";

/** The plan the site makes when it reads a question: only the policy areas and the words searched for matter here. */
const plan = (portfolios: string[], search_text: string): Plan => ({
  question: search_text,
  search_text,
  filters: {} as Plan["filters"],
  portfolios,
  wants_votes: false,
  speakers: [],
  unresolved_speakers: [],
  interpretation: "",
  parser: "llm",
});
const ids = (q: string, portfolios: string[], search = q) => sameQuestionLinks(q, plan(portfolios, search)).map((l) => l.id);

describe("the same question on a sister site", () => {
  it("offers Where Councils Stand for housing, planning and local government, whatever the words", () => {
    expect(ids("What have MPs said about the housing crisis?", ["housing_communities"])).toEqual(["councils"]);
    expect(ids("Green belt development", ["housing_communities", "environment"])).toEqual(["councils"]);
  });

  it("offers Where Councils Stand for an area run partly by councils only when the question names that part", () => {
    expect(ids("Funding for adult social care", ["health"])).toEqual(["councils"]);
    expect(ids("NHS waiting lists", ["health"])).toEqual([]);
    expect(ids("What has been said about SEND provision?", ["education"])).toEqual(["councils"]);
    expect(ids("Tuition fees for university students", ["education"])).toEqual([]);
    expect(ids("Should we send more money to universities?", ["education"])).toEqual([]); // "send", not SEND
    expect(ids("Cuts to rural bus services", ["transport"])).toEqual(["councils"]);
    expect(ids("HS2 costs", ["transport"])).toEqual([]);
    expect(ids("Fortnightly bin collections", ["environment"])).toEqual(["councils"]);
    expect(ids("Farming subsidies", ["environment"])).toEqual([]);
    expect(ids("Council tax rises", ["treasury"])).toEqual(["councils"]);
    expect(ids("Income tax thresholds", ["treasury"])).toEqual([]);
    // the words the site searched for count as well as the reader's own
    expect(ids("What about care for older people at home?", ["health"], "domiciliary care older people")).toEqual([
      "councils",
    ]);
  });

  it("offers Where Countries Stand for foreign affairs, development, defence and international trade", () => {
    expect(ids("What has the government said about sanctions on Russia?", ["foreign"])).toEqual(["countries"]);
    expect(ids("Cuts to the aid budget", ["international_development"])).toEqual(["countries"]);
    expect(ids("Defence spending and NATO", ["defence"])).toEqual(["countries"]);
    expect(ids("Trade deal with India", ["trade"])).toEqual(["countries"]);
    expect(ids("Housing for refugees from Ukraine", ["housing_communities", "foreign"])).toEqual(["councils", "countries"]);
  });

  it("offers nothing for other areas, or without the plan", () => {
    expect(ids("What has been said about trial by jury?", ["justice"])).toEqual([]);
    expect(ids("Relations with the EU", ["exiting_eu"])).toEqual([]);
    expect(ids("Council housing waiting lists", [])).toEqual([]); // the plan names no area: nothing is guessed
    expect(sameQuestionLinks("Council housing", null)).toEqual([]);
  });

  it("asks the question at the sister site's own address, by name and with its spine", () => {
    const p = plan(["foreign"], "Gaza ceasefire");
    const [link] = sameQuestionLinks("Gaza ceasefire", p);
    expect(link.href).toBe("https://wherecountriesstand.com/?q=Gaza%20ceasefire");
    expect(sameQuestionLinks("Council tax", plan(["housing_communities"], "council tax"))[0].href).toBe(
      "https://wherecouncilsstand.com/?q=Council%20tax",
    );
    render(<SameQuestion question="Gaza ceasefire" plan={p} />);
    const a = screen.getByRole("link", { name: "Ask the same question on Where Countries Stand" });
    expect(a.getAttribute("href")).toBe(link.href);
    expect(a.parentElement?.querySelector("svg.wts-spine.wts-cloth--countries")).not.toBeNull();
  });
});

describe("a member's page", () => {
  it("lists their councils on Where Councils Stand, under its name and spine", () => {
    render(
      <SeatCouncils
        seat="Aldershot"
        councils={[
          { gss: "E10000014", name: "Hampshire County Council", tier: "county", kind: "County council", url: "https://wherecouncilsstand.com/councils/E10000014" },
          { gss: "E07000092", name: "Rushmoor", tier: "district", kind: null, url: null },
        ]}
      />,
    );
    const aside = screen.getByRole("complementary", { name: "On Where Councils Stand" });
    expect(aside.querySelector("svg.wts-spine.wts-cloth--councils")).not.toBeNull();
    expect(screen.getByRole("link", { name: "Hampshire County Council" }).getAttribute("href")).toBe(
      "https://wherecouncilsstand.com/councils/E10000014",
    );
    expect(screen.queryByRole("link", { name: "Rushmoor" })).toBeNull();
    expect(screen.getByText(/Also overlapping, with no page on Where Councils Stand: Rushmoor\./)).toBeTruthy();
  });

  it("lists the topic briefings that quote them, as given (newest first), with the day each was prepared", () => {
    render(
      <QuotedIn
        name="Hilary Benn"
        quoted={[
          { title: "The cost of living", path: "/topics/cost-of-living", prepared_at: "2026-11-17T06:10:00+00:00" },
          { title: "Prisons and sentencing", path: "/topics/prisons-and-sentencing", prepared_at: "2026-11-02T06:00:00+00:00" },
        ]}
      />,
    );
    const items = screen.getAllByRole("listitem").map((li) => li.textContent);
    expect(items).toEqual(["The cost of living, 17 November 2026", "Prisons and sentencing, 2 November 2026"]);
    expect(screen.getByRole("link", { name: "The cost of living" }).getAttribute("href")).toBe("/topics/cost-of-living");
  });
});
