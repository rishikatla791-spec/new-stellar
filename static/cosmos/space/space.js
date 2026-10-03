/* The renderer behind the cosmos page: one fixed WebGL2 canvas that draws
 * whichever scenes the page asks for each frame.
 *
 *   intro      the ride through the wormhole near Saturn (wormhole.js)
 *   hero       Gargantua (gargantua.js), with the cosmic flow bent around it
 *   flow       the cosmic flow alone, behind the reading sections
 *   tesseract  the five-dimensional bookshelf lattice (tesseract.js)
 *   footer     the bright flow under the footer
 *
 * The page supplies a director function; each frame it returns what to
 * draw (see main.js). Scenes render in linear light into a half-float
 * target at an adaptive fraction of the screen resolution; post.js adds
 * streaks and glow and maps light to screen colour at full resolution.
 */
import { target, dropTarget, bindTarget, programDone, targetMRT, dropMRT } from './gl.js';
import { buildTable, tableTexture } from './geodesics.js';
import { createGargantua, orbitCamera } from './gargantua.js';
import { createFlow } from './flow.js';
import { createPost } from './post.js';
import { createWormhole } from './wormhole.js';
import { createTesseract } from './tesseract.js';
import { createNoise3D } from './noise3d.js';
import { createParticles } from './particles.js';

export async function createSpace(canvas, opts = {}) {
  let gl = null;
  try {
    gl = canvas.getContext('webgl2', {
      alpha: false, antialias: false, depth: false, stencil: false,
      premultipliedAlpha: false, preserveDrawingBuffer: !!opts.preserve,
      powerPreference: 'high-performance',
    });
  } catch (_) { gl = null; }
  if (!gl) return null;
  if (!gl.getExtension('EXT_color_buffer_float')) return null;
  gl.getExtension('OES_texture_float_linear');

  const hw = navigator.hardwareConcurrency || 8;
  const MAX_DPR = opts.maxDpr || 2;
  const BUDGET = opts.budget || 2.4e6;     // scene pixels at most
  let quality = opts.quality || (hw <= 4 ? 0.7 : 1.0);
  const minQ = 0.5;
  let maxQ = opts.maxQuality || 1.0;

  let res = null;      // GPU resources
  let outW = 0, outH = 0, sceneW = 0, sceneH = 0;
  let scene = null, layer = null, small = null, smokeT = null;
  let lastDustOff = null;
  let director = null;
  let raf = 0;
  let running = false;
  let lost = false;
  let last = 0;
  const frameTimes = [];
  let lastAdapt = 0;
  const stats = { fps: 0, quality, sceneW: 0, sceneH: 0, ms: 0, frames: 0 };

  /* Start every shader compiling at once, build the light-path table on
     the CPU while the driver works, and wait - without blocking the page,
     where the browser can compile in the background.

     Drivers tend to work through the queue in order, so the shaders the
     entry needs go first, and init() returns as soon as those are ready:
     the entry opens on black and stars and does not need Gargantua for
     several seconds. The rest (Gargantua, the tesseract) finish in the
     background; heroReady resolves when they have. */
  let heroResolve = null;
  const heroReady = new Promise((r) => { heroResolve = r; });

  async function waitFor(progs, parallel, doneAt, t0) {
    if (!parallel) return;
    const limit = performance.now() + 30000;
    while (performance.now() < limit) {
      let all = true;
      for (const p of progs) {
        if (doneAt[p.name] != null) continue;
        if (programDone(gl, p, parallel)) doneAt[p.name] = Math.round(performance.now() - t0);
        else all = false;
      }
      if (all) return;
      await new Promise((r) => setTimeout(r, 16));
      if (gl.isContextLost()) throw new Error('context lost while compiling');
    }
  }

  async function init() {
    const t0 = performance.now();
    const tm = {};
    const parallel = gl.getExtension('KHR_parallel_shader_compile');
    const doneAt = {};
    stats.programs = doneAt;
    // first in the queue: what the entry draws
    const first = {
      worm: createWormhole(gl),          // the longest to compile: first
      post: createPost(gl),
      noise: createNoise3D(gl, 64),
      dust: createParticles(gl),
    };
    // then the rest (the sky flow is only needed once the page is up)
    const later = {
      garg: createGargantua(gl),
      flow: createFlow(gl, 256),
      tess: createTesseract(gl),
    };
    gl.flush();
    const table = tableTexture(gl, buildTable());
    tm.table = Math.round(performance.now() - t0);
    await waitFor(Object.values(first).flatMap((p) => p.programs), parallel, doneAt, t0);
    tm.entryReady = Math.round(performance.now() - t0);
    for (const part of Object.values(first)) part.setup();
    first.worm.setNoise(first.noise.tex);
    res = { table, ...first, garg: null, flow: null, tess: null };
    stats.initMs = performance.now() - t0;
    stats.timings = tm;
    stats.parallel = !!parallel;
    // the rest, in the background
    (async () => {
      try {
        await waitFor(Object.values(later).flatMap((p) => p.programs), parallel, doneAt, t0);
        if (!res) return;                      // context lost meanwhile
        for (const part of Object.values(later)) part.setup();
        later.garg.setNoise(first.noise.tex);
        later.flow.updateSky(0, 6);
        res.garg = later.garg;
        res.flow = later.flow;
        res.tess = later.tess;
        tm.heroReady = Math.round(performance.now() - t0);
        heroResolve();
      } catch (err) {
        console.error(err);
      }
    })();
  }

  function resize() {
    const dpr = Math.min(window.devicePixelRatio || 1, MAX_DPR);
    const cssW = canvas.clientWidth || window.innerWidth;
    const cssH = canvas.clientHeight || window.innerHeight;
    const w = Math.max(2, Math.round(cssW * dpr));
    const h = Math.max(2, Math.round(cssH * dpr));
    if (w !== outW || h !== outH) {
      outW = w; outH = h;
      canvas.width = w; canvas.height = h;
    }
    let s = quality;
    if (outW * outH * s * s > BUDGET) s = Math.sqrt(BUDGET / (outW * outH));
    const sw = Math.max(2, Math.round(outW * s));
    const sh = Math.max(2, Math.round(outH * s));
    if (sw !== sceneW || sh !== sceneH) {
      sceneW = sw; sceneH = sh;
      dropTarget(gl, scene);
      dropTarget(gl, layer);
      dropMRT(gl, smokeT);
      smokeT = targetMRT(gl, Math.max(2, Math.round(sw / 2)), Math.max(2, Math.round(sh / 2)));
      scene = target(gl, sw, sh);
      layer = target(gl, sw, sh);
      res.post.resize(sw, sh);
    }
    stats.sceneW = sceneW; stats.sceneH = sceneH;
  }

  function ensureSmall(w, h) {
    if (small && small.w === w && small.h === h) return small;
    dropTarget(gl, small);
    small = target(gl, w, h);
    return small;
  }

  /* Gargantua into the scene target: its smoke first, at half resolution. */
  function drawHero(h, aspect, time) {
    const { garg, flow } = res;
    const cam = heroCam(h, aspect);
    const st = heroState(h, time);
    let smoke = null;
    if ((st.smoke == null || st.smoke > 0) && st.hole > 0) {
      gl.bindFramebuffer(gl.FRAMEBUFFER, smokeT.fb);
      gl.viewport(0, 0, smokeT.w, smokeT.h);
      timed('smoke', () => garg.renderSmoke(st, cam, [smokeT.w, smokeT.h], null, res.table, flow.cube, flow.texel));
      smoke = smokeT;
    }
    bindTarget(gl, scene);
    timed('hero', () => garg.render(st, cam, [sceneW, sceneH], null, res.table, flow.cube, flow.texel, smoke));
  }

  function heroCam(h, aspect) {
    return orbitCamera(h.dist, h.incl, h.azim, h.fov, [0, h.shiftY], aspect, h.roll || 0);
  }

  function heroState(h, time) {
    return {
      time,
      hole: h.hole,
      diskGain: h.diskGain,
      beaming: h.beaming,
      skyGain: h.skyGain,
      skyContrast: h.skyContrast || 0,
      smoke: h.smoke,
      smokeLight: h.smokeLight,
      starGain: h.starGain,
      rIn: h.rIn,
      rOut: h.rOut,
    };
  }

  // Optional GPU timing per pass (?gputime), read back a few frames later.
  const timer = opts.gpuTime ? gl.getExtension('EXT_disjoint_timer_query_webgl2') : null;
  const pendingQ = [];
  stats.gpu = {};
  function timed(name, fn) {
    if (!timer) return fn();
    const q = gl.createQuery();
    gl.beginQuery(timer.TIME_ELAPSED_EXT, q);
    const r = fn();
    gl.endQuery(timer.TIME_ELAPSED_EXT);
    pendingQ.push([name, q]);
    return r;
  }
  function readTimers() {
    if (!timer) return;
    const disjoint = gl.getParameter(timer.GPU_DISJOINT_EXT);
    while (pendingQ.length && gl.getQueryParameter(pendingQ[0][1], gl.QUERY_RESULT_AVAILABLE)) {
      const [name, q] = pendingQ.shift();
      if (!disjoint) {
        const ms = gl.getQueryParameter(q, gl.QUERY_RESULT) / 1e6;
        const prev = stats.gpu[name];
        stats.gpu[name] = prev == null ? ms : prev * 0.9 + ms * 0.1;
      }
      gl.deleteQuery(q);
    }
  }

  function render(f) {
    readTimers();
    const aspect = outW / outH;
    const { flow, garg, post, worm, tess } = res;
    // The sky drifts slowly: a couple of cube faces per frame is plenty.
    if (flow) timed('sky', () => flow.updateSky(f.flowTime, f.skyFaces || 2));

    bindTarget(gl, scene);
    const time = f.time;
    if (f.intro) {
      // Saturn and the wormhole. The far side shows the destination's sky in
      // the hero camera's frame, so leaving the throat lines up with it.
      // Deep in the throat the streaks blur everything: draw the ride at
      // lower resolution there and let the streak pass scale it up.
      const cam = heroCam(f.hero, aspect);
      const ws = f.streak && f.streak.amount > 0.06 ? (f.intro.wormScale || 1) : 1;
      const wt = ws < 1 ? ensureSmall(Math.round(sceneW * ws), Math.round(sceneH * ws)) : scene;
      bindTarget(gl, wt);
      timed('wormhole', () => worm.render(f.intro, aspect, [wt.w, wt.h], cam, flow, time));
      if (wt !== scene) f.streakSrc = wt;
    } else if (!garg) {
      // Gargantua is still compiling: black space (rare - only if the
      // entry was skipped within its first moments on a first visit)
      gl.clearColor(0, 0, 0, 1);
      gl.clear(gl.COLOR_BUFFER_BIT);
    } else if (f.tess && f.tess.visible >= 0.999) {
      // the lattice covers everything: nothing underneath to draw
      gl.clearColor(0, 0, 0, 1);
      gl.clear(gl.COLOR_BUFFER_BIT);
    } else if (f.hero && f.hero.visible > 0) {
      drawHero(f.hero, aspect, time);
    } else {
      // plain cosmic flow
      const cam = heroCam(f.hero, aspect);
      timed('flow', () => garg.render({ ...heroState(f.hero, time), hole: 0 }, cam, [sceneW, sceneH], null, res.table, flow.cube, flow.texel));
    }

    if (f.tess && f.tess.visible > 0.001 && tess) {
      timed('tesseract', () => tess.render(f.tess, aspect, [sceneW, sceneH], time));
    }
    // dust streaming past the camera, for depth
    if (f.dust && f.dust.gain > 0) {
      const d = f.dust;
      const prev = lastDustOff && !f.still ? lastDustOff : d.off;
      const dt = f.streakSrc || scene;
      bindTarget(gl, dt);
      timed('dust', () => res.dust.render({ ...d, offPrev: d.jump ? d.off : prev }, aspect, d.tanFov, [dt.w, dt.h]));
      lastDustOff = d.off.slice();
    }
    if (f.footer && f.footer.h > 0 && flow) {
      const sc = sceneH / outH;
      timed('footer', () => flow.drawRibbon(f.flowTime, [sceneW, sceneH],
        [0, (f.footer.bottom) * sc, sceneW, f.footer.h * sc], f.footer.gain));
    }

    let src = scene;
    if (f.streak && f.streak.amount > 0.002) {
      const from = f.streakSrc || scene;
      src = timed('streaks', () => post.streaks(from, f.streak.center, f.streak.amount, time % 17, f.streak.chroma || 0));
    }
    const bloom = timed('bloom', () => post.bloom(src, f.post.spread == null ? 1 : f.post.spread));
    timed('finish', () => post.finish(src, bloom, outW, outH, f.post));
  }

  function adapt(now, dt) {
    frameTimes.push(dt);
    if (frameTimes.length > 40) frameTimes.shift();
    stats.frames++;
    if (now - lastAdapt < 1200 || frameTimes.length < 30) return;
    const sorted = frameTimes.slice().sort((a, b) => a - b);
    const med = sorted[Math.floor(sorted.length / 2)];
    stats.ms = med;
    stats.fps = 1000 / med;
    let q = quality;
    if (med > 21) q = Math.max(minQ, quality - (med > 30 ? 0.15 : 0.08));
    else if (med < 13.5 && quality < maxQ) q = Math.min(maxQ, quality + 0.05);
    if (q !== quality) {
      quality = q;
      stats.quality = q;
      frameTimes.length = 0;
      resize();
    }
    lastAdapt = now;
  }

  function loop(now) {
    raf = 0;
    if (!running || lost) return;
    const dt = last ? now - last : 16.7;
    last = now;
    const f = director(now / 1000, dt / 1000);
    if (f) {
      render(f);
      if (!f.still) adapt(now, dt);
    }
    if (running && !(f && f.stop)) raf = requestAnimationFrame(loop);
    else running = false;
  }

  canvas.addEventListener('webglcontextlost', (e) => {
    e.preventDefault();
    lost = true;
    cancelAnimationFrame(raf);
    raf = 0;
  });
  canvas.addEventListener('webglcontextrestored', async () => {
    res = null; scene = null; layer = null; small = null; smokeT = null;
    outW = outH = sceneW = sceneH = 0;
    try {
      await init();
    } catch (err) {
      console.error(err);
      return;
    }
    lost = false;
    resize();
    if (running) { last = 0; raf = requestAnimationFrame(loop); }
  });

  try {
    await init();
  } catch (err) {
    console.error(err);
    return null;
  }
  resize();

  const api = {
    gl,
    stats,
    setDirector(fn) { director = fn; },
    start() {
      if (running || lost) return;
      running = true;
      last = 0;
      raf = requestAnimationFrame(loop);
    },
    stop() {
      running = false;
      if (raf) cancelAnimationFrame(raf);
      raf = 0;
    },
    get running() { return running; },
    heroReady,
    get ready() { return !!(res && res.garg); },
    resize,
    /* Draw one frame now (for stills and reduced motion). */
    renderOnce(f) {
      if (lost) return;
      render(f);
    },
    setQuality(q) {
      quality = Math.min(Math.max(q, minQ), 2);
      maxQ = Math.max(maxQ, quality);
      stats.quality = quality;
      resize();
    },
  };
  return api;
}
