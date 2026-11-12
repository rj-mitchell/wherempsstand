/** The operator's page: spending, reports, statements, feedback, visits, briefings and updates. Needs the admin
 * token. */
import "@fontsource-variable/source-serif-4/opsz.css";
import "./styles.css";
import { fmtDate } from "./render";

const KEY = "wts-admin-token";
let token = "";
try {
  token = sessionStorage.getItem(KEY) ?? "";
} catch {
  /* storage blocked: ask each time */
}

const $ = (id: string) => document.getElementById(id)!;

function el<K extends keyof HTMLElementTagNameMap>(tag: K, text?: string, cls?: string): HTMLElementTagNameMap[K] {
  const e = document.createElement(tag);
  if (text !== undefined) e.textContent = text; // never innerHTML: reports and questions are typed by the public
  if (cls) e.className = cls;
  return e;
}

async function call<T>(path: string, body?: unknown, method?: string): Promise<T> {
  const resp = await fetch(path, {
    method: method ?? (body === undefined ? "GET" : "POST"),
    headers: { Authorization: `Bearer ${token}`, ...(body === undefined ? {} : { "Content-Type": "application/json" }) },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (resp.status === 403) throw new Error("That token was not accepted.");
  if (!resp.ok) throw new Error(`The server replied ${resp.status}.`);
  return resp.json() as Promise<T>;
}

function table(headers: string[], rows: (string | number | Node)[][]): HTMLElement {
  if (!rows.length) return el("p", "None.", "muted");
  const t = el("table", undefined, "admin-table");
  const head = t.createTHead().insertRow();
  for (const h of headers) head.append(el("th", h));
  const tb = t.createTBody();
  for (const r of rows) {
    const tr = tb.insertRow();
    for (const c of r) {
      const td = tr.insertCell();
      if (c instanceof Node) td.append(c);
      else td.textContent = String(c);
    }
  }
  const wrap = el("div", undefined, "table-wrap");
  wrap.append(t);
  return wrap;
}

const when = (iso: string | null) => (iso ? `${fmtDate(iso.slice(0, 10))} ${iso.slice(11, 16)}` : "");

interface Report {
  id: number;
  at: string;
  kind: string;
  briefing_id: string | null;
  page: string | null;
  message: string;
  contact: string | null;
}

function reportCard(r: Report): HTMLElement {
  const box = el("article", undefined, "report-form");
  box.append(el("h3", `${r.kind[0].toUpperCase()}${r.kind.slice(1)} · ${when(r.at)}`));
  if (r.briefing_id) {
    const a = el("a", `Briefing /b/${r.briefing_id}`);
    a.href = `/b/${r.briefing_id}`;
    a.target = "_blank";
    box.append(a);
  }
  if (r.page) {
    const a = el("a", `Page ${r.page}`);
    a.href = r.page;
    a.target = "_blank";
    box.append(a);
  }
  box.append(el("p", r.message, "report-message"));
  if (r.contact) box.append(el("p", `Reply to: ${r.contact}`, "muted small"));
  const form = el("form");
  form.innerHTML = `
    <label>What was done (private)<textarea name="resolution" rows="2" required minlength="3"></textarea></label>
    <label>Public note for the corrections page (optional)<textarea name="public_note" rows="2"></textarea></label>
    <div class="form-actions">
      <button type="submit" name="status" value="resolved" class="secondary">Resolved</button>
      <button type="submit" name="status" value="dismissed" class="link small">Dismiss</button>
    </div>`;
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const submitter = (e as SubmitEvent).submitter as HTMLButtonElement | null;
    const data = new FormData(form);
    try {
      await call(`/api/admin/reports/${r.id}/close`, {
        status: submitter?.value ?? "resolved",
        resolution: String(data.get("resolution") ?? ""),
        public_note: String(data.get("public_note") ?? "") || null,
      });
      await load();
    } catch (err) {
      alert(err instanceof Error ? err.message : "Could not close the report.");
    }
  });
  box.append(form);
  return box;
}

interface AdminStatement {
  id: number;
  at: string;
  name: string;
  role: string;
  item_path: string;
  person: number | null;
  text: string;
  link: string | null;
  email: string | null;
  status: "pending" | "published" | "declined" | "withdrawn";
  published_at: string | null;
  checked: string | null;
}

async function act(path: string, body: unknown, what: string) {
  try {
    await call(path, body);
    await load();
  } catch (err) {
    alert(err instanceof Error ? err.message : `Could not ${what}.`);
  }
}

/** A statement to publish, decline, update or withdraw. Its words are shown as text, never as HTML. */
function statementCard(s: AdminStatement): HTMLElement {
  const box = el("article", undefined, "report-form");
  box.append(el("h3", `#${s.id} ${s.status} · ${s.name}, ${s.role} · received ${when(s.at)}`));
  const about = el("a", `About ${s.item_path}`);
  about.href = s.item_path;
  about.target = "_blank";
  box.append(about);
  box.append(el("p", s.person ? `Member ${s.person}` : "No member set: give their number when publishing, unless it is about a group", "muted small"));
  box.append(el("p", s.text, "report-message"));
  box.append(el("p", `${s.text.trim().split(/\s+/).length} words`, "muted small"));
  if (s.link) box.append(el("p", `Link: ${s.link}`, "muted small"));
  box.append(el("p", `Confirm with: ${s.email ?? "(email deleted)"}`, "muted small"));
  if (s.checked) box.append(el("p", `Checked: ${s.checked}`, "muted small"));
  const base = `/api/admin/statements/${s.id}`;
  if (s.status === "pending") {
    const form = el("form");
    form.innerHTML = `
      <label>How you confirmed who sent it (private)<input name="checked" required minlength="3" /></label>
      <label>Member number, if the address isn't their page (as in /people/&lt;number&gt;)<input name="person" inputmode="numeric" /></label>
      <div class="form-actions"><button type="submit" class="secondary">Publish as written</button></div>`;
    const person = form.querySelector<HTMLInputElement>("input[name=person]")!;
    if (s.person) person.value = String(s.person);
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      const data = new FormData(form);
      const n = Number(String(data.get("person") ?? "").trim());
      void act(`${base}/publish`, { checked: String(data.get("checked") ?? ""), person: n > 0 ? n : null }, "publish it");
    });
    box.append(form);
  }
  const buttons = el("div", undefined, "form-actions");
  const button = (label: string, onClick: () => void) => {
    const b = el("button", label, "link small");
    b.type = "button";
    b.addEventListener("click", onClick);
    buttons.append(b);
  };
  button(s.status === "pending" ? "Decline" : "Take down", () => {
    const reason = prompt("Why? This is for the author (tell them by email); it is never shown on the site.");
    if (reason && reason.trim().length >= 3) void act(`${base}/decline`, { reason: reason.trim() }, "decline it");
  });
  button("Update the words", () => {
    const text = prompt("The author's new words, exactly as they sent them:", s.text);
    if (text && text.trim() !== s.text.trim()) void act(`${base}/update`, { text }, "update it");
  });
  button("Withdraw (author's request)", () => {
    if (confirm("Withdraw this statement at its author's request?")) void act(`${base}/withdraw`, {}, "withdraw it");
  });
  box.append(buttons);
  return box;
}

interface Briefing {
  id: string;
  created_at: string;
  question: string;
  withdrawn_at: string | null;
}

function withdrawButton(b: Briefing): Node {
  if (b.withdrawn_at) return el("span", `Withdrawn ${when(b.withdrawn_at)}`, "muted");
  const btn = el("button", "Withdraw", "link small");
  btn.type = "button";
  btn.addEventListener("click", async () => {
    const reason = prompt("Reason for withdrawing. This is shown publicly on the corrections page.");
    if (!reason || reason.trim().length < 3) return;
    try {
      await call(`/api/admin/briefings/${b.id}/withdraw`, { reason: reason.trim() });
      await load();
    } catch (err) {
      alert(err instanceof Error ? err.message : "Could not withdraw the briefing.");
    }
  });
  return btn;
}

async function feature(briefing: string, title?: string, blurb?: string) {
  await call("/api/admin/featured", { briefing, title: title || null, blurb: blurb || null });
  await load();
}

function featureButton(b: Briefing, featured: Set<string>): Node {
  if (b.withdrawn_at) return el("span");
  if (featured.has(b.id)) return el("span", "Featured", "muted small");
  const btn = el("button", "Feature", "link small");
  btn.type = "button";
  btn.addEventListener("click", () => void feature(b.id).catch((e) => alert(e instanceof Error ? e.message : e)));
  return btn;
}

function unfeatureButton(id: string): Node {
  const btn = el("button", "Remove", "link small");
  btn.type = "button";
  btn.addEventListener("click", async () => {
    if (!confirm("Stop featuring this briefing? It stays at its address.")) return;
    await call(`/api/admin/featured/${id}`, undefined, "DELETE");
    await load();
  });
  return btn;
}

async function load() {
  const [usage, reports, statements, briefings, sync, topics] = await Promise.all([
    call<any>("/api/admin/usage"),
    call<{ reports: Report[] }>("/api/admin/reports?status=open"),
    call<{ statements: AdminStatement[] }>("/api/admin/statements"),
    call<{ briefings: Briefing[] }>("/api/admin/briefings?limit=30"),
    call<{ runs: any[] }>("/api/admin/sync"),
    call<{ topics: any[]; featured: any[] }>("/api/admin/topics"),
  ]);
  const featured = new Set<string>(topics.featured.map((f: any) => f.id));
  const link = (href: string, text: string) => {
    const a = el("a", text);
    a.href = href;
    a.target = "_blank";
    return a;
  };
  $("admin-featured").replaceChildren(
    table(
      ["Featured", "Briefing", ""],
      topics.featured.map((f: any) => [when(f.featured_at), link(`/b/${f.id}`, f.title), unfeatureButton(f.id)]),
    ),
  );
  $("admin-topics").replaceChildren(
    table(
      ["Topic", "Last written", "Versions", "Last check"],
      topics.topics.map((t: any) => [
        link(`/topics/${t.slug}`, t.title),
        t.current ? when(t.current.prepared_at) : "Not yet",
        t.versions,
        t.last_check ? `${when(t.last_check.checked_at)}: ${t.last_check.outcome}` : "",
      ]),
    ),
  );
  const usd = (n: number) => `$${(n ?? 0).toFixed(2)}`;
  const alertBoxes = (usage.alerts ?? []).map((a: { level: string; text: string }) =>
    el("p", a.text, a.level === "error" ? "admin-alert error" : "admin-alert"),
  );
  $("admin-alerts").replaceChildren(...(alertBoxes.length ? alertBoxes : [el("p", "No alerts.", "admin-alert ok")]));
  $("admin-popular").replaceChildren(
    table(["Question", "Times asked", "Last asked"], (usage.popular_questions ?? []).map((q: any) => [q.question, q.times, q.last])),
  );
  $("admin-failures").replaceChildren(
    table(["When", "What", "Question", "Error"], (usage.failures ?? []).map((f: any) => [when(f.at), f.kind, f.question ?? "", f.error])),
  );
  $("admin-spend").replaceChildren(
    el("p", `Today: ${usd(usage.today_usd)} (${usage.today_calls} calls). This month (${usage.month}): ${usd(usage.total_usd)}.`),
  );
  const open = reports.reports;
  $("admin-reports").replaceChildren(...(open.length ? open.map(reportCard) : [el("p", "No open reports.", "muted")]));
  const current = statements.statements.filter((st) => st.status === "pending" || st.status === "published");
  $("admin-statements").replaceChildren(
    ...(current.length
      ? [...current].sort((x, y) => (x.status === y.status ? 0 : x.status === "pending" ? -1 : 1)).map(statementCard)
      : [el("p", "No statements waiting or shown.", "muted")]),
  );
  const fb = usage.visits.feedback;
  $("admin-feedback").replaceChildren(
    el("p", `${fb.useful} of ${fb.total} said a briefing was useful.`),
    table(["When", "Question marked not useful"], fb.recent_not_useful.map((f: any) => [when(f.at), f.question])),
  );
  const t = usage.tasks;
  if (t) {
    const pct = (x: { visitors: number; pct: number | null }) => `${x.visitors}${x.pct != null ? ` (${x.pct}%)` : ""}`;
    $("admin-tasks").replaceChildren(
      el("p", `Of ${t.visitors} visitors in the last ${t.days} days (each counted once a day):`),
      table(
        ["Did this", "Visitors"],
        [
          ["Asked a question", pct(t.ask)],
          ["Opened a source", pct(t.source)],
          ["Asked a follow-up", pct(t.follow_up)],
          ["Rated a briefing useful", t.useful.rated ? `${t.useful.yes} of ${t.useful.rated} ratings (${t.useful.pct}%)` : "no ratings yet"],
        ],
      ),
      el(
        "p",
        "Opening a source is the clearest sign a briefing is being checked and used; a low share of follow-ups or sources " +
          "opened, or many 'not useful' ratings, are worth looking into.",
        "muted small",
      ),
    );
  }
  const v = usage.visits;
  $("admin-visits").replaceChildren(
    table(["Day", "Page views", "Visitors"], [...v.by_day].reverse().map((d: any) => [d.day, d.views, d.visitors])),
    el("h3", "Top pages"),
    table(["Page", "Views"], v.top_pages.map((p: any) => [p.path, p.views])),
    el("h3", "Referring sites"),
    table(["Site", "Views"], v.top_referrers.map((r: any) => [r.host, r.views])),
  );
  $("admin-briefings").replaceChildren(
    table(
      ["Prepared", "Question", "", ""],
      briefings.briefings.map((b) => {
        const a = el("a", b.question);
        a.href = `/b/${b.id}`;
        a.target = "_blank";
        return [when(b.created_at), a, featureButton(b, featured), withdrawButton(b)];
      }),
    ),
  );
  $("admin-updates").replaceChildren(
    table(
      ["Started", "Finished", "Status"],
      sync.runs.slice(0, 10).map((r: any) => [when(r.started_at), when(r.finished_at), r.status ?? ""]),
    ),
  );
}

async function open() {
  const err = $("admin-error");
  try {
    await load();
    $("admin-login").hidden = true;
    $("admin-panel").hidden = false;
    err.hidden = true;
    try {
      sessionStorage.setItem(KEY, token);
    } catch {
      /* fine */
    }
  } catch (e) {
    err.textContent = e instanceof Error ? e.message : "Could not load.";
    err.hidden = false;
  }
}

$("feature-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const err = $("feature-error");
  err.hidden = true;
  const v = (id: string) => ($(id) as HTMLInputElement).value.trim();
  try {
    await feature(v("feature-briefing"), v("feature-title"), v("feature-blurb"));
    (e.target as HTMLFormElement).reset();
  } catch (ex) {
    err.textContent = ex instanceof Error ? ex.message : "Could not feature that briefing.";
    err.hidden = false;
  }
});

$("admin-login").addEventListener("submit", (e) => {
  e.preventDefault();
  token = ($("admin-token") as HTMLInputElement).value.trim();
  void open();
});
$("admin-refresh").addEventListener("click", () => void load());
$("admin-signout").addEventListener("click", () => {
  try {
    sessionStorage.removeItem(KEY);
  } catch {
    /* fine */
  }
  location.reload();
});
if (token) void open();
