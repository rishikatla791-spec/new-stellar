/* Stellar landing page: scrolling, reveals, card light and the demo.
 *
 * Everything that moves with the scroll is computed in one place
 * (updateScene), once per frame at most, and written as CSS custom
 * properties; the stylesheet turns those into transforms. Nothing here
 * changes layout while scrolling.
 */
(() => {
  'use strict';

  const root = document.documentElement;
  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const finePointer = window.matchMedia('(hover: hover) and (pointer: fine)').matches;
  const silk = window.stellarSilk;

  const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
  const smoothstep = (a, b, x) => {
    const t = clamp((x - a) / (b - a), 0, 1);
    return t * t * (3 - 2 * t);
  };
  const easeOutCubic = (t) => 1 - Math.pow(1 - t, 3);
  const easeInOutCubic = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);
  const $ = (sel, el = document) => el.querySelector(sel);
  const $$ = (sel, el = document) => Array.from(el.querySelectorAll(sel));

  let paused = reduced;

  // ------------------------------------------------------------- intro
  // Wait for the font (briefly) so the letters gather in their real shapes.
  const fontsReady = document.fonts && document.fonts.ready
    ? Promise.race([document.fonts.ready, new Promise((r) => setTimeout(r, 900))])
    : Promise.resolve();
  fontsReady.then(() => requestAnimationFrame(() => {
    root.classList.add('is-loaded');
    placeIndicator();
  }));

  // ------------------------------------------------------ smooth scroll
  // Wheel input eases toward its target instead of jumping in steps.
  // Keyboard, scrollbar, touch and find-in-page stay native; the target is
  // re-synced whenever the page scrolls by any other means.
  const smooth = (() => {
    const enabled = finePointer && !reduced;
    const TAU = 0.1;                       // seconds to cover ~63% of the gap
    let target = window.scrollY;
    let current = target;
    let running = false;
    let last = 0;
    let glide = null;                      // a timed glide to an anchor

    const maxScroll = () => root.scrollHeight - window.innerHeight;

    function scrollsItself(el, dy) {
      for (let n = el; n && n !== document.body && n !== root; n = n.parentElement) {
        if (n.nodeType !== 1) continue;
        const style = getComputedStyle(n);
        if (!/(auto|scroll)/.test(style.overflowY)) continue;
        if (dy > 0 ? n.scrollTop + n.clientHeight < n.scrollHeight - 1 : n.scrollTop > 0) return true;
      }
      return false;
    }

    function frame(now) {
      const dt = last ? Math.min((now - last) / 1000, 0.05) : 1 / 60;
      last = now;
      if (glide) {
        if (!glide.start) glide.start = now;
        const t = clamp((now - glide.start) / glide.duration, 0, 1);
        current = glide.from + (glide.to - glide.from) * easeInOutCubic(t);
        target = current;
        if (t >= 1) glide = null;
      } else {
        current = target + (current - target) * Math.exp(-dt / TAU);
        if (Math.abs(target - current) < 0.4) current = target;
      }
      window.scrollTo(0, current);
      if (glide || current !== target) requestAnimationFrame(frame);
      else running = false;
    }

    function run() {
      if (running) return;
      running = true;
      last = 0;
      requestAnimationFrame(frame);
    }

    function stop() {
      glide = null;
      target = current = window.scrollY;
    }

    if (enabled) {
      root.classList.add('smooth');
      window.addEventListener('wheel', (e) => {
        if (e.ctrlKey || e.defaultPrevented) return;               // pinch zoom
        if (Math.abs(e.deltaX) > Math.abs(e.deltaY)) return;      // sideways
        if (root.classList.contains('menu-open')) return;
        if (scrollsItself(e.target, e.deltaY)) return;
        e.preventDefault();
        let d = e.deltaY;
        if (e.deltaMode === 1) d *= 16;
        else if (e.deltaMode === 2) d *= window.innerHeight;
        if (!running) current = target = window.scrollY;
        glide = null;
        target = clamp(target + d, 0, maxScroll());
        run();
      }, { passive: false });
      window.addEventListener('scroll', () => {
        if (!running) target = current = window.scrollY;
      }, { passive: true });
      window.addEventListener('keydown', (e) => {
        if (['ArrowUp', 'ArrowDown', 'PageUp', 'PageDown', 'Home', 'End', ' '].includes(e.key)) stop();
      });
      window.addEventListener('pointerdown', stop, { passive: true });
    }

    return {
      to(y) {
        const dest = clamp(y, 0, maxScroll());
        if (!enabled) {
          window.scrollTo({ top: dest, behavior: reduced ? 'auto' : 'smooth' });
          return;
        }
        const from = window.scrollY;
        const distance = Math.abs(dest - from);
        current = from;
        glide = { from, to: dest, start: 0, duration: clamp(600 + distance * 0.32, 800, 1700) };
        run();
      },
    };
  })();

  // In-page links glide, then hand focus to where they land.
  document.addEventListener('click', (e) => {
    const link = e.target.closest('a[href^="#"]');
    if (!link || e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey) return;
    const id = link.getAttribute('href').slice(1);
    const el = id ? document.getElementById(id) : null;
    if (!el) return;
    e.preventDefault();
    closeMenu();
    const y = id === 'home' ? 0 : el.getBoundingClientRect().top + window.scrollY - navOffset();
    smooth.to(y);
    if (history.replaceState) history.replaceState(null, '', id === 'home' ? location.pathname : '#' + id);
    if (!el.hasAttribute('tabindex')) el.setAttribute('tabindex', '-1');
    el.focus({ preventScroll: true });
  });

  function navOffset() {
    return window.innerWidth <= 860 ? 40 : 0;
  }

  // -------------------------------------------------------- the scene
  const hero = $('.hero');
  const sky = $('.sky');
  const closing = $('[data-closing]');
  const flowRail = $('[data-flow]');
  const flowTrack = flowRail && $('.flow__track', flowRail);
  const flowSteps = flowRail ? $$('.step', flowRail) : [];
  const stage = $('[data-tilt-in]');
  const app = stage && $('.app', stage);
  const nav = $('[data-nav]');

  let vh = window.innerHeight;
  let queued = false;

  function queueScene() {
    if (!queued) {
      queued = true;
      requestAnimationFrame(updateScene);
    }
  }

  function updateScene() {
    queued = false;
    const y = window.scrollY;

    // Hero: its content lifts, shrinks a little and fades as you leave.
    const heroP = clamp(y / (hero.offsetHeight * 0.8), 0, 1);
    hero.style.setProperty('--hero', heroP.toFixed(4));

    // The ribbon: owned by the hero, then gone while the content reads,
    // then back under the closing words.
    const s = y / vh;
    const heroI = 1 - smoothstep(0.15, 1.2, s);
    const heroBase = 0.37 + s * 0.5;
    const cr = closing.getBoundingClientRect();
    const closeI = smoothstep(1.0, 0.3, cr.top / vh);
    const closeBase = 1 - (cr.top + cr.height * 0.74) / vh;
    const useClose = closeI > heroI;
    const intensity = Math.max(heroI, closeI);
    if (silk && silk.ready) silk.set({ base: useClose ? closeBase : heroBase, intensity });
    sky.style.setProperty('--sky', intensity.toFixed(3));

    // How it works: the line fills left to right as the section arrives.
    if (flowRail) {
      const r = flowRail.getBoundingClientRect();
      const vertical = window.innerWidth <= 860;
      const p = vertical
        ? clamp((vh * 0.72 - r.top) / (r.height + vh * 0.05), 0, 1)
        : clamp((vh * 0.82 - r.top) / (vh * 0.48), 0, 1);
      flowRail.style.setProperty('--flow', p.toFixed(4));
      const tr = flowTrack.getBoundingClientRect();
      for (const step of flowSteps) {
        const dot = step.firstElementChild.getBoundingClientRect();
        const at = vertical
          ? (dot.top + dot.height / 2 - tr.top) / Math.max(tr.height, 1)
          : (dot.left + dot.width / 2 - tr.left) / Math.max(tr.width, 1);
        step.classList.toggle('is-lit', p >= at - 0.015);
      }
    }

    // The workspace window swings up into place.
    if (app) {
      const r = stage.getBoundingClientRect();
      const t = reduced ? 1 : easeOutCubic(clamp((vh - r.top) / (vh * 0.78), 0, 1));
      app.style.setProperty('--t', t.toFixed(4));
    }

    nav.classList.toggle('is-scrolled', y > 30);
    spy();
    trackVelocity(y);
  }

  window.addEventListener('scroll', queueScene, { passive: true });
  window.addEventListener('resize', () => {
    vh = window.innerHeight;
    placeIndicator();
    queueScene();
  });

  // ------------------------------------------------------------- nav
  const navLinks = $$('[data-spy]');
  const indicator = $('.nav__indicator');
  const linksBox = $('.nav__links');
  const sections = $$('[data-section]');
  let active = 'home';
  let hovering = null;

  function placeIndicator(link) {
    const target = link || hovering || navLinks.find((a) => a.dataset.spy === active);
    if (!target || !indicator) return;
    const lr = target.getBoundingClientRect();
    const br = linksBox.getBoundingClientRect();
    indicator.style.setProperty('--ix', (lr.left - br.left).toFixed(1) + 'px');
    indicator.style.setProperty('--iw', lr.width.toFixed(1) + 'px');
  }

  function spy() {
    const line = vh * 0.42;
    let current = 'home';
    for (const sec of sections) {
      const r = sec.getBoundingClientRect();
      if (r.top <= line && r.bottom > line) { current = sec.dataset.section; break; }
      if (r.top <= line) current = sec.dataset.section;
    }
    if (current === active) return;
    active = current;
    for (const a of navLinks) {
      if (a.dataset.spy === active) a.setAttribute('aria-current', 'true');
      else a.removeAttribute('aria-current');
    }
    placeIndicator();
  }

  for (const a of navLinks) {
    a.addEventListener('pointerenter', () => { hovering = a; placeIndicator(a); });
    a.addEventListener('pointerleave', () => { hovering = null; placeIndicator(); });
  }

  // Phone menu.
  const menuButton = $('[data-menu]');
  function closeMenu() {
    if (!root.classList.contains('menu-open')) return;
    root.classList.remove('menu-open');
    menuButton.setAttribute('aria-expanded', 'false');
    menuButton.setAttribute('aria-label', 'Open menu');
    document.body.style.overflow = '';
  }
  menuButton.addEventListener('click', () => {
    const open = !root.classList.contains('menu-open');
    if (!open) { closeMenu(); return; }
    root.classList.add('menu-open');
    menuButton.setAttribute('aria-expanded', 'true');
    menuButton.setAttribute('aria-label', 'Close menu');
    document.body.style.overflow = 'hidden';
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && root.classList.contains('menu-open')) { closeMenu(); menuButton.focus(); }
  });

  // ----------------------------------------------------------- reveal
  // Elements that arrive together are staggered by their position.
  const revealer = new IntersectionObserver((entries) => {
    const arriving = entries.filter((e) => e.isIntersecting).map((e) => e.target);
    arriving.sort((a, b) => {
      const ra = a.getBoundingClientRect();
      const rb = b.getBoundingClientRect();
      return (ra.top - rb.top) || (ra.left - rb.left);
    });
    arriving.forEach((el, i) => {
      el.style.setProperty('--delay', Math.min(i * 0.085, 0.6).toFixed(3) + 's');
      el.classList.add('is-in');
      revealer.unobserve(el);
      // Once in, drop the delay so hover and resize respond at once.
      setTimeout(() => el.style.removeProperty('--delay'), 2200);
    });
  }, { rootMargin: '0px 0px -10% 0px', threshold: 0.12 });
  if (!reduced) $$('.reveal').forEach((el) => revealer.observe(el));

  // ------------------------------------------------------------ cards
  // Each card lights in its own colour (--c, set in the markup): a
  // spotlight and border glow follow the pointer, and the card tilts a few
  // degrees toward it, eased every frame so it never snaps.
  const cards = $$('[data-card]');

  function makeTilt(card) {
    const s = { rx: 0, ry: 0, lift: 0, trx: 0, try: 0, tlift: 0, raf: 0, last: 0 };
    const step = (now) => {
      const dt = s.last ? Math.min((now - s.last) / 1000, 0.05) : 1 / 60;
      s.last = now;
      const k = 1 - Math.exp(-dt / 0.14);
      s.rx += (s.trx - s.rx) * k;
      s.ry += (s.try - s.ry) * k;
      s.lift += (s.tlift - s.lift) * k;
      card.style.setProperty('--rx', s.rx.toFixed(3) + 'deg');
      card.style.setProperty('--ry', s.ry.toFixed(3) + 'deg');
      card.style.setProperty('--lift', s.lift.toFixed(2) + 'px');
      const moving = Math.abs(s.trx - s.rx) + Math.abs(s.try - s.ry) + Math.abs(s.tlift - s.lift) > 0.01;
      s.raf = moving ? requestAnimationFrame(step) : 0;
      if (!moving) s.last = 0;
    };
    return {
      aim(rx, ry, lift) {
        s.trx = rx; s.try = ry; s.tlift = lift;
        if (!s.raf) s.raf = requestAnimationFrame(step);
      },
    };
  }

  if (finePointer) {
    for (const card of cards) {
      const tilt = reduced ? null : makeTilt(card);
      card.addEventListener('pointerenter', () => {
        card.classList.add('is-hot');
      });
      card.addEventListener('pointermove', (e) => {
        const r = card.getBoundingClientRect();
        const x = e.clientX - r.left;
        const y = e.clientY - r.top;
        card.style.setProperty('--mx', x.toFixed(1) + 'px');
        card.style.setProperty('--my', y.toFixed(1) + 'px');
        if (tilt && !paused) {
          const max = clamp(1500 / r.width, 1.4, 5);
          tilt.aim(-((y / r.height) - 0.5) * max, ((x / r.width) - 0.5) * max, -6);
        }
      });
      card.addEventListener('pointerleave', () => {
        card.classList.remove('is-hot');
        if (tilt) tilt.aim(0, 0, 0);
      });
    }
  } else {
    // Touch screens have no hover: a card lights up while it crosses the
    // middle of the screen instead.
    const middle = new IntersectionObserver((entries) => {
      for (const e of entries) {
        e.target.classList.toggle('is-hot', e.isIntersecting);
        if (e.isIntersecting) {
          e.target.style.setProperty('--mx', '50%');
          e.target.style.setProperty('--my', '0%');
        }
      }
    }, { rootMargin: '-42% 0px -42% 0px' });
    cards.forEach((c) => middle.observe(c));
  }

  // ------------------------------------------------------- chess card
  // After the pawn on e7 promotes, the queen, knight and king show every
  // legal move. They are worked out from the position here rather than
  // drawn by hand: sliding for the queen (stopping at a friendly piece),
  // jumps for the knight, one step for the king. With no black pieces on
  // the board there are no captures or checks to consider. Squares count
  // from the top left: x is the file (a = 0), y the rank (8 = 0).
  const chessBoard = $('[data-chess]');
  if (chessBoard) {
    const pieces = [
      { kind: 'queen', x: 4, y: 0, c: '250 204 21' },    // e8, just promoted
      { kind: 'knight', x: 2, y: 5, c: '45 212 191' },   // c3
      { kind: 'king', x: 6, y: 6, c: '226 232 240' },    // g2
    ];
    const ROUND = [[1, 0], [1, 1], [0, 1], [-1, 1], [-1, 0], [-1, -1], [0, -1], [1, -1]];
    const JUMPS = [[1, 2], [2, 1], [2, -1], [1, -2], [-1, -2], [-2, -1], [-2, 1], [-1, 2]];
    const occupied = new Set(pieces.map((p) => `${p.x},${p.y}`));
    const free = (x, y) => x >= 0 && x < 8 && y >= 0 && y < 8 && !occupied.has(`${x},${y}`);

    const legalMoves = (p) => {
      const out = [];
      if (p.kind === 'queen') {
        for (const [dx, dy] of ROUND) {
          for (let x = p.x + dx, y = p.y + dy; free(x, y); x += dx, y += dy) out.push([x, y]);
        }
      } else {
        for (const [dx, dy] of p.kind === 'knight' ? JUMPS : ROUND) {
          if (free(p.x + dx, p.y + dy)) out.push([p.x + dx, p.y + dy]);
        }
      }
      return out;
    };

    // Squares two pieces can reach get two dots side by side.
    const bySquare = new Map();
    for (const p of pieces) {
      for (const [x, y] of legalMoves(p)) {
        const key = `${x},${y}`;
        if (!bySquare.has(key)) bySquare.set(key, []);
        bySquare.get(key).push({ p, steps: Math.max(Math.abs(x - p.x), Math.abs(y - p.y)) });
      }
    }
    const dots = document.createDocumentFragment();
    for (const [key, reach] of bySquare) {
      const [x, y] = key.split(',');
      reach.forEach((m, i) => {
        const dot = document.createElement('i');
        dot.className = 'art-chess__dot';
        dot.style.cssText = `--x: ${x}; --y: ${y}; --c: ${m.p.c};`
          + ` --ox: ${((i - (reach.length - 1) / 2) * 130).toFixed(0)}%;`
          + ` --d: ${(m.steps * 0.055).toFixed(3)}s`;
        dots.appendChild(dot);
      });
    }
    chessBoard.appendChild(dots);
  }

  // ----------------------------------------------------------- ticker
  // Always left to right; scrolling briefly speeds it up.
  const tickerTrack = $('[data-ticker]');
  const tickerAnim = tickerTrack && tickerTrack.getAnimations ? tickerTrack.getAnimations()[0] : null;
  let lastY = window.scrollY;
  let lastT = performance.now();
  let boost = 0;
  let rate = 1;
  let tickerRaf = 0;

  function trackVelocity(y) {
    const now = performance.now();
    const dt = Math.max(now - lastT, 1);
    const v = Math.abs(y - lastY) / dt;             // px per ms
    lastY = y;
    lastT = now;
    boost = Math.max(boost, Math.min(v * 2.2, 5));
    if (tickerAnim && !tickerRaf && !paused) tickerRaf = requestAnimationFrame(tickRate);
  }

  function tickRate() {
    tickerRaf = 0;
    boost *= 0.92;
    const want = 1 + boost;
    rate += (want - rate) * 0.12;
    if (tickerAnim) tickerAnim.playbackRate = rate;
    if (Math.abs(rate - 1) > 0.005 || boost > 0.005) tickerRaf = requestAnimationFrame(tickRate);
    else if (tickerAnim) tickerAnim.playbackRate = 1;
  }

  // ---------------------------------------------------------- counters
  const counters = $$('[data-count]');
  const countObserver = new IntersectionObserver((entries) => {
    for (const e of entries) {
      if (!e.isIntersecting) continue;
      countObserver.unobserve(e.target);
      const el = e.target;
      const end = Number(el.dataset.count);
      if (reduced || !end) continue;
      const start = performance.now();
      const duration = 1500 + end * 12;
      const step = (now) => {
        const t = clamp((now - start) / duration, 0, 1);
        const eased = t === 1 ? 1 : 1 - Math.pow(2, -10 * t);
        el.textContent = String(Math.round(end * eased));
        if (t < 1) requestAnimationFrame(step);
      };
      el.textContent = '0';
      requestAnimationFrame(step);
    }
  }, { threshold: 0.6 });
  counters.forEach((c) => countObserver.observe(c));

  // -------------------------------------------------------------- demo
  // A scripted conversation: the question types itself, the tools run one
  // after another, the answer streams in and a file arrives.
  const demo = $('[data-demo]');
  const demoCtl = (() => {
    if (!demo) return { finish() {} };
    const ask = $('[data-step="ask"]', demo);
    const reply = $('[data-step="reply"]', demo);
    const tools = $$('.tool', demo);
    const file = $('[data-step="file"]', demo);
    const typeEl = $('[data-type]', demo);
    const streamEl = $('[data-stream]', demo);
    const replay = $('[data-replay]', demo);
    const askText = typeEl.textContent.trim();
    const replyText = streamEl.textContent.trim();
    let run = 0;
    let played = false;

    const wait = (ms, id) => new Promise((resolve, reject) => {
      setTimeout(() => (id === run ? resolve() : reject(new Error('stale'))), ms);
    });
    const show = (el) => el.classList.add('is-shown');

    function reset() {
      $$('[data-step]', demo).forEach((el) => el.classList.remove('is-shown'));
      tools.forEach((t) => t.classList.remove('is-running', 'is-done'));
      typeEl.textContent = '';
      streamEl.textContent = '';
      streamEl.classList.remove('is-streaming');
      replay.classList.remove('is-ready');
    }

    function finish() {
      run++;
      $$('[data-step]', demo).forEach(show);
      tools.forEach((t) => { t.classList.remove('is-running'); t.classList.add('is-done'); });
      typeEl.textContent = askText;
      streamEl.textContent = replyText;
      streamEl.classList.remove('is-streaming');
      replay.classList.add('is-ready');
      played = true;
    }

    async function play() {
      const id = ++run;
      played = true;
      reset();
      try {
        await wait(350, id);
        show(ask);
        for (let i = 1; i <= askText.length; i++) {
          typeEl.textContent = askText.slice(0, i);
          await wait(18 + Math.random() * 34, id);
        }
        await wait(450, id);
        show(reply);
        const durations = [950, 1050, 1500, 1150];
        for (let i = 0; i < tools.length; i++) {
          show(tools[i]);
          tools[i].classList.add('is-running');
          await wait(durations[i] || 1000, id);
          tools[i].classList.remove('is-running');
          tools[i].classList.add('is-done');
          await wait(140, id);
        }
        streamEl.classList.add('is-streaming');
        const parts = replyText.split(/(\s+)/);
        let text = '';
        for (const part of parts) {
          text += part;
          streamEl.textContent = text;
          if (part.trim()) await wait(34 + Math.random() * 46, id);
        }
        streamEl.classList.remove('is-streaming');
        await wait(260, id);
        show(file);
        await wait(700, id);
        replay.classList.add('is-ready');
      } catch (_) { /* replaced by a newer run */ }
    }

    replay.addEventListener('click', () => { if (paused) finish(); else play(); });

    if (paused) finish();
    else {
      const io = new IntersectionObserver((entries) => {
        if (entries.some((e) => e.isIntersecting) && !played) {
          io.disconnect();
          if (paused) finish(); else play();
        }
      }, { threshold: 0.45 });
      io.observe(demo);
    }
    return { finish };
  })();

  // ------------------------------------------------------ pause control
  const motionButton = $('[data-motion]');
  const motionLabel = $('.motion__label', motionButton);

  function setPaused(p, remember) {
    paused = p;
    root.classList.toggle('paused', p);
    motionButton.setAttribute('aria-pressed', String(p));
    motionLabel.textContent = p ? 'Play animation' : 'Pause animation';
    if (silk && silk.ready) silk.setPaused(p);
    if (p) demoCtl.finish();
    if (remember) {
      try { localStorage.setItem('stellar-motion', p ? 'paused' : 'on'); } catch (_) { /* private mode */ }
    }
  }

  let saved = null;
  try { saved = localStorage.getItem('stellar-motion'); } catch (_) { /* private mode */ }
  if (saved === 'paused') setPaused(true, false);
  else if (reduced) setPaused(true, false);
  motionButton.addEventListener('click', () => setPaused(!paused, true));

  // ---------------------------------------------------------------- go
  updateScene();
  if (silk && silk.ready) {
    const s = window.scrollY / vh;
    silk.set({ base: 0.37 + s * 0.5, intensity: 1 - smoothstep(0.15, 1.2, s), immediate: true });
    queueScene();
  }
})();
