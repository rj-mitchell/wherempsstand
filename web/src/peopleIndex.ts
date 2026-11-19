/** The MPs and peers page: anyone by name, constituency or party, or an MP by postcode. */
import "./pages";
import { listFailed } from "./serverText";

interface Member {
  number: number;
  name: string;
  slug: string;
  house: string;
  constituency: string | null;
  party: string | null;
  current?: boolean;
  until?: string | null;
}

const href = (m: { number: number; slug: string }) => `/people/${m.number}/${m.slug}`;

// an older link to the postcode search (/people#your-mp) opens it
const byPostcode = document.getElementById("your-mp") as HTMLDetailsElement | null;
if (byPostcode && window.location.hash === "#your-mp") byPostcode.open = true;

const form = document.getElementById("postcode-form") as HTMLFormElement;
form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const err = document.getElementById("postcode-error")!;
  err.hidden = true;
  const pc = (document.getElementById("postcode") as HTMLInputElement).value.trim();
  if (!pc) return;
  try {
    const r = await fetch(`/api/postcode/${encodeURIComponent(pc)}`);
    const body = await r.json();
    if (!r.ok) throw new Error(typeof body.detail === "string" ? body.detail : "That postcode could not be found.");
    if (!body.number) throw new Error(`${body.constituency} has no MP in the record at present.`);
    window.location.href = href(body);
  } catch (ex) {
    err.textContent = ex instanceof Error ? ex.message : "That postcode could not be found.";
    err.hidden = false;
  }
});

/** One House's members: folded away until opened, except when showing search results. */
function list(title: string, members: Member[], open: boolean): HTMLElement {
  const section = document.createElement("details");
  section.className = "people-group";
  section.open = open;
  const summary = document.createElement("summary");
  const h = document.createElement("h3");
  h.textContent = `${title} (${members.length})`;
  summary.append(h);
  const ul = document.createElement("ul");
  ul.className = "people-list";
  for (const m of members) {
    const li = document.createElement("li");
    const a = document.createElement("a");
    a.href = href(m);
    a.textContent = m.name;
    const meta = document.createElement("span");
    meta.className = "muted small";
    const left = m.until ? `${m.house === "lords" ? "left the Lords" : "MP until"} ${m.until.slice(0, 4)}` : null;
    meta.textContent = ` ${[m.constituency, m.party, left].filter(Boolean).join(", ")}`;
    li.append(a, meta);
    ul.append(li);
  }
  section.append(summary, ul);
  return section;
}

/** A link to the other list: sitting members, or those who have left since 1997 (/people/former). */
function otherList(former: boolean): HTMLParagraphElement {
  const p = document.createElement("p");
  const a = document.createElement("a");
  a.href = former ? "/people" : "/people/former";
  a.textContent = former ? "Sitting MPs and peers" : "Former MPs and peers since 1997";
  p.append(a);
  return p;
}

/** /people/former: everyone who has left either House since 1997, filtered as you type. */
async function loadFormer(live: HTMLElement, input: HTMLInputElement) {
  try {
    const r = await fetch("/api/people?everyone=true");
    if (!r.ok) throw new Error(String(r.status));
    const former = ((await r.json()) as { people: Member[] }).people.filter((m) => !m.current);
    const render = () => {
      const words = input.value.trim().toLowerCase().split(/\s+/).filter(Boolean);
      const hit = former.filter((m) =>
        words.every((w) => [m.name, m.constituency, m.party].some((x) => x?.toLowerCase().includes(w))),
      );
      const mps = hit.filter((m) => m.house === "commons");
      const peers = hit.filter((m) => m.house === "lords");
      live.replaceChildren(
        ...(hit.length ? [] : [Object.assign(document.createElement("p"), { textContent: "No former member matches." })]),
        ...(mps.length ? [list("Former MPs", mps, true)] : []),
        ...(peers.length ? [list("Former members of the House of Lords", peers, true)] : []),
        otherList(true),
      );
    };
    input.addEventListener("input", render);
    render();
  } catch {
    listFailed(live, "The list of former MPs and peers did not load. Reload the page to try again.");
  }
}

async function load() {
  const live = document.getElementById("people-live")!;
  const input = document.getElementById("people-search") as HTMLInputElement;
  if (/^\/people\/former\/?$/.test(window.location.pathname)) return loadFormer(live, input);
  try {
    const resp = await fetch("/api/people");
    if (!resp.ok) throw new Error(String(resp.status));
    const { people } = (await resp.json()) as { people: Member[] };
    const sitting = new Set(people.map((m) => m.number));
    let former: Member[] = [];
    let asked = "";
    let timer: number | undefined;
    const render = () => {
      const q = input.value.trim().toLowerCase();
      const words = q.split(/\s+/).filter(Boolean);
      const hit = people.filter((m) =>
        words.every((w) => [m.name, m.constituency, m.party].some((x) => x?.toLowerCase().includes(w))),
      );
      const mps = hit.filter((m) => m.house === "commons");
      const peers = hit.filter((m) => m.house === "lords");
      const past = q.length >= 3 && asked === q ? former : [];
      live.replaceChildren(
        ...(hit.length || past.length || q.length >= 3
          ? []
          : [Object.assign(document.createElement("p"), { textContent: "No current member matches." })]),
        ...(mps.length ? [list("MPs", mps, !!q)] : []),
        ...(peers.length ? [list("Members of the House of Lords", peers, !!q)] : []),
        ...(past.length ? [list("Former MPs and peers (since 1997)", past, true)] : []),
        ...(q.length >= 3 && asked === q && !hit.length && !past.length
          ? [Object.assign(document.createElement("p"), { textContent: "No member since 1997 matches." })]
          : []),
        otherList(false),
      );
    };
    // former members are looked up on the server as you type (a short pause first, so not on every key)
    const lookUp = () => {
      const q = input.value.trim().toLowerCase();
      window.clearTimeout(timer);
      if (q.length < 3) return;
      timer = window.setTimeout(async () => {
        try {
          const r = await fetch(`/api/people?everyone=true&q=${encodeURIComponent(q)}`);
          const body = (await r.json()) as { people: Member[] };
          if (input.value.trim().toLowerCase() !== q) return;
          former = body.people.filter((m) => !sitting.has(m.number) && !m.current).slice(0, 100);
          asked = q;
          render();
        } catch {
          /* former members are a bonus: the current list still works */
        }
      }, 250);
    };
    input.addEventListener("input", () => {
      render();
      lookUp();
    });
    render();
    lookUp();
  } catch {
    listFailed(live, "The list of MPs and peers did not load. Reload the page to try again.");
  }
}
void load();
