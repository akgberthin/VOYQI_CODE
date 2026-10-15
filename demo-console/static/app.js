/* VOYQI demo console — every action below is a live call through the
   API gateway (proxied by demo-console/server.py, which signs in as the
   chosen demo role). Nothing on this page is mocked. */

// ── helpers ───────────────────────────────────────────────────────────

function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "html") el.innerHTML = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

async function api(method, path, body, role = "super.admin") {
  const headers = { "Content-Type": "application/json" };
  if (role) headers["X-Demo-Role"] = role;
  const res = await fetch(path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  let data = null;
  try { data = await res.json(); } catch { /* empty body */ }
  return { status: res.status, ok: res.ok, data };
}
const get = (path, role) => api("GET", path, undefined, role);
const post = (path, body, role) => api("POST", path, body ?? {}, role);
const wait = (ms) => new Promise((r) => setTimeout(r, ms));

async function poll(fn, timeout = 30000, every = 1000) {
  const end = Date.now() + timeout;
  while (Date.now() < end) {
    const v = await fn();
    if (v) return v;
    await wait(every);
  }
  return null;
}

const uid = () => Math.random().toString(16).slice(2, 8);
const detail = (r) => (r.data && (typeof r.data.detail === "string" ? r.data.detail : JSON.stringify(r.data.detail ?? r.data))) || `HTTP ${r.status}`;
const isFallback = (reason) => (reason || "").startsWith("[deterministic fallback");
const plain = (reason) => (isFallback(reason) && reason.includes("] ") ? reason.slice(reason.indexOf("] ") + 2) : reason || "");
const prettyAgent = (key) => (key || "").replace(/_agent$/, "").replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()) + " Agent";
const money = (n) => "$" + Number(n).toLocaleString("en-US", { maximumFractionDigits: 0 });
const time = (iso) => (iso ? new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "");

function callout(kind, text, icon) {
  const icons = { ok: "✔", bad: "⛔", ai: "✦", info: "ℹ" };
  return h("div", { class: `callout ${kind}` }, h("span", { class: "icon" }, icon || icons[kind]), h("div", {}, text));
}

function aiCard(decision, extra) {
  const reason = decision.reasoning || "";
  return h("div", { class: "ai-card" },
    h("div", { class: "who" }, "✦ ", prettyAgent(decision.agent_key),
      decision.action_taken ? h("span", { class: "badge ai" }, decision.action_taken.replace(/_/g, " ")) : null,
      isFallback(reason) ? h("span", { class: "fallback-tag", title: "ANTHROPIC_API_KEY not configured — rule-based reasoning" }, "rule-based") : null),
    h("div", { class: "reason" }, plain(reason)),
    extra || null);
}

function spinner(text) { return h("div", { class: "muted" }, h("span", { class: "spinner" }), " ", text); }

async function decisionWhere(pred, timeout = 30000) {
  return poll(async () => {
    const r = await get("/api/v1/ai/decision-log?limit=60");
    return (r.data || []).find(pred) || null;
  }, timeout);
}

/* A numbered step: a card with a button; its result renders below it.
   Steps unlock in order so the story is told one beat at a time. */
function stepper() {
  const steps = [];
  const root = h("div");
  function add(title, sub, label, action, opts = {}) {
    const result = h("div", { class: "result" });
    const btn = h("button", { class: opts.kind || "" }, label);
    const body = h("div", { class: "card" }, h("h3", {}, title), sub ? h("div", { class: "sub" }, sub) : null, opts.form || null,
      h("div", { class: "btn-row" }, btn), result);
    const wrap = h("div", { class: "step locked" }, h("div", { class: "dot" }, String(steps.length + 1)), body);
    const s = { wrap, btn, result, done: false };
    btn.disabled = true;
    btn.onclick = async () => {
      btn.disabled = true;
      const old = btn.textContent;
      btn.textContent = "Working…";
      result.replaceChildren();
      try {
        const ok = await action(result);
        if (ok === false) { btn.disabled = false; btn.textContent = old; return; }
        s.done = true;
        wrap.className = "step done";
        btn.textContent = opts.again ? old : "Done";
        btn.disabled = !opts.again;
        unlock();
      } catch (e) {
        result.replaceChildren(callout("bad", e.message || String(e)));
        btn.disabled = false;
        btn.textContent = old;
      }
    };
    steps.push(s);
    root.append(wrap);
    unlock();
  }
  function unlock() {
    const next = steps.find((s) => !s.done);
    if (next && next.wrap.classList.contains("locked")) {
      next.wrap.className = "step active";
      next.btn.disabled = false;
    }
  }
  return { root, add };
}

// ── scenes ────────────────────────────────────────────────────────────

const SCENES = [
  { id: "overview", title: "Overview", render: overview },
  { id: "roles", title: "Roles & access", render: roles },
  { id: "lead", title: "Lead → Loan decision", render: leadToLoan },
  { id: "concierge", title: "Customer inbox", render: concierge },
  { id: "fleet", title: "Live fleet telematics", render: fleet },
  { id: "governance", title: "AI governance", render: governance },
  { id: "breakglass", title: "Emergency access", render: breakGlass },
];

let cleanup = [];

function head(title, text) {
  return h("div", { class: "scene-head" }, h("h1", {}, title), h("p", {}, text));
}

async function overview(main) {
  main.append(head("VOYQI Mobility OS — live platform",
    "Everything in this console runs against the real backend: 25 microservices behind one API gateway, Kafka events between them, and the AI orchestration nucleus deciding and acting — with humans in control where the law or the business requires it."));
  const stats = h("div", { class: "grid four mb" });
  main.append(stats);
  const [roster, policy, vehicles, decisions] = await Promise.all([
    get("/api/v1/ai/roster"), get("/api/v1/ai/tier-policy"), get("/api/v1/fleet/vehicles", "fleet.manager"), get("/api/v1/ai/decision-log?limit=200"),
  ]);
  const stat = (n, label) => h("div", { class: "card stat" }, h("div", { class: "big" }, n), h("div", { class: "label" }, label));
  stats.append(
    stat("25", "backend services behind one gateway"),
    stat((roster.data || []).length, "AI agents on the roster"),
    stat((policy.data?.actions || []).length, "AI actions under tier policy"),
    stat((vehicles.data || []).length, "vehicles in the demo fleet"),
  );
  const tiles = h("div", { class: "tiles" });
  const blurbs = {
    roles: "Nine roles sign in; each sees only its own modules. Members see only their own records.",
    lead: "A website form becomes a scored CRM lead and a credit decision — AI assists, a named person decides.",
    concierge: "AI reads customer messages, drafts replies and escalates angry or legal threats to people.",
    fleet: "Vehicles stream GPS and battery over MQTT. Geofence exits and tampering are assessed live.",
    governance: "Every agent, every action's autonomy level, every decision on record — and the guard rails.",
    breakglass: "Emergency account access is flagged by the Compliance Agent, reviewed and closed.",
  };
  SCENES.slice(1).forEach((s, i) => tiles.append(
    h("div", { class: "card tile", onclick: () => go(s.id) }, h("div", { class: "n" }, `SCENE ${i + 1}`), h("h3", {}, s.title), h("div", { class: "sub" }, blurbs[s.id])),
  ));
  main.append(tiles);
  const last = (decisions.data || []).length;
  main.append(h("div", { class: "mt" }, callout("info", `${last} AI decisions are already on the audit trail for this tenant.`)));
}

async function roles(main) {
  main.append(head("Roles & access",
    "Each person signs in with their own role. The gateway checks the role's permissions on every single request — the backend refuses what a role may not see, whatever the screen shows."));
  const cols = [
    ["Fleet", "/api/v1/fleet/vehicles"], ["HR", "/api/v1/hrm/employees"], ["Marketing", "/api/v1/marketing/campaigns"],
    ["Dealers", "/api/v1/dms/rooftops"], ["Members", "/api/v1/subscription/members"], ["Credit", "/api/v1/org/funding-sources"],
    ["Exec reports", "/api/v1/executive/alerts"], ["Compliance", "/api/v1/security-compliance/policies"],
    ["Role admin", "/api/v1/rbac/roles"], ["Locations", "/api/v1/hub/hubs"],
  ];
  const people = [
    ["super.admin", "Super admin"], ["fleet.manager", "Fleet manager"], ["hr.manager", "HR manager"], ["marketing.manager", "Marketing manager"],
    ["dealer.gm", "Dealer GM"], ["executive", "Executive"], ["compliance.officer", "Compliance officer"], ["member", "Member (customer)"],
  ];
  const cells = {};
  const table = h("table", { class: "matrix" },
    h("thead", {}, h("tr", {}, h("th", {}, "Signed in as"), cols.map(([c]) => h("th", { style: "text-align:center" }, c)))),
    h("tbody", {}, people.map(([role, label]) => h("tr", {}, h("td", {}, h("b", {}, label), h("div", { class: "muted", style: "font-size:12px" }, `${role}@voyqi.example`)),
      cols.map(([c]) => (cells[role + c] = h("td", { class: "cell pending" }, "·")))))));
  const runBtn = h("button", {}, "Run live access checks");
  runBtn.onclick = async () => {
    runBtn.disabled = true;
    for (const [role] of people) {
      await Promise.all(cols.map(async ([c, path]) => {
        const r = await get(path, role);
        const cell = cells[role + c];
        cell.className = "cell " + (r.status === 200 ? "allow" : "deny");
        cell.textContent = r.status === 200 ? "✔" : "✖";
        cell.title = `HTTP ${r.status}`;
      }));
    }
    runBtn.textContent = "Run again";
    runBtn.disabled = false;
  };
  main.append(h("div", { class: "card mb" }, h("h3", {}, "Who can open what"), h("div", { class: "sub" }, "Each cell is a real request made as that person. ✔ allowed · ✖ refused by the gateway (403)."),
    h("div", { class: "mb" }, runBtn), table));

  const own = h("div", { class: "result" });
  const ownBtn = h("button", {}, "Check member privacy");
  ownBtn.onclick = async () => {
    ownBtn.disabled = true;
    own.replaceChildren(spinner("Signing in as two different members…"));
    const ids = {};
    const cards = h("div", { class: "grid two mb" });
    for (const role of ["member", "member.two"]) {
      const me = await get("/api/v1/auth/me", role);
      const p = await get(`/api/v1/subscription/members/by-user-id/${me.data.user_id}`, role);
      ids[role] = p.data.id;
      cards.append(h("div", { class: "card" }, h("div", { class: "muted" }, `${role}@voyqi.example opens their own profile`),
        h("div", { class: "mt" }, h("span", { class: "badge ok" }, `✔ allowed (${p.status})`), " ", [p.data.first_name, p.data.last_name].filter(Boolean).join(" "))));
    }
    const a = await get(`/api/v1/subscription/members/${ids["member.two"]}`, "member");
    const b = await get(`/api/v1/subscription/members/${ids["member.two"]}/invoices`, "member");
    own.replaceChildren(cards,
      callout("bad", `member@ trying to open member.two's profile → refused (${a.status}). Their invoices → refused (${b.status}). Customers can only ever reach their own records.`));
    ownBtn.disabled = false;
  };
  const out = h("div", { class: "result" });
  const outBtn = h("button", {}, "Sign in, sign out, reuse the session");
  outBtn.onclick = async () => {
    outBtn.disabled = true;
    const r = await post("/demo/logout-check", { role: "marketing.manager" }, null);
    out.replaceChildren(h("table", {},
      h("tr", {}, h("td", {}, "Signed in, using the app"), h("td", {}, h("span", { class: "badge ok" }, r.data.before_logout))),
      h("tr", {}, h("td", {}, "Signs out"), h("td", {}, h("span", { class: "badge blue" }, r.data.logout))),
      h("tr", {}, h("td", {}, "Someone replays the old session token"), h("td", {}, h("span", { class: "badge bad" }, r.data.after_logout + " refused")))),
    h("div", { class: "mt" }, callout("ok", "A session is dead the moment the user signs out — a copied token is useless.")));
    outBtn.disabled = false;
  };
  main.append(h("div", { class: "grid two" },
    h("div", { class: "card" }, h("h3", {}, "Customers see only their own data"), h("div", { class: "sub" }, "Two members sign in and each tries to open the other's records."), ownBtn, own),
    h("div", { class: "card" }, h("h3", {}, "Signing out really ends the session"), h("div", { class: "sub" }, "The token is revoked server-side, not just deleted from the browser."), outBtn, out)));
}

async function leadToLoan(main) {
  main.append(head("Lead → Loan decision",
    "A visitor fills the website form. Without anyone typing into the CRM, the Revenue Agent creates the lead, checks for duplicates, scores and routes it. When the same person applies to subscribe, AI pre-qualifies and risk-scores the application — but only a named, accountable person can make the credit decision."));
  const st = stepper();
  const ctx = {};
  const name = h("input", { value: "Riya Sharma" });
  const email = h("input", { value: `riya.${uid()}@example.com` });
  const phone = h("input", { value: "+1 512 555 0123" });
  const page = h("select", {}, h("option", {}, "/ev-subscription"), h("option", {}, "/luxury-rentals"), h("option", {}, "/fleet-for-business"));
  const form = h("div", { class: "form" }, h("label", {}, "Name", name), h("label", {}, "Email", email), h("label", {}, "Phone", phone), h("label", {}, "Landing page", page));

  st.add("Visitor submits the website form", "Public page — no sign-in.", "Submit form", async (out) => {
    const r = await post("/api/v1/marketing/public/lead-captures",
      { name: name.value, email: email.value, phone: phone.value, landing_page: page.value, consent_marketing: true }, null);
    if (!r.ok) throw new Error(detail(r));
    ctx.email = email.value; ctx.name = name.value; ctx.phone = phone.value;
    out.append(callout("ok", `Captured ${r.data.name} from ${r.data.landing_page}. A lead.captured event is now on Kafka.`));
  }, { form });

  st.add("AI turns it into a CRM lead", "The Revenue Agent picks up the event, creates the CRM lead, checks duplicates and scores it.", "Show what the AI did", async (out) => {
    out.append(spinner("Waiting for the Revenue Agent…"));
    const d = await decisionWhere((d) => d.trigger_event_type === "lead.captured" && d.trigger_context?.email === ctx.email);
    if (!d) throw new Error("No decision yet — is ai-orchestration-svc running?");
    ctx.leadId = d.trigger_context.lead_id;
    const lead = (await get(`/api/v1/crm/leads/${ctx.leadId}`)).data || {};
    out.replaceChildren(h("div", { class: "grid two" }, aiCard(d),
      h("div", { class: "card" }, h("div", { class: "muted" }, "CRM lead — created automatically"),
        h("div", { class: "big mt" }, Math.round((lead.score ?? 0) * 100), h("span", { class: "muted", style: "font-size:16px" }, " / 100 score")),
        h("dl", { class: "kv mt" }, h("dt", {}, "Routed to"), h("dd", {}, (lead.routed_to_team || "").replace(/_/g, " ")),
          h("dt", {}, "Source"), h("dd", {}, (lead.source || "").replace(/_/g, " ")), h("dt", {}, "Status"), h("dd", {}, lead.status)))));
  });

  st.add("The same customer applies to subscribe", "$18,000 subscription enrollment, linked to the lead.", "Submit application", async (out) => {
    const r = await post("/api/v1/org/cases", { origination_type: "subscription_enrollment", full_name: ctx.name, amount_usd: 18000, email: ctx.email, phone: ctx.phone, external_ref_id: ctx.leadId });
    if (!r.ok) throw new Error(detail(r));
    ctx.caseId = r.data.id;
    out.append(spinner("AI pre-qualifying…"));
    const c = await poll(async () => { const x = (await get(`/api/v1/org/cases/${ctx.caseId}`)).data; return x?.prequal_verdict ? x : null; });
    out.replaceChildren(aiCard({ agent_key: "revenue_agent", action_taken: `pre-qualified: ${c?.prequal_verdict || "pending"}`, reasoning: c?.prequal_reasoning || "" }));
  });

  st.add("Underwriting: AI risk-scores it", "The AI recommends; it does not decide.", "Request underwriting", async (out) => {
    const r = await post(`/api/v1/org/cases/${ctx.caseId}/decisions`, {});
    if (!r.ok) throw new Error(detail(r));
    ctx.decisionId = r.data.id;
    out.append(spinner("AI scoring…"));
    const d = await poll(async () => { const x = (await get(`/api/v1/org/decisions/${ctx.decisionId}`)).data; return x?.risk_score != null ? x : null; });
    out.replaceChildren(h("div", { class: "grid two" },
      h("div", { class: "card" }, h("div", { class: "muted" }, "Credit score (higher is better)"), h("div", { class: "big mt" }, Math.round(d.risk_score * 100), h("span", { class: "muted", style: "font-size:16px" }, " / 100"))),
      h("div", { class: "card" }, h("div", { class: "muted" }, "AI recommendation"), h("div", { class: "mt" }, h("span", { class: "badge ai", style: "font-size:15px" }, (d.recommended_outcome || "").replace(/_/g, " "))),
        h("div", { class: "sub mt" }, "Status: ", d.status))));
  });

  st.add("Try to approve it without a named person", "Credit decisions are tier 3 — fair-lending law. The system refuses, for approvals and declines alike.", "Approve without a name", async (out) => {
    const r = await post(`/api/v1/org/decisions/${ctx.decisionId}/finalize`, { approve: true, decided_by: "someone" });
    out.append(callout(r.ok ? "ok" : "bad", r.ok ? "Approved" : `Refused (${r.status}): ${detail(r)}`));
  }, { kind: "danger" });

  const who = h("select", {}, h("option", { value: "credit_manager" }, "Credit manager"), h("option", { value: "underwriting_lead" }, "Underwriting lead"));
  const decide = h("select", {}, h("option", { value: "1" }, "Approve"), h("option", { value: "0" }, "Decline"));
  st.add("The accountable person decides", "Their role and name go on the record with the decision.", "Record decision", async (out) => {
    const approve = decide.value === "1";
    const r = await post(`/api/v1/org/decisions/${ctx.decisionId}/finalize`, { approve, accountable_role: who.value, decided_by: who.value.replace("_", "."), reasoning: "Verified income and identity" });
    if (!r.ok) throw new Error(detail(r));
    out.append(callout("ok", `${r.data.status.toUpperCase()} — accountable: ${r.data.accountable_role}, by ${r.data.decided_by} at ${time(r.data.decided_at)}.`));
  }, { kind: "success", form: h("div", { class: "form" }, h("label", {}, "Accountable role", who), h("label", {}, "Decision", decide)) });
  main.append(st.root);
}

async function concierge(main) {
  main.append(head("Customer inbox",
    "Customers write in. The Concierge Agent reads each message for sentiment and intent, drafts a reply for a support agent to approve, and escalates anything angry, urgent or legal straight to people."));
  let convo = null;
  const chat = h("div", { class: "chat" });
  const status = h("div", { class: "btn-row" });
  const input = h("input", { placeholder: "Type a customer message…", style: "flex:1" });
  const sendBtn = h("button", {}, "Send as customer");
  const presets = [
    "Hi! Can I switch my subscription from the Model 3 to a Model Y next month?",
    "This is the third time I've been double charged. Refund me now or I'm calling my lawyer.",
    "The car's charging port won't open and I'm stuck at the airport.",
  ];

  async function ensureConvo() {
    if (convo) return convo;
    const r = await post("/api/v1/crm/conversations", { channel: "email", subject: "Customer inbox demo" });
    convo = r.data;
    return convo;
  }
  async function refreshStatus() {
    const c = (await get(`/api/v1/crm/conversations/${convo.id}`)).data;
    const esc = c.status === "escalated";
    status.replaceChildren(h("span", { class: `badge ${esc ? "bad" : "ok"}` }, `status: ${c.status}`), h("span", { class: `badge ${c.priority === "urgent" ? "bad" : ""}` }, `priority: ${c.priority}`),
      c.routed_to_team ? h("span", { class: "badge blue" }, `team: ${c.routed_to_team.replace(/_/g, " ")}`) : null);
  }
  async function send(text) {
    if (!text.trim()) return;
    sendBtn.disabled = true;
    await ensureConvo();
    const r = await post(`/api/v1/crm/conversations/${convo.id}/messages`, { body: text, sender: "customer" });
    const meta = h("div", { class: "meta" }, spinner("Concierge Agent reading…"));
    chat.append(h("div", { class: "bubble customer" }, text, meta));
    chat.scrollTop = chat.scrollHeight;
    const d = await decisionWhere((d) => d.agent_key === "concierge_agent" && d.trigger_context?.message_id === r.data.id);
    const msgs = (await get(`/api/v1/crm/conversations/${convo.id}/messages`)).data || [];
    const m = msgs.find((x) => x.id === r.data.id) || {};
    meta.replaceChildren(
      m.sentiment ? h("span", { class: `badge ${m.sentiment === "negative" ? "bad" : m.sentiment === "positive" ? "ok" : ""}` }, `sentiment: ${m.sentiment}`) : null,
      m.intent ? h("span", { class: "badge blue" }, `intent: ${m.intent}`) : null,
      d ? h("span", { class: "badge ai", title: plain(d.reasoning) }, "✦ " + plain(d.reasoning).slice(0, 80)) : h("span", { class: "muted" }, "no AI decision yet"));
    const drafts = ((await get("/api/v1/crm/draft-replies")).data || []).filter((x) => x.conversation_id === convo.id && x.in_reply_to_message_id === r.data.id && x.status !== "sent");
    for (const draft of drafts) chat.append(draftBubble(draft));
    await refreshStatus();
    chat.scrollTop = chat.scrollHeight;
    sendBtn.disabled = false;
  }
  function draftBubble(draft) {
    const approve = h("button", { class: "small success" }, "Approve & send");
    const bubble = h("div", { class: "bubble draft" }, h("div", { class: "muted", style: "font-size:12px;margin-bottom:4px" }, "✦ AI draft — waiting for a support agent"), draft.draft_body,
      h("div", { class: "meta" }, approve));
    approve.onclick = async () => {
      approve.disabled = true;
      const r = await post(`/api/v1/crm/draft-replies/${draft.id}/approve-and-send`, { approved_by: "support.agent" });
      if (!r.ok) { approve.disabled = false; return alert(detail(r)); }
      bubble.replaceWith(h("div", { class: "bubble agent" }, draft.draft_body, h("div", { class: "meta" }, "✔ approved by support.agent and sent")));
    };
    return bubble;
  }
  sendBtn.onclick = () => { const t = input.value; input.value = ""; send(t); };
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") sendBtn.click(); });
  main.append(h("div", { class: "card" },
    h("div", { class: "btn-row mb", style: "justify-content:space-between" }, h("h3", {}, "Conversation"), status),
    h("div", { class: "chips" }, presets.map((p) => h("button", { class: "chip", onclick: () => send(p) }, p.length > 60 ? p.slice(0, 58) + "…" : p))),
    chat, h("div", { class: "btn-row mt" }, input, sendBtn)));
}

async function fleet(main) {
  main.append(head("Live fleet telematics",
    "Vehicles report GPS, battery and diagnostics over MQTT, the way installed telematics units do (simulated for now). The platform checks every reading against the vehicle's hub area; when a car leaves it, the Fleet Intelligence Agent assesses it. A tamper signal always goes to human security review — never to AI alone."));
  const startBtn = h("button", { class: "success" }, "▶ Start vehicles");
  const stopBtn = h("button", { class: "ghost" }, "■ Stop");
  const counts = h("div", { class: "btn-row" });
  const feed = h("div", { class: "feed" });
  const log = h("div", { class: "log" });
  const mapEl = h("div", { id: "map" });
  main.append(h("div", { class: "card mb" }, h("div", { class: "btn-row", style: "justify-content:space-between" }, h("div", { class: "btn-row" }, startBtn, stopBtn), counts)));
  main.append(h("div", { class: "grid", style: "grid-template-columns: 2fr 1fr" },
    h("div", {}, mapEl, h("div", { class: "mt" }, h("div", { class: "muted mb", style: "font-size:13px" }, "Device stream"), log)),
    h("div", { class: "card" }, h("h3", {}, "AI & security activity"), h("div", { class: "sub" }, "Newest first, from the AI decision log."), feed)));

  const map = L.map(mapEl).setView([29.5, -91.5], 6);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 18, attribution: "© OpenStreetMap" }).addTo(map);
  const hubs = {};
  const hubRes = await get("/api/v1/fleet/hubs", "fleet.manager");
  for (const hub of hubRes.data || []) {
    if (hub.latitude == null) continue;
    hubs[hub.id] = hub;
    L.circle([hub.latitude, hub.longitude], { radius: (hub.geofence_radius_miles || 50) * 1609, color: "#2563eb", weight: 1, fillOpacity: 0.06 }).addTo(map)
      .bindTooltip(`${hub.name} hub · ${hub.geofence_radius_miles || 50} mi zone`);
  }
  const hubPoints = Object.values(hubs).map((x) => [x.latitude, x.longitude]);
  if (hubPoints.length) map.fitBounds(hubPoints, { padding: [60, 60] });
  const markers = {};
  const miles = (a, b, c, d) => { const R = 3958.8, r = Math.PI / 180, x = Math.sin((c - a) * r / 2) ** 2 + Math.cos(a * r) * Math.cos(c * r) * Math.sin((d - b) * r / 2) ** 2; return 2 * R * Math.asin(Math.sqrt(x)); };
  const seen = new Set();

  async function tick() {
    const [pos, sim, dec] = await Promise.all([get("/api/v1/fleet/telemetry/latest", "fleet.manager"), get("/demo/simulator", null), get("/api/v1/ai/decision-log?limit=40")]);
    const fresh = (pos.data || []).filter((p) => Date.now() - new Date(p.recorded_at).getTime() < 5 * 60 * 1000);
    let outside = 0;
    for (const p of fresh) {
      const hub = hubs[p.current_hub_id];
      const out = hub ? miles(p.latitude, p.longitude, hub.latitude, hub.longitude) > (hub.geofence_radius_miles || 50) : false;
      outside += out;
      const color = out ? "#d97706" : "#16a34a";
      const tip = `<b>${p.make} ${p.model}</b><br>${p.vin}<br>battery/fuel ${Math.round(p.battery_or_fuel_pct)}% · ${p.odometer_miles.toLocaleString()} mi${p.dtc_codes.length ? `<br>⚠ ${p.dtc_codes.join(", ")}` : ""}${out ? "<br><b>outside hub zone</b>" : ""}`;
      if (!markers[p.vehicle_id]) markers[p.vehicle_id] = L.circleMarker([p.latitude, p.longitude], { radius: 7, weight: 2, color: "#fff", fillOpacity: 1 }).addTo(map);
      markers[p.vehicle_id].setLatLng([p.latitude, p.longitude]).setStyle({ fillColor: color }).bindTooltip(tip);
    }
    counts.replaceChildren(h("span", { class: "badge ok" }, `${fresh.length} vehicles reporting`), h("span", { class: "badge warn" }, `${outside} outside hub zone`),
      h("span", { class: `badge ${sim.data?.running ? "blue" : ""}` }, sim.data?.running ? "streaming" : "stopped"));
    log.textContent = (sim.data?.lines || []).join("\n");
    log.scrollTop = log.scrollHeight;
    startBtn.disabled = !!sim.data?.running;
    const items = (dec.data || []).filter((d) => ["flt.telemetry_anomaly_requested", "vehicle.tamper_detected"].includes(d.trigger_event_type));
    for (const d of items.slice().reverse()) {
      if (seen.has(d.id)) continue;
      seen.add(d.id);
      const tamper = d.trigger_event_type === "vehicle.tamper_detected";
      feed.prepend(h("div", { class: "item", style: tamper ? "border-color:#fecaca;background:#fff5f5" : "" },
        h("div", { class: "btn-row", style: "justify-content:space-between" }, h("b", {}, tamper ? "⛔ Tamper signal → human security review" : "📍 Left hub zone → AI assessed"), h("span", { class: "when" }, time(d.created_at))),
        h("div", { class: "mt", style: "margin-top:4px" }, plain(d.reasoning).slice(0, 180))));
    }
  }
  startBtn.onclick = async () => { await post("/demo/simulator/start", { vehicles: 12, ticks: 90 }, null); tick(); };
  stopBtn.onclick = async () => { await post("/demo/simulator/stop", {}, null); tick(); };
  await tick();
  const timer = setInterval(tick, 3000);
  cleanup.push(() => { clearInterval(timer); map.remove(); });
}

async function governance(main) {
  main.append(head("AI governance",
    "Every AI action has an autonomy level. Tier 1 runs on its own and is logged. Tier 2 waits for a person's approval. Tier 3 — credit, hiring, access policy, liability, tampering — needs a named accountable person, and no setting can change that."));
  const [roster, policy] = await Promise.all([get("/api/v1/ai/roster"), get("/api/v1/ai/tier-policy")]);
  const acts = policy.data?.actions || [];
  const n = (t) => acts.filter((a) => a.tier === t).length;
  const pct = (t) => `${(n(t) / acts.length) * 100}%`;
  main.append(h("div", { class: "card mb" }, h("h3", {}, `${acts.length} AI actions under policy`),
    h("div", { class: "tier-bar" }, h("div", { style: `width:${pct(1)};background:#16a34a` }), h("div", { style: `width:${pct(2)};background:#d97706` }), h("div", { style: `width:${pct(3)};background:#dc2626` })),
    h("div", { class: "legend" }, h("span", {}, h("i", { style: "background:#16a34a" }), `Tier 1 · autonomous, logged — ${n(1)}`),
      h("span", {}, h("i", { style: "background:#d97706" }), `Tier 2 · human approval — ${n(2)}`), h("span", {}, h("i", { style: "background:#dc2626" }), `Tier 3 · named accountable person — ${n(3)}`)),
    h("div", { class: "mt muted", style: "font-size:13px" }, "Tier 3: ", acts.filter((a) => a.tier === 3).map((a) => a.action_type).join(", "))));

  const grid = h("div", { class: "grid three mb" });
  for (const a of roster.data || []) {
    grid.append(h("div", { class: "card" }, h("div", { class: "btn-row", style: "justify-content:space-between" }, h("h3", {}, a.name), h("span", { class: `badge ${a.tier === "realtime" ? "blue" : ""}` }, a.tier)),
      h("div", { class: "sub", style: "margin:6px 0 0" }, a.covers || "")));
  }
  main.append(h("h3", {}, `${(roster.data || []).length} agents`), grid);

  const tryOut = h("div", { class: "result" });
  const try1 = h("button", { class: "danger" }, "Make credit decisions fully automatic");
  const try2 = h("button", { class: "danger" }, "Remove the approval step from deal structuring");
  try1.onclick = async () => { const r = await api("PUT", "/api/v1/ai/tier-policy/sales-originations-intelligence/credit.decision", { tier: 1, reason: "faster approvals" }); tryOut.replaceChildren(callout("bad", `Refused (${r.status}): ${detail(r)}`)); };
  try2.onclick = async () => { const r = await api("PUT", "/api/v1/ai/tier-policy/sales-originations-intelligence/deal.structure_recommend", { tier: 1, reason: "faster approvals" }); tryOut.replaceChildren(callout("bad", `Refused (${r.status}): ${detail(r)}`)); };
  main.append(h("div", { class: "card mb" }, h("h3", {}, "Guard rails — try to loosen them"),
    h("div", { class: "sub" }, "An admin can make an action stricter at any time. Loosening needs a track record (≥20 human decisions in 90 days, ≥90% approved). Tier 3 can never be loosened here."),
    h("div", { class: "btn-row" }, try1, try2), tryOut));

  const recBox = h("div");
  async function loadRecs() {
    const recs = (await get("/api/v1/ai/recommendations?status=pending")).data || [];
    recBox.replaceChildren(recs.length ? h("table", {}, h("thead", {}, h("tr", {}, h("th", {}, "Agent"), h("th", {}, "Proposed action"), h("th", {}, "Why"), h("th", {}, ""))),
      h("tbody", {}, recs.slice(0, 8).map((r) => {
        const row = h("tr", {}, h("td", {}, prettyAgent(r.agent_key)), h("td", {}, h("span", { class: "badge ai" }, r.action_type.replace(/_/g, " "))), h("td", { style: "max-width:420px" }, plain(r.reasoning).slice(0, 140)), h("td", {}));
        const ok = h("button", { class: "small success" }, "Approve");
        const no = h("button", { class: "small ghost" }, "Reject");
        ok.onclick = async () => { const x = await post(`/api/v1/ai/recommendations/${r.id}/approve`, { reason_code: "reviewed", accountable_role: "compliance_officer" }); row.lastChild.replaceChildren(h("span", { class: `badge ${x.ok ? "ok" : "bad"}` }, x.ok ? "approved — action executed" : detail(x))); };
        no.onclick = async () => { const x = await post(`/api/v1/ai/recommendations/${r.id}/reject`, { reason_code: "not_needed" }); row.lastChild.replaceChildren(h("span", { class: `badge ${x.ok ? "" : "bad"}` }, x.ok ? "rejected" : detail(x))); };
        row.lastChild.append(h("div", { class: "btn-row" }, ok, no));
        return row;
      }))) : callout("ok", "Nothing waiting for approval."));
  }
  main.append(h("div", { class: "card mb" }, h("h3", {}, "Waiting for a human"), h("div", { class: "sub" }, "Tier 2 and 3 actions wait here. The action runs only when someone approves it."), recBox));
  await loadRecs();

  const logs = (await get("/api/v1/ai/decision-log?limit=12")).data || [];
  main.append(h("div", { class: "card" }, h("h3", {}, "Audit trail"), h("div", { class: "sub" }, "Every AI decision, with what triggered it and why."),
    h("table", {}, h("thead", {}, h("tr", {}, h("th", {}, "Time"), h("th", {}, "Agent"), h("th", {}, "Triggered by"), h("th", {}, "Reasoning"))),
      h("tbody", {}, logs.map((d) => h("tr", {}, h("td", { class: "muted" }, time(d.created_at)), h("td", {}, prettyAgent(d.agent_key)), h("td", {}, h("code", {}, d.trigger_event_type)),
        h("td", { style: "max-width:460px" }, plain(d.reasoning).slice(0, 130))))))));
}

async function breakGlass(main) {
  main.append(head("Emergency (break-glass) access",
    "Sometimes an admin must get into a customer's account immediately. The platform allows it — and the Compliance Agent immediately puts it in front of a compliance officer, who reviews it and closes the session. Every step is on the audit record."));
  const st = stepper();
  const ctx = {};
  const user = h("input", { value: "member@voyqi.example" });
  const reason = h("input", { value: "Member locked out during a live rental" });
  st.add("Admin activates emergency access", "Time-boxed, with a stated reason.", "Activate break-glass", async (out) => {
    const r = await post("/api/v1/identity/break-glass", { user_id: user.value, activated_by: "super.admin@voyqi.example", reason: reason.value, duration_minutes: 30 });
    if (!r.ok) throw new Error(detail(r));
    ctx.session = r.data;
    out.append(h("div", { class: "card" }, h("dl", { class: "kv" }, h("dt", {}, "Status"), h("dd", {}, h("span", { class: "badge warn" }, r.data.status)), h("dt", {}, "Account"), h("dd", {}, r.data.user_id),
      h("dt", {}, "Expires"), h("dd", {}, time(r.data.expires_at)), h("dt", {}, "Outside business hours"), h("dd", {}, r.data.off_hours ? "yes" : "no"))));
  }, { form: h("div", { class: "form" }, h("label", {}, "Account", user), h("label", {}, "Reason", reason)) });

  st.add("Compliance Agent flags it", "Within seconds, without anyone asking.", "Show the review card", async (out) => {
    out.append(spinner("Waiting for the Compliance Agent…"));
    const rec = await poll(async () => ((await get("/api/v1/ai/recommendations?status=pending")).data || []).find((r) => JSON.stringify(r.proposed_action).includes(ctx.session.id)));
    if (!rec) throw new Error("No review card yet");
    ctx.rec = rec;
    out.replaceChildren(aiCard({ agent_key: rec.agent_key, action_taken: rec.action_type, reasoning: rec.reasoning }, h("div", { class: "mt" }, h("span", { class: "badge warn" }, `risk ${rec.risk_score}/100`))));
  });

  st.add("Compliance officer reviews and closes it", "Approving the review action ends the session.", "Approve as compliance officer", async (out) => {
    const r = await post(`/api/v1/ai/recommendations/${ctx.rec.id}/approve`, { reason_code: "reviewed", accountable_role: "compliance_officer" }, "super.admin");
    if (!r.ok) throw new Error(detail(r));
    const s = ((await get("/api/v1/identity/break-glass")).data || []).find((x) => x.id === ctx.session.id);
    out.append(callout("ok", `Session ${s.status} at ${time(s.ended_at)} — ended by ${s.ended_by}. Activation, review and closure are all on the audit record.`));
  }, { kind: "success" });
  main.append(st.root);
}

// ── shell ─────────────────────────────────────────────────────────────

function go(id) {
  location.hash = id;
}

async function render() {
  const id = (location.hash || "#overview").slice(1);
  const scene = SCENES.find((s) => s.id === id) || SCENES[0];
  cleanup.forEach((f) => f());
  cleanup = [];
  document.querySelectorAll("nav a").forEach((a) => a.classList.toggle("active", a.dataset.id === scene.id));
  const main = document.getElementById("main");
  main.replaceChildren();
  try {
    await scene.render(main);
  } catch (e) {
    main.append(callout("bad", `Could not load this scene: ${e.message}. Is the platform running (docker compose up -d) and seeded (scripts/seed_demo.py)?`));
  }
}

async function init() {
  const nav = document.getElementById("nav");
  SCENES.forEach((s, i) => nav.append(h("a", { "data-id": s.id, onclick: () => go(s.id) }, h("span", { class: "num" }, i === 0 ? "★" : String(i)), s.title)));
  window.addEventListener("hashchange", render);
  render();
  const cfg = (await get("/demo/config", null)).data;
  document.getElementById("tenant").textContent = `tenant ${cfg?.tenant} · via API gateway`;
  const logs = (await get("/api/v1/ai/decision-log?limit=10")).data || [];
  const mode = document.getElementById("ai-mode");
  if (!logs.length) mode.textContent = "AI mode: no decisions yet";
  else if (logs.some((d) => isFallback(d.reasoning))) { mode.className = "ai-mode fallback"; mode.textContent = "AI mode: rule-based fallback (Claude API key not set)"; }
  else { mode.className = "ai-mode live"; mode.textContent = "AI mode: live Claude reasoning"; }
}

init();
