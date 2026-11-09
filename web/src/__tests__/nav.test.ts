import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { FOOTER, HEADER_LINKS, NAV } from "../nav";

/** The links inside a partial's <nav> element. */
const links = (file: string) => {
  const html = readFileSync(resolve(__dirname, "../../partials", file), "utf8");
  const nav = html.slice(html.indexOf("<nav"), html.indexOf("</nav>"));
  return [...nav.matchAll(/<a href="([^"]+)">([^<]+)<\/a>/g)].map((m) => ({ href: m[1], label: m[2] }));
};

describe("site navigation", () => {
  it("the static pages' header matches the home page's, with Ask first", () => {
    expect(links("header.html")).toEqual(HEADER_LINKS);
    expect(NAV.map((l) => l.label)).toEqual(["Ask", "This week"]);
    expect(NAV[0]).toEqual({ href: "/", label: "Ask" });
  });

  it("the header's name and home link come before its menu, so Tab reaches them first", () => {
    const html = readFileSync(resolve(__dirname, "../../partials/header.html"), "utf8");
    const app = readFileSync(resolve(__dirname, "../App.tsx"), "utf8");
    expect(html.indexOf('class="title-block"')).toBeGreaterThan(-1);
    expect(html.indexOf('class="title-block"')).toBeLessThan(html.indexOf('class="running-head"'));
    expect(app.indexOf('className="title-block"')).toBeGreaterThan(-1);
    expect(app.indexOf('className="title-block"')).toBeLessThan(app.indexOf('className="running-head"'));
  });

  it("the footer is the same everywhere and holds only pages about the site", () => {
    expect(links("footer.html")).toEqual(FOOTER);
    const header = new Set(HEADER_LINKS.map((l) => l.href));
    expect(FOOTER.filter((l) => header.has(l.href))).toEqual([]);
  });
});
