"use strict";
// Static unit-browser reader. Loads a manifest, then lazy-loads one unit's data at a time
// (text + grammar + senses + a per-unit glossary slice). Zero LLM at serve time.

const DATA = "data";
const state = {
  manifest: [],
  work: null,        // manifest entry {target, slug, units:[...], ...}
  known: null,
  cid: null,         // current unit id
  unit: null, glossary: null, grammar: null, senses: null,  // current unit data
  selectedSeg: null,
  cache: {},         // `${cid}__${known}` -> {unit, glossary, grammar, senses}
};
const $ = (id) => document.getElementById(id);

// Touch devices have no hover, so the desktop "hover = meaning, click = grammar"
// split doesn't work. On touch we disable the tooltip and route a tap into a
// bottom sheet with Meaning/Grammar tabs (see openSheet). body.touch drives the CSS.
const touchMQ = window.matchMedia("(hover: none)");
const isTouch = () => touchMQ.matches;
function applyTouchMode() { document.body.classList.toggle("touch", isTouch()); }

// Right-to-left target languages (Persian, Arabic, Hebrew, Urdu). Drives text direction +
// script-friendly font for the original-language panes; the UI chrome stays LTR.
const RTL = new Set(["fa", "ar", "he", "ur", "ps", "sd"]);
const isRTL = () => RTL.has(state.work?.target);
// Display number for a unit: roman for the classic verse works, plain integer for RTL works
// (where section counts run past the roman table and roman numerals would read oddly).
const unitNum = (n) => (isRTL() ? String(n) : roman(n));

// Cache-buster per page load so the browser never serves a stale manifest/unit from a
// previous build. In-session refetches are avoided by state.cache, so this only forces a
// fresh copy on each new page load.
const BUST = `?v=${Date.now()}`;

async function getJSON(path) {
  const r = await fetch(path + BUST, { cache: "no-store" });
  if (!r.ok) throw new Error(`${path}: ${r.status}`);
  return r.json();
}

async function boot() {
  try {
    state.manifest = await getJSON(`${DATA}/manifest.json`);
  } catch {
    $("reader").innerHTML = `<p class="hint">No data found. Run the precompute first.</p>`;
    return;
  }
  if (!state.manifest.length) {
    $("reader").innerHTML = `<p class="hint">No works available yet.</p>`;
    return;
  }
  const wsel = $("work-select");
  state.manifest.forEach((w, i) => {
    const o = document.createElement("option");
    o.value = i; o.textContent = `${w.title} — ${w.author}`;
    wsel.appendChild(o);
  });
  wsel.onchange = () => selectWork(+wsel.value, true);
  $("lang-select").onchange = () => selectLang($("lang-select").value, true);
  $("prev-unit").onclick = () => step(-1);
  $("next-unit").onclick = () => step(1);
  applyTouchMode();
  if (touchMQ.addEventListener) touchMQ.addEventListener("change", applyTouchMode);
  wireSheet();
  document.addEventListener("keydown", (e) => {
    if (e.target.tagName === "SELECT") return;
    if (e.key === "ArrowLeft") step(-1);
    if (e.key === "ArrowRight") step(1);
  });
  window.addEventListener("hashchange", () => restore(parseHash()));
  // Restore last position: URL hash first, then localStorage, else default to first work.
  if (!(await restore(parseHash())) && !(await restore(loadSaved()))) {
    await selectWork(0, true);
  }
}

function readyUnits() {
  return (state.work?.units || []).filter((c) => c.pairs.includes(state.known));
}

// position = { slug, known, cid }
function parseHash() {
  const raw = decodeURIComponent(location.hash.slice(1));
  if (!raw) return null;
  const p = raw.split("/");
  return p.length >= 3 ? { slug: p[0], known: p[1], cid: p.slice(2).join("/") }
                       : { slug: null, known: null, cid: p[0] };  // legacy "#cid"
}
function writeHash() {
  if (!state.work || !state.cid) return;
  const h = `#${state.work.slug}/${state.known}/${state.cid}`;
  if (location.hash !== h) history.replaceState(null, "", h);
}
function savePos() {
  try { localStorage.setItem("reader-pos",
    JSON.stringify({ slug: state.work.slug, known: state.known, cid: state.cid })); } catch {}
}
function loadSaved() {
  try { return JSON.parse(localStorage.getItem("reader-pos") || "null"); } catch { return null; }
}

// Go to a saved/parsed position, switching work + language as needed. Returns true if it opened.
async function restore(pos) {
  if (!pos || !pos.cid) return false;
  let idx = pos.slug ? state.manifest.findIndex((w) => w.slug === pos.slug) : -1;
  if (idx < 0) idx = state.manifest.findIndex((w) => w.units.some((c) => c.id === pos.cid));
  if (idx < 0) return false;
  await selectWork(idx, false, pos.known);
  const c = state.work.units.find((x) => x.id === pos.cid && x.pairs.includes(state.known));
  if (!c) return false;
  openUnit(pos.cid);
  return true;
}

async function selectWork(i, reopen, preferredKnown) {
  state.work = state.manifest[i];
  $("work-select").value = String(i);
  const langs = [...new Set(state.work.units.flatMap((c) => c.pairs))];
  const lsel = $("lang-select");
  lsel.innerHTML = "";
  langs.forEach((k) => {
    const o = document.createElement("option");
    o.value = k; o.textContent = k.toUpperCase();
    lsel.appendChild(o);
  });
  const known = preferredKnown && langs.includes(preferredKnown) ? preferredKnown : langs[0];
  await selectLang(known, reopen);
}

async function selectLang(known, reopen) {
  state.known = known;
  $("lang-select").value = known;
  $("work-meta").textContent =
    `${state.work.title} · ${state.work.author} · reading in ${known.toUpperCase()}`;
  $("footer-source").textContent = state.work.source || "";
  buildNav();
  if (reopen) {
    const list = readyUnits();
    const stay = list.find((c) => c.id === state.cid) || list[0];
    if (stay) openUnit(stay.id);
  }
}

// ---- unit navigator -------------------------------------------------------
function buildNav() {
  const nav = $("unit-nav");
  nav.innerHTML = "";
  for (const groupName of state.work.groups) {
    const members = state.work.units.filter((c) => c.group === groupName);
    if (!members.length) continue;
    const h = document.createElement("div");
    h.className = "nav-group";
    h.textContent = groupName;
    nav.appendChild(h);
    const ul = document.createElement("div");
    ul.className = "nav-list";
    for (const c of members) {
      const a = document.createElement("button");
      a.className = "nav-unit";
      a.textContent = unitNum(c.num);
      a.title = c.incipit || c.title;
      a.dataset.cid = c.id;
      if (!c.pairs.includes(state.known)) a.disabled = true;
      if (c.id === state.cid) a.classList.add("active");
      a.onclick = () => openUnit(c.id);
      ul.appendChild(a);
    }
    nav.appendChild(ul);
  }
}

async function loadUnit(cid) {
  const key = `${cid}__${state.known}`;
  if (state.cache[key]) return state.cache[key];
  const base = `${DATA}/works/${state.work.target}/${state.work.slug}`;
  const [unit, glossary, grammar, senses] = await Promise.all([
    getJSON(`${base}/units/${cid}.json`),
    getJSON(`${base}/glossary__${state.known}/${cid}.json`),
    getJSON(`${base}/grammar__${state.known}/${cid}.json`),
    getJSON(`${base}/senses__${state.known}/${cid}.json`).catch(() => ({})),
  ]);
  const bundle = { unit, glossary, grammar, senses };
  state.cache[key] = bundle;
  return bundle;
}

async function openUnit(cid) {
  let bundle;
  try { bundle = await loadUnit(cid); }
  catch { $("reader").innerHTML = `<p class="hint">Couldn't load ${cid}.</p>`; return; }
  state.cid = cid;
  Object.assign(state, bundle);
  state.selectedSeg = null;

  const entry = state.work.units.find((c) => c.id === cid);
  $("unit-bar").hidden = false;
  $("unit-here").textContent = `${entry.group} · ${state.work.unit || "Unit"} ${unitNum(entry.num)}`;
  const list = readyUnits();
  const idx = list.findIndex((c) => c.id === cid);
  $("prev-unit").disabled = idx <= 0;
  $("next-unit").disabled = idx < 0 || idx >= list.length - 1;
  document.querySelectorAll(".nav-unit.active").forEach((n) => n.classList.remove("active"));
  document.querySelector(`.nav-unit[data-cid="${CSS.escape(cid)}"]`)?.classList.add("active");

  writeHash();
  savePos();
  render();
  clearGrammar();
  scrollToUnitTop();
  // prefetch next
  if (list[idx + 1]) loadUnit(list[idx + 1].id).catch(() => {});
}

// Scroll so the new unit's bar sits just below the sticky header. scrollIntoView()/block:start
// aligns to the viewport top, which the sticky (in-flow) header then overlaps — so we measure
// the header's actual height at scroll time (robust to it wrapping taller on narrow widths).
function scrollToUnitTop() {
  const header = document.querySelector("header");
  const target = $("unit-bar");
  if (!target) return;
  const gap = (header?.offsetHeight || 0) + 10;
  const y = target.getBoundingClientRect().top + window.scrollY - gap;
  window.scrollTo({ top: Math.max(0, y), behavior: "auto" });
}

function step(d) {
  const list = readyUnits();
  const idx = list.findIndex((c) => c.id === state.cid);
  const next = list[idx + d];
  if (next) openUnit(next.id);
}

// ---- rendering -------------------------------------------------------------
function render() {
  const frag = document.createDocumentFragment();
  for (const stanza of state.unit.stanzas) {
    const sd = document.createElement("div");
    sd.className = "stanza";
    for (const lid of stanza.lines) sd.appendChild(renderLine(lid));
    frag.appendChild(sd);
  }
  const reader = $("reader");
  reader.innerHTML = "";
  reader.dir = isRTL() ? "rtl" : "ltr";
  reader.classList.toggle("rtl", isRTL());
  reader.lang = state.work.target || "";
  reader.appendChild(frag);
}

function renderLine(lid) {
  const line = state.unit.lines[lid];
  const el = document.createElement("span");
  el.className = "line";
  const txt = line.text;
  let cursor = 0;
  const toks = [...line.tokens].sort((a, b) => a.s - b.s);
  toks.forEach((tok, ti) => {
    if (tok.s > cursor) el.appendChild(document.createTextNode(txt.slice(cursor, tok.s)));
    const w = document.createElement("span");
    w.className = "word";
    w.textContent = txt.slice(tok.s, tok.e);
    w.dataset.lid = lid;
    w.dataset.ti = String(ti);
    w.dataset.lemma = tok.lemma;
    if (tok.seg) w.dataset.seg = tok.seg;
    if (state.senses?.[lid]?.[ti] != null) w.classList.add("has-context");
    el.appendChild(w);
    cursor = tok.e;
  });
  if (cursor < txt.length) el.appendChild(document.createTextNode(txt.slice(cursor)));
  el.appendChild(document.createTextNode("\n"));
  return el;
}

// ---- tooltip (hover = meaning) ---------------------------------------------
const tip = $("tooltip");
function senseLine(s) {
  const reg = s.register ? ` <span class="reg">${escapeHtml(s.register)}</span>` : "";
  return `<span class="g">${escapeHtml(s.gloss)}</span>${reg} — ${escapeHtml(s.definition || "")}`;
}
// Build the word-meaning markup (shared by the desktop tooltip and the mobile
// sheet's Meaning tab). `note` lets each surface point the reader at the in-context grammar.
function meaningHTML(w, note) {
  const lemma = w.dataset.lemma;
  const entry = state.glossary[lemma];
  if (!entry) {
    return `<div class="tt-head"><span class="tt-word">${escapeHtml(w.textContent)}</span></div>
      <div class="tt-none">No gloss available.</div>`;
  }
  const ctxIdx = state.senses?.[w.dataset.lid]?.[+w.dataset.ti];
  let html = `<div class="tt-head"><span class="tt-word">${escapeHtml(entry.headword || lemma)}</span>`;
  if (entry.pos) html += `<span class="tt-pos">${escapeHtml(entry.pos)}</span>`;
  html += `</div>`;
  if (ctxIdx != null && entry.senses[ctxIdx]) {
    html += `<div class="tt-context"><div class="lbl">here</div>${senseLine(entry.senses[ctxIdx])}</div>`;
  }
  const others = entry.senses.map((s, i) => ({ s, i })).filter(({ i }) => i !== ctxIdx);
  if (others.length) {
    html += `<ul class="tt-senses">` + others.map(({ s }) => `<li>${senseLine(s)}</li>`).join("") + `</ul>`;
  }
  html += `<div class="tt-note">${escapeHtml(note)}</div>`;
  return html;
}
function showTip(w, ev) {
  if (isTouch()) return;   // touch routes through the bottom sheet instead
  tip.innerHTML = meaningHTML(w, "dictionary gloss · may miss archaic senses — click for the in-context grammar");
  tip.hidden = false;
  positionTip(ev);
}
function positionTip(ev) {
  const pad = 14, r = tip.getBoundingClientRect();
  let x = ev.clientX + pad, y = ev.clientY + pad;
  if (x + r.width > innerWidth - 8) x = ev.clientX - r.width - pad;
  if (y + r.height > innerHeight - 8) y = ev.clientY - r.height - pad;
  tip.style.left = Math.max(8, x) + "px";
  tip.style.top = Math.max(8, y) + "px";
}

// ---- segment selection (click = grammar) -----------------------------------
// Populate the grammar block + highlight the phrase. Returns false if the word
// isn't part of a known segment. Doesn't decide which panel view is visible —
// that's the caller's job (desktop swaps the sidebar; mobile uses tabs).
function fillGrammar(segId) {
  document.querySelectorAll(".word.in-segment").forEach((w) => w.classList.remove("in-segment"));
  const seg = segId && state.unit.segments?.[segId];
  const ctx = $("grammar-context"), ctxLabel = $("grammar-context-label");
  if (!seg) {
    state.selectedSeg = null;
    $("grammar-sentence").textContent = "";
    $("grammar-text").textContent = "Tap a word inside a phrase to see its grammar.";
    ctx.hidden = ctxLabel.hidden = true;
    return false;
  }
  state.selectedSeg = segId;
  document.querySelectorAll(`.word[data-seg="${CSS.escape(segId)}"]`)
    .forEach((w) => w.classList.add("in-segment"));
  const gs = $("grammar-sentence");
  gs.textContent = seg.text.replace(/\n/g, " ");
  gs.dir = isRTL() ? "rtl" : "ltr";
  gs.lang = state.work.target || "";
  $("grammar-text").textContent =
    state.grammar?.segments?.[segId] || "No explanation available.";
  const translation = state.grammar?.sentences?.[seg.sid] || "";
  if (translation) {
    ctx.hidden = ctxLabel.hidden = false;
    ctx.textContent = translation;
  } else {
    ctx.hidden = ctxLabel.hidden = true;
  }
  return true;
}

// Desktop: clicking a word swaps the right sidebar to the grammar view.
function selectSegment(segId) {
  if (!segId || !fillGrammar(segId)) return;
  $("panel-empty").hidden = true;
  $("panel-meaning").hidden = true;
  $("panel-grammar").hidden = false;
}

// Mobile: a tap opens the bottom sheet with both Meaning and Grammar, default tab Meaning.
function openSheet(w) {
  $("meaning-body").innerHTML =
    meaningHTML(w, "dictionary gloss · may miss archaic senses — see the Grammar tab for in-context");
  fillGrammar(w.dataset.seg);
  $("panel-empty").hidden = true;
  setSheetTab("meaning");
  $("panel").classList.add("sheet-open");
}
function setSheetTab(tab) {
  $("panel-meaning").hidden = tab !== "meaning";
  $("panel-grammar").hidden = tab !== "grammar";
  document.querySelectorAll(".sheet-tab").forEach((b) =>
    b.classList.toggle("active", b.dataset.tab === tab));
  const scroll = document.querySelector(".panel-scroll");
  if (scroll) scroll.scrollTop = 0;
}
function closeSheet() {
  $("panel").classList.remove("sheet-open");
}
function wireSheet() {
  $("sheet-close").onclick = closeSheet;
  document.querySelectorAll(".sheet-tab").forEach((b) =>
    b.onclick = () => setSheetTab(b.dataset.tab));
  // swipe the grip down to dismiss
  const grip = $("sheet-grip");
  let y0 = null;
  grip.addEventListener("touchstart", (e) => { y0 = e.touches[0].clientY; }, { passive: true });
  grip.addEventListener("touchend", (e) => {
    if (y0 != null && e.changedTouches[0].clientY - y0 > 55) closeSheet();
    y0 = null;
  });
}

function clearGrammar() {
  $("panel-empty").hidden = false;
  $("panel-meaning").hidden = true;
  $("panel-grammar").hidden = true;
  closeSheet();
}

// ---- events ----------------------------------------------------------------
document.addEventListener("mouseover", (e) => {
  const w = e.target.closest?.(".word"); if (w) showTip(w, e);
});
document.addEventListener("mousemove", (e) => {
  if (!tip.hidden && e.target.closest?.(".word")) positionTip(e);
});
document.addEventListener("mouseout", (e) => {
  if (e.target.closest?.(".word")) tip.hidden = true;
});
document.addEventListener("click", (e) => {
  const w = e.target.closest?.(".word");
  if (isTouch()) {
    if (w) { openSheet(w); return; }
    // tap outside a word and outside the sheet -> dismiss
    if (!e.target.closest?.("#panel")) closeSheet();
    return;
  }
  if (w) selectSegment(w.dataset.seg);
});

const ROMANS = ["", "I","II","III","IV","V","VI","VII","VIII","IX","X","XI","XII","XIII","XIV",
  "XV","XVI","XVII","XVIII","XIX","XX","XXI","XXII","XXIII","XXIV","XXV","XXVI","XXVII",
  "XXVIII","XXIX","XXX","XXXI","XXXII","XXXIII","XXXIV"];
function roman(n) { return ROMANS[n] || String(n); }
function escapeHtml(s) {
  return String(s).replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

boot();
