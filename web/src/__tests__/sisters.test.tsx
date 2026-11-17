import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { SameQuestion } from "../components/SameQuestion";
import { sameQuestionLinks } from "../sisters";

const ids = (q: string) => sameQuestionLinks(q).map((l) => l.id);

describe("the same question on a sister site", () => {
  it("offers Where Councils Stand for local services and Where Countries Stand for foreign affairs", () => {
    expect(ids("What have MPs said about council tax increases?")).toEqual(["councils"]);
    expect(ids("Funding for adult social care")).toEqual(["councils"]);
    expect(ids("What has been said about SEND provision?")).toEqual(["councils"]);
    expect(ids("What has the government said about sanctions on Russia?")).toEqual(["countries"]);
    expect(ids("UN peacekeeping")).toEqual(["countries"]);
    expect(ids("Housing for refugees from Ukraine")).toEqual(["councils", "countries"]);
  });

  it("offers nothing where neither record deals with the subject", () => {
    expect(ids("What has been said about trial by jury?")).toEqual([]);
    expect(ids("Should we send more money to the NHS?")).toEqual([]); // "send", not SEND
    expect(ids("unemployment benefits")).toEqual([]); // "un" inside a word is not the UN
  });

  it("asks the question at the sister site's own address, by name and with its spine", () => {
    const [link] = sameQuestionLinks("Gaza ceasefire");
    expect(link.href).toBe("https://wherecountriesstand.com/?q=Gaza%20ceasefire");
    render(<SameQuestion question="Gaza ceasefire" />);
    const a = screen.getByRole("link", { name: "Ask Where Countries Stand" });
    expect(a.getAttribute("href")).toBe(link.href);
    expect(a.querySelector("svg.wts-spine.wts-cloth--countries")).not.toBeNull();
  });
});
