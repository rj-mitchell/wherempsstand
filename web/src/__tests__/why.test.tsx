import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { WhyChatbot } from "../components/WhyChatbot";

// The family's wording (family/GLOSSARY.md in the charity's repository, "AI briefings").
const LINE = "Briefings are written by AI from the official record only, and every citation is checked.";
const SUMMARY = "How the briefings are checked";
const POINTS = [
  "The AI is given only the Hansard and other parliamentary records found for your question",
  "Each point links to its source.",
  "Hansard is read in twice a day.",
  "If the record holds nothing relevant, the site says “No records found”.",
  "Evidence is drawn from across ministers, the opposition and backbenchers.",
];

const page = (name: string) => readFileSync(resolve(__dirname, "../..", name), "utf8").replace(/\s+/g, " ");

describe("How the briefings are checked", () => {
  it("is one line and a plain fold-out under the question box, with the first five points and no bold lead-ins", () => {
    const { container } = render(<WhyChatbot />);
    expect(screen.getByText(LINE)).toBeTruthy();
    const details = container.querySelector("details");
    expect(details?.open).toBe(false); // closed until the reader opens it, so it takes two lines of the page
    expect(details?.querySelector("summary")?.textContent).toBe(SUMMARY);
    const items = [...container.querySelectorAll("li")].map((li) => li.textContent?.replace(/\s+/g, " ") ?? "");
    expect(items).toHaveLength(5);
    POINTS.forEach((p, i) => expect(items[i].startsWith(p), p).toBe(true));
    expect(container.querySelector("li strong")).toBeNull();
    expect(screen.getByRole("link", { name: "methodology", hidden: true }).getAttribute("href")).toBe(
      "/methodology#ai-briefings",
    );
  });

  it("is said at more length on the About and methodology pages, under the heading AI briefings", () => {
    for (const name of ["about.html", "methodology.html"]) {
      const html = page(name);
      const start = html.indexOf('<h2 id="ai-briefings">');
      expect(start, name).toBeGreaterThan(0);
      const section = html.slice(start, html.indexOf("<h2 ", start + 10));
      expect(section).toContain('<h2 id="ai-briefings">AI briefings</h2>');
      expect(section).toContain("<h3>What the model is given</h3>");
      expect(section).toContain("<h3>How it is checked</h3>");
      expect(section).toContain("Briefings are written by AI.");
      expect(section).toContain("sentences that no source supports are flagged");
      expect(section).toContain("“No records found”");
      expect(section).not.toContain("<strong>");
      expect(html).toContain('<li><a href="#ai-briefings">AI briefings</a></li>');
    }
  });
});
