// Headless replay of the play-along page's highlighting logic.
//
//   node tests/fixtures/sync/viewer_replay.js OUTDIR/<stem>.html OUTDIR/sync_expected.json
//
// Runs the page's own inline <script> unmodified inside a vm with a minimal fake DOM
// (elements for every SVG node with a data-id, an <audio> stub, requestAnimationFrame
// driven by hand), then compares the set of elements with class "playing" against
//   tm   : notes whose timemap interval [on, off) contains t (ids as the timemap names them)
//   base : same, with Verovio's "-rendN" repeat-expansion suffix stripped (what the reader
//          expects to see lit on the page during a repeat)
//   midi : the multiset of pitches sounding in the combined MIDI at t (pitch of each lit
//          element vs pitches sounding), ignoring samples within 6 ms of a MIDI/timemap boundary
// for continuous playback at 60 fps, every event boundary, random seeks, and click-to-seek.
"use strict";
const fs = require("fs");
const vm = require("vm");

const [htmlPath, expPath] = process.argv.slice(2);
const html = fs.readFileSync(htmlPath, "utf8");
const exp = JSON.parse(fs.readFileSync(expPath, "utf8"));
const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)];
const code = scripts[scripts.length - 1][1];
const dataText = html.match(/<script type="application\/json" id="data">([\s\S]*?)<\/script>/)[1];

// ------------------------------------------------------------------ fake DOM
class El {
  constructor(tag) {
    this.tagName = tag.toUpperCase(); this.attrs = {}; this.cls = new Set();
    this.children = []; this.listeners = {}; this.textContent = ""; this._svgEls = [];
  }
  get classList() { const s = this.cls; return { add: c => s.add(c), remove: c => s.delete(c), contains: c => s.has(c) }; }
  set className(v) { this.cls = new Set(String(v).split(/\s+/).filter(Boolean)); }
  get className() { return [...this.cls].join(" "); }
  appendChild(c) { this.children.push(c); return c; }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
  addEventListener(t, f) { (this.listeners[t] = this.listeners[t] || []).push(f); }
  fire(t, ev) { (this.listeners[t] || []).forEach(f => f(ev || { target: this })); }
  set innerHTML(s) {
    this._svgEls = [];
    for (const m of s.matchAll(/<([a-zA-Z]+)((?:\s+[\w:.-]+="[^"]*")*)\s*\/?>/g)) {
      const attrs = {};
      for (const a of m[2].matchAll(/([\w:.-]+)="([^"]*)"/g)) attrs[a[1]] = a[2];
      if (!("data-id" in attrs)) continue;
      const e = new El(m[1]);
      Object.assign(e.attrs, attrs);
      if (attrs.class) e.className = attrs.class;
      this._svgEls.push(e);
    }
  }
  querySelectorAll(sel) { if (sel !== "[data-id]") throw new Error("unexpected selector " + sel); return this._svgEls; }
  closest() { return null; }
  getBoundingClientRect() { return { top: 0, bottom: 0 }; }
}
const els = {};
for (const id of ["title", "notes", "score", "speed", "follow", "audio"]) els[id] = new El(id === "audio" ? "audio" : "div");
els.data = new El("script"); els.data.textContent = dataText;
els.follow.checked = true;
const audio = els.audio;
audio.currentTime = 0; audio.paused = true; audio.playbackRate = 1;
audio.play = () => { audio.paused = false; audio.fire("play"); return Promise.resolve(); };
audio.pause = () => { audio.paused = true; audio.fire("pause"); };
const header = new El("header");
let scrolls = 0;
const document = {
  title: "",
  getElementById: id => els[id] || null,
  createElement: tag => new El(tag),
  querySelector: sel => (sel === "header" ? header : null),
  addEventListener: () => {},
};
const window = { innerHeight: 800, scrollY: 0, scrollTo: () => { scrolls++; } };
let rafQ = [];
const requestAnimationFrame = f => { rafQ.push(f); return rafQ.length; };
vm.runInNewContext(code, { document, window, requestAnimationFrame, console });

// every SVG element the page registered, by key
const allEls = [];
const walk = e => { allEls.push(...e._svgEls); e.children.forEach(walk); };
walk(els.score);
const byKey = new Map(allEls.map(e => [e.getAttribute("data-key"), e]));
const lit = () => new Set(allEls.filter(e => e.cls.has("playing")).map(e => e.getAttribute("data-key")));

// ------------------------------------------------------------------ expectations
const notes = exp.notes;
const pitchOf = new Map();
notes.forEach(n => { pitchOf.set(n.key, n.pitch); pitchOf.set(n.key.replace(/-rend\d+$/, ""), n.pitch); });
const baseKey = k => k.replace(/-rend\d+$/, "");
const boundaries = [];
notes.forEach(n => { [n.tm_on, n.tm_off, n.midi_on, n.midi_off].forEach(v => { if (v != null) boundaries.push(v); }); });
boundaries.sort((a, b) => a - b);
const nearBoundary = (t, tol) => {
  let lo = 0, hi = boundaries.length;
  while (lo < hi) { const m = (lo + hi) >> 1; if (boundaries[m] < t) lo = m + 1; else hi = m; }
  return [lo - 1, lo].some(i => i >= 0 && i < boundaries.length && Math.abs(boundaries[i] - t) <= tol);
};
function expectedAt(t) {
  const tm = new Set(), base = new Set(), midi = [];
  for (const n of notes) {
    if (n.tm_on <= t && (n.tm_off == null || t < n.tm_off)) { tm.add(n.key); base.add(baseKey(n.key)); }
    if (n.midi_on != null && n.midi_on <= t && (n.midi_off == null || t < n.midi_off)) midi.push(n.pitch);
  }
  return { tm, base, midi: midi.sort((a, b) => a - b) };
}
const eqSet = (a, b) => a.size === b.size && [...a].every(x => b.has(x));

const stats = {};
function compare(label, t) {
  const s = (stats[label] = stats[label] || { samples: 0, tm_mismatch: 0, base_mismatch: 0, midi_mismatch: 0, midi_mismatch_near_boundary: 0, examples: [] });
  s.samples++;
  const got = lit(), e = expectedAt(t);
  // elements the page cannot light: timemap keys that have no SVG element
  const tmDrawable = new Set([...e.tm].filter(k => byKey.has(k)));
  if (!eqSet(got, tmDrawable)) s.tm_mismatch++;
  const baseOk = eqSet(got, e.base);
  if (!baseOk) {
    s.base_mismatch++;
    if (s.examples.length < 3) s.examples.push({ t_ms: +t.toFixed(3), lit: [...got], expected_on_page: [...e.base] });
  }
  const gotP = [...got].map(k => pitchOf.get(k)).sort((a, b) => a - b);
  // a lit element may stand for a note that sounds twice at once (repeat + overlap); compare as sets of pitches
  const same = JSON.stringify([...new Set(gotP)]) === JSON.stringify([...new Set(e.midi)]);
  if (!same) { if (nearBoundary(t, 6)) s.midi_mismatch_near_boundary++; else s.midi_mismatch++; }
}

function frame(tms) { audio.currentTime = tms / 1000; const q = rafQ; rafQ = []; q.forEach(f => f(0)); }
function seek(tms) { audio.currentTime = tms / 1000; audio.fire("seeked"); }
const endMs = exp.end_ms;

// 1. continuous playback at 60 fps from 0 to the end, then 'ended'
seek(0);
audio.play();
for (let t = 0; t <= endMs; t += 1000 / 60) { frame(t); compare("play_60fps", t); }
audio.paused = true; audio.fire("ended");
const litAfterEnded = lit().size;

// 2. every event boundary, just before / at / just after, in playback order
const evTimes = [...new Set(notes.flatMap(n => [n.tm_on, n.tm_off]).filter(v => v != null))].sort((a, b) => a - b);
seek(0); audio.play();
for (const T of evTimes) for (const d of [-0.5, 0, 0.5]) { frame(T + d); compare("boundaries", T + d); }
audio.pause();

// 3. random seeks, backwards and forwards (deterministic LCG)
let seed = 12345;
const rnd = () => ((seed = (seed * 1103515245 + 12345) % 2147483648) / 2147483648);
let back = 0, fwd = 0, prev = 0;
for (let i = 0; i < 3000; i++) {
  const t = -500 + rnd() * (endMs + 1500);
  if (t < prev) back++; else fwd++;
  prev = t; seek(t); compare("random_seeks", t);
}
// 4. playing, with a seek every ~40 frames (mix of jumps back and forward)
seek(0); audio.play();
let t = 0;
for (let i = 0; i < 4000; i++) {
  if (i % 40 === 39) { t = rnd() * endMs; seek(t); compare("play_with_seeks", t); }
  t += 1000 / 60; if (t > endMs) t = 0;
  frame(t); compare("play_with_seeks", t);
}
audio.pause();

// 5. click every note / measure that has an onset; check the seek target and highlight
const data = JSON.parse(dataText);
const firstOn = new Map();
data.movements.forEach((mv, mi) => mv.timemap.forEach(e => {
  (e.on || []).forEach(id => { const k = mi + ":" + id; if (!firstOn.has(k)) firstOn.set(k, e.tstamp + mv.offset_ms); });
}));
let clicked = 0, wrongTarget = 0, notLitAfterClick = 0, roundTripShort = 0, clickNotReachable = 0;
const scoreClick = els.score.listeners.click[0];
for (const [k, on] of firstOn) {
  const el = byKey.get(k);
  if (!el) { clickNotReachable++; continue; }
  audio.paused = true;
  scoreClick({ target: { closest: sel => (sel === "g.note" ? el : null) } });
  clicked++;
  if (Math.abs(audio.currentTime * 1000 - on) > 1e-6) wrongTarget++;
  if (audio.currentTime * 1000 < on) roundTripShort++;
  audio.fire("seeked");
  if (!el.cls.has("playing")) notLitAfterClick++;
  audio.pause();
}

const out = {
  html: htmlPath,
  svg_elements_with_data_id: allEls.length,
  timemap_note_keys: new Set(notes.map(n => n.key)).size,
  timemap_keys_without_svg_element: [...new Set(notes.map(n => n.key))].filter(k => !byKey.has(k)).length,
  scenarios: stats,
  seeks: { backward: back, forward: fwd },
  lit_after_ended: litAfterEnded,
  follow_scroll_calls: scrolls,
  click: { clicked, wrong_seek_target: wrongTarget, seek_target_rounds_below_onset: roundTripShort,
           not_lit_right_after_click_seek: notLitAfterClick, timemap_notes_without_element: clickNotReachable },
};
console.log(JSON.stringify(out, null, 1));
