import { ApiError } from "./api";

/** What a saved briefing's address shows when the briefing itself can't be: it was withdrawn (410), it is hidden
 * while a concern about it is looked into (423: the server gives the note that stands in its place), or there is
 * none at this address. */
export function briefingGone(e: unknown): { question: string; error: string } {
  if (e instanceof ApiError && e.status === 410)
    return { question: "Briefing withdrawn", error: "This briefing has been withdrawn." };
  if (e instanceof ApiError && e.status === 423)
    return { question: "Briefing not shown for now", error: e.message || "This briefing is not shown at the moment." };
  return { question: "Briefing not found", error: "There is no briefing at this address. It may have been mistyped." };
}
