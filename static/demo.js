"use strict";
/* Scrollback browser demo.
   Loaded only in the GitHub Pages build, before app.js. It answers the app's
   /api/ calls inside the browser from a bundled set of events, keeps the
   visitor's progress in localStorage and reads posts with the browser's own
   speech voices. The ranking and quiz rules follow scrollback_core. */
(() => {
  const DATA_URL = "static/demo-data.json";
  const STORE_KEY = "scrollback-demo";
  const REPO = "https://github.com/ppolydoras/scrollback";

  // ------------------------------------------------------------------ state
  let data = { facts: [], meta: { eras: [], topics: [] }, built: "" };
  const byId = new Map();
  const served = new Map();
  let queue = [];
  let sinceQuiz = 0;

  const blank = () => ({ i: {}, aff: {}, quiz: {}, daily: {}, settings: {} });
  let store = blank();
  try { store = Object.assign(blank(), JSON.parse(localStorage.getItem(STORE_KEY) || "{}")); } catch (e) { /* private window */ }
  function save() { try { localStorage.setItem(STORE_KEY, JSON.stringify(store)); } catch (e) { /* keep in memory */ } }

  const DEFAULTS = { daily_goal: "20", quiz_every: "7", tts_voice: "", tts_speed: "1.00", language: "en",
    include_people: "0", llm_provider: "none", llm_model: "", llm_base_url: "", contact: "" };
  const setting = (k) => (store.settings[k] !== undefined ? store.settings[k] : DEFAULTS[k]);
  const intSetting = (k, d) => { const n = parseInt(setting(k), 10); return Number.isFinite(n) ? n : d; };

  const nowIso = () => new Date().toISOString().slice(0, 19);
  const todayKey = () => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`; };
  const inter = (id) => (store.i[id] = store.i[id] || { seen_at: null, dwell: 0, liked: false, saved: false, hidden: false });
  const gauss = () => { let u = 0, v = 0; while (!u) u = Math.random(); while (!v) v = Math.random(); return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v); };

  const ready = fetch(DATA_URL).then((r) => r.json()).then((d) => {
    data = d;
    for (const f of d.facts) byId.set(f.id, f);
  });

  // ------------------------------------------------------------------ progress
  const LEVELS = [[0, "Novice"], [100, "Scribe"], [350, "Chronicler"], [800, "Historian"], [1600, "Archivist"], [3000, "Sage"], [5500, "Oracle"]];
  function levelFor(xp) {
    let idx = 0;
    LEVELS.forEach(([t], i) => { if (xp >= t) idx = i; });
    const next = LEVELS[idx + 1];
    return { name: LEVELS[idx][1], number: idx + 1, floor: LEVELS[idx][0], next: next ? next[0] : null, next_name: next ? next[1] : null };
  }
  function streak() {
    const met = new Set(Object.entries(store.daily).filter(([, v]) => v.goal_met).map(([k]) => k));
    const d = new Date();
    const key = (x) => `${x.getFullYear()}-${String(x.getMonth() + 1).padStart(2, "0")}-${String(x.getDate()).padStart(2, "0")}`;
    if (!met.has(key(d))) d.setDate(d.getDate() - 1);
    let n = 0;
    while (met.has(key(d))) { n++; d.setDate(d.getDate() - 1); }
    return n;
  }
  function progress() {
    const day = store.daily[todayKey()] || { cards: 0, xp: 0, goal_met: false };
    const xp = Object.values(store.daily).reduce((a, v) => a + (v.xp || 0), 0);
    return { today: todayKey(), cards: day.cards || 0, goal: intSetting("daily_goal", 20), goal_met: !!day.goal_met,
      xp_today: day.xp || 0, xp, level: levelFor(xp), streak: streak() };
  }
  function bump({ cards = 0, xp = 0, quizTotal = 0, quizCorrect = 0 }) {
    const k = todayKey();
    const d = (store.daily[k] = store.daily[k] || { cards: 0, xp: 0, quiz_total: 0, quiz_correct: 0, goal_met: false });
    d.cards += cards; d.xp += xp; d.quiz_total += quizTotal; d.quiz_correct += quizCorrect;
    if (d.cards >= intSetting("daily_goal", 20)) d.goal_met = true;
    save();
    return progress();
  }

  // ------------------------------------------------------------------ facts out
  function factOut(f) {
    const it = store.i[f.id] || {};
    return { ...f, story: null, story_title: null, story_model: null, liked: !!it.liked, saved: !!it.saved,
      hidden: !!it.hidden, seen: !!it.seen_at };
  }
  const listRow = (f) => {
    const it = store.i[f.id] || {};
    return { id: f.id, year: f.year, month: f.month, day: f.day, text: f.text, title: f.title, image: f.image,
      era: f.era, topics: f.topics, kind: f.kind, liked: !!it.liked, saved: !!it.saved, seen: !!it.seen_at };
  };
  const unseen = () => data.facts.filter((f) => { const it = store.i[f.id]; return !it || (!it.seen_at && !it.hidden); });

  function status() {
    return { facts: data.facts.length, unseen: unseen().length, days_fetched: 366, days_total: 366,
      crawler: { state: "complete", last_error: null }, storyteller: { provider: "none", ready: false, reason: "Not in the demo" },
      progress: progress(), demo: true };
  }

  // ------------------------------------------------------------------ feed ranking
  const tagsOf = (f) => [`era:${f.era}`, `kind:${f.kind}`, ...f.topics.map((t) => `topic:${t}`)];
  function adjust(f, delta) {
    if (!delta) return;
    for (const t of tagsOf(f)) store.aff[t] = Math.max(-30, Math.min(30, (store.aff[t] || 0) + delta));
  }
  function score(f) {
    const t = (tag) => Math.tanh((store.aff[tag] || 0) / 8);
    const today = new Date();
    let s = 0.6 * (f.featured ? 1 : 0) + 0.4 * (f.image ? 1 : 0) + ((f.extract || "").length > 300 ? 0.25 : 0);
    if (f.month === today.getMonth() + 1 && f.day === today.getDate()) s += 1.2;
    s += 1.4 * t(`era:${f.era}`) + 0.5 * t(`kind:${f.kind}`);
    if (f.topics.length) s += f.topics.reduce((a, x) => a + t(`topic:${x}`), 0) / f.topics.length;
    return s + gauss() * 0.55;
  }
  function refill(target) {
    if (queue.length >= target) return;
    const recent = Date.now() - 6 * 3600 * 1000;
    const pool = unseen().filter((f) => !(served.get(f.id) > recent) && !queue.includes(f.id));
    const scored = pool.sort(() => Math.random() - 0.5).slice(0, 400).map((f) => [score(f), f]);
    const lastEras = [];
    let lastTopics = new Set();
    while (scored.length && queue.length < target) {
      let idx = 0;
      if (Math.random() < 0.12) idx = Math.floor(Math.random() * scored.length);
      else {
        let best = -1e9;
        scored.forEach(([s, f], i) => {
          const pen = 0.6 * lastEras.slice(-2).filter((e) => e === f.era).length + 0.3 * f.topics.filter((x) => lastTopics.has(x)).length;
          if (s - pen > best) { best = s - pen; idx = i; }
        });
      }
      const [, f] = scored.splice(idx, 1)[0];
      queue.push(f.id);
      lastEras.push(f.era);
      lastTopics = new Set(f.topics);
    }
  }
  function nextItems(n) {
    n = Math.max(1, Math.min(20, n || 8));
    const every = Math.max(3, intSetting("quiz_every", 7));
    refill(n + 16);
    const ids = [];
    while (queue.length && ids.length < n) {
      const id = queue.shift();
      const it = store.i[id];
      if (it && (it.seen_at || it.hidden)) continue;
      ids.push(id);
      served.set(id, Date.now());
    }
    const items = [];
    for (const id of ids) {
      items.push({ type: "fact", fact: factOut(byId.get(id)) });
      sinceQuiz++;
      if (sinceQuiz >= every) {
        const q = makeQuiz(new Set(ids));
        if (q) { items.push({ type: "quiz", quiz: q }); sinceQuiz = 0; }
      }
    }
    return items;
  }

  function recordView(id, ms) {
    const f = byId.get(id);
    if (!f) throw new Error("no such post");
    const it = inter(id);
    const first = !it.seen_at;
    it.seen_at = it.seen_at || nowIso();
    it.dwell += Math.max(0, ms | 0);
    adjust(f, ms < 1500 ? -0.3 : ms < 6000 ? 0 : ms < 20000 ? 0.5 : 1.0);
    save();
    return first && ms >= 1500 ? bump({ cards: 1, xp: 1 }) : progress();
  }

  const WEIGHTS = { like: 3, unlike: -3, save: 4, unsave: -4, hide: -4, unhide: 4, expand: 1, open: 1 };
  function react(id, action) {
    const f = byId.get(id);
    if (!f || !(action in WEIGHTS)) throw new Error("unknown action");
    const it = inter(id);
    if (action === "like" || action === "unlike") { it.liked = action === "like"; it.liked_at = it.liked ? nowIso() : null; }
    if (action === "save" || action === "unsave") { it.saved = action === "save"; it.saved_at = it.saved ? nowIso() : null; }
    if (action === "hide" || action === "unhide") it.hidden = action === "hide";
    adjust(f, WEIGHTS[action]);
    if (action === "hide") queue = [];
    save();
    return factOut(f);
  }

  // ------------------------------------------------------------------ quizzes
  const BOX_DAYS = [1, 3, 7, 16, 35, 90];
  const yearText = (y) => (y < 0 ? `${-y} BC` : y < 1000 ? `AD ${y}` : String(y));
  function yearOptions(year) {
    const span = year >= 1900 ? 15 : year >= 1700 ? 30 : year >= 1400 ? 60 : year >= 0 ? 150 : 300;
    const gap = Math.max(2, Math.floor(span / 7));
    const max = new Date().getFullYear();
    const opts = new Set([year]);
    for (let i = 0; i < 400 && opts.size < 4; i++) {
      const y = year + (Math.random() < 0.5 ? -1 : 1) * (gap + Math.floor(Math.random() * (span - gap + 1)));
      if (y === 0 || y > max) continue;
      if ([...opts].some((o) => Math.abs(o - y) < gap)) continue;
      opts.add(y);
    }
    for (let step = 1; opts.size < 4; step++) { const y = year - step * gap; if (y !== 0) opts.add(y); }
    return [...opts].sort((a, b) => a - b);
  }
  function makeQuiz(exclude) {
    const now = Date.now();
    let fact = null;
    const due = Object.entries(store.quiz).filter(([id, q]) => q.due <= now && !exclude.has(Number(id)) && byId.has(Number(id)))
      .sort((a, b) => a[1].due - b[1].due);
    if (due.length) fact = byId.get(Number(due[0][0]));
    if (!fact) {
      const cutoff = now - 3 * 60 * 1000;
      const cands = data.facts.filter((f) => {
        const it = store.i[f.id];
        return it && it.seen_at && Date.parse(it.seen_at + "Z") <= cutoff && !it.hidden && it.dwell >= 2500 && !store.quiz[f.id] && !exclude.has(f.id);
      });
      const rank = (f) => { const it = store.i[f.id]; return (it.liked ? 2 : 0) + (it.saved ? 3 : 0) + Math.min(it.dwell / 10000, 2) + Math.random() * 0.5; };
      cands.sort((a, b) => rank(b) - rank(a));
      fact = cands[0] || null;
    }
    if (!fact) return null;
    const base = { fact_id: fact.id, image: fact.image, era: fact.era };
    if (!new RegExp(`\\b${Math.abs(fact.year)}\\b`).test(fact.text)) {
      const options = yearOptions(fact.year);
      return { ...base, kind: "year", question: "When did this happen?", prompt: fact.text, options, labels: options.map(yearText) };
    }
    const others = data.facts.filter((f) => f.id !== fact.id && store.i[f.id] && store.i[f.id].seen_at && Math.abs(f.year - fact.year) >= 5);
    if (!others.length) return null;
    const other = others[Math.floor(Math.random() * others.length)];
    const pair = [{ id: fact.id, text: fact.text }, { id: other.id, text: other.text }].sort(() => Math.random() - 0.5);
    return { ...base, kind: "order", question: "Which happened first?", pair };
  }
  function answer({ kind, fact_id, choice, other_id }) {
    const fact = byId.get(fact_id);
    if (!fact) throw new Error("unknown fact");
    let correct, reveal;
    if (kind === "year") {
      correct = Number(choice) === fact.year;
      reveal = { answer: fact.year, label: yearText(fact.year) };
    } else if (kind === "order") {
      const other = byId.get(other_id);
      const first = fact.year < other.year ? fact : other;
      correct = Number(choice) === first.id;
      reveal = { answer: first.id, years: { [fact.id]: yearText(fact.year), [other.id]: yearText(other.year) } };
    } else throw new Error("unknown quiz");
    const q = store.quiz[fact_id] || { box: -1, correct: 0, wrong: 0 };
    if (correct) { q.box = Math.min(q.box + 1, BOX_DAYS.length - 1); q.due = Date.now() + BOX_DAYS[q.box] * 86400000; q.correct++; }
    else { q.box = 0; q.due = Date.now() + 20 * 60000; q.wrong++; }
    store.quiz[fact_id] = q;
    const prog = bump({ xp: correct ? 10 : 2, quizTotal: 1, quizCorrect: correct ? 1 : 0 });
    return { correct, ...reveal, xp: correct ? 10 : 2, fact: factOut(fact), progress: prog };
  }

  // ------------------------------------------------------------------ explore, saved, stats
  function explore(p) {
    let rows = data.facts.filter((f) => !(store.i[f.id] && store.i[f.id].hidden));
    if (p.era) rows = rows.filter((f) => f.era === p.era);
    if (p.topic) rows = rows.filter((f) => f.topics.includes(p.topic));
    if (p.q) { const q = p.q.toLowerCase(); rows = rows.filter((f) => `${f.text} ${f.title || ""} ${f.extract || ""}`.toLowerCase().includes(q)); }
    if (p.month) rows = rows.filter((f) => f.month === Number(p.month));
    if (p.day) rows = rows.filter((f) => f.day === Number(p.day));
    if (p.unseen === "1") rows = rows.filter((f) => !(store.i[f.id] && store.i[f.id].seen_at));
    const order = p.sort || "chrono";
    if (order === "chrono") rows.sort((a, b) => a.year - b.year || a.month - b.month || a.day - b.day);
    else if (order === "recent") rows.sort((a, b) => b.year - a.year || a.month - b.month || a.day - b.day);
    else rows.sort(() => Math.random() - 0.5);
    const offset = Math.max(0, Number(p.offset) || 0);
    return { total: rows.length, offset, items: rows.slice(offset, offset + 30).map(listRow) };
  }
  function collection(which) {
    const col = which === "liked" ? "liked" : "saved";
    const rows = data.facts.filter((f) => store.i[f.id] && store.i[f.id][col])
      .sort((a, b) => (store.i[b.id][`${col}_at`] || "").localeCompare(store.i[a.id][`${col}_at`] || ""));
    return { total: rows.length, items: rows.map(listRow) };
  }
  function stats() {
    const its = Object.values(store.i);
    const quiz = Object.values(store.quiz);
    const eras = data.meta.eras.map((era) => {
      const fs = data.facts.filter((f) => f.era === era);
      return { era, total: fs.length, read: fs.filter((f) => store.i[f.id] && store.i[f.id].dwell >= 1500).length,
        liked: fs.filter((f) => store.i[f.id] && store.i[f.id].liked).length };
    });
    const topics = Object.entries(store.aff).filter(([k]) => k.startsWith("topic:"))
      .map(([k, v]) => ({ topic: k.slice(6), score: Math.round(v * 10) / 10 })).sort((a, b) => b.score - a.score);
    const days = [];
    for (let k = 13; k >= 0; k--) {
      const d = new Date(); d.setDate(d.getDate() - k);
      const key = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
      const v = store.daily[key];
      days.push({ day: key, cards: v ? v.cards : 0, goal_met: !!(v && v.goal_met) });
    }
    return { read: its.filter((i) => i.dwell >= 1500).length, liked: its.filter((i) => i.liked).length,
      saved: its.filter((i) => i.saved).length, hidden: its.filter((i) => i.hidden).length,
      minutes: Math.round(its.reduce((a, i) => a + i.dwell, 0) / 60000),
      quiz: { correct: quiz.reduce((a, q) => a + q.correct, 0), wrong: quiz.reduce((a, q) => a + q.wrong, 0),
        facts: quiz.length, learned: quiz.filter((q) => q.box >= 3).length },
      eras, topics, days, progress: progress() };
  }

  // ------------------------------------------------------------------ speech
  const synth = window.speechSynthesis || null;
  // Voices on the device first. Online voices (such as Chrome's "Google"
  // voices) send the text to their provider, so they are only a fallback.
  const englishVoices = () => (synth ? synth.getVoices().filter((v) => /^en/i.test(v.lang))
    .sort((a, b) => Number(b.localService) - Number(a.localService)) : []);
  function pickVoice(name) {
    const voices = englishVoices();
    const gb = (v) => /en[-_]GB/i.test(v.lang);
    return voices.find((v) => v.name === name) || voices.find((v) => v.localService && gb(v))
      || voices.find((v) => v.localService) || voices.find(gb) || voices[0] || null;
  }
  function ttsStatus() {
    const voices = englishVoices();
    const voice = pickVoice(setting("tts_voice"));
    return { available: !!(synth && voices.length), demo: true, python: "browser",
      reason: synth ? "This browser has no English voice installed." : "This browser cannot speak.",
      voices: voices.map((v) => ({ key: v.name, label: `${v.name} (${v.lang}${v.localService ? "" : ", online"})` })), voice: voice ? voice.name : null,
      speed: Number(setting("tts_speed")) || 1, worker_running: false, last_error: null, download: {} };
  }

  // The build script stores the same spoken text the installed app sends to
  // Piper (years and dates spelled out). Split it into sentences here.
  function speechParts(f) {
    const parts = [];
    for (const para of (f.speech || f.text).split(/\n\n+/)) {
      const sentences = para.match(/[^.!?]+(?:[.!?]+(?=\s|$)|$)/g) || [para];
      for (const s of sentences) if (s.trim()) parts.push(s.trim());
    }
    return parts;
  }
  // Chrome fills the voice list a moment after the page loads.
  const voicesReady = new Promise((resolve) => {
    if (!synth || synth.getVoices().length) { resolve(); return; }
    synth.addEventListener("voiceschanged", () => resolve(), { once: true });
    setTimeout(resolve, 1500);
  });

  // A stand-in for the <audio> element that app.js drives. It speaks in short
  // chunks because some browsers stop long utterances after about 15 seconds.
  class SpeechAudio extends EventTarget {
    constructor(src) { super(); this._src = src || null; this.paused = true; this.ended = false; this.currentTime = 0; this.duration = NaN; this._run = 0; }
    set src(v) { this._src = v; }
    get src() { return this._src || ""; }
    getAttribute(n) { return n === "src" ? this._src : null; }
    removeAttribute(n) { if (n === "src") this._src = null; }
    load() { this._run++; if (synth) synth.cancel(); this.paused = true; }
    _emit(type) { this.dispatchEvent(new Event(type)); }
    _parts() {
      const src = this._src || "";
      const m = src.match(/\/api\/tts\/fact\/(\d+)\.wav/);
      if (m && byId.has(Number(m[1]))) return speechParts(byId.get(Number(m[1])));
      if (src.startsWith("/api/tts/sample.wav")) return ["This is how Scrollback sounds with this voice.", "On the fourteenth of October, ten sixty-six, the Normans defeated the English at the Battle of Hastings."];
      return null;
    }
    play() {
      if (!synth) { setTimeout(() => this._emit("error"), 0); return Promise.resolve(); }
      if (synth.paused && this._resumable === this._src) {
        synth.resume(); this.paused = false; this._emit("playing"); return Promise.resolve();
      }
      const parts = this._parts();
      if (!parts) { setTimeout(() => this._emit("error"), 0); return Promise.resolve(); }
      const run = ++this._run;
      synth.cancel();
      if (synth.paused) synth.resume(); // Chrome stays paused after cancel()
      const params = new URLSearchParams((this._src.split("?")[1]) || "");
      const voice = pickVoice(params.get("voice") || setting("tts_voice"));
      const rate = Number(params.get("speed") || setting("tts_speed")) || 1;
      this.ended = false; this.paused = false; this.currentTime = 0; this.duration = parts.length; this._resumable = this._src;
      parts.forEach((text, i) => {
        const u = new SpeechSynthesisUtterance(text);
        if (voice) u.voice = voice;
        u.lang = voice ? voice.lang : "en-GB";
        u.rate = rate;
        if (i === 0) u.onstart = () => { if (run === this._run) this._emit("playing"); };
        u.onend = () => {
          if (run !== this._run) return;
          this.currentTime = i + 1;
          this._emit("timeupdate");
          if (i === parts.length - 1) { this.ended = true; this.paused = true; this._resumable = null; this._emit("ended"); }
        };
        u.onerror = (e) => { if (run === this._run && !["interrupted", "canceled"].includes(e.error)) this._emit("error"); };
        synth.speak(u);
      });
      return Promise.resolve();
    }
    pause() { if (synth) synth.pause(); this.paused = true; this._emit("pause"); }
  }

  // ------------------------------------------------------------------ the fake API
  function route(method, path, params, body) {
    if (method === "GET") {
      if (path === "/api/meta") return { eras: data.meta.eras, topics: data.meta.topics };
      if (path === "/api/status") return status();
      if (path === "/api/feed") return { items: nextItems(Number(params.n) || 8), status: status() };
      let m = path.match(/^\/api\/fact\/(\d+)$/);
      if (m) { const f = byId.get(Number(m[1])); if (!f) throw new Error("no such post"); return factOut(f); }
      if (path === "/api/explore") return explore(params);
      if (path === "/api/collection") return collection(params.which);
      if (path === "/api/stats") return stats();
      if (path === "/api/settings") return { settings: { ...DEFAULTS, ...store.settings, _env_locked: [] }, eras: data.meta.eras, topics: data.meta.topics };
      if (path === "/api/tts/status") return ttsStatus();
      if (path === "/api/tts/catalog") return { voices: [] };
      if (/^\/api\/tts\//.test(path)) throw new Error("Your browser could not read this post aloud.");
    } else {
      if (path === "/api/view") return recordView(Number(body.id), Number(body.ms) || 0);
      if (path === "/api/react") return react(Number(body.id), String(body.action));
      if (path === "/api/quiz") return answer({ ...body, fact_id: Number(body.fact_id), other_id: body.other_id != null ? Number(body.other_id) : null });
      if (path === "/api/settings") {
        const changed = [];
        for (const k of ["daily_goal", "quiz_every", "tts_voice", "tts_speed"]) {
          if (body[k] !== undefined && String(body[k]) !== String(setting(k))) { store.settings[k] = String(body[k]); changed.push(k); }
        }
        save();
        return { settings: { ...DEFAULTS, ...store.settings, _env_locked: [] }, changed };
      }
      if (path === "/api/tts/download") throw new Error("Voices can be downloaded in the installed app.");
      if (path === "/api/tts/recheck") return ttsStatus();
      if (path === "/api/refresh" || path === "/api/tts/prefetch") return { ok: true };
    }
    throw new Error("not found");
  }

  const realFetch = window.fetch.bind(window);
  window.fetch = async (input, init = {}) => {
    const url = typeof input === "string" ? input : input.url;
    if (!url.startsWith("/api/")) return realFetch(input, init);
    await ready;
    const u = new URL(url, location.origin);
    if (u.pathname.startsWith("/api/tts/")) await voicesReady;
    const method = (init.method || "GET").toUpperCase();
    let status = 200, out;
    try {
      out = route(method, u.pathname, Object.fromEntries(u.searchParams), init.body ? JSON.parse(init.body) : {});
    } catch (e) {
      status = /not found|no such/.test(e.message) ? 404 : 400;
      out = { error: e.message };
    }
    return new Response(JSON.stringify(out), { status, headers: { "Content-Type": "application/json" } });
  };

  window.SCROLLBACK_DEMO = { audio: new SpeechAudio(), makeAudio: (src) => new SpeechAudio(src) };

  // ------------------------------------------------------------------ demo notice
  document.addEventListener("DOMContentLoaded", () => {
    const foot = document.querySelector(".rail-foot");
    if (foot) {
      const a = document.createElement("a");
      a.href = REPO; a.target = "_blank"; a.rel = "noopener"; a.className = "keys-btn";
      a.textContent = "Get Scrollback on GitHub";
      foot.prepend(a);
    }
    ready.then(() => {
      let seenNotice = false;
      try { seenNotice = sessionStorage.getItem("scrollback-demo-notice") === "1"; sessionStorage.setItem("scrollback-demo-notice", "1"); } catch (e) { /* ignore */ }
      if (seenNotice) return;
      const box = document.getElementById("toasts");
      if (!box) return;
      const t = document.createElement("div");
      t.className = "toast";
      const span = document.createElement("span");
      span.textContent = `Browser demo with ${data.facts.length} events. Your progress stays in this browser.`;
      const link = document.createElement("a");
      link.href = REPO; link.target = "_blank"; link.rel = "noopener"; link.textContent = "Get the app";
      link.style.cssText = "color:inherit;font-weight:800;white-space:nowrap;text-decoration:underline;text-underline-offset:3px";
      t.append(span, link);
      box.append(t);
      setTimeout(() => t.remove(), 9000);
    });
  });
})();
