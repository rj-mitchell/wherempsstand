// Accessibility check for CI: loads the site's main pages in Chromium, light and dark, and fails on any serious or
// critical WCAG 2.2 AA problem found by axe. Usage: node scripts/a11y.mjs http://localhost:8000 [repository]
//
// It also checks checked briefings with every kind of mark (the family's BRIEFINGS.md): fixed copies of real saved
// briefings from tests/fixtures/briefings/ (a quotation not found word for word and a registered interest in
// sw3c524d23; sentences with no source and registered interests in wbhnb95p3u; all found in q3k4u3vqh8), served to
// the page in place of /api/briefings/<id>, on a computer with the record beside the briefing and on a phone with
// the bottom sheet open; and the wait, part-way through writing, from the same briefing's events. The fixtures are
// read from the repository: the second argument, or GITHUB_WORKSPACE in CI, where this script runs from a copy.
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { createRequire } from "node:module";
import { chromium } from "playwright";

const require = createRequire(import.meta.url);
const axe = readFileSync(require.resolve("axe-core/axe.min.js"), "utf8");
const base = process.argv[2] ?? "http://localhost:8000";
const pages = ["/", "/about", "/methodology", "/glossary", "/developers", "/press", "/topics", "/bills", "/people",
  "/charts", "/this-week", "/committees", "/witnesses", "/devolved", "/edms", "/timeline", "/compare", "/corrections",
  "/named", "/accessibility", "/privacy", "/terms", "/status", "/groups", "/donations", "/directorships",
  "/resources", "/lighter-side", "/no-such-page"];

const repo = process.argv[3] ?? process.env.GITHUB_WORKSPACE ?? join(new URL(".", import.meta.url).pathname, "..");
const fixtures = join(repo, "tests", "fixtures", "briefings");
if (!existsSync(fixtures)) {
  console.log(`No briefing fixtures at ${fixtures}: give the repository as the second argument`);
  process.exit(1);
}
const briefing = (id) => readFileSync(join(fixtures, `${id}-2026-11-21.json`), "utf8");
const BRIEFINGS = ["q3k4u3vqh8", "sw3c524d23", "wbhnb95p3u"];

/** The stream of a briefing part-way through being written: the question read, the records found, some text and
 * its citations, and no final event, so the page stays in the wait. */
function partway() {
  const b = JSON.parse(briefing("q3k4u3vqh8"));
  const index = new Map(b.sources.map((s, i) => [s.n, i]));
  const events = [{ type: "progress", step: "reading", passages: 100 }, { type: "plan", plan: b.plan },
    { type: "sources", sources: b.sources, candidates: b.candidates, semantic: b.semantic, evidence: b.evidence }];
  for (const seg of b.answer.segments.slice(0, 8)) {
    for (const n of seg.sources) events.push({ type: "citation", search_result_index: index.get(n), cited_text: "" });
    events.push({ type: "text", text: seg.text });
  }
  return events.map((e) => `event: ${e.type}\ndata: ${JSON.stringify(e)}\n\n`).join("");
}

const browser = await chromium.launch();
let failures = 0;
async function check(page, label) {
  await page.addScriptTag({ content: axe });
  const result = await page.evaluate(async () =>
    window.axe.run(document, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"] } }));
  for (const v of result.violations.filter((v) => v.impact === "serious" || v.impact === "critical")) {
    failures++;
    console.log(`${label}: ${v.id} (${v.impact}) ${v.help}`);
    for (const n of v.nodes.slice(0, 3)) console.log(`    ${n.target.join(" ")}`);
  }
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  if (overflow > 1) {
    failures++;
    console.log(`${label}: the page scrolls sideways by ${overflow}px`);
  }
}
for (const scheme of ["light", "dark"]) {
  for (const width of [1280, 390]) {
    const ctx = await browser.newContext({ viewport: { width, height: 1000 }, colorScheme: scheme, bypassCSP: true });
    const page = await ctx.newPage();
    for (const path of pages) {
      await page.goto(base + path, { waitUntil: "networkidle" }).catch(() => {});
      await check(page, `${scheme} ${width}px ${path}`);
    }
    // checked briefings, with each kind of mark: the record beside the briefing, or the sheet open on a phone
    await page.route("**/api/briefings/*", (route) => {
      const id = new URL(route.request().url()).pathname.split("/").pop();
      return BRIEFINGS.includes(id) ? route.fulfill({ contentType: "application/json", body: briefing(id) }) : route.continue();
    });
    for (const id of BRIEFINGS) {
      await page.goto(`${base}/b/${id}`, { waitUntil: "networkidle" }).catch(() => {});
      await page.locator(".wts-check").waitFor({ timeout: 15000 }).catch(() => {});
      if (!(await page.locator(".wts-check").count())) {
        failures++;
        console.log(`${scheme} ${width}px saved briefing ${id}: the briefing was not shown`);
      }
      if (width < 1024) {
        await page.locator(".answer-body .wts-chip").first().click().catch(() => {});
        await page.locator(".wts-sheet").waitFor({ timeout: 5000 }).catch(() => {});
      }
      await check(page, `${scheme} ${width}px saved briefing ${id}${width < 1024 ? " with its sheet open" : ""}`);
    }
    // the wait, part-way through writing
    await page.route("**/api/ask", (route) => route.fulfill({ contentType: "text/event-stream", body: partway() }));
    await page.goto(base + "/", { waitUntil: "networkidle" }).catch(() => {});
    await page.fill("#q", "What positions have MPs and peers taken on the cost of living?").catch(() => {});
    await page.keyboard.press("Enter");
    await page.locator(".wts-pile").first().waitFor({ timeout: 15000 }).catch(() => {});
    await page.waitForTimeout(1500);
    if (!(await page.locator(".wts-stages").count())) {
      failures++;
      console.log(`${scheme} ${width}px the wait: its stages were not shown`);
    }
    await check(page, `${scheme} ${width}px the wait`);
    await ctx.close();
  }
}
await browser.close();
console.log(failures ? `${failures} accessibility problem(s) found`
  : `No serious accessibility problems on ${pages.length} pages, ${BRIEFINGS.length} saved briefings and the wait`);
process.exit(failures ? 1 : 0);
