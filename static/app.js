"use strict";
/* Scrollback front end. No build step, no dependencies. */
(() => {
  // ---------------------------------------------------------------- helpers
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

  function h(tag, attrs, ...kids) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === null || v === undefined || v === false) continue;
      if (k === "class") el.className = v;
      else if (k === "text") el.textContent = v;
      else if (k === "html") el.innerHTML = v; // only used with our own SVG markup
      else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
      else el.setAttribute(k, v === true ? "" : v);
    }
    for (const kid of kids.flat()) {
      if (kid === null || kid === undefined || kid === false) continue;
      el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
    }
    return el;
  }

  const SVG = (body, cls = "") => `<svg viewBox="0 0 24 24" aria-hidden="true" class="${cls}">${body}</svg>`;
  const ICON = {
    heart: SVG('<path d="M12 20s-7-4.4-7-10a4 4 0 0 1 7-2.6A4 4 0 0 1 19 10c0 5.6-7 10-7 10z"/>'),
    save: SVG('<path d="M7 4h10v16l-5-4-5 4z"/>'),
    hide: SVG('<path d="M3 12s3.5-6 9-6c1.6 0 3 .5 4.2 1.2M21 12s-3.5 6-9 6c-1.6 0-3-.5-4.2-1.2M4 20L20 4"/>'),
    out: SVG('<path d="M14 4h6v6M20 4l-9 9M18 14v5H5V6h5"/>'),
    listen: SVG('<path d="M8 5.5v13l11-6.5z" fill="currentColor"/>', "ic-play") +
      SVG('<rect x="5" y="6" width="3" height="12" rx="1"/><rect x="10.5" y="4" width="3" height="16" rx="1"/><rect x="16" y="8" width="3" height="10" rx="1"/>', "ic-bars") +
      SVG('<path d="M12 4a8 8 0 1 0 8 8"/>', "ic-spin"),
    heartFill: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 21s-8-5-8-11.2A4.5 4.5 0 0 1 12 6.6a4.5 4.5 0 0 1 8 3.2C20 16 12 21 12 21z"/></svg>',
  };
  const icon = (name) => h("span", { html: ICON[name], style: "display:contents" });

  const MONTHS = ["January", "February", "March", "April", "May", "June", "July",
    "August", "September", "October", "November", "December"];
  const NOW_YEAR = new Date().getFullYear();
  const ERAS = [
    ["Ancient", -3000, 499, "--era-ancient"], ["Medieval", 500, 1499, "--era-medieval"],
    ["Early modern", 1500, 1799, "--era-earlymodern"], ["19th century", 1800, 1899, "--era-19c"],
    ["1900 to 1945", 1900, 1945, "--era-1900"], ["1946 to 1999", 1946, 1999, "--era-1946"],
    ["21st century", 2000, NOW_YEAR, "--era-21c"],
  ];
  const pigment = (era) => { const e = ERAS.find((x) => x[0] === era); return `var(${e ? e[3] : "--era-21c"})`; };
  // Piecewise scale: recent centuries get more room because most events are recent.
  const STOPS = [[-3000, 0], [0, 0.16], [500, 0.27], [1500, 0.45], [1800, 0.6],
    [1900, 0.72], [1946, 0.82], [2000, 0.93], [NOW_YEAR, 1]];

  function rulerPos(year) {
    if (year <= STOPS[0][0]) return 0;
    for (let i = 1; i < STOPS.length; i++) {
      const [y1, p1] = STOPS[i];
      if (year <= y1) {
        const [y0, p0] = STOPS[i - 1];
        return p0 + ((year - y0) / (y1 - y0)) * (p1 - p0);
      }
    }
    return 1;
  }

  function yearParts(y) {
    if (y < 0) return [String(-y), "BC"];
    if (y < 1000) return [String(y), "AD"];
    return [String(y), ""];
  }
  function yearText(y) {
    const [n, s] = yearParts(y);
    return s === "BC" ? `${n} BC` : s === "AD" ? `AD ${n}` : n;
  }
  const dateText = (m, d) => `${d} ${MONTHS[m - 1]}`;
  const isToday = (m, d) => { const t = new Date(); return t.getMonth() + 1 === m && t.getDate() === d; };
  const fmt = (n) => Number(n).toLocaleString("en-GB");
  const headlineOf = (f) => f.story_title || f.text;

  const api = {
    async get(path) {
      const r = await fetch(path, { headers: { Accept: "application/json" } });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(data.error || `Request failed (${r.status})`);
      return data;
    },
    async post(path, body, keepalive = false) {
      const r = await fetch(path, {
        method: "POST", keepalive,
        headers: { "Content-Type": "application/json", "X-Scrollback": "1" },
        body: JSON.stringify(body || {}),
      });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(data.error || `Request failed (${r.status})`);
      return data;
    },
  };

  function toast(message, action) {
    const box = $("#toasts");
    const t = h("div", { class: "toast" }, h("span", { text: message }));
    if (action) {
      t.append(h("button", { type: "button", text: action.label, onclick: () => { action.run(); t.remove(); } }));
    }
    box.append(t);
    setTimeout(() => t.remove(), action ? 7000 : 3200);
  }

  const state = { progress: null, status: null, meta: null, route: "feed" };

  // ---------------------------------------------------------------- theme
  const THEME_KEY = "scrollback-theme";
  const darkQuery = window.matchMedia("(prefers-color-scheme: dark)");
  function themePref() {
    try { return localStorage.getItem(THEME_KEY) || localStorage.getItem("annals-theme") || "auto"; } catch (e) { return "auto"; }
  }
  function applyTheme(pref) {
    try { localStorage.setItem(THEME_KEY, pref); } catch (e) { /* private window: keep for this visit */ }
    state.themePref = pref;
    const dark = pref === "dark" || (pref === "auto" && darkQuery.matches);
    document.documentElement.dataset.theme = dark ? "dark" : "light";
    $('meta[name="theme-color"]').setAttribute("content", dark ? "#1A1015" : "#F3EEF0");
    for (const b of $$("[data-theme-pref]")) b.setAttribute("aria-checked", String(b.dataset.themePref === pref));
    for (const r of $$('input[name="ui_theme"]')) r.checked = r.value === pref;
  }
  function cycleTheme() {
    const dark = document.documentElement.dataset.theme === "dark";
    applyTheme(dark ? "light" : "dark");
    toast(dark ? "Light theme" : "Dark theme");
  }
  darkQuery.addEventListener("change", () => { if (state.themePref === "auto") applyTheme("auto"); });
  for (const b of $$("[data-theme-pref]")) b.addEventListener("click", () => applyTheme(b.dataset.themePref));
  $('[data-action="cycle-theme"]').addEventListener("click", cycleTheme);
  applyTheme(themePref());

  // ---------------------------------------------------------------- small animations
  function burst(el, colors, count = 12) {
    if (reduceMotion.matches || !el) return;
    const r = el.getBoundingClientRect();
    const cx = r.left + r.width / 2, cy = r.top + r.height / 2;
    for (let i = 0; i < count; i++) {
      const angle = (Math.PI * 2 * i) / count + Math.random() * 0.4;
      const dist = 34 + Math.random() * 30;
      const t = h("span", { class: "tessera" });
      t.style.left = `${cx}px`;
      t.style.top = `${cy}px`;
      t.style.background = colors[i % colors.length];
      t.style.setProperty("--dx", `${Math.cos(angle) * dist}px`);
      t.style.setProperty("--dy", `${Math.sin(angle) * dist}px`);
      t.style.setProperty("--rot", `${Math.round(Math.random() * 360)}deg`);
      document.body.append(t);
      setTimeout(() => t.remove(), 800);
    }
  }
  function floatText(el, text) {
    if (reduceMotion.matches || !el) return;
    const r = el.getBoundingClientRect();
    const f = h("span", { class: "float-xp", text });
    f.style.left = `${r.left + r.width / 2}px`;
    f.style.top = `${r.top - 6}px`;
    document.body.append(f);
    setTimeout(() => f.remove(), 1200);
  }
  function retrigger(el, cls) {
    if (!el) return;
    el.classList.remove(cls);
    void el.offsetWidth;
    el.classList.add(cls);
  }
  const cssVar = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  function countTo(el, to) {
    const from = Number(el.textContent) || 0;
    if (from === to || reduceMotion.matches) { el.textContent = to; return; }
    const start = performance.now();
    const step = (now) => {
      const k = Math.min(1, (now - start) / 450);
      el.textContent = Math.round(from + (to - from) * (1 - Math.pow(1 - k, 3)));
      if (k < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }

  // ---------------------------------------------------------------- progress
  const RING = 2 * Math.PI * 52;

  function renderProgress(p) {
    if (!p) return;
    const prev = state.progress;
    state.progress = p;
    const ratio = Math.min(1, p.cards / Math.max(1, p.goal));
    $("#ring-fill").style.strokeDashoffset = String(RING * (1 - ratio));
    countTo($("#ring-count"), p.cards);
    $("#ring-goal").textContent = `of ${p.goal} posts today`;
    $(".today-ring").classList.toggle("met", p.goal_met);
    $("#side-streak").textContent = p.streak === 1 ? "1 day" : `${p.streak} days`;
    $("#side-level").textContent = p.level.name;
    const lv = p.level;
    const span = lv.next ? lv.next - lv.floor : 1;
    $("#xp-fill").style.width = lv.next ? `${Math.min(100, ((p.xp - lv.floor) / span) * 100)}%` : "100%";
    $("#xp-note").textContent = lv.next
      ? `${fmt(p.xp)} XP. ${fmt(lv.next - p.xp)} more to reach ${lv.next_name}.`
      : `${fmt(p.xp)} XP. You hold the highest rank.`;
    const pill = $("#top-progress");
    pill.textContent = p.goal_met ? `Goal met, ${p.streak} day streak` : `${p.cards} of ${p.goal} today`;
    pill.classList.toggle("met", p.goal_met);
    if (prev && !prev.goal_met && p.goal_met && prev.today === p.today) {
      retrigger($(".today-ring"), "pulse");
      burst($(".today-ring"), [cssVar("--accent"), cssVar("--ok"), cssVar("--like")], 18);
      toast(`Daily goal reached. Your streak is ${p.streak === 1 ? "1 day" : p.streak + " days"}.`);
    }
    if (prev && p.level.number > prev.level.number) toast(`New rank: ${p.level.name}.`);
  }

  function renderLibrary(st) {
    if (!st) return;
    state.status = st;
    let text = `${fmt(st.facts)} posts in your library, ${fmt(st.unseen)} unread. ` +
      `${st.days_fetched} of ${st.days_total} days of the year fetched.`;
    if (st.crawler && st.crawler.last_error) text += " Wikipedia is not reachable right now. Scrollback will retry.";
    $("#side-library").textContent = text;
  }

  async function refreshStatus() {
    try {
      const st = await api.get("/api/status");
      renderLibrary(st);
      renderProgress(st.progress);
      return st;
    } catch (e) { return null; }
  }

  // ---------------------------------------------------------------- shared pieces of a post
  function ruler(year) {
    const pos = rulerPos(year) * 100;
    const track = h("div", { class: "ruler-track" });
    for (const [name, from, to] of ERAS) {
      const w = (rulerPos(to) - rulerPos(from)) * 100;
      track.append(h("span", { class: year >= from && year <= to ? "on" : "", style: `width:${w}%`, title: name }));
    }
    const era = ERAS.find(([, from, to]) => year >= from && year <= to) || ERAS[0];
    const labelPos = Math.min(86, Math.max(10, pos));
    return h("div", { class: "ruler", role: "img", "aria-label": `${yearText(year)}, ${era[0]}` },
      track,
      h("span", { class: "ruler-mark", style: `left:${pos}%` }),
      h("span", { class: "ruler-era", style: `left:${labelPos}%`, text: era[0] }));
  }

  function yearEl(year, cls) {
    const [n, s] = yearParts(year);
    return h("p", { class: cls, "aria-label": yearText(year) }, n, s ? h("small", { text: s }) : null);
  }

  function media(f, wrapperClass, onFail) {
    const wrap = h("div", { class: wrapperClass });
    const back = h("img", { class: "post-backdrop", src: f.image, alt: "", "aria-hidden": "true", loading: "lazy", referrerpolicy: "no-referrer" });
    const img = h("img", {
      class: "post-img", src: f.image, loading: "lazy", decoding: "async", referrerpolicy: "no-referrer",
      alt: f.title ? `Picture from the Wikipedia article on ${f.title}` : "",
    });
    img.addEventListener("error", onFail);
    wrap.append(back, img);
    return { wrap, img };
  }

  function ghostYear(f) {
    return h("div", { class: "post-ghost", "aria-hidden": "true", text: String(Math.abs(f.year)) });
  }

  // Cover the screen when the picture's shape is close to the screen's shape.
  // Otherwise show all of it over a blurred copy, so faces and maps are not cut.
  function fitImage(post) {
    const img = $(".post-img", post);
    if (!img || !img.naturalWidth) return;
    const box = post.getBoundingClientRect();
    if (!box.width || !box.height) return;
    const ratio = (img.naturalWidth / img.naturalHeight) / (box.width / box.height);
    const small = img.naturalWidth < box.width * 0.45;
    post.classList.toggle("fit-contain", small || ratio > 1.55 || ratio < 0.62);
  }
  let resizeTimer;
  window.addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => $$(".post").forEach(fitImage), 150);
  });

  // ---------------------------------------------------------------- full-screen post
  function railButton(act, label, iconName, extra = {}) {
    const tag = extra.href ? "a" : "button";
    const attrs = { class: `ract ${act}`, "data-act": act, title: extra.title || label };
    if (tag === "button") attrs.type = "button";
    Object.assign(attrs, extra.attrs || {});
    if (extra.href) Object.assign(attrs, { href: extra.href, target: "_blank", rel: "noopener noreferrer" });
    return h(tag, attrs, h("span", { class: "disc", html: ICON[iconName] }), h("span", { text: label }));
  }

  function postEl(f) {
    const post = h("article", { class: "post" + (f.hidden ? " is-hidden" : ""), "data-fact": f.id, "aria-label": `${yearText(f.year)}. ${headlineOf(f)}` });
    post.style.setProperty("--pigment", pigment(f.era));
    const makeNoImage = () => {
      post.classList.add("no-image");
      post.classList.remove("fit-contain");
      const m = $(".post-media", post);
      if (m) m.replaceWith(ghostYear(f));
    };
    if (f.image) {
      const { wrap, img } = media(f, "post-media", makeNoImage);
      img.addEventListener("load", () => fitImage(post));
      post.append(wrap);
    } else {
      post.classList.add("no-image");
      post.append(ghostYear(f));
    }
    post.append(h("div", { class: "post-scrim" }));

    post.append(h("div", { class: "post-top" },
      isToday(f.month, f.day) ? h("span", { class: "badge is-today", text: "On this day" }) : null,
      h("span", { class: "badge", text: dateText(f.month, f.day) }),
      f.kind === "birth" ? h("span", { class: "badge", text: "Born" }) : null,
      f.kind === "death" ? h("span", { class: "badge", text: "Died" }) : null));

    const text = h("div", { class: "post-text" },
      yearEl(f.year, "post-year"),
      ruler(f.year),
      h("h2", { class: "post-headline", text: headlineOf(f) }));
    if (f.story) {
      text.append(h("p", { class: "post-body", text: f.story }));
    } else if (f.extract || f.description) {
      text.append(h("p", { class: "post-body", text: f.extract || f.description }));
    }
    text.append(h("div", {}, h("button", { class: "more", type: "button", "data-act": "read", text: "Read more" })));
    post.append(text);

    post.append(h("div", { class: "post-rail" },
      railButton("like", "Like", "heart", { title: "Like (L)", attrs: { "aria-pressed": String(!!f.liked) } }),
      railButton("save", "Save", "save", { title: "Save (S)", attrs: { "aria-pressed": String(!!f.saved) } }),
      railButton("hide", "Not for me", "hide", { title: "Not for me (X)" }),
      railButton("listen", "Listen", "listen", { title: "Listen to this post (P)" }),
      f.url ? railButton("open", "Source", "out", { title: "Open on Wikipedia (O)", href: f.url }) : null));

    post.append(h("div", { class: "post-hidden" },
      h("p", { text: "Hidden. You will see fewer posts like this." }),
      h("button", { class: "btn small", type: "button", "data-act": "unhide", text: "Undo" })));
    post._fact = f;
    return post;
  }

  // ---------------------------------------------------------------- reader dialog
  function readerEl(f) {
    const el = h("article", { class: "reader-post", "data-fact": f.id });
    el.style.setProperty("--pigment", pigment(f.era));
    if (f.image) {
      const { wrap } = media(f, "rd-media", () => { wrap.className = "rd-media no-image"; wrap.replaceChildren(ghostYear(f)); });
      $$("img", wrap).forEach((i) => i.setAttribute("loading", "eager"));
      el.append(wrap);
    } else {
      el.append(h("div", { class: "rd-media no-image" }, ghostYear(f)));
    }
    const body = h("div", { class: "rd-body" });
    body.append(h("header", { class: "rd-head" },
      yearEl(f.year, "rd-year"),
      h("div", { class: "rd-when" },
        isToday(f.month, f.day) ? "On this day, " : "", dateText(f.month, f.day),
        f.kind === "birth" ? ". Born" : f.kind === "death" ? ". Died" : "")));
    body.append(ruler(f.year));
    body.append(h("h2", { class: "rd-title", text: headlineOf(f) }));
    if (f.story) {
      body.append(h("p", { class: "rd-lede", text: f.text }));
      body.append(h("p", { class: "rd-text", text: f.story }));
      const src = h("p", { class: "rd-text", hidden: true, text: f.extract || "" });
      const btn = h("button", { class: "linkish", type: "button", text: "Show the Wikipedia text" });
      btn.addEventListener("click", () => {
        src.hidden = !src.hidden;
        btn.textContent = src.hidden ? "Show the Wikipedia text" : "Hide the Wikipedia text";
      });
      body.append(h("p", { class: "rd-meta" }, `Retold by ${f.story_model} from the Wikipedia text. `, f.extract ? btn : null), src);
    } else if (f.extract || f.description) {
      body.append(h("p", { class: "rd-text", text: f.extract || f.description }));
    }
    body.append(h("p", { class: "rd-meta", text: f.topics && f.topics.length ? `${f.era}. Topics: ${f.topics.join(", ")}.` : `${f.era}.` }));
    if (f.related && f.related.length) {
      const ul = h("ul", { class: "related" });
      for (const r of f.related) {
        ul.append(h("li", {},
          h("a", { href: r.url, target: "_blank", rel: "noopener noreferrer", text: r.title }),
          r.description ? h("span", { text: `, ${r.description}` }) : null));
      }
      body.append(h("p", { class: "rd-meta", text: "Linked articles" }), ul);
    }
    const abtn = (act, label, iconName, attrs = {}) =>
      h("button", { class: `btn ${act}`, type: "button", "data-act": act, ...attrs }, h("span", { html: ICON[iconName], style: "display:contents" }), h("span", { class: "lbl", text: label }));
    body.append(h("div", { class: "rd-actions" },
      abtn("like", "Like", "heart", { "aria-pressed": String(!!f.liked) }),
      abtn("save", "Save", "save", { "aria-pressed": String(!!f.saved) }),
      abtn("listen", "Listen", "listen"),
      abtn("hide", "Not for me", "hide"),
      f.url ? h("a", { class: "btn", href: f.url, target: "_blank", rel: "noopener noreferrer", "data-act": "open" },
        h("span", { html: ICON.out, style: "display:contents" }), "Wikipedia") : null));
    el.append(body);
    el._fact = f;
    return el;
  }

  const reader = $("#reader");
  function openReader(fact, fromFeed = false) {
    const body = $("#reader-body");
    body.replaceChildren(readerEl(fact));
    body.scrollTop = 0;
    if (!reader.open) reader.showModal();
    renderPlayer();
    if (fromFeed) api.post("/api/react", { id: fact.id, action: "expand" }).catch(() => {});
  }
  async function openReaderById(id) {
    try { openReader(await api.get(`/api/fact/${id}`)); } catch (e) { toast(e.message); }
  }
  reader.addEventListener("click", (ev) => {
    if (ev.target === reader || ev.target.closest('[data-action="close-reader"]')) reader.close();
  });

  // ---------------------------------------------------------------- reactions
  function syncCards(f) {
    for (const el of $$(`[data-fact="${f.id}"]`)) {
      el._fact = { ...el._fact, liked: f.liked, saved: f.saved, hidden: f.hidden };
      for (const b of $$('[data-act="like"]', el)) b.setAttribute("aria-pressed", String(!!f.liked));
      for (const b of $$('[data-act="save"]', el)) b.setAttribute("aria-pressed", String(!!f.saved));
      if (el.classList.contains("post")) el.classList.toggle("is-hidden", !!f.hidden);
    }
  }

  async function react(el, act, sourceBtn) {
    const f = el._fact;
    let action = act;
    if (act === "like") action = f.liked ? "unlike" : "like";
    if (act === "save") action = f.saved ? "unsave" : "save";
    const optimistic = { ...f };
    if (action === "like" || action === "unlike") optimistic.liked = action === "like";
    if (action === "save" || action === "unsave") optimistic.saved = action === "save";
    if (action === "hide" || action === "unhide") optimistic.hidden = action === "hide";
    syncCards(optimistic);
    const btn = sourceBtn || $(`[data-act="${act}"]`, el);
    if (action === "like") {
      retrigger(btn, "pop");
      burst($(".disc", btn) || btn, [cssVar("--like"), cssVar("--accent")]);
    }
    if (action === "save") retrigger(btn, "drop");
    try {
      const updated = await api.post("/api/react", { id: f.id, action });
      syncCards(updated);
      if (action === "save") toast("Saved. Find it under Saved.");
      if (action === "hide") {
        if (player.fact && player.fact.id === f.id) player.handsFree ? advance() : stopPlayer();
        if (el.closest("#feed")) setTimeout(() => { if (activeSlide() === el.closest(".slide") && el._fact.hidden) go(1); }, 1100);
      }
    } catch (e) {
      syncCards(f);
      toast(`Could not update the post: ${e.message}`);
    }
  }

  document.addEventListener("click", (ev) => {
    const btn = ev.target.closest("[data-act]");
    if (!btn) return;
    const el = btn.closest("[data-fact]");
    if (!el || !el._fact) return;
    const act = btn.dataset.act;
    if (act === "open") { api.post("/api/react", { id: el._fact.id, action: "open" }).catch(() => {}); return; }
    ev.preventDefault();
    if (act === "read") { openReader(el._fact, true); return; }
    if (act === "listen") { listenTo(el._fact, el.closest(".slide")); return; }
    react(el, act, btn);
  });

  // double-click (or double-tap) a picture to like
  function heartAt(post, x, y) {
    if (reduceMotion.matches) return;
    const r = post.getBoundingClientRect();
    const hp = h("div", { class: "heart-pop", html: ICON.heartFill });
    hp.style.left = `${x - r.left}px`;
    hp.style.top = `${y - r.top}px`;
    post.append(hp);
    setTimeout(() => hp.remove(), 900);
  }
  function likeByTap(ev) {
    const post = ev.target.closest(".post");
    if (!post || post.classList.contains("is-hidden") || ev.target.closest("button, a, .post-text")) return;
    heartAt(post, ev.clientX, ev.clientY);
    if (!post._fact.liked) react(post, "like");
    else retrigger($('[data-act="like"]', post), "pop");
  }
  let lastTap = { t: 0, x: 0, y: 0 };

  // ---------------------------------------------------------------- quiz cards
  function quizCard(q) {
    const box = h("section", { class: "quiz", "aria-label": "Quiz" });
    box.append(h("p", { class: "quiz-kicker", text: "Quiz on something you have read" }));
    box.append(h("h2", { text: q.question }));
    let options;
    if (q.kind === "year") {
      box.append(h("p", { class: "quiz-prompt", text: q.prompt }));
      options = h("div", { class: "options years", role: "group", "aria-label": "Answers" });
      q.options.forEach((y, i) => options.append(
        h("button", { class: "opt", type: "button", "data-choice": y, "data-key": i + 1 }, h("kbd", { text: i + 1 }), q.labels[i])));
    } else if (q.kind === "order") {
      options = h("div", { class: "options", role: "group", "aria-label": "Answers" });
      q.pair.forEach((p, i) => options.append(
        h("button", { class: "opt", type: "button", "data-choice": p.id, "data-key": i + 1 }, h("kbd", { text: i + 1 }), h("span", { text: p.text }))));
    } else {
      options = h("div", { class: "options", role: "group", "aria-label": "Answers" });
      q.options.forEach((o, i) => options.append(
        h("button", { class: "opt", type: "button", "data-choice": i, "data-key": i + 1 }, h("kbd", { text: i + 1 }), h("span", { text: o }))));
    }
    box.append(options);
    const verdict = h("p", { class: "verdict", hidden: true, "aria-live": "polite" });
    box.append(verdict);
    options.addEventListener("click", (ev) => {
      const btn = ev.target.closest(".opt");
      if (btn && !btn.disabled) answerQuiz(q, btn, options, verdict);
    });
    return h("div", { class: "quiz-slide" }, box);
  }

  async function answerQuiz(q, btn, options, verdict) {
    const buttons = $$(".opt", options);
    buttons.forEach((b) => { b.disabled = true; });
    const body = { kind: q.kind, fact_id: q.fact_id, choice: Number(btn.dataset.choice) };
    if (q.kind === "order") body.other_id = q.pair.find((p) => p.id !== q.fact_id).id;
    try {
      const res = await api.post("/api/quiz", body);
      const answer = String(res.answer);
      let rightBtn = null;
      buttons.forEach((b) => {
        if (b.dataset.choice === answer) { b.classList.add("right"); rightBtn = b; }
        else if (b === btn) b.classList.add("wrong");
      });
      if (res.correct) {
        burst(rightBtn, [cssVar("--quiz-text"), cssVar("--quiz-right"), "#FFFFFF"], 16);
        floatText(rightBtn, `+${res.xp} XP`);
      }
      verdict.replaceChildren();
      verdict.append(h("strong", { text: res.correct ? "Correct." : "Not quite." }), " ");
      if (q.kind === "year") verdict.append(`It happened in ${res.label}.`);
      else if (q.kind === "order") verdict.append(q.pair.map((p) => `${res.years[p.id]}: ${p.text}`).join(" "));
      else verdict.append(res.fact.text);
      verdict.append(h("span", { class: "xp", text: ` +${res.xp} XP. ` }));
      verdict.append(h("button", { class: "linkish", type: "button", text: "Read the post", onclick: () => openReader(res.fact) }));
      verdict.hidden = false;
      renderProgress(res.progress);
    } catch (e) {
      buttons.forEach((b) => { b.disabled = false; });
      toast(`Could not check the answer: ${e.message}`);
    }
  }

  // ---------------------------------------------------------------- feed
  const feedEl = $("#feed");
  const feed = { loading: false, emptyTimer: null, active: null, start: 0, paused: null };

  const slidesAll = () => $$(".slide", feedEl);
  function activeSlide() {
    const slides = slidesAll();
    if (!slides.length) return null;
    const idx = Math.round(feedEl.scrollTop / Math.max(1, feedEl.clientHeight));
    return slides[Math.min(slides.length - 1, Math.max(0, idx))];
  }
  function scrollToSlide(slide) {
    const smooth = !reduceMotion.matches && !document.hidden && state.route === "feed";
    slide.scrollIntoView({ behavior: smooth ? "smooth" : "auto", block: "start" });
  }
  function go(delta) {
    const slides = slidesAll();
    const cur = activeSlide();
    const idx = Math.max(0, Math.min(slides.length - 1, slides.indexOf(cur) + delta));
    if (slides[idx]) scrollToSlide(slides[idx]);
  }

  function endView() {
    const a = feed.active;
    feed.active = null;
    if (!a) return;
    a.classList.remove("is-active");
    if (!a.dataset.factId) return;
    const ms = Math.round(performance.now() - feed.start);
    if (ms < 800) return;
    api.post("/api/view", { id: Number(a.dataset.factId), ms }, true)
      .then(renderProgress).catch(() => {});
  }
  function startView(slide) {
    if (feed.active === slide) return;
    endView();
    feed.active = slide;
    feed.start = performance.now();
    slide.classList.add("is-active");
    const post = $(".post", slide);
    if (post) fitImage(post);
    const slides = slidesAll();
    if (slides.indexOf(slide) >= slides.length - 4) loadMore();
    playerFollowsScroll(slide);
  }

  const observer = new IntersectionObserver((entries) => {
    if (state.route !== "feed" || document.hidden) return;
    for (const e of entries) {
      if (e.isIntersecting && e.intersectionRatio >= 0.6) startView(e.target);
      else if (feed.active === e.target && !player.handsFree) endView();
    }
  }, { root: feedEl, threshold: [0.6] });

  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      if (player.handsFree && player.status !== "idle") return; // keep listening in the background
      feed.paused = feed.active;
      endView();
    } else if (state.route === "feed" && feed.paused) {
      startView(feed.paused);
      feed.paused = null;
    }
  });
  window.addEventListener("pagehide", endView);

  function addSlide(content, factId) {
    const slide = h("div", { class: "slide" }, content);
    if (factId) slide.dataset.factId = factId;
    feedEl.append(slide);
    observer.observe(slide);
    return slide;
  }

  function emptyNotice(st) {
    const n = h("div", { class: "notice" });
    if (st && st.crawler && st.crawler.last_error && st.facts === 0) {
      n.append(h("h2", { text: "Wikipedia is not reachable" }),
        h("p", { text: "Scrollback gets its posts from Wikipedia and will keep retrying on its own. Check that this computer is online." }),
        h("button", { class: "btn primary", type: "button", text: "Try again now",
          onclick: () => api.post("/api/refresh").then(() => toast("Trying again.")) }));
    } else if (!st || st.facts === 0 || (st.unseen === 0 && st.days_fetched < st.days_total)) {
      n.append(h("div", { class: "loader", "aria-hidden": "true" }),
        h("h2", { text: st && st.facts ? "Fetching more history" : "Fetching history from Wikipedia" }),
        h("p", { text: "The first posts appear within a few seconds. Scrollback keeps fetching the rest of the year in the background." }));
    } else {
      n.append(h("h2", { text: "You have read everything" }),
        h("p", { text: "Every post in your library has been read. Explore lets you go back to any of them." }),
        h("a", { class: "btn primary", href: "#/explore", text: "Open Explore" }));
    }
    return h("div", { class: "notice-slide" }, n);
  }

  async function loadMore() {
    if (feed.loading) return false;
    feed.loading = true;
    let added = false;
    try {
      const data = await api.get("/api/feed?n=8");
      renderLibrary(data.status);
      renderProgress(data.status.progress);
      const empty = $("#feed-empty", feedEl);
      if (data.items.length) {
        if (empty) empty.remove();
        clearTimeout(feed.emptyTimer);
        for (const item of data.items) {
          if (item.type === "fact") { addSlide(postEl(item.fact), item.fact.id); added = true; }
          else addSlide(quizCard(item.quiz)).classList.add("slide-quiz");
        }
      } else {
        const notice = emptyNotice(data.status);
        if (empty) empty.replaceChildren(notice);
        else addSlide(notice).id = "feed-empty";
        clearTimeout(feed.emptyTimer);
        feed.emptyTimer = setTimeout(() => { feed.loading = false; loadMore(); }, 4000);
      }
    } catch (e) {
      toast(`Could not load posts: ${e.message}`);
    } finally {
      feed.loading = false;
    }
    return added;
  }

  feedEl.addEventListener("dblclick", (ev) => { if (ev.pointerType !== "touch") likeByTap(ev); });
  feedEl.addEventListener("pointerup", (ev) => {
    if (ev.pointerType !== "touch") return;
    const now = performance.now();
    if (now - lastTap.t < 320 && Math.hypot(ev.clientX - lastTap.x, ev.clientY - lastTap.y) < 40) {
      likeByTap(ev);
      lastTap = { t: 0, x: 0, y: 0 };
    } else {
      lastTap = { t: now, x: ev.clientX, y: ev.clientY };
    }
  });

  // ---------------------------------------------------------------- listening (Piper)
  const audio = $("#audio");
  const player = { handsFree: false, fact: null, slide: null, status: "idle", tts: null, followTimer: null };

  async function ttsStatus(force = false) {
    if (!player.tts || force) {
      try { player.tts = await api.get("/api/tts/status"); } catch (e) { player.tts = null; }
    }
    return player.tts;
  }

  async function ttsReady() {
    let st = await ttsStatus();
    if (!st || !st.available) st = await ttsStatus(true);
    if (st && st.available) return true;
    toast(st ? st.reason : "Text to speech is not available.", { label: "Settings", run: () => { location.hash = "#/settings"; } });
    return false;
  }

  function renderPlayer() {
    const active = player.status !== "idle";
    document.body.classList.toggle("is-listening", active);
    const live = player.handsFree || active;
    $("#listen-btn").hidden = live;
    $("#listen-btn").setAttribute("aria-pressed", String(player.handsFree));
    const box = $("#player-live");
    box.hidden = !live;
    $(".view-feed").classList.toggle("has-player", live);
    box.classList.toggle("is-paused", player.status === "paused" || player.status === "idle");
    box.classList.toggle("is-loading", player.status === "loading");
    $("#pl-toggle").setAttribute("aria-label", player.status === "playing" ? "Pause" : "Play");
    $("#pl-next").hidden = !player.handsFree;
    const f = player.fact;
    let title = player.handsFree ? "Hands-free" : "Listening";
    if (f) title = `${yearText(f.year)}, ${headlineOf(f)}`;
    if (player.status === "loading") title = f ? `Preparing ${yearText(f.year)}` : "Preparing";
    $("#pl-title").textContent = title;

    for (const b of $$('[data-act="listen"]')) {
      const el = b.closest("[data-fact]");
      const mine = f && el && el._fact && el._fact.id === f.id;
      b.classList.toggle("is-loading", !!mine && player.status === "loading");
      b.classList.toggle("is-playing", !!mine && player.status === "playing");
      const lbl = $(".lbl", b) || b.lastElementChild;
      if (lbl && b.closest(".rd-actions")) lbl.textContent = mine && player.status === "playing" ? "Pause" : "Listen";
    }
    for (const p of $$(".post.is-playing")) p.classList.remove("is-playing");
    if (player.slide && active) { const p = $(".post", player.slide); if (p) p.classList.add("is-playing"); }
  }

  function setMediaSession(f) {
    if (!("mediaSession" in navigator) || !f) return;
    try {
      navigator.mediaSession.metadata = new MediaMetadata({
        title: headlineOf(f),
        artist: `Scrollback, ${yearText(f.year)}`,
        album: f.era,
        artwork: f.image ? [{ src: f.image, sizes: "512x512" }] : [],
      });
    } catch (e) { /* older browsers */ }
  }

  function prefetchAhead() {
    if (!player.slide) return;
    const ids = [];
    let s = player.slide.nextElementSibling;
    while (s && ids.length < 3) {
      if (s.dataset.factId) ids.push(Number(s.dataset.factId));
      s = s.nextElementSibling;
    }
    if (ids.length) api.post("/api/tts/prefetch", { ids }).catch(() => {});
  }

  function playFact(f, slide) {
    player.fact = f;
    player.slide = slide || null;
    player.status = "loading";
    audio.src = `/api/tts/fact/${f.id}.wav`;
    audio.play().catch((err) => { if (err.name !== "AbortError") onAudioError(); });
    setMediaSession(f);
    prefetchAhead();
    renderPlayer();
  }

  async function listenTo(f, slide) {
    if (player.fact && player.fact.id === f.id && player.status !== "idle") {
      if (player.status === "playing") audio.pause();
      else audio.play().catch(() => {});
      return;
    }
    if (!(await ttsReady())) return;
    playFact(f, slide);
  }

  function stopPlayer() {
    player.handsFree = false;
    player.status = "idle";
    player.fact = null;
    player.slide = null;
    audio.pause();
    audio.removeAttribute("src");
    audio.load();
    $("#pl-progress").style.width = "0";
    if ("mediaSession" in navigator) navigator.mediaSession.metadata = null;
    renderPlayer();
  }

  function nextFactSlide(from, dir = 1) {
    let s = from ? (dir > 0 ? from.nextElementSibling : from.previousElementSibling) : null;
    while (s) {
      const post = $(".post", s);
      if (s.dataset.factId && post && !post.classList.contains("is-hidden")) return s;
      s = dir > 0 ? s.nextElementSibling : s.previousElementSibling;
    }
    return null;
  }

  async function advance(dir = 1) {
    const from = player.slide || activeSlide();
    let next = nextFactSlide(from, dir);
    if (!next && dir > 0) {
      for (let i = 0; i < 3 && !next; i++) {
        await loadMore();
        next = nextFactSlide(from, dir);
        if (!next) await new Promise((r) => setTimeout(r, 1500));
      }
    }
    if (!next) {
      if (dir > 0) { toast("No more posts for now."); stopPlayer(); }
      return;
    }
    playFact(next._fact || $(".post", next)._fact, next);
    scrollToSlide(next);
    startView(next);
  }

  function playerFollowsScroll(slide) {
    clearTimeout(player.followTimer);
    if (player.status === "idle" || slide === player.slide) return;
    player.followTimer = setTimeout(() => {
      if (feed.active !== slide) return;
      const post = $(".post", slide);
      if (player.handsFree && post && !post.classList.contains("is-hidden")) playFact(post._fact, slide);
      else if (player.handsFree) audio.pause();       // a quiz: wait here
      else if (player.slide) stopPlayer();            // single post played and you moved on
    }, 380);
  }

  async function toggleHandsFree() {
    if (player.handsFree) { stopPlayer(); return; }
    if (!(await ttsReady())) return;
    if (state.route !== "feed") location.hash = "#/feed";
    player.handsFree = true;
    let slide = activeSlide();
    if (slide && !slide.dataset.factId) slide = nextFactSlide(slide);
    if (slide) {
      playFact($(".post", slide)._fact, slide);
      if (slide !== activeSlide()) { scrollToSlide(slide); startView(slide); }
    } else {
      renderPlayer();
    }
    toast("Hands-free on. Scrollback reads each post and moves on by itself.");
  }

  async function togglePlayActive() {
    const slide = activeSlide();
    if (player.status !== "idle" && (!slide || slide === player.slide || !slide.dataset.factId)) {
      if (player.status === "playing" || player.status === "loading") audio.pause();
      else audio.play().catch(() => {});
      return;
    }
    if (slide && slide.dataset.factId) listenTo($(".post", slide)._fact, slide);
  }

  async function onAudioError() {
    const src = audio.getAttribute("src");
    if (!src) return;
    let msg = "Could not play this post.";
    try {
      const r = await fetch(src);
      if (!r.ok) msg = (await r.json()).error || msg;
    } catch (e) { /* keep the generic message */ }
    stopPlayer();
    ttsStatus(true);
    toast(msg, { label: "Settings", run: () => { location.hash = "#/settings"; } });
  }

  audio.addEventListener("playing", () => { player.status = "playing"; renderPlayer(); });
  audio.addEventListener("waiting", () => { if (player.status !== "idle") { player.status = "loading"; renderPlayer(); } });
  audio.addEventListener("pause", () => {
    if (audio.ended || player.status === "idle") return;
    player.status = "paused";
    renderPlayer();
  });
  audio.addEventListener("ended", () => {
    if (player.handsFree) {
      player.status = "loading";
      renderPlayer();
      setTimeout(() => { if (player.handsFree) advance(1); }, 650);
    } else {
      stopPlayer();
    }
  });
  audio.addEventListener("error", () => { if (player.status !== "idle") onAudioError(); });
  audio.addEventListener("timeupdate", () => {
    const d = audio.duration;
    $("#pl-progress").style.width = d && isFinite(d) ? `${(audio.currentTime / d) * 100}%` : "0";
  });

  $("#listen-btn").addEventListener("click", toggleHandsFree);
  $("#pl-toggle").addEventListener("click", () => {
    if (player.status === "playing" || player.status === "loading") audio.pause();
    else if (player.fact) audio.play().catch(() => {});
    else togglePlayActive();
  });
  $("#pl-next").addEventListener("click", () => advance(1));
  $("#pl-stop").addEventListener("click", stopPlayer);

  if ("mediaSession" in navigator) {
    const set = (name, fn) => { try { navigator.mediaSession.setActionHandler(name, fn); } catch (e) { /* unsupported */ } };
    set("play", () => { if (player.fact) audio.play().catch(() => {}); else togglePlayActive(); });
    set("pause", () => audio.pause());
    set("stop", stopPlayer);
    set("nexttrack", () => { if (player.handsFree) advance(1); else go(1); });
    set("previoustrack", () => { if (player.handsFree) advance(-1); else go(-1); });
  }

  // ---------------------------------------------------------------- keyboard
  document.addEventListener("keydown", (ev) => {
    if (ev.ctrlKey || ev.metaKey || ev.altKey) return;
    const tag = (ev.target.tagName || "").toLowerCase();
    if (["input", "select", "textarea"].includes(tag)) return;
    const key = ev.key.toLowerCase();
    if (ev.key === "?") { $("#keys").showModal(); return; }
    if (key === "t" && !$("#keys").open) { cycleTheme(); return; }
    if (key === "h" && !$("#keys").open) { toggleHandsFree(); return; }
    if ($("#keys").open) return;
    if (reader.open) {
      if (key === "p") { const el = $(".reader-post", reader); if (el) listenTo(el._fact, null); }
      return;
    }
    if (state.route !== "feed") return;
    const slide = activeSlide();
    if (!slide) return;
    const post = $(".post", slide);
    if ((ev.key === " " || ev.key === "Enter") && ev.target.closest("button, a")) return;
    if (key === "j" || ev.key === "ArrowDown" || ev.key === "PageDown" || (ev.key === " " && !ev.shiftKey)) { ev.preventDefault(); go(1); }
    else if (key === "k" || ev.key === "ArrowUp" || ev.key === "PageUp" || (ev.key === " " && ev.shiftKey)) { ev.preventDefault(); go(-1); }
    else if (key === "p") { ev.preventDefault(); togglePlayActive(); }
    else if (post && key === "l") react(post, "like");
    else if (post && key === "s") react(post, "save");
    else if (post && key === "x") react(post, post._fact.hidden ? "unhide" : "hide");
    else if (post && key === "o" && post._fact.url) { window.open(post._fact.url, "_blank", "noopener"); api.post("/api/react", { id: post._fact.id, action: "open" }).catch(() => {}); }
    else if (post && ev.key === "Enter") { ev.preventDefault(); openReader(post._fact, true); }
    else if (/^[1-4]$/.test(ev.key)) {
      const opt = $(`.opt[data-key="${ev.key}"]`, slide);
      if (opt && !opt.disabled) opt.click();
    }
  });
  $('[data-action="shortcuts"]').addEventListener("click", () => $("#keys").showModal());

  // ---------------------------------------------------------------- list rows (explore, saved)
  function listRow(it) {
    const [n, s] = yearParts(it.year);
    const meta = `${dateText(it.month, it.day)}, ${it.era}`;
    const marks = h("span", { class: "marks" },
      it.liked ? h("span", { class: "liked", html: ICON.heartFill, title: "Liked" }) : null,
      it.saved ? h("span", { class: "saved", html: SVG('<path d="M7 4h10v16l-5-4-5 4z" fill="currentColor"/>'), title: "Saved" }) : null,
      !it.seen ? h("span", { class: "unread", title: "Unread" }) : null);
    const noimg = () => { const e = h("span", { class: "noimg" }); e.style.setProperty("--pigment", pigment(it.era)); return e; };
    return h("li", {}, h("button", { class: "row", type: "button", onclick: () => openReaderById(it.id) },
      h("span", { class: "ry", "aria-label": yearText(it.year) }, n, s ? h("small", { text: s }) : null),
      it.image ? h("img", { src: it.image, alt: "", loading: "lazy", referrerpolicy: "no-referrer", onerror: (e) => e.target.replaceWith(noimg()) })
        : noimg(),
      h("span", {}, h("span", { class: "rt", text: it.text }), h("span", { class: "rm", text: meta })),
      marks));
  }

  // ---------------------------------------------------------------- explore
  const explore = { era: "", topic: "", q: "", month: "", day: "", sort: "chrono", unseen: false, offset: 0, built: false };

  function buildExplore(section) {
    const meta = state.meta || { eras: [], topics: [] };
    const search = h("input", { type: "search", placeholder: "Battle, Byzantium, telescope", "aria-label": "Search" });
    let timer;
    search.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(() => { explore.q = search.value; runExplore(true); }, 300); });
    const topic = h("select", { "aria-label": "Topic" }, h("option", { value: "", text: "All topics" }),
      meta.topics.map((t) => h("option", { value: t, text: t })));
    topic.addEventListener("change", () => { explore.topic = topic.value; runExplore(true); });
    const month = h("select", { "aria-label": "Month" }, h("option", { value: "", text: "Any month" }),
      MONTHS.map((m, i) => h("option", { value: i + 1, text: m })));
    month.addEventListener("change", () => { explore.month = month.value; runExplore(true); });
    const day = h("select", { "aria-label": "Day" }, h("option", { value: "", text: "Any day" }),
      Array.from({ length: 31 }, (_, i) => h("option", { value: i + 1, text: i + 1 })));
    day.addEventListener("change", () => { explore.day = day.value; runExplore(true); });
    const sort = h("select", { "aria-label": "Order" },
      h("option", { value: "chrono", text: "Oldest first" }), h("option", { value: "recent", text: "Newest first" }),
      h("option", { value: "random", text: "Random" }));
    sort.addEventListener("change", () => { explore.sort = sort.value; runExplore(true); });
    const unread = h("input", { type: "checkbox" });
    unread.addEventListener("change", () => { explore.unseen = unread.checked; runExplore(true); });
    const todayBtn = h("button", { class: "btn small", type: "button", text: "Today's date" });
    todayBtn.addEventListener("click", () => {
      const t = new Date();
      month.value = explore.month = String(t.getMonth() + 1);
      day.value = explore.day = String(t.getDate());
      runExplore(true);
    });

    const chips = h("div", { class: "eras-bar", role: "group", "aria-label": "Era" });
    const chipFor = (value, label) => {
      const c = h("button", { class: "chip", type: "button", "aria-pressed": String(explore.era === value) },
        value ? h("span", { class: "dot", style: `background:${pigment(value)}` }) : null, label);
      c.addEventListener("click", () => {
        explore.era = value;
        $$(".chip", chips).forEach((x) => x.setAttribute("aria-pressed", "false"));
        c.setAttribute("aria-pressed", "true");
        runExplore(true);
      });
      return c;
    };
    chips.append(chipFor("", "All eras"), ...meta.eras.map((e) => chipFor(e, e)));

    section.replaceChildren(h("div", { class: "page" },
      h("h1", { class: "page-title", text: "Explore" }),
      h("p", { class: "page-sub", text: "Everything Scrollback has fetched so far. Filter by era, topic or date. You can also search for a word." }),
      h("div", { class: "filters" },
        h("label", { class: "field" }, "Search", search),
        h("label", { class: "field" }, "Topic", topic),
        h("label", { class: "field" }, "Month", month),
        h("label", { class: "field" }, "Day", day),
        h("label", { class: "field" }, "Order", sort),
        todayBtn,
        h("label", { class: "check" }, unread, "Unread only")),
      chips,
      h("p", { class: "count", id: "ex-count" }),
      h("ul", { class: "list", id: "ex-list" }),
      h("div", { class: "more-row", id: "ex-more" })));
    explore.built = true;
  }

  async function runExplore(reset) {
    if (reset) explore.offset = 0;
    const p = new URLSearchParams({ era: explore.era, topic: explore.topic, q: explore.q, month: explore.month,
      day: explore.day, sort: explore.sort, unseen: explore.unseen ? "1" : "0", offset: explore.offset });
    try {
      const data = await api.get(`/api/explore?${p}`);
      const list = $("#ex-list");
      if (reset) list.replaceChildren();
      data.items.forEach((it) => list.append(listRow(it)));
      $("#ex-count").textContent = data.total === 1 ? "1 post" : `${fmt(data.total)} posts`;
      const shown = data.offset + data.items.length;
      const more = $("#ex-more");
      more.replaceChildren();
      if (!data.total) {
        list.replaceChildren(h("li", { class: "empty" }, h("strong", { text: "No posts match" }),
          "Clear a filter or try a shorter search word."));
      } else if (shown < data.total && explore.sort !== "random") {
        more.append(h("button", { class: "btn", type: "button", text: "Show more",
          onclick: () => { explore.offset = shown; runExplore(false); } }));
      }
    } catch (e) { toast(e.message); }
  }

  // ---------------------------------------------------------------- saved
  let savedWhich = "saved";
  async function renderSaved(section) {
    const tabs = h("div", { class: "tabs", role: "group", "aria-label": "Collection" },
      ["saved", "liked"].map((w) => h("button", { class: "chip", type: "button", "aria-pressed": String(savedWhich === w),
        text: w === "saved" ? "Saved" : "Liked", onclick: () => { savedWhich = w; renderSaved(section); } })));
    const list = h("ul", { class: "list" });
    section.replaceChildren(h("div", { class: "page" },
      h("h1", { class: "page-title", text: savedWhich === "saved" ? "Saved" : "Liked" }),
      h("p", { class: "page-sub", text: savedWhich === "saved"
        ? "Posts you saved, newest first."
        : "Posts you liked, newest first. Likes also give similar posts more space in your feed." }),
      tabs, list));
    try {
      const data = await api.get(`/api/collection?which=${savedWhich}`);
      if (!data.items.length) {
        list.append(h("li", { class: "empty" },
          h("strong", { text: savedWhich === "saved" ? "Nothing saved yet" : "Nothing liked yet" }),
          savedWhich === "saved" ? "Press Save on a post to keep it here. The S key does the same."
            : "Press Like on a post or double-click its picture. The L key does the same."));
      }
      data.items.forEach((it) => list.append(listRow(it)));
    } catch (e) { toast(e.message); }
  }

  // ---------------------------------------------------------------- progress page
  async function renderStats(section) {
    let s;
    try { s = await api.get("/api/stats"); } catch (e) { toast(e.message); return; }
    renderProgress(s.progress);
    const q = s.quiz;
    const answered = q.correct + q.wrong;
    const numbers = h("div", { class: "numbers" },
      [[fmt(s.read), "posts read"], [fmt(s.minutes), "minutes reading or listening"],
        [answered ? `${Math.round((q.correct / answered) * 100)}%` : "None", answered ? `of ${fmt(answered)} quiz answers right` : "quiz answers yet"],
        [fmt(q.learned), "facts learned, answered right three times running"],
        [fmt(s.progress.streak), s.progress.streak === 1 ? "day streak" : "days streak"]]
        .map(([v, l]) => h("div", { class: "number" }, h("strong", { text: v }), h("span", { text: l }))));

    const maxDay = Math.max(s.progress.goal, ...s.days.map((d) => d.cards));
    const days = h("div", { class: "days", role: "img",
      "aria-label": `Posts read on each of the last 14 days: ${s.days.map((d) => d.cards).join(", ")}` },
      s.days.map((d, i) => h("div", { class: "day" + (d.goal_met ? " met" : ""), title: `${d.day}: ${d.cards} posts` },
        h("div", { class: "bar", style: `height:${(d.cards / maxDay) * 100}%;animation-delay:${i * 30}ms` }),
        h("span", { text: Number(d.day.slice(8)) }))));

    const eraRows = h("div", { class: "bars" }, s.eras.map((e) => h("div", { class: "bar-row" },
      h("span", { text: e.era }),
      h("div", { class: "track" }, h("div", { class: "fill", style: `width:${e.total ? (e.read / e.total) * 100 : 0}%;--pigment:${pigment(e.era)}` })),
      h("span", { class: "num", text: `${fmt(e.read)} of ${fmt(e.total)}` }))));

    const topicRows = s.topics.length
      ? h("div", { class: "bars aff" }, s.topics.map((t) => {
        const w = Math.min(50, (Math.abs(t.score) / 30) * 50);
        const style = t.score >= 0 ? `left:50%;width:${w}%` : `left:${50 - w}%;width:${w}%`;
        return h("div", { class: "bar-row" }, h("span", { text: t.topic }),
          h("div", { class: "track" }, h("div", { class: `fill ${t.score >= 0 ? "pos" : "neg"}`, style })),
          h("span", { class: "num", text: t.score > 0 ? `+${t.score}` : String(t.score) }));
      }))
      : h("p", { class: "empty", text: "Nothing yet. Read, like and save posts and this fills in." });

    section.replaceChildren(h("div", { class: "page" },
      h("h1", { class: "page-title", text: "Progress" }),
      h("p", { class: "page-sub", text: `Rank: ${s.progress.level.name}. ${fmt(s.progress.xp)} XP in total. A post counts as read after you stay on it for a moment.` }),
      numbers,
      h("div", { class: "block" }, h("h2", { class: "block-title", text: "The last 14 days" }), days),
      h("div", { class: "block" }, h("h2", { class: "block-title", text: "How much of each era you have read" }), eraRows),
      h("div", { class: "block" }, h("h2", { class: "block-title", text: "What your feed has learned" }),
        h("p", { class: "page-sub", text: "Topics you read for longer, like or save get more space. Not for me and quick skips give them less." }),
        topicRows)));
  }

  // ---------------------------------------------------------------- settings
  const SPEEDS = [["0.85", "Slower"], ["1.00", "Normal"], ["1.15", "A little faster"], ["1.30", "Faster"]];

  function voiceSection(tts) {
    const fs = h("fieldset", {}, h("legend", { text: "Listening" }));
    let line, cls;
    if (tts.available) {
      const v = tts.voices.find((x) => x.key === tts.voice);
      line = `Ready. Voice: ${v ? v.label : tts.voice}.`;
      cls = "ok";
    } else {
      line = tts.reason || "Text to speech is not ready.";
      cls = "bad";
    }
    if (tts.last_error) { line += ` Last error: ${tts.last_error}`; cls = "bad"; }
    fs.append(h("p", { class: "status-line " + cls, text: line }));

    if (!tts.python) {
      fs.append(h("p", { class: "hint" }, "Run this once in a terminal, from the Scrollback folder. It installs Piper in a private Python environment and downloads a British English voice of about 60 MB: ",
        h("code", { text: "./scripts/setup-tts.sh" })));
      fs.append(h("div", { class: "form-actions" }, h("button", { class: "btn", type: "button", text: "Check again",
        onclick: async () => { player.tts = await api.post("/api/tts/recheck"); renderSettings($('.view[data-view="settings"]')); } })));
      return fs;
    }

    const voiceSel = h("select", { name: "tts_voice", disabled: !tts.voices.length },
      tts.voices.length ? tts.voices.map((v) => h("option", { value: v.key, text: v.label, selected: v.key === tts.voice }))
        : h("option", { value: "", text: "No voice installed yet" }));
    const cur = Number(tts.speed || 1).toFixed(2);
    const speedSel = h("select", { name: "tts_speed" },
      SPEEDS.map(([v, l]) => h("option", { value: v, text: l, selected: Math.abs(Number(v) - Number(cur)) < 0.04 })));
    fs.append(h("div", { class: "row-inline" },
      h("label", { class: "field" }, "Voice", voiceSel),
      h("label", { class: "field" }, "Speaking speed", speedSel)));
    if (tts.voices.length) {
      fs.append(h("div", { class: "form-actions" }, h("button", { class: "btn", type: "button", text: "Play a sample",
        onclick: (ev) => {
          const b = ev.currentTarget;
          b.disabled = true;
          b.textContent = "Preparing the sample";
          const a = new Audio(`/api/tts/sample.wav?voice=${encodeURIComponent(voiceSel.value)}&speed=${speedSel.value}&t=${Date.now()}`);
          a.addEventListener("playing", () => { b.textContent = "Playing"; });
          a.addEventListener("ended", () => { b.disabled = false; b.textContent = "Play a sample"; });
          a.addEventListener("error", () => { b.disabled = false; b.textContent = "Play a sample"; toast("The sample could not be made. Check the voice."); });
          a.play().catch(() => {});
        } })));
    }

    // adding voices
    const dl = tts.download || {};
    const addSel = h("select", { "aria-label": "Voice to download" }, h("option", { value: "", text: "Loading the voice list" }));
    const addBtn = h("button", { class: "btn", type: "button", text: "Download", disabled: true });
    const progress = h("div", { class: "dl-progress", hidden: !dl.active }, h("span", { style: `width:${dl.total ? (dl.done / dl.total) * 100 : 3}%` }));
    const note = h("p", { class: "hint", text: dl.active ? `Downloading ${dl.voice}.` : dl.error ? `The last download failed: ${dl.error}` : "Voices come from the Piper project on Hugging Face. Medium quality is the best balance of sound and speed." });
    fs.append(h("div", { class: "row-inline" }, h("label", { class: "field" }, "Add a voice", addSel), addBtn), progress, note);
    api.get("/api/tts/catalog").then(({ voices }) => {
      addSel.replaceChildren(...voices.map((v) => h("option", { value: v.key, disabled: v.installed,
        text: `${v.label}${v.size_mb ? `, ${v.size_mb} MB` : ""}${v.installed ? ", installed" : ""}` })));
      const first = voices.find((v) => !v.installed);
      if (first) addSel.value = first.key;
      addBtn.disabled = dl.active || !first;
    }).catch(() => { addSel.replaceChildren(h("option", { value: "", text: "The voice list could not be loaded" })); });
    addBtn.addEventListener("click", async () => {
      if (!addSel.value) return;
      try {
        await api.post("/api/tts/download", { voice: addSel.value });
        addBtn.disabled = true;
        watchDownload();
      } catch (e) { toast(e.message); }
    });
    if (dl.active) watchDownload();
    return fs;
  }

  let dlTimer = null;
  function watchDownload() {
    clearTimeout(dlTimer);
    dlTimer = setTimeout(async () => {
      const st = await ttsStatus(true);
      if (state.route !== "settings" || !st) return;
      const d = st.download;
      const bar = $(".dl-progress span");
      if (bar) { bar.parentElement.hidden = !d.active; bar.style.width = `${d.total ? (d.done / d.total) * 100 : 3}%`; }
      if (d.active) watchDownload();
      else {
        toast(d.error ? `Download failed: ${d.error}` : `${d.voice} is ready.`);
        renderSettings($('.view[data-view="settings"]'));
      }
    }, 900);
  }

  async function renderSettings(section) {
    let data, st, tts;
    try { [data, st, tts] = await Promise.all([api.get("/api/settings"), api.get("/api/status"), ttsStatus(true)]); }
    catch (e) { toast(e.message); return; }
    tts = tts || { available: false, reason: "Could not ask Scrollback about text to speech.", voices: [] };
    const s = data.settings;
    const locked = new Set(s._env_locked || []);
    const lockNote = (k) => locked.has(k) ? h("p", { class: "hint", text: "Set by an environment variable, so it cannot be changed here." }) : null;
    const input = (name, attrs) => h("input", { name, value: s[name], disabled: locked.has(name), ...attrs });

    const provider = h("select", { name: "llm_provider", disabled: locked.has("llm_provider") },
      [["none", "Off"], ["anthropic", "Anthropic API"], ["openai", "OpenAI-compatible (Ollama, LiteLLM, vLLM, OpenAI)"]]
        .map(([v, l]) => h("option", { value: v, text: l, selected: s.llm_provider === v })));
    const people = h("input", { type: "checkbox", name: "include_people", checked: s.include_people === "1" });

    const sl = st.storyteller;
    let line, cls;
    if (sl.provider === "none") { line = "The storyteller is off. Posts show the Wikipedia text."; cls = "off"; }
    else if (!sl.ready) { line = `Not ready: ${sl.reason}.`; cls = "bad"; }
    else if (sl.last_error) { line = `Last request failed: ${sl.last_error}`; cls = "bad"; }
    else { line = `Ready, using ${sl.model}. ${fmt(sl.done)} posts retold so far.`; cls = "ok"; }

    const themeRadios = h("div", { class: "segmented", role: "radiogroup", "aria-label": "Theme" },
      [["auto", "Match the system"], ["light", "Light"], ["dark", "Dark"]].map(([v, l]) =>
        h("label", {}, h("input", { type: "radio", name: "ui_theme", value: v, checked: state.themePref === v,
          onchange: () => applyTheme(v) }), h("span", { text: l }))));

    const form = h("form", { class: "form" },
      h("fieldset", {}, h("legend", { text: "Appearance" }), themeRadios,
        h("p", { class: "hint", text: "Changes straight away. The T key switches between light and dark." })),
      voiceSection(tts),
      h("fieldset", {}, h("legend", { text: "Reading" }),
        h("label", { class: "field" }, "Daily goal, in posts", input("daily_goal", { type: "number", min: 1, max: 500 })),
        h("label", { class: "field" }, "Show a quiz after every", input("quiz_every", { type: "number", min: 3, max: 50 })),
        h("p", { class: "hint", text: "Quizzes only ask about posts you have already read. Wrong answers come back after 20 minutes, right ones after longer and longer gaps. Hands-free listening passes over quizzes." })),
      h("fieldset", {}, h("legend", { text: "Content" }),
        h("label", { class: "field" }, "Wikipedia language code", input("language", { type: "text", maxlength: 12 })),
        lockNote("language"),
        h("p", { class: "hint", text: "en works best. Wikipedia only publishes its On this day data for some languages, such as de, fr, es, it, pt, ru and sv." }),
        h("label", { class: "check" }, people, "Include births and deaths of notable people"),
        h("label", { class: "field" }, "Your contact for Wikimedia (optional)", input("contact", { type: "text", placeholder: "you@example.com" })),
        lockNote("contact"),
        h("p", { class: "hint", text: "Wikimedia asks automated tools to say who runs them. If you fill this in, it is sent in the User-Agent header of requests to Wikipedia and nowhere else." })),
      h("fieldset", {}, h("legend", { text: "Storyteller (optional)" }),
        h("p", { class: "status-line " + cls, text: line }),
        h("label", { class: "field" }, "Model provider", provider), lockNote("llm_provider"),
        h("label", { class: "field" }, "Model name", input("llm_model", { type: "text", placeholder: "claude-haiku-5-5 or llama3.1:8b" })), lockNote("llm_model"),
        h("label", { class: "field" }, "Base URL", input("llm_base_url", { type: "text", placeholder: "http://localhost:11434/v1" })), lockNote("llm_base_url"),
        h("p", { class: "hint", text: "The storyteller rewrites upcoming posts into short stories and writes extra quiz questions. It gets only the Wikipedia text for each post and is told not to add facts. API keys are read from the ANTHROPIC_API_KEY or OPENAI_API_KEY environment variables and are never stored by Scrollback." })),
      h("div", { class: "form-actions" },
        h("button", { class: "btn primary", type: "submit", text: "Save settings" }),
        h("button", { class: "btn", type: "button", text: "Fetch from Wikipedia now",
          onclick: () => api.post("/api/refresh").then(() => toast("Fetching started.")).catch((e) => toast(e.message)) })));

    form.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const out = {};
      for (const el of form.elements) {
        if (!el.name || el.disabled || el.name.startsWith("ui_")) continue;
        out[el.name] = el.type === "checkbox" ? (el.checked ? "1" : "0") : el.value;
      }
      try {
        const res = await api.post("/api/settings", out);
        toast(res.changed.length ? "Settings saved." : "Nothing changed.");
        if (res.changed.some((k) => ["language", "include_people"].includes(k))) {
          stopPlayer();
          feedEl.replaceChildren();
          explore.built = false;
          loadMore();
        }
        renderSettings(section);
        refreshStatus();
      } catch (e) { toast(`Settings not saved: ${e.message}`); }
    });

    section.replaceChildren(h("div", { class: "page" },
      h("h1", { class: "page-title", text: "Settings" }),
      h("p", { class: "page-sub", text: "Scrollback runs only on this computer. Your reading history stays in a local database." }),
      form));
  }

  // ---------------------------------------------------------------- routing
  const ROUTES = ["feed", "explore", "saved", "stats", "settings"];
  function route() {
    const name = (location.hash.replace(/^#\/?/, "") || "feed").split("?")[0];
    const r = ROUTES.includes(name) ? name : "feed";
    if (state.route === "feed" && r !== "feed" && !player.handsFree) endView();
    state.route = r;
    for (const sec of $$(".view")) sec.hidden = sec.dataset.view !== r;
    for (const a of $$(".nav a")) {
      if (a.dataset.route === r) a.setAttribute("aria-current", "page");
      else a.removeAttribute("aria-current");
    }
    const section = $(`.view[data-view="${r}"]`);
    if (r === "feed") {
      if (!slidesAll().length) loadMore();
      else if (player.slide) { player.slide.scrollIntoView({ block: "start" }); startView(player.slide); }
      else { const s = activeSlide(); if (s) startView(s); }
    } else if (r === "explore") {
      if (!explore.built) buildExplore(section);
      runExplore(true);
    } else if (r === "saved") renderSaved(section);
    else if (r === "stats") renderStats(section);
    else if (r === "settings") renderSettings(section);
    document.title = r === "feed" ? "Scrollback" : `${r === "stats" ? "Progress" : r[0].toUpperCase() + r.slice(1)}, Scrollback`;
  }
  window.addEventListener("hashchange", route);

  async function init() {
    try { state.meta = await api.get("/api/meta"); } catch (e) { state.meta = { eras: [], topics: [] }; }
    await refreshStatus();
    route();
    renderPlayer();
    ttsStatus();
    setInterval(refreshStatus, 30000);
  }
  init();
})();
