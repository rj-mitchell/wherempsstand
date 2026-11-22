import { describe, expect, it } from "vitest";
import { parseChunk } from "../api";
import { demoteHeadings, highlightParts, renderAnswer, segmentsToMarkdown, shorten, typographic } from "../render";
import type { Segment } from "../types";

const seg = (text: string, status: Segment["status"], sources: number[] = []): Segment => ({
  text,
  status,
  sources,
  cited: sources.map((n) => ({ source: n, cited_text: "x" })),
});

describe("answer rendering", () => {
  it("turns citations into chips that name their sources, numbered for printing", () => {
    const html = renderAnswer(segmentsToMarkdown([seg("Ministers cited costs", "cited", [1, 3]), seg(".", "connective")]), {
      chip: (n) => (n === 3 ? "Emma Reynolds, Commons, 3 November 2026" : null),
    });
    expect(html).toContain('<button type="button" class="wts-chip" data-source="1" data-seg="0" data-wts-conversion="source_opened">');
    expect(html).toContain('<span class="wts-chip__n"><span class="wts-vh">Source</span> 3</span> <span>Emma Reynolds, Commons, 3 November 2026</span>');
    expect(html).not.toContain("aria-label");
    expect(html).not.toContain("⟦");
  });

  it("marks text with no source, always, the first with the tag", () => {
    const html = renderAnswer(segmentsToMarkdown([seg("An uncited claim about 2023.", "unsupported"), seg(" Another one.", "unsupported")]));
    expect(html).toContain('<span class="wts-nosource" id="nosource-0">An uncited claim about 2023.<span class="wts-nosource__tag">No source</span><span class="wts-vh"> (no source)</span></span>');
    expect(html).toContain('<span class="wts-nosource" id="nosource-1">Another one.<span class="wts-vh"> (no source)</span></span>');
  });

  it("keeps the space after a passage with no source outside its mark, so the tag never touches the next word", () => {
    const html = renderAnswer(
      segmentsToMarkdown([seg("The motion fell to a veto ", "unsupported"), seg("in November", "cited", [2]), seg(".", "connective")]),
    );
    expect(html).toContain('<span class="wts-vh"> (no source)</span></span> in November');
    const tabbed = renderAnswer(segmentsToMarkdown([seg("Said nothing\t", "unsupported"), seg("then spoke.", "connective")]));
    expect(tabbed).toMatch(/\(no source\)<\/span><\/span>\s+then spoke/);
  });

  it("marks a sentence whose citations all failed the check as it marks an unsupported one, saying why", () => {
    const html = renderAnswer(segmentsToMarkdown([seg("A claim its source did not bear out.", "invalid")]));
    expect(html).toContain('<span class="wts-nosource" id="nosource-0">A claim its source did not bear out.');
    expect(html).toContain("(its citation did not match the source and was removed)");
    expect(html).not.toContain("wts-chip");
  });

  it("keeps a list's marker outside the mark of a passage with no source", () => {
    const html = renderAnswer(segmentsToMarkdown([seg("- A first point with no source\n- A second", "unsupported")]));
    expect(html.match(/<li>/g)).toHaveLength(2);
    expect(html.match(/class="wts-nosource"/g)).toHaveLength(2);
  });

  it("marks quotations not found in their sources, the record's own words, and loaded words, in the same segment", () => {
    const text = 'He said "we will never do that" and called it a disgrace, "a matter for the whole House".';
    const s: Segment = {
      text,
      sources: [1],
      cited: [],
      status: "cited",
      misquotes: [{ start: 8, end: 31, text: "we will never do that" }],
      loaded: [{ start: 49, end: 57, word: "disgrace" }],
    };
    const html = renderAnswer(segmentsToMarkdown([s]));
    expect(html).toContain('<span class="wts-quote wts-quote--missing" id="misquote-0" data-seg="0">\u201cwe will never do that\u201d');
    expect(html).toContain("Not found word for word</span><span class=\"wts-vh\"> (quotation not found word for word in source 1)</span>");
    expect(html).toContain('<span class="wts-quote wts-quote--checked" data-seg="0" data-source="1">\u201ca matter for the whole House\u201d');
    expect(html).toContain("(quotation, found word for word in source 1)");
    expect(html).toContain('<span class="loaded"');
  });

  it("sanitises model output", () => {
    const html = renderAnswer(segmentsToMarkdown([seg('<img src=x onerror="alert(1)"> text', "cited", [1])]));
    expect(html).not.toContain("onerror");
  });

  it("keeps markdown lists intact around citations", () => {
    const md = segmentsToMarkdown([seg("- First point", "cited", [1]), seg("\n- Second point", "cited", [2])]);
    const html = renderAnswer(md);
    expect(html.match(/<li>/g)?.length).toBe(2);
  });
});

describe("highlighting", () => {
  it("marks exact cited quotes in the verbatim text", () => {
    const parts = highlightParts("The line to Crewe is delayed. Costs rose.", ["Costs rose."]);
    expect(parts).toEqual([
      { text: "The line to Crewe is delayed. ", hit: false },
      { text: "Costs rose.", hit: true },
    ]);
  });
});

describe("server-sent events", () => {
  it("parses data lines", () => {
    const evs = [...parseChunk('event: text\ndata: {"type":"text","text":"Hi"}')];
    expect(evs).toEqual([{ type: "text", text: "Hi" }]);
  });
});

describe("people", () => {
  it("attributes speakers the way Hansard prints them", async () => {
    const { attribution, seatTypeLabel, partyAbbr, houseOf } = await import("../people");
    const sp = {
      person_id: "p",
      date: "2024-01-01",
      name: "Hilary Benn",
      house: "commons",
      constituency: "Leeds Central",
      region: "Yorkshire and The Humber",
      party: "Labour",
      settlement: "city",
      urban_area: "West Yorkshire",
      urban_area_pop: 1_864_516,
      offices: [],
      other_names: [],
    };
    expect(attribution(sp)).toEqual({ name: "Hilary Benn", tail: "(Leeds Central) (Lab)" });
    expect(seatTypeLabel(sp)).toBe("city seat (West Yorkshire urban area, 1.9m people)");
    expect(seatTypeLabel({ ...sp, settlement: "town", urban_area_pop: 172_345 })).toBe("town seat (urban area of 172,000)");
    expect(partyAbbr("Labour/Co-operative")).toBe("Lab/Co-op");
    expect(houseOf({ chamber: "Lords written answers", speaker: null })).toBe("lords");
    expect(houseOf({ chamber: "Westminster Hall", speaker: null })).toBe("commons");
  });
});

describe("references", () => {
  const base = {
    n: 3, kind: "record" as const, title: "t", url: "u", date: "2023-03-09", text: "", twfy_url: "tw",
  };
  it("formats Hansard-style references", async () => {
    const { formalReference } = await import("../export");
    expect(formalReference({ ...base, chamber: "House of Commons", colnum: "412" })).toBe("HC Deb 9 March 2023, col 412");
    expect(formalReference({ ...base, chamber: "House of Lords", colnum: "1050" })).toBe("HL Deb 9 March 2023, col 1050");
    expect(formalReference({ ...base, chamber: "Westminster Hall", colnum: "120WH" })).toBe(
      "HC Deb 9 March 2023, col 120WH (Westminster Hall)");
    expect(formalReference({ ...base, chamber: "Written answers", record_type: "answer", uin: "12345" })).toBe(
      "Written question 12345, answered 9 March 2023");
  });
});

describe("demoteHeadings", () => {
  it("starts a briefing's headings below the question", () => {
    expect(demoteHeadings("<h1 id=x>T</h1><h2>S</h2><h5>Deep</h5>")).toBe("<h3 id=x>T</h3><h4>S</h4><h6>Deep</h6>");
    expect(demoteHeadings("<header>ok</header>")).toBe("<header>ok</header>");
  });
});

describe("stageQuestion", () => {
  it("asks about one sitting of a bill, naming the stage, House and date", async () => {
    const { stageQuestion } = await import("../stage");
    expect(stageQuestion("Rail Bill", { date: "2024-03-05", house: "commons", stages: ["Second reading"] })).toBe(
      "What arguments were made at the second reading of the Rail Bill in the House of Commons on 5 March 2024?",
    );
    expect(stageQuestion("Rail Bill", { date: "2024-03-05", house: "lords", stages: ["Debate"] })).toBe(
      "What arguments were made on the Rail Bill in the House of Lords on 5 March 2024?",
    );
    expect(stageQuestion("Rail Bill", { date: "2024-03-05", house: "commons", stages: ["Public Bill Committee"] })).toContain(
      "at the Public Bill Committee of",
    );
  });
});

describe("links to other sites", () => {
  it("builds searches with the words and dates", async () => {
    const { ELSEWHERE } = await import("../elsewhere");
    expect(ELSEWHERE.hansard("onshore wind", "2020-01-01", "2024-12-31")).toBe(
      "https://hansard.parliament.uk/search/Contributions?searchTerm=onshore%20wind&startDate=2020-01-01&endDate=2024-12-31",
    );
    expect(ELSEWHERE.hansard("wind")).toBe("https://hansard.parliament.uk/search/Contributions?searchTerm=wind");
    expect(ELSEWHERE.twfyPerson(10777, "lords")).toBe("https://www.theyworkforyou.com/peer/?p=10777");
    expect(ELSEWHERE.writeToThem("SW1A 0AA")).toBe("https://www.writetothem.com/?pc=SW1A%200AA");
  });
});

describe("typographic quotes", () => {
  it("turns typewriter quotes and apostrophes into printers' ones, in text only", () => {
    expect(typographic('<p>She said "it\'s too high" and the Lords\' view.</p>')).toBe(
      "<p>She said “it’s too high” and the Lords’ view.</p>",
    );
    expect(typographic('<p>&quot;Quoted&quot;<button class="wts-chip" data-source="2">2</button>.</p>')).toBe(
      '<p>“Quoted”<button class="wts-chip" data-source="2">2</button>.</p>',
    );
    expect(typographic('<p>"One"</p><p>"Two"</p>')).toBe("<p>“One”</p><p>“Two”</p>");
  });
});

describe("group labels", () => {
  it("stand on their own line above the text that follows", () => {
    const html = renderAnswer("**Committee witnesses - supportive**\nDr Price-Forbes said it works.");
    expect(html).toMatch(/<p><strong>Committee witnesses - supportive<\/strong><\/p>\s*<p>Dr Price-Forbes/);
  });
  it("leave a bold phrase inside a sentence alone", () => {
    expect(renderAnswer("A **bold** word.\nNext line.")).toContain("<strong>bold</strong> word.");
  });
});

describe("register field names", () => {
  it("are shown in sentence case, keeping abbreviations", async () => {
    const { sentenceCase } = await import("../render");
    expect(sentenceCase("Regularity Of Payment")).toBe("Regularity of payment");
    expect(sentenceCase("Is Sole Beneficiary")).toBe("Is sole beneficiary");
    expect(sentenceCase("Donor Other Details")).toBe("Donor other details");
    expect(sentenceCase("Value")).toBe("Value");
    expect(sentenceCase("Received Date In UK")).toBe("Received date in UK");
  });
});

describe("shorten", () => {
  it("cuts a long title at a word, as the server does", () => {
    expect(shorten("Short title", 60)).toBe("Short title");
    const long = "All-Party Parliamentary Group on Something Very Long Indeed, And Even Longer Than Sixty";
    const cut = shorten(long, 60);
    expect(cut.length).toBeLessThanOrEqual(60);
    expect(cut).toBe("All-Party Parliamentary Group on Something Very Long Indeed…");
  });
});
