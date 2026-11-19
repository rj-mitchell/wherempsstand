/** The bills page: every bill debated since 1997, newest first, filtered as you type; and the same for one year's
 * bills (/bills/year/2024), with links to every year, so each bill can be reached from here. */
import "./pages";
import { fmtDate, keepTogether } from "./render";
import { listFailed } from "./serverText";

interface BillRow {
  slug: string;
  name: string;
  first: string;
  last: string;
  houses: string[];
  sittings: number;
  divisions: number;
  latest: string;
}

const LIMIT = 100;
// an archive page shows one year's bills, all of them
const YEAR = window.location.pathname.match(/^\/bills\/year\/(\d{4})\/?$/)?.[1] ?? null;

/** The years bills were debated in, newest first. */
function billYears(bills: { first: string; last: string }[]): string[] {
  const years = new Set<string>();
  for (const b of bills) for (let y = Number(b.first.slice(0, 4)); y <= Number(b.last.slice(0, 4)); y++) years.add(String(y));
  return [...years].sort().reverse();
}

function yearLinks(years: string[]): HTMLElement {
  const nav = document.createElement("nav");
  nav.setAttribute("aria-label", "Bills by the year they were debated");
  const p = document.createElement("p");
  p.append("Bills by the year they were debated: ");
  years.forEach((y, i) => {
    if (i) p.append(" · ");
    const a = document.createElement("a");
    a.href = `/bills/year/${y}`;
    a.textContent = y;
    if (y === YEAR) a.setAttribute("aria-current", "page");
    p.append(a);
  });
  nav.append(p);
  return nav;
}

function row(b: BillRow): HTMLLIElement {
  const li = document.createElement("li");
  const a = document.createElement("a");
  a.href = `/bills/${b.slug}`;
  a.className = "topic-link";
  a.textContent = b.name;
  const meta = document.createElement("p");
  meta.className = "muted small";
  const years = b.first.slice(0, 4) === b.last.slice(0, 4) ? b.first.slice(0, 4) : `${b.first.slice(0, 4)}–${b.last.slice(0, 4)}`;
  meta.textContent = `${years}, ${b.sittings} sitting${b.sittings === 1 ? "" : "s"}, ${b.divisions} vote${b.divisions === 1 ? "" : "s"}, latest: ${b.latest}, ${keepTogether(fmtDate(b.last))}`;
  li.append(a, meta);
  return li;
}

async function load() {
  const live = document.getElementById("bills-live")!;
  const input = document.getElementById("bills-search") as HTMLInputElement;
  try {
    const resp = await fetch("/api/bills");
    if (!resp.ok) throw new Error(String(resp.status));
    const all = ((await resp.json()) as { bills: BillRow[] }).bills;
    const years = billYears(all);
    // a bill is in a year's list if it was debated that year (the server's archive page lists the same)
    const bills = YEAR ? all.filter((b) => b.first.slice(0, 4) <= YEAR && YEAR <= b.last.slice(0, 4)) : all;
    const limit = YEAR ? bills.length : LIMIT;
    const render = () => {
      const words = input.value.toLowerCase().split(/\s+/).filter(Boolean);
      const hits = bills.filter((b) => words.every((w) => b.name.toLowerCase().includes(w)));
      const ul = document.createElement("ul");
      ul.className = "topic-list";
      for (const b of hits.slice(0, limit)) ul.append(row(b));
      const count = document.createElement("p");
      count.className = "muted small";
      count.textContent = words.length
        ? `${hits.length.toLocaleString("en-GB")} bill${hits.length === 1 ? "" : "s"} found${hits.length > limit ? `; the ${limit} most recent are shown` : ""}.`
        : YEAR
          ? `${bills.length.toLocaleString("en-GB")} bills debated in ${YEAR}.`
          : `${bills.length.toLocaleString("en-GB")} bills. The ${Math.min(LIMIT, bills.length)} most recently debated are shown; search to find others, or choose a year.`;
      live.replaceChildren(count, ul, yearLinks(years));
    };
    input.addEventListener("input", render);
    render();
  } catch {
    listFailed(live, "The list of bills did not load. Reload the page to try again.");
  }
}

void load();
