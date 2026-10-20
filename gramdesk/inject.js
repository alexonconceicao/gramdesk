// Gramdesk: script injetado no início de cada documento do Instagram.
// 1) Modo fantasma: descarta as requisições que marcam story como visto.
// 2) Índice de mídia: lê as respostas JSON que o próprio site já recebe e
//    guarda as URLs originais (maior resolução) para download.
// Nada sai daqui para fora do Instagram: o resultado só é lido pelo app.
(() => {
  'use strict';
  const KEY = '__GD_KEY__';
  const GHOST = __GD_GHOST__;
  if (Object.prototype.hasOwnProperty.call(window, KEY)) return;

  const APP_ID = '936619743392459';
  const SEEN_RE = /StoriesV\d*Seen|ReelSeen|viewSeenAt|\/stories\/reel\/seen|story_seen|\/media\/seen\//i;
  const API_RE = /graphql|\/api\/v1\//;
  const MAX = 4000;

  // ---------- modo fantasma ----------
  const bodyText = (b) => {
    try {
      if (b == null) return '';
      if (typeof b === 'string') return b;
      if (b instanceof URLSearchParams) return b.toString();
      if (b instanceof FormData) {
        const out = [];
        for (const [k, v] of b.entries()) if (typeof v === 'string') out.push(k + '=' + v);
        return out.join('&');
      }
    } catch (_) { /* corpo ilegível: segue */ }
    return '';
  };
  const isSeen = (url, body) => SEEN_RE.test(String(url || '')) || SEEN_RE.test(bodyText(body));
  const FAKE = '{"status":"ok","data":{}}';

  const fakeXhr = (xhr) => {
    const def = (k, v) => { try { Object.defineProperty(xhr, k, { configurable: true, get: () => v }); } catch (_) {} };
    def('readyState', 4); def('status', 200); def('statusText', 'OK');
    def('responseText', FAKE); def('response', FAKE); def('responseURL', '');
    setTimeout(() => {
      for (const t of ['readystatechange', 'load', 'loadend']) {
        try { xhr.dispatchEvent(new Event(t)); } catch (_) {}
      }
    }, 0);
  };

  // ---------- índice de mídia ----------
  const byPk = new Map(), byCode = new Map(), byImg = new Map(), reels = new Map();
  const put = (m, k, v) => {
    if (!k) return;
    if (m.has(k)) m.delete(k);
    m.set(k, v);
    if (m.size > MAX) m.delete(m.keys().next().value);
  };
  const base = (u) => { try { return new URL(u, location.href).pathname.split('/').pop() || ''; } catch (_) { return ''; } };
  const area = (x) => (x && x.width || 0) * (x && x.height || 0);
  const biggest = (list) => list.reduce((a, b) => (area(b) > area(a) ? b : a));

  const mediaOf = (n) => {
    const cands = n && n.image_versions2 && n.image_versions2.candidates;
    const img = Array.isArray(cands) && cands.length ? biggest(cands) : null;
    const vids = n && n.video_versions;
    if (Array.isArray(vids) && vids.length) {
      const v = biggest(vids);
      if (v && v.url) return { kind: 'video', url: v.url, thumb: img && img.url || '' };
    }
    if (img && img.url) return { kind: 'image', url: img.url, thumb: img.url };
    return null;
  };

  const record = (n) => {
    const raw = n.pk != null ? n.pk : (typeof n.id === 'string' ? n.id.split('_')[0] : n.id);
    const user = n.user || n.owner || {};
    const item = {
      pk: raw != null ? String(raw) : '',
      code: String(n.code || n.shortcode || ''),
      user: String(user.username || ''),
      userPk: String(user.pk || user.id || ''),
      media: [],
    };
    if (Array.isArray(n.carousel_media) && n.carousel_media.length) {
      for (const c of n.carousel_media) { const m = mediaOf(c); if (m) item.media.push(m); }
    } else {
      const m = mediaOf(n); if (m) item.media.push(m);
    }
    if (!item.media.length) return;
    put(byPk, item.pk, item);
    put(byCode, item.code, item);
    for (const m of item.media) { put(byImg, base(m.thumb), item); put(byImg, base(m.url), item); }
  };

  const walk = (o, depth) => {
    if (!o || typeof o !== 'object' || depth > 40) return;
    if (Array.isArray(o)) { for (const x of o) walk(x, depth + 1); return; }
    const isMedia = (o.image_versions2 || o.video_versions || o.carousel_media) && (o.pk != null || o.id != null);
    if (isMedia) record(o);
    if (Array.isArray(o.items) && o.user && o.id != null) {
      put(reels, String(o.id), o.items.map((i) => String(i.pk != null ? i.pk : String(i.id || '').split('_')[0])));
    }
    for (const k in o) {
      if (isMedia && k === 'carousel_media') continue;
      const v = o[k];
      if (v && typeof v === 'object') walk(v, depth + 1);
    }
  };

  const ingest = (t) => {
    if (typeof t !== 'string' || t.length < 32) return;
    if (t.indexOf('image_versions2') === -1 && t.indexOf('video_versions') === -1) return;
    if (t.startsWith('for (;;);')) t = t.slice(9);
    const parse = (s) => { try { walk(JSON.parse(s), 0); return true; } catch (_) { return false; } };
    if (!parse(t)) for (const line of t.split('\n')) if (line.trim()) parse(line);
  };

  const scanInline = () => {
    for (const s of document.querySelectorAll('script[type="application/json"]')) {
      if (s.__gd) continue;
      s.__gd = 1;
      ingest(s.textContent || '');
    }
  };

  // ---------- ganchos de rede ----------
  const origFetch = window.fetch;
  window.fetch = function (input, init) {
    let url = '';
    try { url = typeof input === 'string' ? input : (input && input.url) || String(input); } catch (_) {}
    if (GHOST && isSeen(url, init && init.body)) {
      return Promise.resolve(new Response(FAKE, { status: 200, headers: { 'Content-Type': 'application/json' } }));
    }
    const p = origFetch.apply(this, arguments);
    if (API_RE.test(url)) {
      p.then((r) => { try { r.clone().text().then(ingest, () => {}); } catch (_) {} }, () => {});
    }
    return p;
  };

  const XP = XMLHttpRequest.prototype;
  const xOpen = XP.open, xSend = XP.send;
  const xUrl = new WeakMap();
  XP.open = function (method, url) {
    try { xUrl.set(this, String(url)); } catch (_) {}
    return xOpen.apply(this, arguments);
  };
  XP.send = function (body) {
    const url = xUrl.get(this) || '';
    if (GHOST && isSeen(url, body)) { fakeXhr(this); return undefined; }
    if (API_RE.test(url)) {
      this.addEventListener('load', function () {
        try {
          if (this.responseType === '' || this.responseType === 'text') ingest(this.responseText);
          else if (this.responseType === 'json') walk(this.response, 0);
        } catch (_) {}
      });
    }
    return xSend.apply(this, arguments);
  };

  if (navigator.sendBeacon) {
    const beacon = navigator.sendBeacon.bind(navigator);
    navigator.sendBeacon = function (url, data) {
      if (GHOST && isSeen(url, data)) return true;
      return beacon(url, data);
    };
  }

  // ---------- resolução do item na tela ----------
  const ALPHA = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_';
  const codeToPk = (code) => {
    let n = 0n;
    for (const ch of code.slice(0, 11)) {
      const i = ALPHA.indexOf(ch);
      if (i < 0) return '';
      n = n * 64n + BigInt(i);
    }
    return n.toString();
  };

  const api = async (path) => {
    const r = await origFetch.call(window, path, {
      credentials: 'include',
      headers: { 'X-IG-App-ID': APP_ID, 'X-Requested-With': 'XMLHttpRequest' },
    });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    ingest(await r.text());
  };

  const byPkOrFetch = async (pk) => {
    if (!/^\d{1,25}$/.test(pk)) return null;
    if (!byPk.has(pk)) { try { await api('/api/v1/media/' + pk + '/info/'); } catch (_) {} }
    return byPk.get(pk) || null;
  };
  const byCodeOrFetch = async (code) => {
    if (!/^[A-Za-z0-9_-]{5,64}$/.test(code)) return null;
    return byCode.get(code) || byPkOrFetch(codeToPk(code));
  };

  const visibleArea = (el) => {
    const r = el.getBoundingClientRect();
    const w = Math.max(0, Math.min(r.right, innerWidth) - Math.max(r.left, 0));
    const h = Math.max(0, Math.min(r.bottom, innerHeight) - Math.max(r.top, 0));
    return w * h;
  };
  const pickElement = (x, y) => {
    if (x != null && y != null) {
      for (const el of document.elementsFromPoint(x, y)) if (el.tagName === 'IMG' || el.tagName === 'VIDEO') return el;
    }
    let best = null, bestA = 150 * 150;
    for (const el of document.querySelectorAll('video, img')) {
      const a = visibleArea(el);
      if (a > bestA) { best = el; bestA = a; }
    }
    return best;
  };
  const codeNear = (el) => {
    let node = el;
    for (let i = 0; node && i < 14; i++, node = node.parentElement) {
      const a = node.querySelector && node.querySelector('a[href*="/p/"], a[href*="/reel/"], a[href*="/tv/"]');
      if (a) {
        const m = a.getAttribute('href').match(/\/(?:p|reel|tv)\/([A-Za-z0-9_-]+)/);
        if (m) return m[1];
      }
      if (node.tagName === 'ARTICLE' || (node.getAttribute && node.getAttribute('role') === 'dialog')) break;
    }
    return '';
  };

  const fromElement = async (el) => {
    if (!el) return null;
    const src = el.tagName === 'VIDEO' ? (el.poster || el.currentSrc || el.src) : (el.currentSrc || el.src);
    const hit = byImg.get(base(src));
    if (hit) return hit;
    const code = codeNear(el);
    if (code) { const it = await byCodeOrFetch(code); if (it) return it; }
    if (src && /^https:/.test(src)) {
      return { pk: '', code: '', user: '', userPk: '', media: [{ kind: el.tagName === 'VIDEO' ? 'video' : 'image', url: src }] };
    }
    return null;
  };

  const fromPath = async () => {
    const p = location.pathname;
    let m = p.match(/^\/stories\/(?!highlights\/)[^/]+\/(\d+)/);
    if (m) return byPkOrFetch(m[1]);
    m = p.match(/^\/(?:[^/]+\/)?(?:p|reel|reels|tv)\/([A-Za-z0-9_-]+)/);
    if (m) return byCodeOrFetch(m[1]);
    return null;
  };

  const currentItem = async (x, y) => {
    scanInline();
    if (x != null) return (await fromElement(pickElement(x, y))) || fromPath();
    const onStory = location.pathname.startsWith('/stories/');
    if (onStory) return (await fromPath()) || fromElement(pickElement());
    return (await fromPath()) || fromElement(pickElement());
  };

  const reelItems = async () => {
    scanInline();
    const p = location.pathname;
    let reelId = '';
    let m = p.match(/^\/stories\/highlights\/(\d+)/);
    if (m) reelId = 'highlight:' + m[1];
    if (!reelId) {
      const cur = await currentItem();
      if (cur && cur.userPk) reelId = cur.userPk;
    }
    if (!reelId && (m = p.match(/^\/stories\/([A-Za-z0-9._]{1,30})\//))) {
      const r = await origFetch.call(window, '/api/v1/users/web_profile_info/?username=' + encodeURIComponent(m[1]), {
        credentials: 'include', headers: { 'X-IG-App-ID': APP_ID },
      });
      if (r.ok) { const j = await r.json(); reelId = String(j && j.data && j.data.user && j.data.user.id || ''); }
    }
    if (!reelId) return [];
    if (!reels.has(reelId)) await api('/api/v1/feed/reels_media/?reel_ids=' + encodeURIComponent(reelId));
    return (reels.get(reelId) || []).map((pk) => byPk.get(pk)).filter(Boolean);
  };

  const toFiles = (items, tag) => {
    const out = [];
    for (const it of items) {
      it.media.forEach((m, i) => {
        const id = it.code || it.pk || 'midia';
        const suffix = it.media.length > 1 ? '_' + (i + 1) : '';
        out.push({ url: m.url, thumb: m.thumb || '', name: (it.user || 'instagram') + '_' + tag + '_' + id + suffix });
      });
    }
    return out;
  };

  const run = async (mode, x, y) => {
    if (mode === 'stories') return toFiles(await reelItems(), 'story');
    const it = await currentItem(x, y);
    if (!it) return [];
    return toFiles([it], location.pathname.startsWith('/stories/') ? 'story' : 'post');
  };

  // ---------- botões de download sobre cada mídia ----------
  // Uma camada própria (shadow DOM fechado) desenha um botão em cima de cada
  // foto/vídeo visível, sem mexer na estrutura do Instagram. O clique vai para
  // o app por um canal de console assinado com um token que o site não conhece.
  const TOKEN = '__GD_TOKEN__';
  const BUTTONS = __GD_BUTTONS__;
  const rawLog = console.log.bind(console);
  const send = (obj) => { try { rawLog('\u0000GD:' + TOKEN + ':' + JSON.stringify(obj)); } catch (_) {} };

  const srcOf = (el) => (el.tagName === 'VIDEO' ? (el.poster || el.currentSrc || el.src || '') : (el.currentSrc || el.src || ''));
  const indexIn = (item, el) => {
    const b = base(srcOf(el));
    return b ? item.media.findIndex((m) => base(m.url) === b || base(m.thumb) === b) : -1;
  };

  const resolveFor = async (el) => {
    scanInline();
    let item = byImg.get(base(srcOf(el))) || null;
    if (!item) item = await fromElement(el);
    if ((!item || !item.pk) && location.pathname.startsWith('/stories/')) item = (await fromPath()) || item;
    if (!item) return null;
    let idx = indexIn(item, el);
    if (idx < 0 && el.tagName === 'VIDEO') {
      const vids = item.media.map((m, i) => [m, i]).filter(([m]) => m.kind === 'video');
      if (vids.length === 1) idx = vids[0][1];
    }
    if (idx < 0 && item.media.length === 1) idx = 0;
    return { item, idx };
  };

  const ICON_DL = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 4v11"/><path d="M7 10l5 5 5-5"/><path d="M5 20h14"/></svg>';
  const ICON_OK = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12l5 5 9-10"/></svg>';
  const ICON_ALL = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="13" height="13" rx="2.5"/><path d="M8 21h11a2 2 0 0 0 2-2V8"/></svg>';

  let host = null, layer = null, raf = 0;
  const pool = [];

  const ensureHost = () => {
    if (host && host.isConnected) return true;
    if (!document.documentElement) return false;
    host = document.createElement('gd-overlay');
    host.style.cssText = 'position:fixed;inset:0;pointer-events:none;z-index:2147483646;display:block;';
    const root = host.attachShadow({ mode: 'closed' });
    root.innerHTML = '<style>' +
      '.b{position:fixed;display:none;align-items:center;justify-content:center;gap:6px;height:32px;padding:0 12px 0 9px;' +
      'box-sizing:border-box;border:1.5px solid rgba(255,255,255,.55);border-radius:16px;' +
      'background:linear-gradient(45deg,#fa7e1e,#d62976,#962fbf);color:#fff;font:700 12.5px system-ui,sans-serif;letter-spacing:.2px;' +
      'cursor:pointer;pointer-events:auto;opacity:.95;text-shadow:0 1px 2px rgba(0,0,0,.35);' +
      'box-shadow:0 3px 14px rgba(0,0,0,.45);transition:opacity .15s,transform .15s,filter .15s;}' +
      '.b:hover{opacity:1;transform:scale(1.06);filter:brightness(1.12);}' +
      '.b.all{background:rgba(12,12,16,.78);backdrop-filter:blur(8px);-webkit-backdrop-filter:blur(8px);}' +
      '.b.all:hover{background:linear-gradient(45deg,#fa7e1e,#d62976,#962fbf);}' +
      '.b svg{width:16px;height:16px;flex:none}' +
      '.b[data-s=busy]{opacity:1;animation:p 1s ease-in-out infinite}' +
      '.b[data-s=done]{opacity:1;background:#16a34a}' +
      '.b[data-s=fail]{opacity:1;background:#e11d48}' +
      '@keyframes p{50%{opacity:.55}}' +
      '</style><div id="l"></div>';
    layer = root.getElementById('l');
    document.documentElement.appendChild(host);
    return true;
  };

  const makeButton = () => {
    const b = document.createElement('button');
    b.className = 'b';
    b.addEventListener('click', (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      if (b._el) download(b._el, b._all, b);
    }, true);
    for (const t of ['mousedown', 'mouseup', 'pointerdown', 'pointerup', 'dblclick']) {
      b.addEventListener(t, (ev) => ev.stopPropagation(), true);
    }
    layer.appendChild(b);
    pool.push(b);
    return b;
  };

  const flash = (b, state, ms) => {
    b.dataset.s = state;
    b.innerHTML = state === 'done' ? ICON_OK + '<span>Salvo</span>' : state === 'fail' ? '<span>Não achei</span>' : state === 'busy' ? ICON_DL + '<span>Baixando</span>' : label(b);
    if (ms) setTimeout(() => { if (b.dataset.s === state) { b.dataset.s = ''; paint(b); } }, ms);
  };
  const label = (b) => (b._all ? ICON_ALL + '<span>' + b._label + '</span>' : ICON_DL + '<span>' + (b._label || 'Baixar') + '</span>');
  const paint = (b) => {
    b.classList.toggle('all', !!b._all);
    b.innerHTML = label(b);
    b.title = b._all ? 'Baixar todas as mídias deste post' : 'Baixar esta mídia';
  };

  const download = async (el, all, b) => {
    if (b.dataset.s === 'busy') return;
    flash(b, 'busy');
    try {
      const r = await resolveFor(el);
      let files = [];
      let partial = false;
      if (r) {
        const tag = location.pathname.startsWith('/stories/') ? 'story' : 'post';
        files = toFiles([r.item], tag);
        if (!all && r.idx >= 0) files = files.filter((_f, i) => i === r.idx);
        else if (!all) partial = r.item.media.length > 1;
      }
      send({ type: 'download', files, partial });
      flash(b, files.length ? 'done' : 'fail', 1600);
    } catch (_) {
      flash(b, 'fail', 1600);
    }
  };

  const visible = (el) => {
    const r = el.getBoundingClientRect();
    let L = r.left, T = r.top, R = r.right, B = r.bottom;
    let n = el.parentElement;
    for (let i = 0; n && i < 14 && n !== document.body; i++, n = n.parentElement) {
      const cs = getComputedStyle(n);
      if (cs.overflowX !== 'visible' || cs.overflowY !== 'visible') {
        const q = n.getBoundingClientRect();
        L = Math.max(L, q.left); T = Math.max(T, q.top); R = Math.min(R, q.right); B = Math.min(B, q.bottom);
      }
    }
    L = Math.max(L, 0); T = Math.max(T, 0); R = Math.min(R, innerWidth); B = Math.min(B, innerHeight);
    return { L, T, R, B, w: R - L, h: B - T, rw: r.width, rh: r.height };
  };

  // Algo do site (modal, menu, story aberto) cobre a mídia nesse ponto?
  const covered = (el, x, y) => {
    const hit = document.elementFromPoint(x, y);
    if (!hit) return true;
    let anc = el;
    for (let i = 0; anc && i < 7; i++, anc = anc.parentElement) {
      if (anc === hit || anc.contains(hit)) return false;
    }
    return true;
  };

  const PROFILE_RE = /profile picture|foto do perfil|foto de perfil|avatar/i;
  const candidates = () => {
    const out = [];
    const taken = [];
    const els = [...document.querySelectorAll('video'), ...document.querySelectorAll('img')];
    for (const el of els) {
      if (out.length >= 24) break;
      if (el.tagName === 'IMG' && PROFILE_RE.test(el.alt || '')) continue;
      const r = el.getBoundingClientRect();
      if (r.width < 180 || r.height < 180 || r.bottom < 0 || r.top > innerHeight) continue;
      const c = visible(el);
      if (c.w < 140 || c.h < 120) continue;
      if (covered(el, c.L + 27, c.T + 27) && covered(el, (c.L + c.R) / 2, (c.T + c.B) / 2)) continue;
      // Imagem de capa sob o próprio vídeo: um botão só.
      if (taken.some((t) => Math.abs(t.L - c.L) < 8 && Math.abs(t.T - c.T) < 8 && Math.abs(t.w - c.w) < 16)) continue;
      taken.push(c);
      out.push({ el, c });
    }
    return out;
  };

  const place = (i, el, all, label, css) => {
    const b = pool[i] || makeButton();
    if (b._el !== el || b._all !== all || b._label !== label) {
      b._el = el; b._all = all; b._label = label;
      if (b.dataset.s !== 'busy') { b.dataset.s = ''; paint(b); }
    }
    b.style.left = css.left != null ? css.left + 'px' : '';
    b.style.right = css.right != null ? css.right + 'px' : '';
    b.style.top = css.top + 'px';
    b.style.display = 'flex';
    return i + 1;
  };

  const render = () => {
    raf = 0;
    if (!BUTTONS || document.hidden || !ensureHost()) return;
    const story = location.pathname.startsWith('/stories/');
    let used = 0;
    host.style.display = 'none'; // fora do teste de clique enquanto mede
    const list = candidates();
    host.style.display = 'block';
    for (const { el, c } of list) {
      const it = byImg.get(base(srcOf(el)));
      const n = it ? it.media.length : 0;
      if (story) {
        used = place(used, el, false, 'Baixar story', { right: innerWidth - c.R + 12, top: c.T + 72 });
      } else {
        used = place(used, el, false, n > 1 ? 'Baixar esta' : 'Baixar', { left: c.L + 10, top: c.T + 10 });
        if (n > 1) used = place(used, el, true, 'Todas (' + n + ')', { left: c.L + 10, top: c.T + 48 });
      }
    }
    for (let i = used; i < pool.length; i++) pool[i].style.display = 'none';
  };
  const schedule = () => { if (BUTTONS && !raf) raf = requestAnimationFrame(render); };

  if (BUTTONS) {
    addEventListener('scroll', schedule, { capture: true, passive: true });
    addEventListener('resize', schedule, { passive: true });
    addEventListener('click', () => setTimeout(schedule, 380), { capture: true, passive: true });
    addEventListener('keyup', () => setTimeout(schedule, 380), { capture: true, passive: true });
    document.addEventListener('visibilitychange', schedule);
    setInterval(() => { try { scanInline(); } catch (_) {} schedule(); }, 700);
  }

  // ---------- pesquisa de perfil ----------
  // Coleta somente leitura, no ritmo de uma pessoa navegando, usando a sessão
  // da própria conta. Cada lote vai para o app pelo canal assinado.
  const researchJobs = new Map();
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const humanPause = () => sleep(1300 + Math.random() * 1700);
  const clip = (s, n) => (typeof s === 'string' ? s.slice(0, n) : '');
  const num = (v) => (typeof v === 'number' && isFinite(v) ? v : null);

  const apiJson = async (path) => {
    const r = await origFetch.call(window, path, {
      credentials: 'include',
      headers: { 'X-IG-App-ID': APP_ID, 'X-Requested-With': 'XMLHttpRequest' },
    });
    if (r.status === 429) throw new Error('rate-limit');
    if (r.status === 401 || r.status === 403) throw new Error('sem-acesso');
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const text = await r.text();
    ingest(text);
    return JSON.parse(text.startsWith('for (;;);') ? text.slice(9) : text);
  };

  const tagsOf = (text, re) => [...new Set((text.match(re) || []).map((t) => t.toLowerCase()))].slice(0, 40);
  const HASH_RE = /#[\p{L}\p{N}_]+/gu;
  const MENTION_RE = /@[A-Za-z0-9._]{1,30}/g;

  const mediaInfo = (n) => {
    const m = mediaOf(n) || {};
    const cands = n && n.image_versions2 && n.image_versions2.candidates;
    const img = Array.isArray(cands) && cands.length ? biggest(cands) : null;
    return {
      kind: m.kind || (n.is_video ? 'video' : 'image'),
      url: m.url || n.video_url || n.display_url || '',
      thumb: m.thumb || n.display_url || '',
      width: num((img && img.width) || (n.dimensions && n.dimensions.width) || n.original_width),
      height: num((img && img.height) || (n.dimensions && n.dimensions.height) || n.original_height),
      duration: num(n.video_duration),
      alt: clip(n.accessibility_caption || '', 600),
    };
  };

  // Aceita item da API v1 (image_versions2) ou nó GraphQL (display_url).
  const normPost = (n) => {
    const caption = clip(
      (n.caption && n.caption.text) ||
      (n.edge_media_to_caption && n.edge_media_to_caption.edges && n.edge_media_to_caption.edges[0] &&
        n.edge_media_to_caption.edges[0].node.text) || '',
      6000,
    );
    let media = [];
    if (Array.isArray(n.carousel_media) && n.carousel_media.length) media = n.carousel_media.map(mediaInfo);
    else if (n.edge_sidecar_to_children && n.edge_sidecar_to_children.edges) {
      media = n.edge_sidecar_to_children.edges.map((e) => mediaInfo(e.node));
    } else media = [mediaInfo(n)];
    const code = String(n.code || n.shortcode || '');
    const type = n.product_type === 'clips' ? 'reel'
      : (media.length > 1 ? 'carousel' : (media[0] && media[0].kind === 'video' ? 'video' : 'image'));
    const loc = n.location || null;
    const music = n.clips_metadata && n.clips_metadata.music_info && n.clips_metadata.music_info.music_asset_info;
    return {
      pk: String(n.pk || (typeof n.id === 'string' ? n.id.split('_')[0] : n.id) || ''),
      code,
      url: code ? 'https://www.instagram.com/p/' + code + '/' : '',
      type,
      taken_at: num(n.taken_at || n.taken_at_timestamp),
      caption,
      hashtags: tagsOf(caption, HASH_RE),
      mentions: tagsOf(caption, MENTION_RE),
      likes: num(n.like_count != null ? n.like_count : n.edge_liked_by && n.edge_liked_by.count),
      comments: num(n.comment_count != null ? n.comment_count : n.edge_media_to_comment && n.edge_media_to_comment.count),
      views: num(n.play_count || n.view_count || n.video_view_count),
      location: loc ? { name: clip(loc.name || '', 200), city: clip(loc.city || '', 120), lat: num(loc.lat), lng: num(loc.lng) } : null,
      tagged: ((n.usertags && n.usertags.in) || []).map((t) => t.user && t.user.username).filter(Boolean).slice(0, 30),
      music: music ? clip((music.title || '') + (music.display_artist ? ' · ' + music.display_artist : ''), 200) : '',
      pinned: !!(n.timeline_pinned_user_ids && n.timeline_pinned_user_ids.length),
      media,
    };
  };

  const normProfile = (u) => ({
    id: String(u.id || u.pk || ''),
    username: String(u.username || ''),
    full_name: clip(u.full_name || '', 200),
    biography: clip(u.biography || '', 3000),
    bio_links: (u.bio_links || []).map((l) => ({ title: clip(l.title || '', 200), url: clip(l.url || l.lynx_url || '', 1000) })).slice(0, 20),
    external_url: clip(u.external_url || '', 1000),
    category: clip(u.category_name || u.business_category_name || u.category || '', 200),
    is_business: !!(u.is_business_account || u.is_professional_account),
    is_verified: !!u.is_verified,
    is_private: !!u.is_private,
    followers: num(u.edge_followed_by ? u.edge_followed_by.count : u.follower_count),
    following: num(u.edge_follow ? u.edge_follow.count : u.following_count),
    posts_total: num(u.edge_owner_to_timeline_media ? u.edge_owner_to_timeline_media.count : u.media_count),
    public_email: clip(u.business_email || u.public_email || '', 200),
    public_phone: clip(u.business_phone_number || u.public_phone_number || '', 60),
    address: clip(u.business_address_json || '', 1000),
    profile_pic: clip((u.hd_profile_pic_url_info && u.hd_profile_pic_url_info.url) || u.profile_pic_url_hd || u.profile_pic_url || '', 2000),
  });

  const research = async (rid, username, opts) => {
    const job = { cancel: false };
    researchJobs.set(rid, job);
    const emit = (phase, data) => send({ type: 'research', rid, phase, data });
    const check = () => { if (job.cancel) throw new Error('cancelado'); };
    try {
      emit('progress', { step: 'perfil' });
      const info = await apiJson('/api/v1/users/web_profile_info/?username=' + encodeURIComponent(username));
      const user = info && info.data && info.data.user;
      if (!user) throw new Error('perfil não encontrado');
      const profile = normProfile(user);
      emit('profile', profile);
      if (profile.is_private && !user.followed_by_viewer) {
        emit('progress', { step: 'privado' });
      }

      // Posts: API v1 paginada; se falhar, usa os 12 que vêm no perfil.
      const max = Math.max(1, Math.min(300, opts.posts | 0 || 60));
      let got = 0, nextMax = '', page = 0, viaGraph = false;
      const collected = [];
      while (got < max) {
        check();
        await humanPause();
        let items = [], more = false;
        try {
          const q = '/api/v1/feed/user/' + profile.id + '/?count=12' + (nextMax ? '&max_id=' + encodeURIComponent(nextMax) : '');
          const j = await apiJson(q);
          items = j.items || [];
          more = !!j.more_available;
          nextMax = j.next_max_id || '';
        } catch (e) {
          if (String(e.message) === 'rate-limit') throw e;
          if (page === 0) {
            const edges = (user.edge_owner_to_timeline_media && user.edge_owner_to_timeline_media.edges) || [];
            items = edges.map((e) => e.node);
            viaGraph = true;
            more = false;
          } else throw e;
        }
        const posts = items.slice(0, max - got).map(normPost);
        collected.push(...posts);
        got += posts.length;
        page += 1;
        emit('posts', { page, posts, via: viaGraph ? 'graphql' : 'v1' });
        emit('progress', { step: 'posts', got, max });
        if (!more || !nextMax || !posts.length) break;
      }

      if (opts.comments) {
        const top = collected.filter((p) => p.comments > 0).sort((a, b) => b.comments - a.comments)
          .slice(0, Math.max(0, Math.min(25, opts.commentPosts | 0 || 15))).map((p) => p.pk);
        for (const pk of top) {
          check();
          if (!/^\d{1,25}$/.test(pk)) continue;
          await humanPause();
          try {
            const j = await apiJson('/api/v1/media/' + pk + '/comments/?can_support_threading=true&permalink_enabled=false');
            const comments = (j.comments || []).slice(0, 20).map((c) => ({
              user: String((c.user && c.user.username) || ''),
              text: clip(c.text || '', 1500),
              likes: num(c.comment_like_count),
              created_at: num(c.created_at),
            }));
            emit('comments', { pk, comments });
          } catch (e) {
            if (String(e.message) === 'rate-limit') throw e;
          }
          emit('progress', { step: 'comentarios' });
        }
      }

      if (opts.highlights && profile.id) {
        check();
        await humanPause();
        try {
          const tray = await apiJson('/api/v1/highlights/' + profile.id + '/highlights_tray/');
          const list = (tray.tray || []).slice(0, 30).map((h) => ({
            id: String(h.id || ''),
            title: clip(h.title || '', 200),
            cover: clip((h.cover_media && h.cover_media.cropped_image_version && h.cover_media.cropped_image_version.url) || '', 2000),
          }));
          for (let i = 0; i < list.length; i += 5) {
            check();
            await humanPause();
            const ids = list.slice(i, i + 5).map((h) => 'reel_ids=' + encodeURIComponent(h.id)).join('&');
            const j = await apiJson('/api/v1/feed/reels_media/?' + ids);
            const reelsById = j.reels || {};
            for (const h of list.slice(i, i + 5)) {
              const r = reelsById[h.id];
              h.items = ((r && r.items) || []).slice(0, 60).map((it) => ({
                pk: String(it.pk || ''),
                taken_at: num(it.taken_at),
                media: mediaInfo(it),
                links: ((it.story_link_stickers || []).map((s) => s.story_link && s.story_link.url)).filter(Boolean).slice(0, 5),
              }));
            }
          }
          emit('highlights', list);
        } catch (e) {
          if (String(e.message) === 'rate-limit') throw e;
          emit('progress', { step: 'destaques-indisponiveis' });
        }
      }
      emit('done', { posts: got });
    } catch (e) {
      emit('error', { message: String((e && e.message) || e) });
    } finally {
      researchJobs.delete(rid);
    }
  };

  const results = new Map();
  const bridge = Object.freeze({
    start(id, mode, x, y) {
      if (!/^[a-f0-9]{16}$/.test(id) || results.size > 32) return false;
      results.set(id, null);
      run(mode, x, y).then(
        (files) => results.set(id, { ok: true, files }),
        (e) => results.set(id, { ok: false, error: String((e && e.message) || e) }),
      );
      return true;
    },
    research(rid, username, opts) {
      if (!/^[a-f0-9]{16}$/.test(rid) || !/^[A-Za-z0-9._]{1,30}$/.test(username) || researchJobs.size > 2) return false;
      research(rid, username, opts && typeof opts === 'object' ? opts : {});
      return true;
    },
    cancelResearch(rid) {
      const j = researchJobs.get(rid);
      if (j) j.cancel = true;
      return !!j;
    },
    take(id) {
      const r = results.get(id);
      if (!r) return null;
      results.delete(id);
      return JSON.stringify(r);
    },
  });
  Object.defineProperty(window, KEY, { value: bridge, enumerable: false, configurable: false, writable: false });
})();
