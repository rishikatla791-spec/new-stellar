/* The sign-in stage: two lamps that light the focused field.
 *
 * The light is rendered with WebGL into a canvas over the card (screen
 * blended, so it only ever adds light). Per pixel, for each lamp:
 *
 *   cone   light scattered by the air in the beam: a soft Gaussian profile
 *          that widens with distance, brightest at the lens and fading
 *          along the beam, with slow drifting haze in it;
 *   bloom  the glow around the hot lens;
 *   pool   the light landing on the focused field: a bright patch where the
 *          beam meets it, falling off down the field, and a hot top edge.
 *
 * Lamps behave like tungsten lamps: switching on, the filament heats from
 * dull orange to warm white in about a quarter of a second, with a stutter
 * as it catches, and the rail along the top flashes as the power arrives;
 * switching off, it cools back through orange over about half a second.
 * Brightness rises with heat to the power 2.4, so a cooling lamp reddens as
 * it dims. Each beam's aim point follows the focus on a spring, so the
 * lamps swing over with a little momentum, the second a moment after the
 * first. Dust drifting through the card shows only where the light is.
 *
 * Without WebGL the card gets .no-gl and a CSS beam stands in.
 */
(() => {
  "use strict";

  const card = document.querySelector("[data-stage]");
  const stage = document.querySelector("[data-spot]");
  const form = card && card.querySelector("[data-spot-form]");
  if (!card || !form) return;
  card.classList.add("is-staged");

  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const targets = [...card.querySelectorAll(".spot-field input, .spot-submit")];
  const holderOf = (el) => el.closest(".spot-field") || el;
  const HEAD_SCALE = 1.18;
  const LENS = 26.5 * HEAD_SCALE;   // pivot to lens centre, along the lamp
  const PIVOT_Y = 20;       // card top to the pivot under the mount
  const FIELD_RADIUS = 6;

  // ---------------------------------------------------------------- state
  const lamps = [...card.querySelectorAll(".rig")].map((el, i) => {
    const side = el.classList.contains("rig--left") ? -1 : 1;
    return {
      el, side,
      head: el.querySelector(".rig__head"),
      lens: el.querySelector(".rig__lens"),
      delay: i * 0.035,
      gain: i ? 0.88 : 1,
      aim: null, vel: { x: 0, y: 0 }, goal: null, goalAt: 0,
      heat: 0, power: 0, reach: 200, lensPos: { x: 0, y: 0 }, dir: { x: 0, y: 1 },
    };
  });
  let lit = false, litAt = 0, coldStart = true;
  let current = null;
  const fieldA = { rect: null, w: 0 }, fieldB = { rect: null, w: 0 };
  let rail = 0;
  let clock = 0;

  const now = () => performance.now() / 1000;

  function rectIn(el) {
    const c = card.getBoundingClientRect(), r = el.getBoundingClientRect();
    return { x: r.left - c.left - card.clientLeft, y: r.top - c.top - card.clientTop, w: r.width, h: r.height };
  }
  function pivotOf(l) { return { x: l.el.offsetLeft, y: PIVOT_Y }; }
  function restAim(l) {
    const p = pivotOf(l), a = 32 * Math.PI / 180;
    return { x: p.x - l.side * Math.sin(a) * 300, y: p.y + Math.cos(a) * 300 };
  }
  function aimFor(l, r) {
    return { x: r.x + r.w * (l.side < 0 ? 0.25 : 0.77), y: r.y + 1.5 };
  }
  function setGoal(goalFn) {
    const t = now();
    lamps.forEach((l) => { l.goal = goalFn(l); l.goalAt = t + (reduced ? 0 : l.delay); });
  }

  // ------------------------------------------------------------- focus
  function light(el) {
    clearTimeout(offTimer);
    if (current === el) return;
    if (current) holderOf(current).classList.remove("is-focus");
    const r = rectIn(el);
    if (current && fieldA.rect) { fieldB.rect = fieldA.rect; fieldB.w = fieldA.w; }
    fieldA.rect = r; fieldA.w = 0;
    current = el;
    holderOf(el).classList.add("is-focus");
    if (!lit) {
      lit = true;
      litAt = now();
      coldStart = lamps.every((l) => l.heat < 0.3);
      card.classList.add("is-lit");
      stage && stage.classList.add("is-lit");
    }
    setGoal((l) => aimFor(l, r));
    if (!gl) fallbackAim();
    run();
  }

  let offTimer = 0;
  function dark() {
    if (current) holderOf(current).classList.remove("is-focus");
    current = null;
    lit = false;
    card.classList.remove("is-lit");
    stage && stage.classList.remove("is-lit");
    // The pool stays on the field and fades with the cooling lamps.
    if (!gl) lamps.forEach((l) => { l.head.style.transform = ""; });
    run();
  }

  card.addEventListener("focusin", (e) => { if (targets.includes(e.target)) light(e.target); });
  card.addEventListener("focusout", () => {
    // Moving between fields passes through "nothing focused" for a moment.
    clearTimeout(offTimer);
    offTimer = setTimeout(() => { if (!targets.includes(document.activeElement)) dark(); }, 60);
  });

  function checkReady() { card.classList.toggle("is-ready", form.checkValidity()); }
  form.addEventListener("input", checkReady);
  form.addEventListener("change", checkReady);
  setTimeout(checkReady, 400);
  setTimeout(checkReady, 1500);
  checkReady();

  // ----------------------------------------------------------- physics
  function ignition(t) {
    // A lamp catching: two quick stutters, then steady.
    if (reduced || !coldStart || t > 0.26) return 1;
    if (t < 0) return 0;
    if (t < 0.035) return 0.4;
    if (t < 0.06) return 0.95;
    if (t < 0.095) return 0.5;
    if (t < 0.16) return 1;
    if (t < 0.19) return 0.78;
    return 1;
  }

  function step(dt) {
    const t = now();
    let moving = false;
    for (const l of lamps) {
      if (!l.aim) l.aim = restAim(l);
      // Lit: aim where the focus is (the second lamp a moment later).
      // Going dark: stay on the last field while cooling, then rest.
      let g;
      if (lit) {
        if (l.goal && t >= l.goalAt) l.shown = l.goal;
        g = l.shown || restAim(l);
      } else {
        if (l.heat < 0.08) l.shown = null;
        g = l.shown || restAim(l);
      }
      // Damped spring on the aim point: quick when lit, a slow settle back
      // to rest once the lamp has gone dark.
      const kx = lit ? 420 : 70, cx = 2 * (lit ? 0.8 : 1) * Math.sqrt(kx);
      const ky = lit ? 1800 : 70, cy = 2 * Math.sqrt(ky);
      if (reduced) { l.aim = { ...g }; l.vel = { x: 0, y: 0 }; }
      else {
        const n = Math.max(1, Math.ceil(dt / 0.004)), h = dt / n;
        for (let i = 0; i < n; i++) {
          l.vel.x += (kx * (g.x - l.aim.x) - cx * l.vel.x) * h;
          l.vel.y += (ky * (g.y - l.aim.y) - cy * l.vel.y) * h;
          l.aim.x += l.vel.x * h;
          l.aim.y += l.vel.y * h;
        }
      }
      if (Math.hypot(g.x - l.aim.x, g.y - l.aim.y) > 0.3 || Math.hypot(l.vel.x, l.vel.y) > 0.5) moving = true;

      // Heat: a filament warms and cools exponentially.
      const lt = t - litAt - l.delay;
      const target = lit && lt >= 0 ? 1 : 0;
      const tau = reduced ? 0.001 : (target ? 0.11 : 0.38);
      l.heat += (target - l.heat) * (1 - Math.exp(-dt / tau));
      if (l.heat < 0.0005 && !target) l.heat = 0;
      l.power = Math.pow(l.heat, 2.4) * (lit ? ignition(lt) : 1) * l.gain;
      if (Math.abs(target - l.heat) > 0.002) moving = true;

      // Geometry for the renderer.
      const p = pivotOf(l);
      const dx = l.aim.x - p.x, dy = Math.max(1, l.aim.y - p.y);
      const len = Math.hypot(dx, dy);
      l.dir = { x: dx / len, y: dy / len };
      l.lensPos = { x: p.x + l.dir.x * LENS, y: p.y + l.dir.y * LENS };
      l.reach = Math.max(10, (l.aim.y - l.lensPos.y) / l.dir.y);
      const deg = Math.atan2(l.dir.x, l.dir.y) * 180 / Math.PI;
      l.head.style.transform = `rotate(${(-deg).toFixed(2)}deg) scale(${HEAD_SCALE})`;

      // The lens itself: dull, then orange, then white-hot.
      const q = Math.min(1, l.power * 1.4);
      const warm = l.heat < 0.55 ? [255, 120 + 150 * l.heat, 40 + 120 * l.heat] : [255, 246, 228];
      const mix = (a, b) => Math.round(a + (b - a) * q);
      l.lens.style.background = `rgb(${mix(44, warm[0])},${mix(44, warm[1])},${mix(44, warm[2])})`;
      l.lens.style.boxShadow = q > 0.02
        ? `0 0 ${(3 + 5 * q).toFixed(1)}px ${(q).toFixed(2)}px rgba(255,236,204,${(0.85 * q).toFixed(2)})`
        : "none";
    }

    if (!lit && fieldA.rect && lamps.every((l) => l.heat < 0.01)) { fieldA.rect = null; fieldA.w = 0; }
    // Fields cross-fade as the focus moves.
    const fk = 1 - Math.exp(-dt / (reduced ? 0.001 : 0.08));
    fieldA.w += ((fieldA.rect ? 1 : 0) - fieldA.w) * fk;
    fieldB.w += (0 - fieldB.w) * fk;
    if (fieldB.w < 0.003) { fieldB.w = 0; fieldB.rect = null; }
    if (fieldB.rect || (fieldA.rect && fieldA.w < 0.997)) moving = true;

    // The rail flashes as power arrives.
    const rt = t - litAt;
    rail = lit && coldStart && !reduced && rt < 0.5
      ? Math.min(1, rt / 0.025) * Math.exp(-Math.max(0, rt - 0.025) / 0.07)
      : rail * Math.exp(-dt / 0.05);
    if (rail > 0.003) moving = true;

    return moving || lit;
  }

  // -------------------------------------------------------------- WebGL
  const canvas = card.querySelector(".spot-light");
  let gl = null;
  try {
    gl = canvas && canvas.getContext("webgl", { alpha: false, antialias: false, premultipliedAlpha: false, preserveDrawingBuffer: false });
  } catch (_) { gl = null; }

  const COMMON = `
    precision highp float;
    uniform vec2 uRes; uniform float uDpr; uniform float uTime;
    uniform vec2 uL0; uniform vec2 uD0; uniform vec2 uL1; uniform vec2 uD1;
    uniform vec2 uReach; uniform vec2 uPow; uniform vec2 uHeat;
    const float W0 = 3.0;
    const float GROW = 0.245;
    float hash(vec2 p) { p = fract(p * vec2(123.34, 456.21)); p += dot(p, p + 45.32); return fract(p.x * p.y); }
  `;

  const FRAG = COMMON + `
    uniform vec4 uFA; uniform vec4 uFB; uniform vec2 uFW; uniform float uFR;
    uniform vec4 uRail;
    float vnoise(vec2 p) {
      vec2 i = floor(p), f = fract(p);
      vec2 u = f * f * (3.0 - 2.0 * f);
      return mix(mix(hash(i), hash(i + vec2(1.0, 0.0)), u.x),
                 mix(hash(i + vec2(0.0, 1.0)), hash(i + vec2(1.0, 1.0)), u.x), u.y);
    }
    float fbm(vec2 p) {
      float s = 0.0, a = 0.5;
      for (int i = 0; i < 3; i++) { s += a * vnoise(p); p = p * 2.03 + vec2(7.1, 3.7); a *= 0.5; }
      return s;
    }
    // Filament colour: dull orange when cool, warm white when hot.
    vec3 tint(float h) { return mix(vec3(1.0, 0.30, 0.05), vec3(1.0), smoothstep(0.18, 0.95, h)); }
    float sdBox(vec2 p, vec2 b, float r) { vec2 q = abs(p) - b + r; return length(max(q, 0.0)) + min(max(q.x, q.y), 0.0) - r; }

    // Light scattered in the beam's air.
    float cone(vec2 p, vec2 L, vec2 D, float reach, out float s) {
      vec2 q = p - L;
      s = dot(q, D);
      float rs = q.x * D.y - q.y * D.x;
      float sp = max(s, 0.0);
      float w = W0 + sp * GROW;
      float rr = rs * rs / (w * w);
      // The beam stops where it meets the field: on the field's top edge,
      // which is level, not square to the beam.
      float endY = L.y + D.y * reach;
      float core = exp(-rr * 1.6) * (1.0 - smoothstep(endY - 2.5, endY + 1.0, p.y));
      float halo = 0.06 * exp(-rr * 0.18) * smoothstep(10.0, 60.0, sp) * (1.0 - smoothstep(endY - 30.0, endY + 24.0, p.y));
      float along = exp(-sp / 430.0) * (0.55 + 0.45 * exp(-sp / 34.0));
      float n = fbm(vec2(sp * 0.016 - uTime * 0.12, rs / w * 0.9 + uTime * 0.02));
      return (core * (0.78 + 0.44 * n) + halo) * along * smoothstep(-3.0, 6.0, s);
    }

    // Light landing on a field.
    float pool(vec2 p, vec4 f, vec2 L, vec2 D) {
      if (f.z < 1.0) return 0.0;
      vec2 hs = f.zw * 0.5;
      float inside = 1.0 - smoothstep(-0.6, 0.6, sdBox(p - (f.xy + hs), hs, uFR));
      if (inside <= 0.0) return 0.0;
      float t = (f.y - L.y) / max(D.y, 0.05);
      float xi = L.x + D.x * t;
      float w = W0 + max(t, 0.0) * GROW;
      float dx = (p.x - xi) / w;
      float hx = exp(-dx * dx * 1.7);
      float v = clamp((p.y - f.y) / f.w, 0.0, 1.0);
      float body = hx * mix(1.0, 0.62, v) + 0.1 * exp(-dx * dx * 0.3) * mix(1.0, 0.7, v);
      float rim = exp(-pow((p.y - f.y - 1.0) / 1.15, 2.0)) * (0.35 + 0.9 * hx);
      return inside * (body + rim);
    }

    vec3 lamp(vec2 p, vec2 L, vec2 D, float reach, float pw, float heat) {
      float s;
      float b = cone(p, L, D, reach, s);
      vec3 c = mix(vec3(1.0, 0.73, 0.46), vec3(1.0, 0.88, 0.70), exp(-max(s, 0.0) / 70.0)) * tint(heat);
      float pl = pool(p, uFA, L, D) * uFW.x + pool(p, uFB, L, D) * uFW.y;
      float d = length(p - L);
      float glow = exp(-d / 4.0) * 1.5 + exp(-d * d / 900.0) * 0.30 + exp(-d / 90.0) * 0.05;
      return pw * (c * (b * 2.05 + pl * 0.8) + vec3(1.0, 0.92, 0.80) * tint(heat) * glow);
    }

    void main() {
      vec2 p = vec2(gl_FragCoord.x, uRes.y * uDpr - gl_FragCoord.y) / uDpr;
      vec3 E = vec3(0.0);
      if (uPow.x > 0.0005) E += lamp(p, uL0, uD0, uReach.x, uPow.x, uHeat.x);
      if (uPow.y > 0.0005) E += lamp(p, uL1, uD1, uReach.y, uPow.y, uHeat.y);
      if (uRail.w > 0.001) {
        float dy = abs(p.y - uRail.y);
        float mx = smoothstep(uRail.x - 2.0, uRail.x + 8.0, p.x) * (1.0 - smoothstep(uRail.z - 8.0, uRail.z + 2.0, p.x));
        E += vec3(1.0, 0.42, 0.10) * uRail.w * mx * (exp(-dy * dy / 0.7) * 1.3 + exp(-dy / 6.0) * 0.22);
      }
      vec3 col = 1.0 - exp(-E);
      col += (hash(gl_FragCoord.xy + fract(uTime) * 91.0) - 0.5) / 255.0;
      gl_FragColor = vec4(max(col, 0.0), 1.0);
    }
  `;

  const DUST_VERT = COMMON + `
    attribute vec4 aP;     // x, y, size, phase
    varying float vB;
    float beam(vec2 p, vec2 L, vec2 D, float reach) {
      vec2 q = p - L;
      float s = dot(q, D), rs = q.x * D.y - q.y * D.x, sp = max(s, 0.0);
      float w = W0 + sp * GROW;
      return exp(-rs * rs / (w * w) * 1.4) * exp(-sp / 430.0) * smoothstep(4.0, 22.0, s) * (1.0 - smoothstep(reach - 6.0, reach, s));
    }
    void main() {
      vec2 p = aP.xy;
      float b = beam(p, uL0, uD0, uReach.x) * uPow.x + beam(p, uL1, uD1, uReach.y) * uPow.y;
      float tw = 0.5 + 0.5 * sin(uTime * (1.2 + aP.w * 2.2) + aP.w * 40.0);
      vB = b * (0.35 + 0.65 * tw);
      gl_Position = vec4(p.x / uRes.x * 2.0 - 1.0, 1.0 - p.y / uRes.y * 2.0, 0.0, 1.0);
      gl_PointSize = b > 0.015 ? aP.z * uDpr : 0.0;
    }
  `;
  const DUST_FRAG = `
    precision mediump float;
    varying float vB;
    void main() {
      vec2 c = gl_PointCoord * 2.0 - 1.0;
      float a = exp(-dot(c, c) * 3.2);
      gl_FragColor = vec4(vec3(1.0, 0.93, 0.80) * a * vB * 1.5, 1.0);
    }
  `;
  const QUAD_VERT = `attribute vec2 aQ; void main() { gl_Position = vec4(aQ, 0.0, 1.0); }`;

  let prog, dustProg, quadBuf, dustBuf, U = {}, DU = {};
  const DUST = 90;
  const dust = new Float32Array(DUST * 4);
  const dustVel = new Float32Array(DUST * 2);

  function compile(type, src) {
    const s = gl.createShader(type);
    gl.shaderSource(s, src);
    gl.compileShader(s);
    if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s));
    return s;
  }
  function link(vs, fs, names) {
    const p = gl.createProgram();
    gl.attachShader(p, compile(gl.VERTEX_SHADER, vs));
    gl.attachShader(p, compile(gl.FRAGMENT_SHADER, fs));
    gl.linkProgram(p);
    if (!gl.getProgramParameter(p, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(p));
    const u = {};
    for (const n of names) u[n] = gl.getUniformLocation(p, n);
    return { p, u };
  }

  const SHARED = ["uRes", "uDpr", "uTime", "uL0", "uD0", "uL1", "uD1", "uReach", "uPow", "uHeat"];
  if (gl) {
    try {
      const a = link(QUAD_VERT, FRAG, [...SHARED, "uFA", "uFB", "uFW", "uFR", "uRail"]);
      prog = a.p; U = a.u;
      const b = link(DUST_VERT, DUST_FRAG, SHARED);
      dustProg = b.p; DU = b.u;
      quadBuf = gl.createBuffer();
      gl.bindBuffer(gl.ARRAY_BUFFER, quadBuf);
      gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
      dustBuf = gl.createBuffer();
    } catch (err) {
      if (window.console) console.warn("Stellar sign-in light:", err.message);
      gl = null;
    }
  }
  if (!gl) card.classList.add("no-gl");

  let cssW = 1, cssH = 1, dpr = 1;
  function resize() {
    cssW = card.clientWidth;
    cssH = card.clientHeight;
    dpr = Math.min(window.devicePixelRatio || 1, 2);
    if (gl) {
      canvas.width = Math.max(1, Math.round(cssW * dpr));
      canvas.height = Math.max(1, Math.round(cssH * dpr));
    }
    if (current) fieldA.rect = rectIn(current);
    seedDust(false);
  }

  let seeded = false;
  function seedDust(force) {
    if (seeded && !force) return;
    seeded = true;
    for (let i = 0; i < DUST; i++) {
      dust[i * 4] = Math.random() * cssW;
      dust[i * 4 + 1] = Math.random() * cssH;
      dust[i * 4 + 2] = 1.1 + Math.random() * 1.9;
      dust[i * 4 + 3] = Math.random();
      dustVel[i * 2] = (Math.random() - 0.5) * 5;
      dustVel[i * 2 + 1] = 1 + Math.random() * 4;
    }
  }
  function moveDust(dt) {
    if (reduced) return;
    for (let i = 0; i < DUST; i++) {
      const ph = dust[i * 4 + 3] * 6.283;
      let x = dust[i * 4] + (dustVel[i * 2] + 4 * Math.sin(clock * 0.35 + ph)) * dt;
      let y = dust[i * 4 + 1] + (dustVel[i * 2 + 1] + 2.5 * Math.cos(clock * 0.27 + ph * 1.7)) * dt;
      if (x < -4) x += cssW + 8; else if (x > cssW + 4) x -= cssW + 8;
      if (y > cssH + 4) y -= cssH + 8;
      dust[i * 4] = x;
      dust[i * 4 + 1] = y;
    }
  }

  function setShared(u) {
    const [a, b] = lamps;
    gl.uniform2f(u.uRes, cssW, cssH);
    gl.uniform1f(u.uDpr, dpr);
    gl.uniform1f(u.uTime, clock);
    gl.uniform2f(u.uL0, a.lensPos.x, a.lensPos.y);
    gl.uniform2f(u.uD0, a.dir.x, a.dir.y);
    gl.uniform2f(u.uL1, b.lensPos.x, b.lensPos.y);
    gl.uniform2f(u.uD1, b.dir.x, b.dir.y);
    gl.uniform2f(u.uReach, a.reach, b.reach);
    gl.uniform2f(u.uPow, a.power, b.power);
    gl.uniform2f(u.uHeat, a.heat, b.heat);
  }
  const rect4 = (r) => (r ? [r.x, r.y, r.w, r.h] : [0, 0, 0, 0]);

  function render() {
    if (!gl) return;
    gl.viewport(0, 0, canvas.width, canvas.height);
    gl.disable(gl.BLEND);
    gl.useProgram(prog);
    setShared(U);
    gl.uniform4f(U.uFA, ...rect4(fieldA.rect));
    gl.uniform4f(U.uFB, ...rect4(fieldB.rect));
    gl.uniform2f(U.uFW, fieldA.w, fieldB.w);
    gl.uniform1f(U.uFR, FIELD_RADIUS);
    const r0 = lamps[0].el.offsetLeft, r1 = lamps[1].el.offsetLeft;
    gl.uniform4f(U.uRail, Math.min(r0, r1), 8.5, Math.max(r0, r1), rail);
    gl.bindBuffer(gl.ARRAY_BUFFER, quadBuf);
    const aQ = gl.getAttribLocation(prog, "aQ");
    gl.enableVertexAttribArray(aQ);
    gl.vertexAttribPointer(aQ, 2, gl.FLOAT, false, 0, 0);
    gl.drawArrays(gl.TRIANGLES, 0, 3);
    gl.disableVertexAttribArray(aQ);

    if (lamps[0].power + lamps[1].power > 0.002) {
      gl.enable(gl.BLEND);
      gl.blendFunc(gl.ONE, gl.ONE);
      gl.useProgram(dustProg);
      setShared(DU);
      gl.bindBuffer(gl.ARRAY_BUFFER, dustBuf);
      gl.bufferData(gl.ARRAY_BUFFER, dust, gl.DYNAMIC_DRAW);
      const aP = gl.getAttribLocation(dustProg, "aP");
      gl.enableVertexAttribArray(aP);
      gl.vertexAttribPointer(aP, 4, gl.FLOAT, false, 0, 0);
      gl.drawArrays(gl.POINTS, 0, DUST);
      gl.disableVertexAttribArray(aP);
      gl.disable(gl.BLEND);
    }
  }

  // ------------------------------------------------------------ fallback
  function fallbackAim() {
    if (!current) return;
    const r = rectIn(current);
    for (const l of lamps) {
      const p = pivotOf(l), a = aimFor(l, r);
      const dx = a.x - p.x, dy = a.y - p.y;
      const deg = Math.atan2(dx, dy) * 180 / Math.PI;
      l.head.style.transition = "transform .45s cubic-bezier(.34,1.25,.64,1)";
      l.head.style.transform = `rotate(${(-deg).toFixed(2)}deg)`;
      l.head.style.setProperty("--reach", `${Math.max(40, Math.hypot(dx, dy) - LENS).toFixed(0)}px`);
      l.head.style.setProperty("--spread", `${Math.min(150, Math.max(80, r.w * 0.36)).toFixed(0)}px`);
    }
  }

  // ---------------------------------------------------------------- loop
  let raf = 0, last = 0;
  function frame(ts) {
    raf = 0;
    const t = ts / 1000;
    const dt = last ? Math.min(t - last, 0.05) : 1 / 60;
    last = t;
    clock += dt;
    const busy = step(dt);
    moveDust(dt);
    render();
    if (busy) raf = requestAnimationFrame(frame);
    else last = 0;
  }
  function run() {
    if (!gl) return;
    if (!raf) raf = requestAnimationFrame(frame);
  }

  if (window.ResizeObserver) new ResizeObserver(() => { resize(); run(); }).observe(card);
  else window.addEventListener("resize", () => { resize(); run(); });
  resize();
  document.addEventListener("visibilitychange", () => { last = 0; if (!document.hidden) run(); });

  // A field focused by autofocus before this script ran: bring the lights
  // up a moment after the page appears, so the entrance is seen.
  if (targets.includes(document.activeElement)) {
    const el = document.activeElement;
    setTimeout(() => { if (document.activeElement === el) light(el); }, 550);
  }
})();
