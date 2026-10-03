/* Stellar - cosmos landing page: the director.
 *
 * One fixed canvas draws space (static/cosmos/space/). Every frame this
 * file decides what it shows, from the clock and the scroll position:
 *
 *   entry       the ride through the wormhole near Saturn (intro.js)
 *   hero        Gargantua; scrolling lifts the camera over the disk and
 *               draws it back until the hole fades into the cosmic flow
 *   sections    the cosmic flow alone, drifting with the scroll
 *   tesseract   the lattice, flown through as its section scrolls by
 *   footer      the bright flow, only where the footer is on screen
 *
 * Everything that moves with the scroll is computed in one place per
 * frame and written as CSS custom properties; nothing changes layout.
 */
import { createSpace } from './space/space.js';
import { introFrame, INTRO } from './intro.js';

const root = document.documentElement;
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => Array.from(el.querySelectorAll(s));
const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
const smooth = (a, b, x) => {
  const t = clamp((x - a) / (b - a), 0, 1);
  return t * t * (3 - 2 * t);
};
const easeIO = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);

const params = new URLSearchParams(location.search);
const reduced = root.classList.contains('reduced');
const finePointer = window.matchMedia('(hover: hover) and (pointer: fine)').matches;
const stillAt = params.has('still') ? Number(params.get('still') || 12) : null;

// The hero shot: Gargantua seen close, from 21 Schwarzschild radii and
// 4.5 degrees above its disk, placed in the upper part of the screen with a
// lens shift so the horizon stays level. A high-energy disk: white heat at
// the inner edge, fibrous orange strands, a wide deep band of gas.
const HERO = {
  dist: 21, incl: 85.5, azim: 0, fov: 40, roll: 0,
  // the sky behind it is black: only stars and a few far galaxies
  diskGain: 4, beaming: 0.25, skyGain: 0.08, starGain: 0.7, rIn: 2.6, rOut: 16,
  smoke: 0, smokeLight: 2.0,    // no smoke: the disk is clean
  holeY: 0.33,                   // where the hole sits, from the top
};
const heroTan = Math.tan((HERO.fov * Math.PI) / 360);
HERO.shiftY = -(1 - 2 * HERO.holeY) * heroTan;

// ---------------------------------------------------------------- state
let space = null;
let phase = root.classList.contains('entering') ? 'entry' : 'live';
let entryStart = 0;
let entryEndAt = 0;          // when 'live' began (for the fade after a skip)
let skipped = false;
let clock = stillAt == null ? 9 : stillAt;   // scene time (seconds)
let lastNow = 0;
let paused = reduced;
let vh = window.innerHeight;
let scrollY = window.scrollY;
let lastScrollY = scrollY;
let scrollVel = 0;
let dirty = true;
let dustZ = 0;               // how far the camera has drifted through the dust
let dustScroll = window.scrollY;

// ------------------------------------------------------------ elements
const canvas = $('#space');
const hero = $('[data-hero]');
const heroSection = $('.hero');
const nav = $('[data-nav]');
const tess = $('[data-tess]');
const about = $('#about');
const steps = $$('[data-steps] .step');
const meter = $('[data-meter]');
const footer = $('[data-footer]');
const caption = $('[data-caption]');
const skipBtn = $('[data-skip]');
const replayBtn = $('[data-replay]');
const motionBtn = $('[data-motion]');
const motionLabel = $('[data-motion-label]');
const cube = $('[data-hypercube]');

// --------------------------------------------------------------- space
async function startSpace() {
  try {
    // ?nogl shows the page as it looks without WebGL
    space = params.has('nogl') ? null : await createSpace(canvas, { gpuTime: params.has('gputime') });
  } catch (err) {
    console.error(err);
    space = null;
  }
  if (!space) {
    root.classList.add('no-webgl');
    goLive(true);
    return;
  }
  window.__cosmos = { space, skip: () => endEntry(true), get phase() { return phase; } };
  // The entry can start at once; the page itself waits for Gargantua's
  // shaders (they finish compiling in the background during the entry).
  if (phase !== 'entry') await space.heroReady;
  root.classList.add('space-ready', 'space-on');
  space.setDirector(direct);
  if (phase === 'entry') entryStart = 0;
  space.start();
}

// --------------------------------------------------------------- entry
const CAPTIONS = [
  [1.2, 'Saturn'],
  [7.6, 'The wormhole'],
  [11.4, 'Through the wormhole'],
  [14.8, 'Gargantua'],
];

function entryCaption(t) {
  let text = '';
  for (const [at, c] of CAPTIONS) if (t >= at) text = c;
  if (caption.textContent !== text) {
    caption.classList.remove('is-on');
    caption.textContent = text;
    if (text) requestAnimationFrame(() => caption.classList.add('is-on'));
  }
}

function endEntry(byUser) {
  if (phase !== 'entry') return;
  skipped = !!byUser;
  goLive(false);
}

function goLive(immediate) {
  phase = 'live';
  entryEndAt = 0;
  root.classList.remove('entering');
  root.style.overflow = '';
  caption.classList.remove('is-on');
  if (replayBtn && space && !reduced) replayBtn.hidden = false;
  requestAnimationFrame(() => root.classList.add('is-live'));
  if (immediate) root.classList.add('is-live');
  wake();
}

function replayEntry() {
  if (!space || reduced) return;
  window.scrollTo(0, 0);
  root.classList.remove('is-live');
  root.classList.add('entering');
  root.style.overflow = 'hidden';
  phase = 'entry';
  entryStart = 0;
  skipped = false;
  wake();
}

if (phase === 'entry') {
  root.style.overflow = 'hidden';
  window.scrollTo(0, 0);
}
// Any of these ends the entry - the first time or after Replay.
skipBtn.addEventListener('click', () => endEntry(true));
window.addEventListener('keydown', (e) => {
  if (phase === 'entry' && (e.key === 'Escape' || e.key === 'Enter' || e.key === ' ')) {
    e.preventDefault();
    endEntry(true);
  }
});
window.addEventListener('wheel', () => { if (phase === 'entry') endEntry(true); }, { passive: true });
window.addEventListener('touchstart', () => { if (phase === 'entry') endEntry(true); }, { passive: true });
replayBtn && replayBtn.addEventListener('click', () => { replayEntry(); skipBtn.focus({ preventScroll: true }); });

// ------------------------------------------------------------ director
// Called by the renderer once per frame; returns what to draw.
function direct(now, dt) {
  if (!lastNow) lastNow = now;
  const step = Math.min(now - lastNow, 0.1);
  lastNow = now;
  if (!paused && stillAt == null) clock += step;
  scrollVel *= Math.exp(-step / 0.15);          // speed fades when the scrolling stops

  const aspect = canvas.width / Math.max(canvas.height, 1);
  const k = canvas.height / Math.max(vh, 1);          // canvas px per CSS px
  // a tight glow (spread < 1), so the shadow stays black next to the fire
  const post = { exposure: 1, bloom: 0.75, spread: 0.6, fade: 1, flash: 0, time: clock, grain: 0.032, vignette: 0.26 };

  // ---------------------------------------------------------- entry
  if (phase === 'entry') {
    if (!entryStart) entryStart = now;
    const t = params.has('entryAt') ? Number(params.get('entryAt')) : now - entryStart;
    const f = introFrame(t, HERO, aspect);
    entryCaption(t);
    post.fade = f.fade;
    post.flash = f.flash;
    if (f.done) {
      endEntry(false);
    } else {
      const h = { ...HERO, visible: 1, hole: 1, ...f.hero };
      // before the throat: the Sun's flare; then the exit burst; then
      // Gargantua's own faint ghosts
      post.flare = f.useHero && f.flare[3] < 1.5 ? [0, 0, 0, 0] : f.flare;
      post.grade = f.grade;
      dustZ = f.travel;
      const view = f.view;
      const dust = {
        right: f.useHero ? [1, 0, 0] : [view[0], view[1], view[2]],
        up: f.useHero ? [0, 1, 0] : [view[3], view[4], view[5]],
        fwd: f.useHero ? [0, 0, 1] : [view[6], view[7], view[8]],
        off: [0, 0, dustZ], tanFov: f.useHero ? Math.tan((h.fov * Math.PI) / 360) : f.tanFov,
        // eases to the page's own dust level by the end, so nothing jumps
        // no particles during the entry: only light bent by the wormhole;
        // the page's faint dust comes up as Gargantua settles
        gain: f.useHero ? 0.35 * smooth(INTRO.END - 2.5, INTRO.END, t) : 0,
        focus: 12, aperture: 0.014,
      };
      return { time: clock, flowTime: clock, hero: h, intro: f.useHero ? null : f, streak: f.streak, post, dust, skyFaces: 1 };
    }
  }
  if (!entryEndAt) entryEndAt = now;
  if (skipped) post.fade = smooth(0, 0.9, now - entryEndAt);

  // ------------------------------------------------------- journey
  // One continuous shot, driven by the scroll, as in the film: from the
  // hero we fall toward Gargantua - the shadow centres and swells, the dust
  // streams past, the last light at the edges goes out as the shadow fills
  // the frame. The features read in that darkness; the tesseract emerges
  // from it; then the stars return and we come out into the cosmic flow
  // above the footer. No section swaps one picture for another.
  const s = scrollY / vh;
  const dive = clamp(s / 0.9, 0, 1);
  const fall = Math.pow(dive, 1.35);                  // accelerating, like a fall
  // A slow drift, so the light bending around the hole visibly shifts:
  // it is being worked out live, frame by frame (calmer as we fall).
  const drift = smooth(0, 5, now - entryEndAt) * (1 - smooth(0, 0.5, dive));
  const inside = dive >= 1;                            // the frame is all shadow
  let emerge = 0;                                      // coming back out, after the tesseract
  if (about) {
    const r = about.getBoundingClientRect();
    emerge = smooth(0.95, 0.05, r.top / vh);
  }
  const h = {
    ...HERO,
    dist: HERO.dist * Math.pow(4.0 / HERO.dist, fall) + drift * 1.5 * Math.sin(clock * 0.07 + 1.3),
    incl: HERO.incl - 6 * fall + drift * 0.8 * Math.sin(clock * 0.11),
    azim: 8 * fall + drift * 10 * Math.sin(clock * 0.078),
    fov: HERO.fov + 10 * fall,
    // the shadow comes to the centre of the frame as we fall
    shiftY: HERO.shiftY * (1 - smooth(0, 0.75, dive)),
    hole: inside ? 0 : 1,
    // inside, black; stars return as we come back out
    skyGain: inside ? 0.18 * emerge : HERO.skyGain,
    starGain: inside ? HERO.starGain * emerge : HERO.starGain,
    visible: 1,
  };
  // the last light at the frame's edges goes out as the shadow fills it,
  // and the exposure eases down as we near the disk, so it never blinds
  post.fade *= 1 - smooth(0.82, 1.0, dive) * (inside ? 0 : 1);
  post.exposure *= 1 - 0.7 * fall;

  // ------------------------------------------------------ tesseract
  let tf = null;
  if (tess) {
    const r = tess.getBoundingClientRect();
    const enter = smooth(0, 1, (vh - r.top) / (vh * 0.85));
    // it is gone before the next section's words reach the middle
    const leave = smooth(0.4, 1.0, r.bottom / vh);
    const vis = enter * leave;
    if (vis > 0.001) {
      const p = clamp(-r.top / Math.max(r.height - vh, 1), 0, 1);
      tf = { visible: vis, progress: p, glow: 1, fov: 56 };
    }
  }

  // --------------------------------------------------------- footer
  let ff = null;
  if (footer) {
    const r = footer.getBoundingClientRect();
    if (r.top < vh) {
      ff = { bottom: (vh - r.bottom) * k, h: r.height * k, gain: smooth(0, 1, (vh - r.top) / (vh * 0.7)) };
    }
  }

  // streaks while falling, and while flying through the tesseract quickly
  let streak = null;
  if (!reduced) {
    const zone = Math.max(tf ? tf.visible : 0, smooth(0.3, 0.7, dive) * (1 - smooth(0.95, 1.0, dive)));
    const amt = Math.min(Math.abs(scrollVel) / 9000, 0.07) * zone;
    if (amt > 0.002) streak = { center: [0.5, 0.5 - 0.5 * h.shiftY / Math.tan((h.fov * Math.PI) / 360)], amount: amt };
  }

  // Dust drifting past: slowly on its own, and forward as the page scrolls,
  // so near and far specks move at different speeds - depth.
  if (!paused && stillAt == null) dustZ += 0.1 * step;
  dustZ += (scrollY - dustScroll) / vh * (9 + 30 * smooth(0.2, 0.8, dive) * (1 - smooth(0.95, 1.05, s / 0.9)));
  dustScroll = scrollY;
  const dust = {
    right: [1, 0, 0], up: [0, 1, 0], fwd: [0, 0, 1], off: [0, 0, dustZ],
    tanFov: Math.tan((h.fov * Math.PI) / 360),
    // (after the fade into the shadow the dust comes back gently)
    gain: 0.35 * (1 - 0.6 * (tf ? tf.visible : 0)) * (inside ? smooth(1.0, 1.3, s / 0.9) : 1),
    focus: 12, aperture: 0.014,
  };
  const frame = { time: clock, flowTime: clock, hero: h, tess: tf, footer: ff, streak, post, dust, skyFaces: 1 };
  // with the motion paused, draw only when something changed
  if ((paused || stillAt != null) && !dirty && !(skipped && now - entryEndAt < 1)) frame.stop = true;
  dirty = false;
  return frame;
}

/* Gargantua's core seen through the lens: faint ghosts, no starburst. */
function holeFlare(h, strength) {
  const tan = Math.tan((h.fov * Math.PI) / 360);
  return [0.5, 0.5 - 0.5 * h.shiftY / tan, 0.16 * clamp(strength, 0, 1), 0];
}

function wake() {
  dirty = true;
  if (space && !space.running) {
    lastNow = 0;
    space.start();
  }
}

// --------------------------------------------------------- page scene
let queued = false;
function onScroll() {
  scrollY = window.scrollY;
  if (!queued) {
    queued = true;
    requestAnimationFrame(updatePage);
  }
  wake();
}

let lastT = performance.now();
function updatePage() {
  queued = false;
  const now = performance.now();
  const dtS = Math.max((now - lastT) / 1000, 1 / 240);
  scrollVel = scrollVel * 0.6 + ((scrollY - lastScrollY) / dtS) * 0.4;
  lastScrollY = scrollY;
  lastT = now;

  const heroP = clamp(scrollY / (vh * 0.8), 0, 1);
  heroSection.style.setProperty('--hero', heroP.toFixed(4));
  root.style.setProperty('--hero-fade', (1 - smooth(0.5, 1.3, scrollY / vh)).toFixed(3));
  nav.classList.toggle('is-scrolled', scrollY > 30);

  if (tess) {
    const r = tess.getBoundingClientRect();
    const p = clamp(-r.top / Math.max(r.height - vh, 1), 0, 1);
    meter.parentElement.style.setProperty('--p', p.toFixed(4));
    const active = r.top < vh * 0.5 && r.bottom > vh * 0.5 ? Math.min(3, Math.floor(p * 4.2)) : -1;
    steps.forEach((el, i) => {
      el.classList.toggle('is-on', i === active);
      el.classList.toggle('is-past', active > i);
    });
    cubeVisible = r.top < vh && r.bottom > 0;
    cubeProgress = p;
    if (cubeVisible) startCube();
  }
  spy();
}

window.addEventListener('scroll', onScroll, { passive: true });
window.addEventListener('resize', () => {
  vh = window.innerHeight;
  if (space) space.resize();
  placeIndicator();
  onScroll();
});

// -------------------------------------------------------- smooth scroll
// Wheel input eases toward its target; keyboard, scrollbar and touch
// stay native.
const smoothScroll = (() => {
  const enabled = finePointer && !reduced;
  const TAU = 0.11;
  let target = window.scrollY, current = target, running = false, last = 0, glide = null;
  const maxScroll = () => root.scrollHeight - window.innerHeight;

  function frame(now) {
    const dt = last ? Math.min((now - last) / 1000, 0.05) : 1 / 60;
    last = now;
    if (glide) {
      if (!glide.start) glide.start = now;
      const t = clamp((now - glide.start) / glide.duration, 0, 1);
      current = glide.from + (glide.to - glide.from) * easeIO(t);
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
  if (enabled) {
    root.classList.add('smooth');
    window.addEventListener('wheel', (e) => {
      if (phase !== 'live' || e.ctrlKey || e.defaultPrevented) return;
      if (Math.abs(e.deltaX) > Math.abs(e.deltaY)) return;
      e.preventDefault();
      let d = e.deltaY;
      if (e.deltaMode === 1) d *= 16;
      else if (e.deltaMode === 2) d *= window.innerHeight;
      if (!running) current = target = window.scrollY;
      glide = null;
      target = clamp(target + d, 0, maxScroll());
      run();
    }, { passive: false });
    window.addEventListener('scroll', () => { if (!running) target = current = window.scrollY; }, { passive: true });
    const stop = () => { glide = null; target = current = window.scrollY; };
    window.addEventListener('keydown', (e) => {
      if (['ArrowUp', 'ArrowDown', 'PageUp', 'PageDown', 'Home', 'End', ' '].includes(e.key)) stop();
    });
    window.addEventListener('pointerdown', stop, { passive: true });
  }
  return {
    to(y) {
      const dest = clamp(y, 0, maxScroll());
      if (!enabled) { window.scrollTo({ top: dest, behavior: reduced ? 'auto' : 'smooth' }); return; }
      current = window.scrollY;
      glide = { from: current, to: dest, start: 0, duration: clamp(700 + Math.abs(dest - current) * 0.28, 900, 2000) };
      run();
    },
  };
})();

document.addEventListener('click', (e) => {
  const link = e.target.closest('a[href^="#"]');
  if (!link || e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey) return;
  const id = link.getAttribute('href').slice(1);
  const el = id ? document.getElementById(id) : null;
  if (!el) return;
  e.preventDefault();
  let y = id === 'home' ? 0 : el.getBoundingClientRect().top + window.scrollY;
  if (id === 'tesseract') y += vh * 0.15;             // land inside the lattice
  smoothScroll.to(y);
  if (history.replaceState) history.replaceState(null, '', id === 'home' ? location.pathname : '#' + id);
  if (!el.hasAttribute('tabindex')) el.setAttribute('tabindex', '-1');
  el.focus({ preventScroll: true });
});

// ----------------------------------------------------------------- nav
const navLinks = $$('[data-spy]');
const indicator = $('.nav__indicator');
const linksBox = $('.nav__links');
const sections = $$('[data-section]');
let active = 'home';
let hovering = null;

function placeIndicator(link) {
  const t = link || hovering || navLinks.find((a) => a.dataset.spy === active);
  if (!t || !indicator) return;
  const lr = t.getBoundingClientRect();
  const br = linksBox.getBoundingClientRect();
  indicator.style.setProperty('--ix', (lr.left - br.left).toFixed(1) + 'px');
  indicator.style.setProperty('--iw', lr.width.toFixed(1) + 'px');
}
function spy() {
  const line = vh * 0.45;
  let cur = 'home';
  for (const sec of sections) {
    const r = sec.getBoundingClientRect();
    if (r.top <= line) cur = sec.dataset.section;
  }
  if (cur === active) return;
  active = cur;
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

// -------------------------------------------------------------- reveal
const revealer = new IntersectionObserver((entries) => {
  const arriving = entries.filter((x) => x.isIntersecting).map((x) => x.target);
  arriving.sort((a, b) => {
    const ra = a.getBoundingClientRect(), rb = b.getBoundingClientRect();
    return (ra.top - rb.top) || (ra.left - rb.left);
  });
  arriving.forEach((el, i) => {
    el.style.setProperty('--delay', Math.min(i * 0.085, 0.6).toFixed(3) + 's');
    el.classList.add('is-in');
    revealer.unobserve(el);
    setTimeout(() => el.style.removeProperty('--delay'), 2200);
  });
}, { rootMargin: '0px 0px -10% 0px', threshold: 0.12 });
if (!reduced) $$('.reveal').forEach((el) => revealer.observe(el));
else $$('.reveal').forEach((el) => el.classList.add('is-in'));

// --------------------------------------------------------------- cards
// Each card lights in its own colour: a spotlight and border glow follow
// the pointer.
if (finePointer) {
  for (const card of $$('[data-card]')) {
    card.addEventListener('pointerenter', () => card.classList.add('is-hot'));
    card.addEventListener('pointermove', (e) => {
      const r = card.getBoundingClientRect();
      card.style.setProperty('--mx', (e.clientX - r.left).toFixed(1) + 'px');
      card.style.setProperty('--my', (e.clientY - r.top).toFixed(1) + 'px');
    });
    card.addEventListener('pointerleave', () => card.classList.remove('is-hot'));
  }
}

// ------------------------------------------------------------ counters
const countObserver = new IntersectionObserver((entries) => {
  for (const x of entries) {
    if (!x.isIntersecting) continue;
    countObserver.unobserve(x.target);
    const el = x.target;
    const end = Number(el.dataset.count);
    if (reduced || !end) continue;
    const start = performance.now();
    const dur = 1500 + end * 12;
    const tick = (n) => {
      const t = clamp((n - start) / dur, 0, 1);
      el.textContent = String(Math.round(end * (t === 1 ? 1 : 1 - Math.pow(2, -10 * t))));
      if (t < 1) requestAnimationFrame(tick);
    };
    el.textContent = '0';
    requestAnimationFrame(tick);
  }
}, { threshold: 0.6 });
$$('[data-count]').forEach((c) => countObserver.observe(c));

// ----------------------------------------------------------- hypercube
// A real tesseract: the sixteen corners of a four-dimensional cube,
// turned in the x-w and y-z planes, then seen in perspective twice
// (4D -> 3D -> 2D). Its 32 edges are drawn as lines.
let cubeVisible = false;
let cubeProgress = 0;
let cubeRaf = 0;
const cubeVerts = [];
for (let i = 0; i < 16; i++) cubeVerts.push([i & 1 ? 1 : -1, i & 2 ? 1 : -1, i & 4 ? 1 : -1, i & 8 ? 1 : -1]);
const cubeEdges = [];
for (let i = 0; i < 16; i++) for (let b = 0; b < 4; b++) { const j = i ^ (1 << b); if (j > i) cubeEdges.push([i, j]); }
let cubeLines = [];
let cubeDots = [];
if (cube) {
  const ns = 'http://www.w3.org/2000/svg';
  cubeLines = cubeEdges.map(() => { const l = document.createElementNS(ns, 'line'); cube.appendChild(l); return l; });
  cubeDots = cubeVerts.map(() => { const c = document.createElementNS(ns, 'circle'); cube.appendChild(c); return c; });
}
function drawCube(t) {
  const a = t * 0.42 + cubeProgress * 2.4;   // x-w plane
  const b = t * 0.27 + cubeProgress * 1.1;   // y-z plane
  const ca = Math.cos(a), sa = Math.sin(a), cb = Math.cos(b), sb = Math.sin(b);
  const tilt = 0.5, ct = Math.cos(tilt), st = Math.sin(tilt);
  const pts = cubeVerts.map(([x, y, z, w]) => {
    const x1 = x * ca - w * sa, w1 = x * sa + w * ca;
    const y1 = y * cb - z * sb, z1 = y * sb + z * cb;
    const k4 = 2.6 / (3.2 - w1);                      // 4D -> 3D
    let X = x1 * k4, Y = y1 * k4, Z = z1 * k4;
    const Y2 = Y * ct - Z * st, Z2 = Y * st + Z * ct;  // tip toward us
    const k3 = 3.6 / (5.2 - Z2);                       // 3D -> 2D
    return [X * k3 * 40, Y2 * k3 * 40, Z2, w1];
  });
  cubeEdges.forEach(([i, j], n) => {
    const p = pts[i], q = pts[j], l = cubeLines[n];
    l.setAttribute('x1', p[0].toFixed(2)); l.setAttribute('y1', p[1].toFixed(2));
    l.setAttribute('x2', q[0].toFixed(2)); l.setAttribute('y2', q[1].toFixed(2));
    const depth = clamp(0.5 + (p[2] + q[2]) * 0.12 + (p[3] + q[3]) * 0.12, 0.12, 1);
    l.setAttribute('stroke-opacity', depth.toFixed(3));
    l.setAttribute('stroke-width', (0.6 + depth * 1.1).toFixed(2));
  });
  pts.forEach((p, n) => {
    const d = cubeDots[n];
    const depth = clamp(0.5 + p[2] * 0.25 + p[3] * 0.25, 0.15, 1);
    d.setAttribute('cx', p[0].toFixed(2)); d.setAttribute('cy', p[1].toFixed(2));
    d.setAttribute('r', (1 + depth * 1.6).toFixed(2));
    d.setAttribute('fill-opacity', depth.toFixed(3));
  });
}
function startCube() {
  if (cubeRaf || !cube) return;
  const tick = (n) => {
    cubeRaf = 0;
    drawCube(paused ? 3 : n / 1000);
    if (cubeVisible && !paused) cubeRaf = requestAnimationFrame(tick);
  };
  cubeRaf = requestAnimationFrame(tick);
}

// --------------------------------------------------------------- pause
function setPaused(p, remember) {
  paused = p;
  root.classList.toggle('paused', p);
  motionBtn.setAttribute('aria-pressed', String(p));
  motionLabel.textContent = p ? 'Play motion' : 'Pause motion';
  if (remember) {
    try { localStorage.setItem('stellar-cosmos-motion', p ? 'paused' : 'on'); } catch (_) { /* private mode */ }
  }
  if (!p) startCube();
  wake();
}
let saved = null;
try { saved = localStorage.getItem('stellar-cosmos-motion'); } catch (_) { /* private mode */ }
if (saved === 'paused' || reduced) setPaused(true, false);
motionBtn.addEventListener('click', () => setPaused(!paused, true));

// ------------------------------------------------------------------ go
const fontsReady = document.fonts && document.fonts.ready
  ? Promise.race([document.fonts.ready, new Promise((r) => setTimeout(r, 900))])
  : Promise.resolve();
// Let the loader paint before the (synchronous) shader compile.
requestAnimationFrame(() => setTimeout(async () => {
  updatePage();
  fontsReady.then(() => placeIndicator());
  await startSpace();
  if (phase === 'live') goLive(true);
  updatePage();
}, 30));
