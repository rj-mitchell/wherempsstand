import { createHash } from "node:crypto";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, resolve } from "node:path";
import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { BRAND } from "../brand";
import { SiteFooter } from "../components/SiteFooter";
import { FAMILY, PROJECTS, sister } from "../family";

const web = resolve(__dirname, "../..");
const read = (path: string) => readFileSync(join(web, path), "utf8");
const pages = readdirSync(web).filter((f) => f.endsWith(".html"));

/** Every file under a folder, as paths relative to it. */
const walk = (dir: string, prefix = ""): string[] =>
  readdirSync(dir).flatMap((f) =>
    statSync(join(dir, f)).isDirectory() ? walk(join(dir, f), `${prefix}${f}/`) : [`${prefix}${f}`],
  );

describe("the Where They Stand family binding", () => {
  it("is the kit's copy, unedited", () => {
    // sync.mjs writes a hash of every file it copies into VERSION; editing a copy here breaks the match. To change
    // anything in public/family/, change the kit and sync again (docs/DESIGN.md, "The family binding").
    const dir = join(web, "public/family");
    const version = read("public/family/VERSION");
    const hash = createHash("sha256");
    for (const path of walk(dir).filter((p) => p !== "VERSION").sort()) {
      hash.update(path).update("\0").update(readFileSync(join(dir, path))).update("\0");
    }
    expect(version).toContain("site mps");
    expect(version).toContain(`sha256 ${hash.digest("hex")}`);
  });

  it("is on every page: the head, the endorsement, the footer shelf and the family's headings", () => {
    expect(read("partials/head.html")).toContain("<!--#include family/head-->");
    expect(read("partials/header.html")).toContain('<div class="family-endorse"><!--#include family/endorsement--></div>');
    expect(read("partials/footer.html")).toContain('<div class="family-shelf"><!--#include family/shelf--></div>');
    expect(pages.length).toBeGreaterThan(30);
    for (const page of pages) {
      const html = read(page);
      expect(html, page).toContain("<!--#include head-->");
      expect(html, page).toContain('<body class="wts-headings">');
      // one set of icons and one theme colour: the kit's
      expect(html, page).not.toMatch(/rel="(icon|apple-touch-icon)"|name="theme-color"/);
    }
    expect(read("partials/head.html")).not.toMatch(/rel="(icon|apple-touch-icon)"|name="theme-color"|og:image/);
  });

  it("the home page's footer is the static pages' footer", () => {
    const { container } = render(<SiteFooter />);
    const kit = document.createElement("div");
    kit.innerHTML = FAMILY.shelf;
    expect(container.querySelector(".family-shelf")?.innerHTML).toBe(kit.innerHTML);
    expect(container.querySelectorAll(".family-shelf .wts-book")).toHaveLength(5);
    expect(container.querySelector(".independence")?.textContent).toBe(BRAND.independence);
    expect(read("partials/footer.html")).toContain(`<p class="independence">${BRAND.independence}</p>`);
    expect(BRAND.independence).toBe(
      "Where MPs Stand is independent. It is not affiliated with or endorsed by the UK Parliament.",
    );
  });

  it("browsers that ask for the icons at the root get the kit's", () => {
    for (const f of ["favicon.ico", "apple-touch-icon.png"]) {
      expect(readFileSync(join(web, "public", f)).equals(readFileSync(join(web, "public/family", f))), f).toBe(true);
    }
    const manifest = JSON.parse(read("public/manifest.webmanifest")) as { theme_color: string; icons: { src: string }[] };
    expect(manifest.theme_color.toLowerCase()).toBe("#1d4535");
    expect(manifest.icons.every((i) => i.src.startsWith("/family/"))).toBe(true);
  });

  it("knows each sister site by its name, address and spine", () => {
    expect(PROJECTS.map((p) => p.id)).toEqual(["mps", "councils", "landlords", "countries", "lives"]);
    expect(sister("councils")).toMatchObject({ name: "Where Councils Stand", url: "https://wherecouncilsstand.com/" });
    expect(sister("countries")?.spine).toMatch(/^<svg class="wts-spine wts-cloth--countries"[\s\S]*<\/svg>$/);
  });
});
