/**
 * Measurement for Google Ad Grants (docs/DECISIONS.md, 50): the marks the family kit's measure.js reads, which are on
 * the pages whether the switch is on or off and do nothing without the script, and "Measurement choices" in the
 * footer, drawn only when the server has put measure.js on the page. The server's side is tests/test_measure.py.
 */
import { fireEvent, render, screen } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import cost from "./fixtures/briefings/q3k4u3vqh8-2026-11-21.json";
import { api } from "../api";
import { AnswerView } from "../components/AnswerView";
import { ReportProblem } from "../components/ReportProblem";
import { SiteFooter } from "../components/SiteFooter";
import { measuring, reportSent } from "../measure";
import type { Answer, SourceCard, Turn } from "../types";

const web = resolve(__dirname, "../..");
const b = cost as unknown as { answer: Answer; sources: SourceCard[]; question: string; created_at: string };
const turn: Turn = {
  id: "t1",
  question: b.question,
  settings: {} as Turn["settings"],
  sources: b.sources,
  streamText: "",
  answer: b.answer,
  status: "done",
  preparedAt: b.created_at,
  briefingId: "q3k4u3vqh8",
};

function withMeasureScript() {
  const s = document.createElement("script");
  s.setAttribute("src", "/family/measure.js");
  s.setAttribute("data-tag", "AW-123456789");
  document.head.append(s);
  return s;
}

afterEach(() => {
  document.head.querySelectorAll('script[src="/family/measure.js"]').forEach((s) => s.remove());
  delete window.wtsMeasure;
});

describe("the footer", () => {
  it("has no Measurement choices while the page does not measure, as the site ships", () => {
    expect(measuring()).toBe(false);
    const { container } = render(<SiteFooter />);
    expect(container.textContent).not.toContain("Measurement choices");
    expect(container.querySelector("[data-wts-measure-open]")).toBeNull();
  });

  it("has Measurement choices on a line of its own after the notices while it does, and the six links stay six", () => {
    withMeasureScript();
    expect(measuring()).toBe(true);
    const { container } = render(<SiteFooter />);
    const line = container.querySelector(".footer > p.measure-choices");
    expect(line?.previousElementSibling?.matches("p.licence")).toBe(true);
    const link = line?.querySelector("a");
    expect(link?.getAttribute("href")).toBe("/privacy#measurement");
    expect(link?.hasAttribute("data-wts-measure-open")).toBe(true);
    expect(link?.textContent).toBe("Measurement choices");
    expect(container.querySelectorAll(".footer-nav a")).toHaveLength(6);
  });
});

describe("the marks for each conversion", () => {
  it("mark a finished briefing's end with its id, its chips, its source cards and its downloads", () => {
    const { container } = render(<AnswerView turn={turn} onCite={vi.fn()} />);
    const end = container.querySelectorAll('[data-wts-conversion="briefing_read"]');
    expect(end).toHaveLength(1);
    expect(end[0].getAttribute("data-wts-briefing")).toBe("q3k4u3vqh8");
    expect(end[0].matches("p.ai-note")).toBe(true);
    const chips = container.querySelectorAll(".wts-chip");
    expect(chips.length).toBeGreaterThan(0);
    for (const chip of chips) expect(chip.getAttribute("data-wts-conversion")).toBe("source_opened");
    const downloads = [...container.querySelectorAll('[data-wts-conversion="download"]')].map((e) => e.textContent);
    expect(downloads).toEqual(["Download Word", "Print or save as PDF"]);
  });

  it("do not mark a briefing still being written", () => {
    const { container } = render(
      <AnswerView turn={{ ...turn, answer: undefined, status: "answering", streamText: "Some text" }} onCite={vi.fn()} />,
    );
    expect(container.querySelector('[data-wts-conversion="briefing_read"]')).toBeNull();
  });

  it("mark the links to the record in the source's slip", () => {
    const { container } = render(<AnswerView turn={turn} onCite={vi.fn()} />);
    fireEvent.click(container.querySelector(".wts-chip")!);
    const record = [...document.querySelectorAll(".wts-slip__foot a")];
    expect(record.length).toBeGreaterThan(0);
    for (const a of record) expect(a.getAttribute("data-wts-conversion")).toBe("source_opened");
  });

  it("mark the site's own Donate links, and the kit marks the shelf's", () => {
    expect(readFileSync(join(web, "about.html"), "utf8")).toContain(
      '<a data-donate href="#" data-wts-conversion="donate_click">support the site</a>',
    );
    expect(readFileSync(join(web, "src/components/SupportReminder.tsx"), "utf8")).toContain(
      'data-wts-conversion="donate_click"',
    );
    expect(readFileSync(join(web, "public/family/shelf.html"), "utf8")).toContain(
      'href="https://wheretheystand.uk/donate" data-wts-conversion="donate_click">Donate</a>',
    );
  });

  it("say a report was sent only once it was, and nothing without the kit's script", async () => {
    expect(() => reportSent()).not.toThrow();
    const conversion = vi.fn(() => true);
    window.wtsMeasure = { conversion, open: vi.fn(), choice: () => null };
    const sent = vi.spyOn(api, "report").mockResolvedValue({ ok: true } as never);
    render(<ReportProblem briefingId="q3k4u3vqh8" />);
    fireEvent.click(screen.getByText("Report a problem"));
    fireEvent.change(screen.getByLabelText("Details"), { target: { value: "The date in the second paragraph is wrong." } });
    expect(conversion).not.toHaveBeenCalled();
    fireEvent.submit(document.querySelector("form.report-form")!);
    await screen.findByRole("status");
    expect(sent).toHaveBeenCalledOnce();
    expect(conversion).toHaveBeenCalledWith("report_sent");
    sent.mockRestore();
  });

  it("never mark a hidden thank-you message, which the kit would count when the page loads", () => {
    for (const page of ["corrections.html", "named.html"]) {
      expect(readFileSync(join(web, page), "utf8"), page).not.toContain('data-wts-conversion="report_sent"');
    }
  });
});
