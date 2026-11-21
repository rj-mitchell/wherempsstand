import { describe, expect, it } from "vitest";
import { briefingDocx, crc32, documentXml, downloadName } from "../docx";
import type { Answer, SourceCard } from "../types";

const answer = {
  markdown: "## Labour\n\nMPs **welcomed** the bill [Source 1].\n\n- A point & another <one>",
  segments: [{ text: "", sources: [1], cited: [] }],
  no_records: false,
  citations_total: 1,
  citations_invalid: 0,
  grounded_ratio: 1,
  unsupported_segments: 0,
} as unknown as Answer;
const sources = [
  {
    n: 1,
    kind: "record",
    title: "",
    url: "https://example.org/1",
    date: "2024-01-16",
    chamber: "House of Commons",
    record_type: "speech",
    colnum: "851",
    twfy_url: "https://example.org/t",
    speaker: null,
    speaker_name_raw: "A Member",
    heading: "Rwanda Bill",
    text: "",
  },
] as unknown as SourceCard[];

describe("Word download", () => {
  it("computes the standard CRC-32", () => {
    expect(crc32(new TextEncoder().encode("123456789"))).toBe(0xcbf43926);
  });

  it("writes well-formed document XML with headings, bold, citations and sources", () => {
    const xml = documentXml("What was said?", answer, sources, "https://example.org/b/x", new Date("2026-10-11"));
    const doc = new DOMParser().parseFromString(xml, "application/xml");
    expect(doc.getElementsByTagName("parsererror")).toHaveLength(0);
    expect(xml).toContain('w:val="Heading1"');
    expect(xml).toContain("<w:b/>");
    expect(xml).toContain('w:val="superscript"');
    expect(xml).toContain("A point &amp; another &lt;one&gt;");
    expect(xml).toContain("HC Deb 16 January 2024, col 851");
    // the check summary comes first, after the title
    const summary = "Checked against Hansard: 1 of 1 citations matched to the record, 0 of 0 quotations found word for word, 0 sentences without a source.";
    expect(xml.indexOf(summary)).toBeGreaterThan(0);
    expect(xml.indexOf(summary)).toBeLessThan(xml.indexOf("Written by AI"));
  });

  it("packages a zip that starts with a local file header", () => {
    const bytes = briefingDocx("What was said?", answer, sources, "https://example.org/b/x");
    expect([...bytes.slice(0, 4)]).toEqual([0x50, 0x4b, 0x03, 0x04]);
    expect(new TextDecoder().decode(bytes)).toContain("word/document.xml");
  });

  it("names the file after the question", () => {
    expect(downloadName("What did MPs say about the Rwanda Bill in 2024?")).toBe(
      "what-did-mps-say-about-the-rwanda-bill.docx",
    );
  });
});

describe("embed code", () => {
  it("links the card back to the briefing, in the site's words rather than the reader's question", async () => {
    const { embedCode } = await import("../components/AnswerView");
    const code = embedCode('https://wherempsstand.com/b/abc"<x>');
    expect(code).toContain('<a href="https://wherempsstand.com/b/abc&quot;&lt;x>"');
    expect(code).toContain('src="https://wherempsstand.com/b/abc&quot;&lt;x>/card.png"');
    expect(code).toContain('alt="Where MPs Stand: a briefing written by AI from Hansard"');
    expect(code).toContain("CC BY 4.0");
  });

  it("gives a saved briefing a neutral title for sharing", async () => {
    const { briefingTitle } = await import("../components/AnswerView");
    expect(briefingTitle("2026-11-06T09:00:00+00:00")).toBe("Briefing from Hansard, 6 November 2026");
    expect(briefingTitle()).toBe("Briefing from Hansard");
  });
});
