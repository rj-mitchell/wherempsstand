import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { WhyChatbot } from "../components/WhyChatbot";

// The family's wording (family/GLOSSARY.md in the charity's repository, "Why use this rather than a chatbot?").
const LINE = "Answers only from the official record, every point linked and checked.";
const SUMMARY = "Why use this rather than a chatbot?";
const LAST = "Briefings here are written by AI too. The difference is what the AI is given and how it is checked.";
const POINTS = [
  "Only the record.",
  "Every point linked and checked.",
  "Up to date.",
  "It says when there is nothing.",
  "Balanced by design.",
  "Records searched in full.",
  "Open.",
];

const page = (name: string) => readFileSync(resolve(__dirname, "../..", name), "utf8").replace(/\s+/g, " ");

describe("Why use this rather than a chatbot?", () => {
  it("is one line and a plain fold-out under the question box, with the first five points", () => {
    const { container } = render(<WhyChatbot />);
    expect(screen.getByText(LINE)).toBeTruthy();
    const details = container.querySelector("details");
    expect(details?.open).toBe(false); // closed until the reader opens it, so it takes two lines of the page
    expect(details?.querySelector("summary")?.textContent).toBe(SUMMARY);
    expect([...container.querySelectorAll("li > strong")].map((s) => s.textContent)).toEqual(POINTS.slice(0, 5));
    expect(details?.textContent).toContain(LAST);
    expect(screen.getByRole("link", { name: "How it works", hidden: true }).getAttribute("href")).toBe(
      "/methodology#chatbot",
    );
  });

  it("is said at more length, in the same order, on the About and methodology pages", () => {
    for (const name of ["about.html", "methodology.html"]) {
      const html = page(name);
      const section = html.slice(html.indexOf('<h2 id="chatbot">'), html.indexOf('<h2 id="co'));
      expect(section).toContain(`<h2 id="chatbot">${SUMMARY}</h2>`);
      expect([...section.matchAll(/<li> <strong>([^<]+)<\/strong>/g)].map((m) => m[1])).toEqual(POINTS);
      expect(section).toContain(LAST);
      expect(html).toContain(`<li><a href="#chatbot">${SUMMARY}</a></li>`);
    }
  });
});
