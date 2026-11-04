// Bay Area New-Home True Price: map, home list, community drawer and the agent box.
// Data: data/site.json (communities, homes, plan index), data/c/<id>.json (one community), data/demo_answers.json.
// API (optional, same origin): POST /api/ask, GET /api/health.
"use strict";

const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const usd = (n) => (n == null ? "—" : "$" + Math.round(n).toLocaleString("en-US"));
const short = (n) => (n == null ? "—" : n >= 1e6 ? "$" + (n / 1e6).toFixed(2).replace(/\.?0+$/, "") + "M" : "$" + Math.round(n / 1e3) + "K");
const day = (d) => new Date(d + "T12:00:00").toLocaleDateString("en-US", { month: "short", day: "numeric" });
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
    `A lower rate is worth what it would cost to buy: one point (1% of the loan) per ${m.assumptions.rate_pct_per_point}% below the no-points rate, ` +
    `which is the market rate for a fixed loan and an estimated ${m.assumptions.arm_par_spread_pct}% lower for an ARM.`;
  const hm = m.history || {};
  if (hm.checks) $("#method-history").innerHTML = `Prices are checked every day: ${hm.checks} check${hm.checks > 1 ? "s" : ""} ` +
    `since ${day(hm.first)}${hm.price_changed != null ? `; at the last one, ${hm.price_changed} asking price${hm.price_changed === 1 ? "" : "s"} changed, ` +
    `${hm.new} home${hm.new === 1 ? "" : "s"} appeared and ${hm.gone} came off the list` : ""}. ` +
    `<a class="text-sky-700 underline" href="data/price_history.csv" download>Download the full price history (CSV)</a>.`;
  examples();
  await initMap();
  ["#f-county", "#f-max", "#f-beds", "#f-product", "#f-now"].forEach((id) => $(id).addEventListener("change", () => { S.shown = 30; render(); }));
  $("#f-county").addEventListener("change", fitCounty);
  render();
  S.map.onMove(followMap);
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

const PIN = "#0369a1";

// Two map backends with one interface: Google Maps when the deploy has a browser key (config.js, written at export, not in git),
// else Leaflet on OpenStreetMap tiles.
function leafletMap(pts) {
  const map = L.map("map", { scrollWheelZoom: true }).setView([37.6, -121.8], 9);
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 18, attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
  }).addTo(map);
  const layer = L.layerGroup().addTo(map);
  map.invalidateSize();
  if (pts.length) map.fitBounds(pts, { padding: [20, 20], maxZoom: 12 });
  return {
    clear: () => layer.clearLayers(),
    pin: (lat, lng, radius, color, html, hollow) => L.circleMarker([lat, lng], { radius, color: hollow ? "#475569" : "#0f172a",
      weight: hollow ? 2 : 1, fillColor: hollow ? "#ffffff" : color, fillOpacity: 0.9 }).bindPopup(html).addTo(layer),
    focus: (lat, lng) => map.setView([lat, lng], Math.max(map.getZoom(), 12)),
    fit: (points) => map.fitBounds(points, { padding: [20, 20], maxZoom: 12 }),
    contains: (lat, lng) => map.getBounds().contains([lat, lng]),
    onMove: (fn) => map.on("moveend", fn),
  };
}

async function googleMap(key, pts) {
  await new Promise((resolve, reject) => {
    window.__gmapsReady = resolve;
    const tag = document.createElement("script");
    tag.src = `https://maps.googleapis.com/maps/api/js?key=${encodeURIComponent(key)}&v=weekly&loading=async&callback=__gmapsReady`;
    tag.async = true;
    tag.onerror = reject;
    document.head.appendChild(tag);
  });
  const { Map, InfoWindow } = await google.maps.importLibrary("maps");
  const { Marker } = await google.maps.importLibrary("marker");
  const map = new Map($("#map"), { center: { lat: 37.6, lng: -121.8 }, zoom: 9, gestureHandling: matchMedia("(pointer: coarse)").matches ? "cooperative" : "greedy",
    mapTypeControl: false, streetViewControl: false, clickableIcons: false });
  const fit = (points) => {
    const b = new google.maps.LatLngBounds();
    points.forEach(([lat, lng]) => b.extend({ lat, lng }));
    map.fitBounds(b, 20);
    google.maps.event.addListenerOnce(map, "idle", () => map.getZoom() > 12 && map.setZoom(12));
  };
  if (pts.length) fit(pts);
  const info = new InfoWindow();
  let pins = [];
  return {
    clear: () => { pins.forEach((m) => m.setMap(null)); pins = []; },
    pin: (lat, lng, radius, color, html, hollow) => {
      const m = new Marker({ map, position: { lat, lng }, icon: { path: google.maps.SymbolPath.CIRCLE, scale: radius,
        fillColor: hollow ? "#ffffff" : color, fillOpacity: 0.9, strokeColor: hollow ? "#475569" : "#0f172a", strokeWeight: hollow ? 2 : 1 } });
      m.addListener("click", () => { info.setContent(html); info.open({ anchor: m, map }); });
      pins.push(m);
    },
    focus: (lat, lng) => { map.setCenter({ lat, lng }); map.setZoom(Math.max(map.getZoom(), 12)); },
    fit,
    contains: (lat, lng) => map.getBounds()?.contains({ lat, lng }) ?? true,
    onMove: (fn) => map.addListener("idle", fn),
  };
}

async function initMap() {
  $("#legend").innerHTML = `<span class="flex items-center gap-1"><span class="inline-block w-3 h-3 rounded-full" style="background:${PIN}"></span>homes for sale (bigger = more homes)</span>` +
    `<span class="flex items-center gap-1"><span class="inline-block w-3 h-3 rounded-full bg-white border-2 border-slate-600"></span>floor plans only, no priced home</span>`;
  const pts = S.site.communities.filter((c) => c.lat != null).map((c) => [c.lat, c.lng]);
  if (window.GMAPS_KEY) {
    const useOsm = (why) => {
      console.warn("Google Maps did not load; using OpenStreetMap", why);
      window.gm_authFailure = null;
      $("#map").innerHTML = "";
      $("#map-credit").textContent = "";
      S.map = leafletMap(pts);
    };
    // A refused key (wrong address, billing off) raises no error: Google calls this, sometimes before the map exists,
    // and paints "Oops" over it.
    let refused = false;
    window.gm_authFailure = () => {
      refused = true;
      if (S.map) { useOsm("key refused"); S.map.onMove(followMap); render(); }
    };
    try {
      const google = await googleMap(window.GMAPS_KEY, pts);
      if (refused) { useOsm("key refused"); return; }
      S.map = google;
      $("#map-credit").textContent = "Map data © Google.";
      return;
    } catch (e) {
      useOsm(e);
      return;
    }
  }
  S.map = leafletMap(pts);
}

// Choosing a county brings its pins into view.
function fitCounty() {
  const county = $("#f-county").value;
  const pts = S.site.communities.filter((c) => c.lat != null && (c.homes || c.list_without_homes) && (!county || c.county === county))
    .map((c) => [c.lat, c.lng]);
  if (pts.length) S.map.fit(pts);
}

// A move between two readings far apart happened somewhere between them: don't present the later date as the day it happened.
const moveDate = (x) => (x.since && (new Date(x.date) - new Date(x.since)) / 864e5 > 3 ? `Between ${day(x.since)} and ${day(x.date)}` : day(x.date));

const shortDate = (d) => new Date(`${d}T12:00:00`).toLocaleDateString("en-US", { month: "short", day: "numeric" });

// Communities with plans but no priced home that pass the filters. A price or bedroom filter can't vouch for one it knows nothing about.
function plansOnly() {
  const county = $("#f-county").value, max = +$("#f-max").value || null, minBeds = +$("#f-beds").value || null;
  const product = $("#f-product").value;
  if ($("#f-now").checked) return [];
  return S.site.communities.filter((c) => c.list_without_homes && (!county || c.county === county) &&
    (!max || (c.advertised_from != null && c.advertised_from <= max)) && (!minBeds || (c.plan_beds_max ?? 0) >= minBeds) &&
    (!product || c.product === product));
}

// The builder's advertised price and how it and the offers have moved since we started checking. Prices and offers only as read.
function advertisedHTML(c) {
  if (!c.tracked_since) return "";
  const moved = c.price_changes.at(-1);
  return `<div class="mt-1 text-xs num">${c.advertised_from ? `Advertised from <b>${short(c.advertised_from)}</b>` : "No advertised price"}${moved ? `, was ${short(moved.was)} (${moveDate(moved)})` : ""}</div>
    ${c.incentives_added.slice(0, 2).map((i) => `<div class="text-xs text-emerald-700">First seen ${shortDate(i.date)}: ${esc(i.label)}</div>`).join("")}
    ${c.incentives_ended.slice(0, 2).map((i) => `<div class="text-xs text-slate-500">Last seen ${shortDate(i.last_seen)}: ${esc(i.label)}</div>`).join("")}`;
}

// What a community starts at: the builder's advertised price, else its cheapest home for sale.
const startingPrice = (c, hs) => c.advertised_from ?? c.min_asking ?? hs?.[0]?.asking ?? null;

function pinPopup(c, hs) {
  return `<div class="text-sm"><div class="font-semibold">${esc(c.name)}</div>
      <div class="text-xs text-slate-500">${esc(c.builder)} · ${esc(c.city)}</div>
      ${hs ? `<div class="mt-1 num">${hs.length} home${hs.length > 1 ? "s" : ""} match · true price from <b>${short(hs[0].true_price_high ?? hs[0].asking)}</b></div>`
        : `<div class="mt-1 text-xs">${c.status === "coming_soon" ? "Coming soon. " : ""}Floor plans only, no priced home right now.</div>`}
      ${advertisedHTML(c)}
      <a href="#c/${c.id}" class="inline-block mt-2 text-sky-700 font-medium">Open community →</a></div>`;
}

function render() {
  const homes = filtered();
  const byComm = {};
  homes.forEach((h) => (byComm[h.community_id] ||= []).push(h));
  const plans = plansOnly();
  $("#count").textContent = `${homes.length} homes in ${Object.keys(byComm).length} communities` +
    (plans.length ? ` · ${plans.length} more with floor plans only` : "");

  S.map.clear();
  plans.filter((c) => c.lat != null).forEach((c) => S.map.pin(c.lat, c.lng, 5, "", pinPopup(c), true));
  Object.entries(byComm).forEach(([id, hs]) => {
    const c = S.comms[id];
    if (c.lat != null) S.map.pin(c.lat, c.lng, 6 + Math.min(10, Math.sqrt(hs.length) * 2), PIN, pinPopup(c, hs));
  });
  S.rows = [...Object.keys(byComm).map((id) => ({ c: S.comms[id], hs: byComm[id] })), ...plans.map((c) => ({ c }))];
  const unmapped = S.rows.filter((r) => r.c.lat == null).map((r) => r.c.name);
  $("#unmapped").textContent = unmapped.length
    ? `No map pin yet (no location we can read on the builder's page): ${unmapped.join(", ")}. They're in the list.`
    : "";

  renderList();
}

// The list shows what the map shows. A community with no pin can't be placed, so it's listed only while every pin is in view.
const followMap = () => { S.shown = 30; renderList(); };
function inView(rows) {
  const pinned = rows.filter((r) => r.c.lat != null);
  const seen = pinned.filter((r) => S.map.contains(r.c.lat, r.c.lng));
  return seen.length === pinned.length ? rows : seen;
}

function renderList() {
  const rows = inView(S.rows);
  $("#list-head").textContent = `${rows.length} ${rows.length === 1 ? "community" : "communities"} in this map view, lowest starting price first · prices as of ${day(S.site.meta.generated)}`;
  rows.sort((a, b) => (startingPrice(a.c, a.hs) ?? Infinity) - (startingPrice(b.c, b.hs) ?? Infinity) || a.c.name.localeCompare(b.c.name));
  const list = rows.slice(0, S.shown).map(({ c, hs }) => {
    const start = startingPrice(c, hs), moved = c.price_changes?.at(-1);
    const offers = c.incentives?.filter((i) => i.amount_usd || i.rate).length || 0;
    return `<li><a href="#c/${c.id}" class="block px-4 py-3 hover:bg-slate-50">
      <div class="flex justify-between gap-3"><div class="min-w-0">
        <div class="font-medium truncate">${esc(c.name)}</div>
        <div class="text-xs text-slate-500 truncate">${esc(c.builder)} · ${esc(c.city)}, ${esc(c.county)}${c.status === "coming_soon" ? " · coming soon" : ""}</div>
        <div class="text-xs text-slate-500 num">${hs ? `${hs.length} home${hs.length > 1 ? "s" : ""} for sale` : "Floor plans only"}${offers ? ` · ${offers} incentive${offers > 1 ? "s" : ""}` : ""}${c.has_past_sales ? " · county records" : ""}</div>
      </div><div class="text-right shrink-0 num">
        <div class="text-[11px] text-slate-500">${start ? "from" : ""}</div>
        <div class="font-semibold">${start ? short(start) : "Price not given"}</div>
        ${moved ? `<div class="text-[11px] ${moved.now < moved.was ? "text-emerald-700" : "text-rose-700"}">${moved.now < moved.was ? "down" : "up"} ${short(Math.abs(moved.now - moved.was))} ${day(moved.date)}</div>` : ""}
      </div></div></a></li>`;
  });
  if (rows.length > S.shown) list.push(`<li class="p-3 text-center"><button id="more" class="text-sky-700 text-sm">Show ${Math.min(30, rows.length - S.shown)} more</button></li>`);
  if (!rows.length) list.push(`<li class="p-4 text-sm text-slate-500">${S.rows.length ? "No communities in this map view. Zoom out or move the map." : "No communities match. Loosen a filter."}</li>`);
  $("#list").innerHTML = list.join("");
  $("#more")?.addEventListener("click", () => { S.shown += 30; renderList(); });
}

// ---------- community page ----------
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
  wireChart(d.series);
  document.title = `${d.name}, ${S.comms[cid].city} · Bay Area New-Home True Price`;
  $("#drawer").scrollTop = 0;
  if (m[1] === "h") {
    const row = document.getElementById(`home-${id}`);
    row?.classList.add("row-hi");
    row?.scrollIntoView({ block: "center" });
  }
}

function openDrawer(html) {
  $("#drawer").innerHTML = html;
  $("#drawer").classList.remove("translate-x-full");
  $("#drawer").classList.add("shadow-2xl");
  $("#backdrop").classList.remove("hidden");
  document.body.style.overflow = "hidden";
}
function closeDrawer() {
  document.title = "Bay Area New-Home True Price";
  $("#drawer").classList.add("translate-x-full");
  $("#drawer").classList.remove("shadow-2xl");
  $("#backdrop").classList.add("hidden");
  document.body.style.overflow = "";
}

function historyHTML(h) {
  const hs = h.history || [];
  if (hs.length < 2) return "";
  const diff = hs[hs.length - 1][1] - hs[0][1];
  const head = diff === 0 ? `Asking unchanged since ${day(hs[0][0])}`
    : `Asking ${diff < 0 ? "down" : "up"} ${usd(Math.abs(diff))} since ${day(hs[0][0])}`;
  const tone = diff < 0 ? "text-emerald-700" : diff > 0 ? "text-rose-700" : "text-slate-500";
  const rows = hs.map(([d, a]) => `<li>${day(d)}: asking ${usd(a)}</li>`).join("");
  return `<details class="text-xs mt-1"><summary class="cursor-pointer ${tone}">${head} · ${hs.length} daily checks</summary>
    <ul class="pl-4 mt-1 num">${rows}</ul></details>`;
}

// One home's math in plain sums. The two ends can come from different offers.
function priceSums(asking, valueLow, valueHigh, offers) {
  if (!valueHigh) return "";
  const named = (value, pick) => offers.find((o) => pick(o) === value)?.label || "offers";
  const low = (o) => o.value_low, high = (o) => o.value_high;
  const lowName = named(valueLow, low), highName = named(valueHigh, high);
  const sum = (value, name) => `${usd(asking)} − ${usd(value)}${name ? ` (${esc(name)})` : ""} = <b>${usd(asking - value)}</b>`;
  if (valueLow === valueHigh) return `<div>${sum(valueHigh, highName)}</div>`;
  const same = valueLow && lowName === highName;
  return `${same ? `<div>${esc(highName)}</div>` : ""}
    <div>If the "up to" offer pays nothing: ${valueLow ? sum(valueLow, same ? "" : lowName) : `<b>${usd(asking)}</b>`}</div>
    <div>If it pays in full: ${sum(valueHigh, same ? "" : highName)}</div>`;
}

const WHY = { eligibility_limited: "some buyers only, not counted", conditional: "paid only if the builder misses its date, not counted" };

// Advertised price over time: one series, held between readings, so it steps. Hover names each reading.
const CHART = { w: 640, h: 190, l: 58, r: 16, t: 12, b: 26 };
function chartScales(series) {
  const ts = series.map(([d]) => new Date(d + "T12:00:00").getTime());
  const ps = series.map(([, p]) => p);
  const lo = Math.min(...ps), hi = Math.max(...ps), pad = Math.max((hi - lo) * 0.25, hi * 0.01);
  const x = (v) => CHART.l + (v - ts[0]) / (ts.at(-1) - ts[0] || 1) * (CHART.w - CHART.l - CHART.r);
  const y = (v) => CHART.t + (hi + pad - v) / (hi - lo + 2 * pad) * (CHART.h - CHART.t - CHART.b);
  return { ts, x, y, lo, hi, pad };
}
function priceChart(series) {
  if (series.length < 2) return "";
  const { ts, x, y, lo, hi, pad } = chartScales(series);
  const path = series.map(([, p], i) => (i ? `H${x(ts[i]).toFixed(1)}V${y(p).toFixed(1)}` : `M${x(ts[0]).toFixed(1)} ${y(p).toFixed(1)}`)).join("");
  const ticks = [hi + pad * 0.6, (hi + lo) / 2, lo - pad * 0.6].map((v) => `<line x1="${CHART.l}" x2="${CHART.w - CHART.r}" y1="${y(v)}" y2="${y(v)}" stroke="#e2e8f0"/>
    <text x="${CHART.l - 6}" y="${y(v) + 4}" text-anchor="end" font-size="11" fill="#64748b">${short(v)}</text>`).join("");
  const changes = series.map(([, p], i) => (i && p !== series[i - 1][1]
    ? `<circle cx="${x(ts[i])}" cy="${y(p)}" r="4.5" fill="${PIN}" stroke="#fff" stroke-width="2"/>` : "")).join("");
  return `<div class="relative mt-2"><svg id="chart" viewBox="0 0 ${CHART.w} ${CHART.h}" class="w-full" role="img" aria-label="Advertised starting price by date">
      ${ticks}<path d="${path}" fill="none" stroke="${PIN}" stroke-width="2"/>${changes}
      <text x="${CHART.l}" y="${CHART.h - 8}" font-size="11" fill="#64748b">${day(series[0][0])}</text>
      <text x="${CHART.w - CHART.r}" y="${CHART.h - 8}" text-anchor="end" font-size="11" fill="#64748b">${day(series.at(-1)[0])}</text>
      <line id="chart-x" y1="${CHART.t}" y2="${CHART.h - CHART.b}" stroke="#94a3b8" visibility="hidden"/>
      <rect x="${CHART.l}" y="0" width="${CHART.w - CHART.l - CHART.r}" height="${CHART.h}" fill="transparent"/></svg>
    <div id="chart-tip" class="hidden absolute top-0 bg-white border border-slate-200 rounded px-2 py-1 text-xs num shadow-sm pointer-events-none"></div></div>`;
}
function wireChart(series) {
  const svg = $("#chart");
  if (!svg) return;
  const { ts, x } = chartScales(series);
  const tip = $("#chart-tip"), line = $("#chart-x");
  svg.addEventListener("mousemove", (e) => {
    const box = svg.getBoundingClientRect(), mx = (e.clientX - box.left) / box.width * CHART.w;
    const i = ts.reduce((best, v, k) => (Math.abs(x(v) - mx) < Math.abs(x(ts[best]) - mx) ? k : best), 0);
    line.setAttribute("x1", x(ts[i])); line.setAttribute("x2", x(ts[i])); line.setAttribute("visibility", "visible");
    tip.textContent = `${day(series[i][0])}: ${usd(series[i][1])}`;
    tip.classList.remove("hidden");
    tip.style.left = `${Math.min(x(ts[i]) / CHART.w * box.width + 8, box.width - 150)}px`;
  });
  svg.addEventListener("mouseleave", () => { line.setAttribute("visibility", "hidden"); tip.classList.add("hidden"); });
}

function drawerHTML(d) {
  const c = S.comms[d.community_id];
  const inc = Object.fromEntries(d.incentives.map((i) => [i.incentive_id, i]));
  const homes = d.homes.map((h) => {
    const offers = h.offers.map((o) => `<li>${esc(inc[o.incentive_id]?.label || "Offer")}: ${o.why === "in_price" ? "<span class='text-slate-500'>already in the asking price</span>" : `<span class="num">${range(o.value_low, o.value_high)}</span>${o.counted ? "" : ` <span class='text-slate-400'>(${WHY[o.why] || "not counted"})</span>`}`}</li>`).join("");
    return `<tr id="home-${h.home_id}" class="border-t border-slate-100 align-top">
      <td class="py-2 pr-2"><div class="font-medium">${esc(h.address || h.lot || "—")}</div>
        <div class="text-xs text-slate-500">${esc(h.plan || "")} · ${beds(h.beds)} bd · ${h.baths ?? "?"} ba${h.sqft ? " · " + h.sqft.toLocaleString() + " sq ft" : ""}</div>
        <div class="text-xs text-slate-500">${ready(h)}</div>
        ${historyHTML(h)}
        ${offers ? `<details class="text-xs mt-1"><summary class="cursor-pointer text-sky-700">${h.offers.length} incentive${h.offers.length > 1 ? "s" : ""}</summary><ul class="list-disc pl-4 mt-1">${offers}</ul></details>` : ""}</td>
      <td class="py-2 pr-2 text-right num">${usd(h.asking)}</td>
      <td class="py-2 text-right num font-semibold" data-true="${h.home_id}">${range(h.true_price_low, h.true_price_high)}</td></tr>
      <tr><td colspan="3" class="pb-2 text-xs num text-slate-600" data-sums="${h.home_id}">${h.asking && h.true_price_high != null ? priceSums(h.asking, h.asking - h.true_price_high, h.asking - h.true_price_low,
        h.offers.filter((o) => o.counted).map((o) => ({ ...o, label: inc[o.incentive_id]?.label }))) : ""}</td></tr>`;
  }).join("");

  const ps = d.past_sales;
  const hasRecorded = (ps.records || []).some((r) => r.recorded_price != null);
  const recs = (ps.records || []).map((r) => `<tr class="border-t border-slate-100">
      <td class="py-1.5 pr-2">${esc(r.address)}</td>
      <td class="py-1.5 pr-2 text-right num">${r.assessed_value != null ? usd(r.assessed_value) : "not reset yet"}</td>
      <td class="py-1.5 pr-2 num">${esc(r.year_built || r.sale_date)}</td>
      <td class="py-1.5 pr-2 text-right num">${r.living_sqft ? r.living_sqft.toLocaleString("en-US") : "—"}</td>
      ${hasRecorded ? `<td class="py-1.5 text-right num">${r.recorded_price != null ? usd(r.recorded_price) : "—"}</td>` : ""}</tr>`).join("");
  const heldBack = ps.withheld ? ` ${ps.withheld === 1 ? "1 value" : ps.withheld + " values"} not shown: under 60% of this community's median per sq ft, so possibly a home still being built when assessed.` : "";
  const recordsTable = `<details class="mt-2"><summary class="text-sm text-sky-700 cursor-pointer">Each home's county record (${(ps.records || []).length})</summary>
        <div class="overflow-x-auto mt-1"><table class="w-full text-xs"><thead><tr class="text-slate-500 text-left">
          <th class="py-1 font-normal">Address</th><th class="py-1 font-normal text-right">Assessed value</th>
          <th class="py-1 font-normal">${ps.built_period ? "Built" : "Sold"}</th><th class="py-1 font-normal text-right">Sq ft</th>
          ${hasRecorded ? `<th class="py-1 font-normal text-right">Recorded sale price</th>` : ""}</tr></thead>
          <tbody>${recs}</tbody></table></div>
        <p class="text-[11px] text-slate-400 mt-1">Assessed value: land + improvements on the county's ${esc((ps.records || [])[0]?.roll_year ?? "")} roll, as published. On a fully assessed new home that's usually the sale price.${hasRecorded ? " Recorded sale prices come from Alameda's transfer list." : ""}</p></details>`;
  const summary = ps.past_sales === "found"
    ? `<div class="grid grid-cols-2 sm:grid-cols-3 gap-3 num">
        <div><div class="text-xs text-slate-500">Median assessed value</div><div class="text-lg font-semibold">${usd(ps.median_value)}</div></div>
        <div><div class="text-xs text-slate-500">Middle half</div><div>${short(ps.p25)} – ${short(ps.p75)}</div></div>
        <div><div class="text-xs text-slate-500">Median asking today</div><div>${usd(ps.median_asking_now)}</div></div></div>
       <p class="text-xs text-slate-500 mt-2">${ps.n} homes, ${ps.built_period ? "built " + esc(ps.built_period) : "sold " + esc(ps.period)}; ${esc(ps.method)}. ${esc(ps.caveat)}</p>`
    : `<p class="text-sm text-slate-500">${esc(ps.reason)}</p>${ps.past_sales ? `<p class="text-xs text-slate-500 mt-2">Records show ${esc(ps.method)}. ${esc(ps.caveat)}</p>` : ""}`;
  const past = ps.past_sales ? `${summary}${heldBack ? `<p class="text-xs text-slate-500 mt-1">${heldBack}</p>` : ""}${recordsTable}` : summary;

  const communityOffers = d.community_incentives || d.incentives;
  const incs = communityOffers.length ? communityOffers.map((i) => `<li class="py-2 border-t border-slate-100">
      <div class="flex justify-between gap-3"><div><div class="font-medium">${esc(i.label || KIND[i.kind])}</div>
        <div class="text-xs text-slate-500">${i.rate ? `${i.rate}% rate${i.apr ? `, ${i.apr}% APR` : ""} · ` : ""}${i.structure === "term not stated" ? "Rate offer: the builder's page doesn't say how long it lasts, so it's valued for 5 years" : KIND[i.kind] || esc(i.kind)}${i.amount_is_up_to ? " · up to: may pay less, or nothing" : ""}${/\bFHA\b/.test(i.label || "") ? " · FHA loan: its mortgage insurance isn't counted" : ""}${i.lender ? " · with " + esc(i.lender) : ""}${i.expires ? " · ends " + esc(i.expires) : ""}${i.eligibility ? " · " + esc(i.eligibility) : ""}${i.included_in_price ? " · already in the asking price" : ""}</div></div>
        <div class="text-right num text-sm shrink-0">${i.included_in_price ? "in the price" : i.value_high != null ? range(i.value_low, i.value_high) : "not priced"}${i.homes_priced > 1 ? `<div class="text-[11px] text-slate-400">across ${i.homes_priced} homes</div>` : ""}</div></div>
      <a href="${esc(i.source_url)}" target="_blank" rel="noopener" class="text-xs text-sky-700">Builder's terms ↗</a></li>`).join("")
    : `<li class="text-sm text-slate-500">No incentives listed on the builder's site.</li>`;

  const m = S.site.meta.assumptions.unadvertised_incentive_pct;
  const series = d.series || [], seen = d.offers_seen || [];
  const moves = (c.price_changes || []).map((x) => `<li>${moveDate(x)}: from ${usd(x.was)} to ${usd(x.now)}</li>`).join("");
  const pageOffers = (c.incentives || []).map((i) => `<li class="py-1 border-t border-slate-100 text-sm">${esc(i.label || KIND[i.kind])}${i.expires ? ` <span class="text-xs text-slate-500">(ends ${esc(i.expires)})</span>` : ""}</li>`).join("");
  const offerRows = seen.map((o) => `<tr class="border-t border-slate-100 align-top"><td class="py-1.5 pr-3">${esc(o.label)}</td>
      <td class="py-1.5 pr-3 num whitespace-nowrap">${day(o.first_seen)}</td>
      <td class="py-1.5 num whitespace-nowrap ${o.active ? "text-emerald-700" : "text-slate-500"}">${o.active ? "still offered" : day(o.last_seen)}</td></tr>`).join("");
  const start = startingPrice(c, null);
  const offerCount = communityOffers.length || (c.incentives || []).length;
  const extra = start ? `${usd(0)} to ${usd(Math.round(start * m[1] / 100 / 1000) * 1000)}` : "";
  const trend = c.tracked_since ? `<section><h3 class="font-semibold">Advertised price over time</h3>
      <p class="text-sm num mt-1">${c.advertised_from ? `Starts at <b>${usd(c.advertised_from)}</b> as of ${day(c.last_checked)}, the builder's advertised starting price.${c.min_asking && c.min_asking < c.advertised_from ? " Some homes below are listed for less." : ""}` : "No price on the builder's page."}
        ${series.length < 2 ? `Only one reading so far (${day(c.tracked_since)}).` : moves ? "" : `Unchanged since ${day(series[0][0])}.`}</p>
      ${priceChart(series)}
      ${moves ? `<ul class="text-xs text-slate-700 mt-1 flex flex-col gap-0.5">${moves}</ul>` : ""}
      ${series.length > 1 ? `<details class="text-xs mt-2"><summary class="cursor-pointer text-sky-700">All ${series.length} readings</summary><ul class="pl-4 mt-1 num">${series.map(([s, v]) => `<li>${day(s)}: ${usd(v)}</li>`).join("")}</ul></details>` : ""}
    </section>
    <section><h3 class="font-semibold">Incentives over time</h3>
      ${offerRows ? `<div class="overflow-x-auto"><table class="w-full text-sm mt-1"><thead><tr class="text-xs text-slate-500 text-left"><th class="py-1 font-normal">Incentive with an amount or rate</th><th class="py-1 font-normal">First seen</th><th class="py-1 font-normal">Last seen</th></tr></thead><tbody>${offerRows}</tbody></table></div>`
        : `<p class="text-sm text-slate-500 mt-1">No incentive with an amount or rate on record.</p>`}
      <p class="text-[11px] text-slate-400 mt-1">First reading ${day(c.tracked_since)}${(d.archive_copies || []).length ? ` (<a class="underline" href="${esc(d.archive_copies[0].url.replace("id_/", "/"))}" target="_blank" rel="noopener">Internet Archive copy of the builder's page</a>)` : ""}; older prices aren't on record. Checked daily from Oct 27.</p></section>` : "";

  const plans = d.plans.map((p) => {
    const facts = [p.downstairs_bedroom === true && "bedroom downstairs", p.downstairs_bedroom === false && "no bedroom downstairs",
      p.primary_floor && `owner's suite on floor ${p.primary_floor}`, p.loft && "loft", p.flex_room && "flex room",
      p.garage_bays && `${p.garage_bays}-car garage`].filter(Boolean);
    const rooms = (p.rooms || []).filter((r) => r.dims).map((r) => `${esc(r.name)} ${esc(r.dims)}`).join(" · ");
    return `<li class="py-2 border-t border-slate-100"><div class="flex justify-between gap-3">
      <div class="font-medium">${p.url ? `<a class="text-sky-700" href="${esc(p.url)}" target="_blank" rel="noopener">${esc(p.name)} ↗</a>` : esc(p.name)}</div><div class="num text-sm text-right">${p.from_price ? `from ${usd(p.from_price)}${p.price_as_of ? `<div class="text-[11px] text-slate-500">${day(p.price_as_of)}</div>` : ""}` : ""}</div></div>
      ${(p.price_changes || []).map((x) => `<div class="text-xs num ${x.now < x.was ? "text-emerald-700" : "text-rose-700"}">${moveDate(x)}: ${usd(x.was)} to ${usd(x.now)}</div>`).join("")}
      ${(p.home_offers || []).length ? `<details class="text-xs mt-1"><summary class="cursor-pointer text-sky-700">${p.home_offers.length} incentive${p.home_offers.length > 1 ? "s" : ""} on this plan's homes</summary><ul class="list-disc pl-4 mt-1">${p.home_offers.map((o) => `<li>${esc(o.label)}${o.address ? `: ${esc(o.address)}` : ""}</li>`).join("")}</ul></details>` : ""}
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
      <button id="close" class="text-sky-700 hover:text-sky-900 text-sm whitespace-nowrap" aria-label="Back to all communities">← All communities</button></div>
    <div class="px-5 py-4 flex flex-col gap-6 max-w-4xl mx-auto">
      ${trend}
      <section><h3 class="font-semibold mb-2">Earlier homes in this community: county records</h3>${past}</section>
      ${homes ? `<section><div class="flex flex-wrap items-center justify-between gap-2 mb-2">
          <h3 class="font-semibold">Homes for sale</h3><span class="text-xs text-slate-500">Prices as of ${day(c.fetched)}</span></div>
        <p class="text-xs text-slate-500 mb-2">True price at 20% down. Where two figures show, the lower counts an "up to" offer in full and the higher counts it as $0.</p>
        <div class="overflow-x-auto"><table class="w-full text-sm"><thead><tr class="text-xs text-slate-500 text-left">
          <th class="py-1 font-normal">Home</th><th class="py-1 font-normal text-right">Asking</th><th class="py-1 font-normal text-right">True price</th></tr></thead>
          <tbody>${homes}</tbody></table></div></section>`
      : `<section><h3 class="font-semibold mb-1">Homes for sale</h3><p class="text-sm text-slate-500">No move-in-ready home listed right now.</p></section>`}
      <section><h3 class="font-semibold mb-1">${offerCount} community-wide incentive${offerCount === 1 ? "" : "s"}</h3>
        <p class="text-xs text-slate-500 mb-1">${homes ? `In dollars, for a home at 20% down. The true price counts the largest unless the terms say they combine; ask the builder whether yours do.` : "On the builder's page. Not priced: there's no specific home to price them on."}</p>
        <ul>${homes || !pageOffers ? incs : pageOffers}</ul>
        ${extra ? `<p class="text-sm mt-2 rounded-lg bg-slate-50 border border-slate-200 px-3 py-2"><b>Worth asking:</b> builders may give more than they advertise. Our estimate is ${m[0]}–${m[1]}% of the price, ${extra} at the starting price. Not counted in any price here; only the builder can confirm it.</p>` : ""}</section>
      <section><h3 class="font-semibold mb-1">Floor plans</h3><p class="text-xs text-slate-500 mb-1">Each price shows the date it was read. Incentives tied to one home sit under that home's floor plan.</p><ul>${plans || `<li class="text-sm text-slate-500">No plans listed.</li>`}</ul></section>
    </div>`;
}

// ---------- agent ----------
const EXAMPLES = [
  "I need at least 4 bedrooms, a true price under $1.1M, and to move in by January. Contra Costa or San Joaquin County. What are my best options?",
  "What incentives does Lilac at Oakwood Trails offer, and what are they worth?",
  "What did earlier buyers pay at Cairnway in Tracy, compared with today's asking prices?",
  "What's the true price of 1576 Torrey Dr in Manteca?",
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
  const label = { checked: `Answer checked by hand${res.checked ? " on " + day(res.checked) + "; prices may have moved since" : ""}`, cached: "Live answer from Muse Spark Open (asked earlier today)",
    live: `Live answer from Muse Spark Open · $${(res.cost_usd ?? 0).toFixed(3)}` }[res.source] || "";
  box.innerHTML = `<div class="text-[11px] uppercase tracking-wide text-slate-400 mb-1">${label}</div>${answerHTML(res.answer)}${stepsHTML(res.steps)}`;
}

// What the agent did: its read-only tool calls, in order.
function stepsHTML(steps) {
  if (!steps || !steps.length) return "";
  return `<details open class="mt-3 border-t border-slate-100 pt-2"><summary class="text-xs text-slate-500 cursor-pointer">How the agent got this: ${steps.length} tool call${steps.length > 1 ? "s" : ""}</summary>
    <ol class="list-decimal pl-4 mt-1 text-xs text-slate-500">${steps.map((t) => `<li>${esc(t)}</li>`).join("")}</ol></details>`;
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
