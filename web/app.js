// Bay Area New-Home True Price: map, home list, community drawer and the agent box.
// Data: data/site.json (communities, homes, plan index), data/c/<id>.json (one community), data/demo_answers.json.
// API (optional, same origin): POST /api/ask, GET /api/reprice, GET /api/health.
"use strict";

const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const usd = (n) => (n == null ? "—" : "$" + Math.round(n).toLocaleString("en-US"));
const short = (n) => (n == null ? "—" : n >= 1e6 ? "$" + (n / 1e6).toFixed(2).replace(/\.?0+$/, "") + "M" : "$" + Math.round(n / 1e3) + "K");
const range = (lo, hi) => (lo == null ? "—" : lo === hi ? usd(hi) : `${usd(lo)} – ${usd(hi)}`);
const beds = (b) => (b == null ? "?" : Number.isInteger(b) ? b : b.toFixed(1));
const MONTH = (ym) => (ym ? new Date(ym + "-15").toLocaleString("en-US", { month: "short", year: "numeric" }) : "date not given");
const ready = (h) => (h.move_in_status === "now" ? "Ready now" : h.ready_month ? `Ready ${MONTH(h.ready_month)}` : "Ready date not given");
const KIND = {
  flat_cash: "Cash", price_cut: "Price cut", closing_credit: "Closing-cost credit", perm_rate: "Lower fixed rate",
  temp_buydown: "Temporary rate buydown", arm_promo: "Adjustable-rate offer", first_year_rate: "First-year rate",
  unquantified: "Not priced",
};
const PRODUCT = { SFD: "House", TH: "Townhome", condo: "Condo", mixed: "Mixed" };

const S = { site: null, comms: {}, homes: {}, plans: {}, demo: {}, detail: {}, api: false, map: null, layer: null, shown: 30 };

// Same normalisation as tools/export_site_data.question_key
function questionKey(q) {
  return q.toLowerCase().replace(/[^a-z0-9$.%\s]/g, " ").replace(/\.(?!\d)/g, " ").split(/\s+/).filter(Boolean).join(" ");
}

async function getJSON(url, opts) {
  const r = await fetch(url, opts);
  const body = await r.json().catch(() => null);
  if (!r.ok) throw Object.assign(new Error((body && body.error) || `HTTP ${r.status}`), { status: r.status, body });
  return body;
}

// ---------- boot ----------
async function boot() {
  const [site, demo] = await Promise.all([getJSON("data/site.json"), getJSON("data/demo_answers.json").catch(() => ({}))]);
  S.site = site;
  S.demo = demo;
  site.communities.forEach((c) => (S.comms[c.id] = c));
  site.homes.forEach((h) => (S.homes[h.home_id] = h));
  site.plans.forEach((p) => (S.plans[p.plan_id] = p));
  const m = site.meta;
  $("#stats").textContent = `${m.homes} homes · ${m.communities} communities · ${m.builders} builders · ` +
    `market rate ${m.market_rate}% (${m.rate_source}, week of ${m.rate_date}) · data read ${m.generated}`;
  $("#method-rate").innerHTML = `Every figure assumes <b>${m.assumptions.down_payment_pct}% down</b> and a ` +
    `<b>${m.assumptions.term_years}-year fixed</b> mortgage at the market rate, <b>${m.market_rate}%</b> ` +
    `(Freddie Mac's weekly survey via FRED, ${m.rate_date}); a loan above the county's conforming limit pays 0.25 points more. ` +
    `Open a community to rerun it with your own down payment.`;
  examples();
  initMap();
  ["#f-county", "#f-max", "#f-beds", "#f-product", "#f-now"].forEach((id) => $(id).addEventListener("change", () => { S.shown = 30; render(); }));
  render();
  window.addEventListener("hashchange", route);
  $("#backdrop").addEventListener("click", () => (location.hash = ""));
  document.addEventListener("keydown", (e) => e.key === "Escape" && location.hash && (location.hash = ""));
  getJSON("api/health").then(() => (S.api = true)).catch(() => (S.api = false)).finally(route);
}

// ---------- filters, map, list ----------
function filtered() {
  const county = $("#f-county").value, max = +$("#f-max").value || null, minBeds = +$("#f-beds").value || null;
  const product = $("#f-product").value, now = $("#f-now").checked;
  return S.site.homes.filter((h) =>
    (!county || h.county === county) &&
    (!max || (h.true_price_high ?? h.asking ?? Infinity) <= max) &&
    (!minBeds || (h.beds ?? 0) >= minBeds) &&
    (!product || h.product === product) &&
    (!now || h.move_in_status === "now"));
}

const BINS = [
  [0, "#94a3b8", "none listed"], [0.01, "#86efac", "under 5%"], [5, "#22c55e", "5–10%"],
  [10, "#15803d", "10–20%"], [20, "#14532d", "20%+"],
];
const colorFor = (pct) => [...BINS].reverse().find(([lo]) => (pct || 0) >= lo)[1];

function initMap() {
  S.map = L.map("map", { scrollWheelZoom: false }).setView([37.6, -121.8], 9);
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 18, attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
  }).addTo(S.map);
  S.layer = L.layerGroup().addTo(S.map);
  $("#legend").innerHTML = `<span class="font-medium">Incentives worth up to (share of asking):</span>` +
    BINS.map(([, c, l]) => `<span class="flex items-center gap-1"><span class="inline-block w-3 h-3 rounded-full" style="background:${c}"></span>${l}</span>`).join("");
  const pts = S.site.communities.filter((c) => c.lat != null).map((c) => [c.lat, c.lng]);
  S.map.invalidateSize();
  if (pts.length) S.map.fitBounds(pts, { padding: [20, 20], maxZoom: 12 });
}

function render() {
  const homes = filtered();
  const byComm = {};
  homes.forEach((h) => (byComm[h.community_id] ||= []).push(h));
  $("#count").textContent = `${homes.length} homes in ${Object.keys(byComm).length} communities`;

  S.layer.clearLayers();
  const unmapped = [];
  Object.entries(byComm).forEach(([id, hs]) => {
    const c = S.comms[id];
    if (c.lat == null) return unmapped.push(c);
    const cheapest = hs[0];
    L.circleMarker([c.lat, c.lng], {
      radius: 6 + Math.min(10, Math.sqrt(hs.length) * 2), color: "#0f172a", weight: 1,
      fillColor: colorFor(c.max_discount_pct_best), fillOpacity: 0.9,
    }).bindPopup(`<div class="text-sm"><div class="font-semibold">${esc(c.name)}</div>
      <div class="text-xs text-slate-500">${esc(c.builder)} · ${esc(c.city)}</div>
      <div class="mt-1 num">${hs.length} home${hs.length > 1 ? "s" : ""} match · true price from <b>${short(cheapest.true_price_high ?? cheapest.asking)}</b></div>
      <div class="text-xs num">Incentives worth up to ${c.max_discount_pct_best ?? 0}% of asking (${c.max_discount_pct ?? 0}% guaranteed)</div>
      <a href="#c/${c.id}" class="inline-block mt-2 text-sky-700 font-medium">Open community →</a></div>`)
      .addTo(S.layer);
  });
  $("#unmapped").textContent = unmapped.length
    ? `${unmapped.length} matching ${unmapped.length > 1 ? "communities have" : "community has"} no map pin yet (streets too new for public maps): ${unmapped.map((c) => c.name).join(", ")}. They're in the list.`
    : "";

  const list = homes.slice(0, S.shown).map((h) => {
    const sure = h.discount_low || 0, best = h.discount_high || 0;
    const badge = best > 0
      ? `<span class="text-[11px] rounded-full px-2 py-0.5 ${sure > 0 ? "bg-emerald-100 text-emerald-800" : "bg-slate-100 text-slate-600"}">` +
        (sure > 0 ? `${short(sure)} off for sure` : "") + (best > sure ? `${sure > 0 ? ", " : ""}up to ${short(best)}` : "") + `</span>`
      : "";
    return `<li><a href="#h/${h.home_id}" class="block px-4 py-3 hover:bg-slate-50">
      <div class="flex justify-between gap-3"><div class="min-w-0">
        <div class="font-medium truncate">${esc(h.address || h.lot || "Address not listed")}</div>
        <div class="text-xs text-slate-500 truncate">${esc(h.community)} · ${esc(h.city)} · ${esc(h.builder)}</div>
        <div class="text-xs text-slate-500 num">${beds(h.beds)} bd · ${h.baths ?? "?"} ba · ${h.sqft ? h.sqft.toLocaleString() + " sq ft" : "size not given"} · ${ready(h)}</div>
      </div><div class="text-right shrink-0 num">
        <div class="font-semibold">${short(h.true_price_high ?? h.asking)}</div>
        ${h.true_price_low != null && h.true_price_low < h.true_price_high ? `<div class="text-[11px] text-slate-500">best case ${short(h.true_price_low)}</div>` : ""}
        <div class="text-[11px] text-slate-400 ${h.true_price_high < h.asking ? "line-through" : ""}">asking ${short(h.asking)}</div>
      </div></div>${badge ? `<div class="mt-1">${badge}</div>` : ""}</a></li>`;
  });
  if (homes.length > S.shown) list.push(`<li class="p-3 text-center"><button id="more" class="text-sky-700 text-sm">Show ${Math.min(30, homes.length - S.shown)} more</button></li>`);
  if (!homes.length) list.push(`<li class="p-4 text-sm text-slate-500">No homes match. Loosen a filter.</li>`);
  $("#list").innerHTML = list.join("");
  $("#more")?.addEventListener("click", () => { S.shown += 30; render(); });
}

// ---------- community drawer ----------
async function route() {
  const m = location.hash.match(/^#([ch])\/(\d+)$/);
  if (!m) return closeDrawer();
  const id = +m[2];
  const cid = m[1] === "c" ? id : S.homes[id]?.community_id;
  if (!cid || !S.comms[cid]) return closeDrawer();
  openDrawer(`<div class="p-6 text-sm text-slate-500">Loading…</div>`);
  const d = (S.detail[cid] ||= await getJSON(`data/c/${cid}.json`));
  openDrawer(drawerHTML(d));
  $("#close").addEventListener("click", () => (location.hash = ""));
  $("#down")?.addEventListener("change", (e) => reprice(d, +e.target.value));
  if (m[1] === "h") {
    const row = document.getElementById(`home-${id}`);
    row?.classList.add("row-hi");
    row?.scrollIntoView({ block: "center" });
  }
  const c = S.comms[cid];
  if (c.lat != null) S.map.setView([c.lat, c.lng], Math.max(S.map.getZoom(), 12));
}

function openDrawer(html) {
  $("#drawer").innerHTML = html;
  $("#drawer").classList.remove("translate-x-full");
  $("#backdrop").classList.remove("hidden");
  document.body.style.overflow = "hidden";
}
function closeDrawer() {
  $("#drawer").classList.add("translate-x-full");
  $("#backdrop").classList.add("hidden");
  document.body.style.overflow = "";
}

function drawerHTML(d) {
  const c = S.comms[d.community_id];
  const inc = Object.fromEntries(d.incentives.map((i) => [i.incentive_id, i]));
  const homes = d.homes.map((h) => {
    const offers = h.offers.map((o) => `<li>${esc(inc[o.incentive_id]?.label || "Offer")}: <span class="num">${range(o.value_low, o.value_high)}</span>${o.counted ? "" : " <span class='text-slate-400'>(not counted)</span>"}</li>`).join("");
    return `<tr id="home-${h.home_id}" class="border-t border-slate-100 align-top">
      <td class="py-2 pr-2"><div class="font-medium">${esc(h.address || h.lot || "—")}</div>
        <div class="text-xs text-slate-500">${esc(h.plan || "")} · ${beds(h.beds)} bd · ${h.baths ?? "?"} ba${h.sqft ? " · " + h.sqft.toLocaleString() + " sq ft" : ""}</div>
        <div class="text-xs text-slate-500">${ready(h)}</div>
        ${offers ? `<details class="text-xs mt-1"><summary class="cursor-pointer text-sky-700">${h.offers.length} offer${h.offers.length > 1 ? "s" : ""} (pick one)</summary><ul class="list-disc pl-4 mt-1">${offers}</ul></details>` : ""}</td>
      <td class="py-2 pr-2 text-right num">${usd(h.asking)}</td>
      <td class="py-2 text-right num font-semibold" data-true="${h.home_id}">${range(h.true_price_low, h.true_price_high)}</td></tr>`;
  }).join("");

  const ps = d.past_sales;
  const past = ps.past_sales
    ? `<div class="grid grid-cols-2 sm:grid-cols-3 gap-3 num">
        <div><div class="text-xs text-slate-500">Median paid (est.)</div><div class="text-lg font-semibold">${usd(ps.median_est_price)}</div></div>
        <div><div class="text-xs text-slate-500">Middle half</div><div>${short(ps.p25)} – ${short(ps.p75)}</div></div>
        <div><div class="text-xs text-slate-500">Median asking today</div><div>${usd(ps.median_asking_now)}</div></div></div>
       <p class="text-xs text-slate-500 mt-2">${ps.n} sales, ${esc(ps.period)}; from ${esc(ps.method)}. ${esc(ps.caveat)}</p>`
    : `<p class="text-sm text-slate-500">${esc(ps.reason)}</p>`;

  const incs = d.incentives.length ? d.incentives.map((i) => `<li class="py-2 border-t border-slate-100">
      <div class="flex justify-between gap-3"><div><div class="font-medium">${esc(i.label || KIND[i.kind])}</div>
        <div class="text-xs text-slate-500">${KIND[i.kind] || esc(i.kind)}${i.amount_is_up_to ? " · up to: may pay less, or nothing" : ""}${i.lender ? " · with " + esc(i.lender) : ""}${i.expires ? " · ends " + esc(i.expires) : ""}${i.eligibility ? " · " + esc(i.eligibility) : ""}${i.included_in_price ? " · already in the asking price" : ""}</div></div>
        <div class="text-right num text-sm shrink-0">${i.included_in_price ? "in the price" : i.value_high != null ? range(i.value_low, i.value_high) : "not priced"}${i.homes_priced > 1 ? `<div class="text-[11px] text-slate-400">across ${i.homes_priced} homes</div>` : ""}</div></div>
      <a href="${esc(i.source_url)}" target="_blank" rel="noopener" class="text-xs text-sky-700">Builder's terms ↗</a></li>`).join("")
    : `<li class="text-sm text-slate-500">No incentives listed on the builder's site.</li>`;

  const plans = d.plans.map((p) => {
    const facts = [p.downstairs_bedroom === true && "bedroom downstairs", p.downstairs_bedroom === false && "no bedroom downstairs",
      p.primary_floor && `owner's suite on floor ${p.primary_floor}`, p.loft && "loft", p.flex_room && "flex room",
      p.garage_bays && `${p.garage_bays}-car garage`].filter(Boolean);
    const rooms = (p.rooms || []).filter((r) => r.dims).map((r) => `${esc(r.name)} ${esc(r.dims)}`).join(" · ");
    return `<li class="py-2 border-t border-slate-100"><div class="flex justify-between gap-3">
      <div class="font-medium">${esc(p.name)}</div><div class="num text-sm">${p.from_price ? "from " + usd(p.from_price) : ""}</div></div>
      <div class="text-xs text-slate-500 num">${beds(p.beds_min)}${p.beds_max && p.beds_max !== p.beds_min ? "–" + beds(p.beds_max) : ""} bd · ${p.baths ?? "?"} ba ·
        ${p.sqft_min ? p.sqft_min.toLocaleString() + (p.sqft_max && p.sqft_max !== p.sqft_min ? "–" + p.sqft_max.toLocaleString() : "") + " sq ft" : ""}${p.stories ? " · " + p.stories + " story" : ""}</div>
      ${facts.length ? `<div class="text-xs mt-1">${facts.join(" · ")}</div>` : ""}
      ${rooms ? `<div class="text-xs text-slate-600 mt-1">${rooms}</div>` : ""}
      ${p.layout_read_from_image ? `<div class="text-[11px] text-violet-700 mt-1">Read from the floor-plan image by Muse Spark</div>` : ""}</li>`;
  }).join("");

  const downs = [5, 10, 15, 20, 25, 30].map((v) => `<option value="${v}" ${v === 20 ? "selected" : ""}>${v}%</option>`).join("");
  return `<div class="sticky top-0 bg-white border-b border-slate-200 px-5 py-4 flex justify-between gap-3 z-10">
      <div class="min-w-0"><h2 class="text-lg font-semibold truncate">${esc(d.name)}</h2>
        <div class="text-xs text-slate-500">${esc(d.builder)} · ${esc(c.city)}, ${esc(d.county)} County · ${PRODUCT[d.product] || "Type not given"}${d.age_restricted ? " · 55+" : ""}</div>
        <a href="${esc(d.source_url)}" target="_blank" rel="noopener" class="text-xs text-sky-700">Builder's page ↗</a>
        <span class="text-xs text-slate-400"> read ${esc(c.fetched || "")}</span></div>
      <button id="close" class="text-slate-500 hover:text-slate-800 text-2xl leading-none px-2" aria-label="Close">×</button></div>
    <div class="px-5 py-4 flex flex-col gap-6">
      <section><div class="flex flex-wrap items-center justify-between gap-2 mb-2">
          <h3 class="font-semibold">Homes for sale</h3>
          <label class="text-xs text-slate-600 flex items-center gap-2">Down payment
            <select id="down" class="border border-slate-300 rounded px-1.5 py-1" ${S.api ? "" : "disabled title='Needs the live server'"}>${downs}</select></label></div>
        <p id="down-note" class="text-xs text-slate-500 mb-2">True price at 20% down: guaranteed – best case.${S.api ? "" : " (Changing the down payment needs the live server.)"}</p>
        <div class="overflow-x-auto"><table class="w-full text-sm"><thead><tr class="text-xs text-slate-500 text-left">
          <th class="py-1 font-normal">Home</th><th class="py-1 font-normal text-right">Asking</th><th class="py-1 font-normal text-right">True price</th></tr></thead>
          <tbody>${homes || `<tr><td class="py-2 text-slate-500" colspan="3">No homes listed now.</td></tr>`}</tbody></table></div></section>
      <section><h3 class="font-semibold mb-1">Incentives, in dollars</h3>
        <p class="text-xs text-slate-500 mb-1">Ranges run from the guaranteed value to the best case. Offers don't stack unless their terms say so.</p>
        <ul>${incs}</ul></section>
      <section><h3 class="font-semibold mb-2">What earlier buyers paid</h3>${past}</section>
      <section><h3 class="font-semibold mb-1">Floor plans</h3><ul>${plans || `<li class="text-sm text-slate-500">No plans listed.</li>`}</ul></section>
    </div>`;
}

async function reprice(d, down) {
  $("#down-note").textContent = `Recalculating at ${down}% down…`;
  const priced = d.homes.filter((h) => h.offers.length && h.asking);
  try {
    const out = await Promise.all(priced.map((h) => getJSON(`api/reprice?home_id=${h.home_id}&down=${down}`)));
    out.forEach((r) => {
      const cell = document.querySelector(`[data-true="${r.home_id}"]`);
      if (cell) cell.textContent = range(r.true_price_low, r.true_price_high);
    });
    $("#down-note").textContent = `True price at ${down}% down: guaranteed – best case. Homes without offers are unchanged.`;
  } catch (e) {
    $("#down-note").textContent = `Couldn't recalculate: ${e.message}`;
  }
}

// ---------- agent ----------
const EXAMPLES = [
  "I need at least 4 bedrooms, a true price under $1.1M, and to move in by January. Contra Costa or San Joaquin County. What are my best options?",
  "What incentives does Lilac at Oakwood Trails offer, and what are they worth?",
  "What did earlier buyers pay at Cairnway in Tracy, compared with today's asking prices?",
  "If I put 10% down on 1576 Torrey Dr in Manteca, what's its true price?",
  "Which floor plans at Driftway in Lathrop have a bedroom downstairs?",
  "What's the cheapest home in a 55+ community?",
];

function examples() {
  const qs = EXAMPLES.filter((q) => S.demo[questionKey(q)]);
  $("#examples").innerHTML = qs.map((q, i) =>
    `<button data-i="${i}" class="text-left text-xs bg-slate-100 hover:bg-slate-200 text-slate-700 rounded-full px-3 py-1">${esc(q)}</button>`).join("");
  $("#examples").querySelectorAll("button").forEach((b) => b.addEventListener("click", () => { $("#q").value = qs[+b.dataset.i]; ask(); }));
  $("#ask-form").addEventListener("submit", (e) => { e.preventDefault(); ask(); });
}

async function ask() {
  const q = $("#q").value.trim();
  if (!q) return;
  const box = $("#answer");
  box.classList.remove("hidden");
  box.innerHTML = `<p class="text-sm text-slate-500">Thinking… the agent is searching the listings (usually 5–20 seconds).</p>`;
  $("#ask-btn").disabled = true;
  let res;
  try {
    res = S.api ? await getJSON("api/ask", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ question: q }) })
      : S.demo[questionKey(q)] ? { ...S.demo[questionKey(q)], source: "checked" }
      : { error: "The live agent isn't running on this copy of the site. Try one of the example questions." };
  } catch (e) {
    res = { error: e.message };
  }
  $("#ask-btn").disabled = false;
  if (res.error) {
    box.innerHTML = `<p class="text-sm text-rose-700">${esc(res.error)}</p>`;
    return;
  }
  const label = { checked: "Answer checked by hand", cached: "Live answer from Muse Spark Open (asked earlier today)",
    live: `Live answer from Muse Spark Open · ${res.turns} steps · $${(res.cost_usd ?? 0).toFixed(3)}` }[res.source] || "";
  box.innerHTML = `<div class="text-[11px] uppercase tracking-wide text-slate-400 mb-1">${label}</div>${answerHTML(res.answer)}`;
}

// Markdown subset (paragraphs, lists, bold) with [H12] / [C3] / [P7] citations as numbered source links.
function answerHTML(md) {
  const sources = [];
  const cite = (tok) => {
    const kind = tok[0], id = +tok.slice(1);
    let n = sources.findIndex((s) => s.tok === tok);
    if (n < 0) { sources.push({ tok, kind, id }); n = sources.length - 1; }
    const href = kind === "H" ? `#h/${id}` : kind === "C" ? `#c/${id}` : S.plans[id] ? `#c/${S.plans[id].community_id}` : "#";
    return `<a class="cite" href="${href}" title="${esc(sourceLabel(kind, id))}">${n + 1}</a>`;
  };
  const inline = (s) => esc(s)
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/\[((?:[HCP]\d+)(?:\s*,\s*[HCP]\d+)*)\]/g, (_, g) => g.split(/\s*,\s*/).map(cite).join(""));
  const out = [];
  let list = null;
  for (const raw of md.split("\n")) {
    const line = raw.trim().replace(/^#+\s*/, "");
    const li = line.match(/^(?:[-*•]|(\d+)[.)])\s+(.*)$/);
    if (li) {
      const tag = li[1] ? "ol" : "ul";
      if (list !== tag) { if (list) out.push(`</${list}>`); out.push(`<${tag}>`); list = tag; }
      out.push(`<li>${inline(li[2])}</li>`);
      continue;
    }
    if (list) { out.push(`</${list}>`); list = null; }
    if (line) out.push(`<p>${inline(line)}</p>`);
  }
  if (list) out.push(`</${list}>`);
  const src = sources.map((s, i) => {
    const url = s.kind === "H" ? S.homes[s.id]?.source_url : s.kind === "C" ? S.comms[s.id]?.source_url : S.comms[S.plans[s.id]?.community_id]?.source_url;
    const date = s.kind === "H" ? S.homes[s.id]?.fetched : S.comms[s.kind === "C" ? s.id : S.plans[s.id]?.community_id]?.fetched;
    return `<li><span class="text-slate-400">${i + 1}.</span> ${esc(sourceLabel(s.kind, s.id))}${url ? ` · <a class="text-sky-700" href="${esc(url)}" target="_blank" rel="noopener">builder's page ↗</a>` : ""}${date ? ` <span class="text-slate-400">read ${esc(date)}</span>` : ""}</li>`;
  }).join("");
  return `<div class="answer text-sm leading-relaxed">${out.join("")}</div>` +
    (src ? `<div class="mt-3 text-xs text-slate-600"><div class="font-medium mb-1">Sources</div><ol class="space-y-0.5">${src}</ol></div>` : "");
}

function sourceLabel(kind, id) {
  if (kind === "H") { const h = S.homes[id]; return h ? `${h.address || h.lot}, ${h.community} (${h.builder})` : `home ${id}`; }
  if (kind === "C") { const c = S.comms[id]; return c ? `${c.name}, ${c.city} (${c.builder})` : `community ${id}`; }
  const p = S.plans[id], c = p && S.comms[p.community_id];
  return p ? `${p.name} plan at ${c ? c.name : "community " + p.community_id}` : `plan ${id}`;
}

boot().catch((e) => {
  document.querySelector("main").insertAdjacentHTML("afterbegin", `<p class="text-rose-700 text-sm">Couldn't load the data: ${esc(e.message)}</p>`);
});
