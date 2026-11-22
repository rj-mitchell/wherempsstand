/**
 * Measurement for Google Ad Grants, off as the site ships (docs/DECISIONS.md, 50; the charity's family/MEASUREMENT.md).
 * The switch is the server's ([ads] in config/settings.toml): while it is on, the server adds the family kit's
 * measure.js to every page's head, and the kit's script does the rest. The pages only mark where a conversion happens
 * (data-wts-conversion, inert without the script) and say when a report or statement was sent.
 */

declare global {
  interface Window {
    /** the family kit's measure.js, on the page only while measuring */
    wtsMeasure?: { conversion(name: string): boolean; open(): void; choice(): string | null };
  }
}

/** Whether this page measures: the server put the kit's measure.js in its head. */
export function measuring(): boolean {
  return typeof document !== "undefined" && document.querySelector('script[src="/family/measure.js"][data-tag]') !== null;
}

/** A report or a statement was sent: counted only while measuring, and only if the reader accepted. */
export function reportSent(): void {
  window.wtsMeasure?.conversion("report_sent");
}
