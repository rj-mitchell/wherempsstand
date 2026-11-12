import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { besideCards, StatementBox } from "../components/Statements";
import { SourceList } from "../components/Sources";
import {
  DISCLAIMER,
  KIND_ALIASES,
  pagePath,
  paragraphs,
  safeLink,
  statementElement,
  statementProblem,
  wordCount,
  type Statement,
} from "../statements";
import type { SourceCard } from "../types";

const st: Statement = {
  id: 7,
  name: "Hilary Benn",
  role: "MP for Leeds South",
  item: "/b/abcdefghjk",
  person: 10669,
  text: "I have always supported trial by jury.\n\nMy speech of 2008 makes that clear.",
  link: "https://hansard.parliament.uk/commons/2008-06-11",
  date: "2026-11-12",
};

const card: SourceCard = {
  n: 1,
  kind: "record",
  title: "t",
  url: "https://hansard.parliament.uk/Commons/2018-03-07",
  date: "2018-03-07",
  chamber: "House of Commons",
  text: "We face inflation in costs.",
  twfy_url: "https://www.theyworkforyou.com/debates/?id=2018-03-07a.1.1",
  official_url: "https://hansard.parliament.uk/Commons/2018-03-07",
  url_kind: "sitting",
  colnum: "827",
  heading: "HS2",
  speaker: {
    person_id: "uk.org.publicwhip/person/10669",
    date: "2018-03-07",
    name: "Hilary Benn",
    house: "commons",
    constituency: "Leeds Central",
    region: "Yorkshire and The Humber",
    party: "Labour",
    offices: [],
    other_names: [],
  },
};

describe("a statement in someone's own words", () => {
  it("is headed with their name and the date, and ends with the words that say it is theirs", () => {
    render(<StatementBox s={st} />);
    const box = screen.getByRole("complementary", { name: "Statement from Hilary Benn, 12 November 2026" });
    expect(within(box).getByRole("heading", { level: 3 })).toHaveTextContent("Statement from Hilary Benn, 12 November 2026");
    expect(within(box).getByText("MP for Leeds South")).toBeInTheDocument();
    expect(box.querySelectorAll("blockquote p")).toHaveLength(2);
    expect(box.lastElementChild).toHaveTextContent("In their own words. Not checked or endorsed by Where MPs Stand.");
    const link = within(box).getByRole("link", { name: "The record they point to" });
    expect(link).toHaveAttribute("href", st.link);
    expect(link.getAttribute("rel")).toContain("nofollow");
  });

  it("is shown as text, never as HTML, and only ordinary web links are made links", () => {
    render(<StatementBox s={{ ...st, text: "<img src=x onerror=alert(1)> words", link: "javascript:alert(1)" }} about />);
    expect(screen.getByText("<img src=x onerror=alert(1)> words")).toBeInTheDocument();
    expect(document.querySelector("img")).toBeNull();
    expect(screen.queryByRole("link", { name: "The record they point to" })).toBeNull();
    expect(screen.getByRole("link", { name: "a briefing" })).toHaveAttribute("href", "/b/abcdefghjk");
    expect(safeLink("https://example.org/a b")).toBeNull();
  });

  it("is drawn the same way on the pages not drawn with React", () => {
    const el = statementElement({ ...st, text: "<b>bold</b>" }, 2);
    expect(el.querySelector("h2")?.textContent).toBe("Statement from Hilary Benn, 12 November 2026");
    expect(el.querySelector("b")).toBeNull();
    expect(el.lastElementChild?.textContent).toBe(DISCLAIMER);
    expect(el.getAttribute("aria-labelledby")).toBe(el.querySelector("h2")?.id);
  });

  it("sits beside the first source card from the member it is about", () => {
    const second = { ...card, n: 2 };
    const other = { ...st, id: 8, person: 10777 };
    expect([...besideCards([card, second], [st, other]).entries()]).toEqual([[1, [st]]]);
    render(<SourceList sources={[card, second]} onOpen={() => {}} open statements={[st]} />);
    const items = screen.getAllByRole("listitem");
    expect(within(items[0]).getByRole("complementary")).toHaveAttribute("id", "statement-beside-7");
    expect(within(items[1]).queryByRole("complementary")).toBeNull();
  });
});

describe("sending a statement", () => {
  const ok = { name: "Hilary Benn", role: "MP", item: "/people/10669", text: "five words are enough here", email: "a@b.uk", link: "" };

  it("checks the length, the required fields and the link before it is sent", () => {
    expect(statementProblem(ok)).toBeNull();
    expect(statementProblem({ ...ok, text: Array(150).fill("w").join(" ") })).toBeNull();
    expect(statementProblem({ ...ok, text: Array(151).fill("w").join(" ") })).toBe("A statement can be up to 150 words. This one is 151.");
    expect(statementProblem({ ...ok, name: "" })).toMatch(/name/);
    expect(statementProblem({ ...ok, role: "" })).toMatch(/role/);
    expect(statementProblem({ ...ok, item: " " })).toMatch(/address of the page/);
    expect(statementProblem({ ...ok, email: "nope" })).toMatch(/email/);
    expect(statementProblem({ ...ok, link: "javascript:alert(1)" })).toMatch(/full web address/);
    expect(wordCount("  one\ntwo   three ")).toBe(3);
    expect(paragraphs("a\n\n\n b \n\nc")).toEqual(["a", "b", "c"]);
  });

  it("files a member's page under its number, whatever name follows it", () => {
    expect(pagePath("/people/10669/hilary-benn")).toBe("/people/10669");
    expect(pagePath("/debates/2008-06-11/commons/x/")).toBe("/debates/2008-06-11/commons/x");
  });

  it("the page has the form, with a trap field for spam bots, and the response times", () => {
    const html = readFileSync(resolve(__dirname, "../../if-youre-named.html"), "utf8");
    expect(html).toContain("<h1>If you're named</h1>");
    expect(html).toContain("We reply within 5 working days, and decide within 20 working days.");
    for (const route of ["Something is wrong", "You've been characterised unfairly", "Add a statement in your own words"])
      expect(html).toContain(route);
    for (const field of ["name", "role", "item", "text", "link", "email", "website"]) expect(html).toContain(`name="${field}"`);
    expect(html).toContain('href="/corrections?kind=correction#report"');
    expect(html).toContain('href="/corrections?kind=complaint#report"');
  });
});

describe("the report form", () => {
  it("keeps the values the server knows, and takes the routes' short names in ?kind=", () => {
    const html = readFileSync(resolve(__dirname, "../../corrections.html"), "utf8");
    const values = [...html.matchAll(/name="kind" value="(\w+)"/g)].map((m) => m[1]);
    expect(values).toEqual(["correction", "complaint", "takedown", "general"]);
    for (const v of Object.values(KIND_ALIASES)) expect(values).toContain(v);
    expect(KIND_ALIASES.wrong).toBe("correction");
    expect(KIND_ALIASES.unfair).toBe("complaint");
    expect(KIND_ALIASES.takedown).toBe("takedown");
    expect(html).toContain("We reply within 5 working days, and decide within 20 working days.");
    expect(html).toContain('<a href="/if-youre-named">If you\'re named on this site</a>');
  });
});
