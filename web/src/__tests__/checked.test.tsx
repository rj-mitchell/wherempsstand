/**
 * A checked briefing as the family shows it (the charity's family/BRIEFINGS.md): the strip's counts, the record's
 * own words, chips, the marks for text with no source, the record beside the briefing and the bottom sheet, and the
 * wait. The briefings are fixed copies of real ones (web/src/__tests__/fixtures/briefings/, taken on 21 November 2026), and the
 * strip's counts are held to the same counts.json as wts-core's check_counts on the server (tests/test_checked.py).
 */
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import counts from "./fixtures/briefings/counts.json";
import veto from "./fixtures/briefings/kGfKBI3cRLw-shown-2026-11-21.json";
import cost from "./fixtures/briefings/q3k4u3vqh8-2026-11-21.json";
import immigration from "./fixtures/briefings/sw3c524d23-2026-11-21.json";
import councils from "./fixtures/briefings/wbhnb95p3u-2026-11-21.json";
import { checkCounts, chipLabel, countsInWords, foundSummary, planWords, quotations } from "../checked";
import { AnswerView } from "../components/AnswerView";
import { announcement, Stages } from "../components/Wait";
import { renderBriefing } from "../render";
import type { Answer, SourceCard, Turn } from "../types";
import { withInterests } from "../types";

type Saved = { answer: Answer; sources: SourceCard[]; question: string; created_at: string; interests?: never[] };
const BRIEFINGS = { q3k4u3vqh8: cost, sw3c524d23: immigration, wbhnb95p3u: councils } as unknown as Record<string, Saved>;
const ITALY = "supports all initiatives that seek to impose self-limitations on the use of veto";

const turnOf = (b: Saved, extra: Partial<Turn> = {}): Turn => ({
  id: "t1",
  question: b.question,
  settings: {} as Turn["settings"],
  sources: withInterests(b.sources, b.interests),
  streamText: "",
  answer: b.answer,
  status: "done",
  preparedAt: b.created_at,
  ...extra,
});

const html = (b: Saved) => {
  const by = new Map(b.sources.map((s) => [s.n, s]));
  const el = document.createElement("div");
  el.innerHTML = renderBriefing(b.answer.segments, { chip: (n) => (by.has(n) ? chipLabel(by.get(n)!) : null) });
  return el;
};

function wideScreen(wide: boolean) {
  window.matchMedia = ((q: string) => ({
    matches: q.includes("min-width: 64rem") ? wide : false,
    media: q,
    addEventListener: () => {},
    removeEventListener: () => {},
  })) as unknown as typeof window.matchMedia;
}

afterEach(() => {
  delete (window as { matchMedia?: unknown }).matchMedia;
  vi.useRealTimers();
});

describe("the check strip's counts", () => {
  it.each(Object.keys(BRIEFINGS))("are wts-core's check_counts on the answer as shown (%s)", (name) => {
    expect(checkCounts(BRIEFINGS[name].answer)).toEqual((counts as Record<string, unknown>)[name]);
  });

  it("count a quotation shown beside its citation as the verifier did, and the sentences as shown", () => {
    expect(checkCounts(veto.answer as unknown as Answer)).toEqual(counts.kGfKBI3cRLw);
  });

  it("are said in words", () => {
    expect(countsInWords(counts.wbhnb95p3u)).toBe(
      "19 of 19 citations matched to the record, 15 of 15 quotations found word for word, 2 sentences without a source",
    );
  });
});

describe("the display rule, in the browser", () => {
  it("never marks a quotation found word for word as having no source", () => {
    const el = document.createElement("div");
    el.innerHTML = renderBriefing((veto.answer as unknown as Answer).segments);
    const quote = [...el.querySelectorAll(".wts-quote--checked")].find((q) => q.textContent?.includes(ITALY))!;
    expect(quote).toBeTruthy();
    expect(quote.closest(".wts-nosource")).toBeNull();
    expect(quote.querySelector(".wts-vh")?.textContent).toBe(" (quotation, found word for word in source 1)");
    // its chip comes straight after it; the words after it, cited to the same source, do not repeat it
    expect(quote.nextElementSibling?.matches('.wts-chip[data-source="1"]')).toBe(true);
    expect(quote.nextElementSibling?.nextElementSibling?.matches(".wts-chip")).toBe(false);
    // the words before it keep their mark, the first with "No source"
    const first = el.querySelector(".wts-nosource")!;
    expect(first.textContent).toContain("Italy said it");
    expect(first.querySelector(".wts-nosource__tag")?.textContent).toBe("No source");
  });
});

describe("the marks", () => {
  it("each carry their text equivalent, and the tag is on the first passage with no source only", () => {
    const el = html(BRIEFINGS.wbhnb95p3u);
    const none = [...el.querySelectorAll(".wts-nosource")];
    expect(none.length).toBeGreaterThanOrEqual(2);
    none.forEach((n) => expect(n.querySelector(":scope > .wts-vh")?.textContent).toBe(" (no source)"));
    expect(el.querySelectorAll(".wts-nosource__tag")).toHaveLength(1);
    expect(none[0].querySelector(".wts-nosource__tag")).not.toBeNull();
    const checked = el.querySelectorAll(".wts-quote--checked");
    expect(checked).toHaveLength(counts.wbhnb95p3u.quotes_found);
    checked.forEach((q) => {
      expect(q.querySelector(".wts-quote__tick")?.textContent).toBe("✓ word for word");
      expect(q.querySelector(".wts-vh")?.textContent).toMatch(/^ \(quotation, found word for word in sources? [\d, and]+\)$/);
    });
  });

  it("show quotations not found word for word, in words", () => {
    const el = html(BRIEFINGS.sw3c524d23);
    const missing = el.querySelectorAll(".wts-quote--missing");
    expect(missing).toHaveLength(2);
    missing.forEach((q) => {
      expect(q.querySelector(".wts-quote__tick")?.textContent).toBe("Not found word for word");
      expect(q.querySelector(".wts-vh")?.textContent).toMatch(/^ \(quotation not found word for word in source \d+\)$/);
    });
    expect(el.querySelectorAll(".wts-quote--checked")).toHaveLength(counts.sw3c524d23.quotes_found);
  });

  it("say when a passage's every citation failed and was removed", () => {
    const el = document.createElement("div");
    el.innerHTML = renderBriefing([{ text: "A claim its source did not bear out.", sources: [], cited: [], status: "invalid" }]);
    expect(el.querySelector(".wts-nosource .wts-vh")?.textContent).toBe(
      " (its citation did not match the source and was removed)",
    );
  });

  it("name each chip by its visible words: who, where and when", () => {
    const el = html(BRIEFINGS.q3k4u3vqh8);
    const chip = el.querySelector<HTMLButtonElement>('.wts-chip[data-source="3"]')!;
    expect(chip.getAttribute("aria-label")).toBeNull();
    expect(chip.textContent).toBe("Source 3 Emma Reynolds, Commons, 3 November 2026");
    expect(chip.querySelector(".wts-chip__n")?.textContent).toBe("Source 3");
  });

  it("mark a speaker's registered interest on the chip in words", () => {
    const b = BRIEFINGS.wbhnb95p3u;
    const { container } = render(<AnswerView turn={turnOf(b)} onCite={vi.fn()} />);
    const flagged = container.querySelectorAll(".wts-chip .chip-interest");
    expect(flagged.length).toBeGreaterThan(0);
    expect(flagged[0].textContent).toBe("Registered interest that may relate");
  });

  it("find every quotation the verifier counted in a briefing with none missing", () => {
    expect(quotations(BRIEFINGS.q3k4u3vqh8.answer.segments).filter((q) => q.found)).toHaveLength(19);
  });
});

describe("a finished briefing", () => {
  it("opens with the strip, says what to check and links to it, and always shows text with no source", () => {
    const { container } = render(<AnswerView turn={turnOf(BRIEFINGS.wbhnb95p3u)} onCite={vi.fn()} />);
    const strip = screen.getByRole("region", { name: "Checked against Hansard" });
    expect(within(strip).getByRole("link", { name: "How it was checked" }).getAttribute("href")).toMatch(/details$/);
    expect(within(strip).getByText("To check")).toBeInTheDocument();
    const first = within(strip).getByRole("link", { name: "Show the first" });
    expect(container.querySelector(first.getAttribute("href")!)?.classList.contains("wts-nosource")).toBe(true);
    expect(screen.queryByRole("button", { name: /Show (them|it)/ })).toBeNull(); // never hidden behind a button
    expect(screen.getByRole("heading", { name: "How this briefing was checked" })).toBeInTheDocument();
    expect(screen.getByText(/2 statements are not tied to a cited source/)).toBeInTheDocument();
  });

  it("on a narrower screen, opens a chip's passage in a sheet that holds focus and gives it back", () => {
    wideScreen(false);
    render(<AnswerView turn={turnOf(BRIEFINGS.q3k4u3vqh8)} onCite={vi.fn()} />);
    const chip = screen.getAllByRole("button", { name: "Source 3 Emma Reynolds, Commons, 3 November 2026" })[0];
    chip.focus();
    fireEvent.click(chip);
    const sheet = screen.getByRole("dialog", { name: "Source 3: the record" });
    expect(sheet.getAttribute("aria-modal")).toBe("true");
    expect(document.activeElement).toBe(within(sheet).getByRole("heading"));
    expect(sheet.querySelector("mark.wts-now")?.textContent).toBe("Those are the global headwinds we are facing");
    expect(sheet.querySelectorAll("mark").length).toBeGreaterThan(1); // every run quoted from it, highlighted
    expect(within(sheet).getByText("House of Commons")).toBeInTheDocument();
    expect(within(sheet).getByText("3 November 2026, column 825")).toBeInTheDocument();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.activeElement).toBe(chip);
  });

  it("on a wide screen, shows the record beside it from the start, following the chip chosen", () => {
    wideScreen(true);
    const b = BRIEFINGS.q3k4u3vqh8;
    render(<AnswerView turn={turnOf(b)} onCite={vi.fn()} />);
    const panel = screen.getByRole("complementary", { name: "The record beside the briefing" });
    const cited = new Set(b.answer.segments.filter((s) => s.status === "cited").flatMap((s) => s.sources)).size;
    // open on the first source the text cites (source 3, the third in the source list of those cited)
    const order = [...new Set(b.answer.segments.filter((s) => s.status === "cited").flatMap((s) => s.sources))].sort((x, y) => x - y);
    expect(within(panel).getByText(`Source 3, ${order.indexOf(3) + 1} of ${cited}`)).toBeInTheDocument();
    expect(panel.querySelector("mark.wts-now")?.textContent).toBe("Those are the global headwinds we are facing");
    fireEvent.click(within(panel).getByRole("button", { name: "Previous source" }));
    fireEvent.click(screen.getAllByRole("button", { name: /^Source 3 Emma Reynolds/ })[0]);
    expect(within(panel).getByText(/^Source 3, \d+ of/)).toBeInTheDocument();
    expect(panel.querySelector("mark.wts-now")?.textContent).toBe("Those are the global headwinds we are facing");
    fireEvent.click(within(panel).getByRole("button", { name: "Next source" }));
    expect(within(panel).queryByText(/^Source 3, /)).toBeNull();
  });
});

describe("the wait", () => {
  const b = BRIEFINGS.q3k4u3vqh8;

  it("names its stages, the one under way marked Now; sources only has the first two", () => {
    const { rerender } = render(<Stages at={1} />);
    const items = screen.getAllByRole("listitem");
    expect(items.map((li) => li.textContent)).toEqual([
      "✓ DoneReading the question",
      "NowSearching the record",
      "NextWriting from these records only",
      "NextChecking every citation and quotation",
    ]);
    rerender(<Stages at={1} sourcesOnly />);
    expect(screen.getAllByRole("listitem")).toHaveLength(2);
  });

  it("announces each stage once, and the result in words at the end", () => {
    const base = turnOf(b, { answer: undefined });
    expect(announcement({ ...base, status: "planning", sources: [] })).toBe("Reading the question.");
    expect(announcement({ ...base, status: "searching", sources: [] })).toBe("Searching the record.");
    expect(announcement({ ...base, status: "answering" })).toBe(
      `Found ${foundSummary(b.sources)}. Writing the briefing from these records only.`,
    );
    expect(announcement({ ...base, status: "verifying" })).toBe("Checking every citation and quotation.");
    expect(announcement(turnOf(b))).toBe(
      "Briefing ready. Checked against Hansard: 19 of 19 citations matched to the record, 19 of 19 quotations found " +
        "word for word, 0 sentences without a source.",
    );
  });

  it("sums up the records found as the record holds them", () => {
    expect(foundSummary(b.sources)).toMatch(/^34 records from \d+ speakers: \d+ from Hansard, .* and \d+ from committee evidence$/);
  });

  it("reads the question as large chips", () => {
    const plan = (cost as unknown as { plan: NonNullable<Turn["plan"]> }).plan;
    expect(planWords(plan, null)).toEqual(["Commons and Lords", "since 17 November 2025", "all parties"]);
  });

  it("deals the records found into a pile of Hansard columns, marking each one the text cites", () => {
    const writing = turnOf(b, { answer: undefined, status: "answering", streamText: "Ministers said", cited: [3], live: true });
    const { container } = render(<AnswerView turn={writing} onCite={vi.fn()} />);
    const cards = container.querySelectorAll(".wts-card");
    expect(cards).toHaveLength(34);
    const three = container.querySelector('.wts-card[data-n="3"]')!;
    expect(three.classList.contains("is-cited")).toBe(true);
    expect(three.querySelector(".wts-card__top")?.textContent).toBe("Commons, 3 November 2026col. 825");
    expect(three.querySelector(".wts-card__who")?.textContent).toBe("Emma Reynolds Cited");
    expect(screen.getByText(/Writing from these 34 records only. The AI may use these and nothing else./)).toBeInTheDocument();
    expect(screen.getByRole("list", { name: "Preparing the briefing" })).toBeInTheDocument();
  });

  it("runs the strip's counts up through the real results when the briefing ends, then settles", () => {
    vi.useFakeTimers();
    wideScreen(true);
    const { container } = render(<AnswerView turn={turnOf(b, { live: true })} onCite={vi.fn()} />);
    const strip = screen.getByRole("region", { name: "Checked against Hansard" });
    expect(strip.querySelector(".wts-check__list")?.getAttribute("aria-hidden")).toBe("true");
    expect(within(strip).getByText("sentences without a source: counted last")).toBeInTheDocument();
    expect(container.querySelector(".wts-card")).not.toBeNull(); // the pile, still beside the text
    act(() => {
      vi.advanceTimersByTime(5000);
    });
    expect(strip.querySelector(".wts-check__list")?.getAttribute("aria-hidden")).toBeNull();
    expect([...strip.querySelectorAll(".wts-check__num")].map((n) => n.textContent)).toEqual(["19", "19", "0"]);
    expect(container.querySelectorAll(".wts-quote--checked.is-checked")).toHaveLength(19);
    expect(container.querySelector(".wts-card")).toBeNull();
    expect(container.querySelector(".wts-record")).not.toBeNull();
    expect(screen.queryByRole("list", { name: "Preparing the briefing" })).toBeNull();
  });
});
