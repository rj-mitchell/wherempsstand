import { describe, expect, it } from "vitest";
import { ApiError } from "../api";
import { briefingGone } from "../gone";

describe("a saved briefing's address, when the briefing can't be shown", () => {
  it("says a withdrawn briefing was withdrawn", () => {
    expect(briefingGone(new ApiError(410, "This briefing has been withdrawn."))).toEqual({
      question: "Briefing withdrawn",
      error: "This briefing has been withdrawn.",
    });
  });

  it("shows the owner's note in place of a briefing hidden while a concern is looked into", () => {
    const note = "This briefing is not shown while a concern about it is looked into.";
    expect(briefingGone(new ApiError(423, note))).toEqual({ question: "Briefing not shown for now", error: note });
    expect(briefingGone(new ApiError(423, "")).error).toBe("This briefing is not shown at the moment.");
  });

  it("says there is no briefing for anything else", () => {
    for (const e of [new ApiError(404, "There is no briefing at this address."), new Error("offline")])
      expect(briefingGone(e)).toEqual({
        question: "Briefing not found",
        error: "There is no briefing at this address. It may have been mistyped.",
      });
  });
});
