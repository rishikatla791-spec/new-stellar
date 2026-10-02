/* Stellar silk: a procedural ribbon of light that flows left to right.
 *
 * Every shape on the canvas is generated, nothing is sampled from an image.
 * A few hundred thin fibers are grouped into twisting sheets around one
 * travelling spine. Where a sheet turns edge-on its fibers bunch together
 * and burn white; where it faces the viewer they fan out into a crimson
 * veil. Every wave in the field is a function of (q - speed * t), so the
 * ribbon's body, its folds and the light pulses inside it all travel to the
 * right, each at its own steady speed.
 *
 * Pipeline: fibers and dust are drawn additively into a half-float "energy"
 * buffer, three blurred copies of it give the bloom, and one final pass
 * turns energy into colour (crimson -> pink -> white).
 *
 * The page talks to it through window.stellarSilk; see the bottom.
 */
(() => {
  'use strict';

  const canvas = document.getElementById('silk');
  if (!canvas) return;
  const root = document.documentElement;

  // ---------------------------------------------------------------- tuning
  // Speeds are in "q units" per second. The visible width is ~1.9 q, so a
  // speed of 0.06 crosses the screen in about half a minute.
  const SPEED = {
    body: 1.0,        // global multiplier for the whole field
  };
  const SEGMENTS = 220;          // points along every fiber
  const Q0 = -0.08;              // q at the left edge of the canvas
  const START_TIME = 18.0;       // a pleasant configuration to open on
  const REVEAL_SECONDS = 2.8;    // light flows in from the left on load

  const api = {
    ready: false,
    set() {},
    setPaused() {},
    get paused() { return true; },
  };
  window.stellarSilk = api;

  let gl = null;
  try {
    gl = canvas.getContext('webgl2', {
      alpha: false, antialias: false, depth: false, stencil: false,
      premultipliedAlpha: false, preserveDrawingBuffer: false,
      powerPreference: 'high-performance',
    });
  } catch (_) { gl = null; }
  if (!gl) { root.classList.add('no-webgl'); return; }

  // --------------------------------------------------------------- shaders
  const FIELD = /* glsl */`
    uniform float uT;
    uniform float uBase;
    uniform float uAmp;
    uniform float uSpan;
    uniform vec2 uRes;
    uniform float uDpr;
    uniform float uReveal;
    uniform float uEncode;
    uniform vec4 uPointer;   // q, y (0..1 from bottom), strength, unused

    const float Q0 = ${Q0.toFixed(3)};
    const float MARGIN = 0.16;      // fibers start and end off-canvas
    const float TAU = 6.2831853;

    // The ribbon's centre line. Four waves, all moving right, at speeds
    // that differ just enough for the shape to keep changing.
    float spine(float q) {
      float t = uT;
      return 0.064 * sin(3.1  * (q - 0.046 * t) + 0.9)
           + 0.042 * sin(5.6  * (q - 0.064 * t) + 2.4)
           + 0.015 * sin(10.1 * (q - 0.090 * t) + 4.2)
           + 0.034 * sin(1.55 * (q - 0.031 * t) + 5.1);
    }

    vec2 toPx(float q, float y) {
      return vec2((q - Q0) / uSpan * uRes.x, y * uRes.y);
    }

    // The intro: everything left of the front is lit.
    float revealMask(float q) {
      float front = mix(Q0 - 0.35, Q0 + uSpan + 0.6, uReveal);
      return 1.0 - smoothstep(front - 0.42, front, q);
    }
    float revealHead(float q) {
      float front = mix(Q0 - 0.35, Q0 + uSpan + 0.6, uReveal);
      float d = (q - front + 0.08) / 0.07;
      return exp(-d * d) * (1.0 - smoothstep(0.82, 1.0, uReveal));
    }
  `;

  const FIBER_VS = /* glsl */`#version 300 es
    precision highp float;
    layout(location = 0) in vec2 aCorner;   // u along the fiber, side -1/+1
    layout(location = 1) in vec4 aSheet;    // phase, orbit radius, width, twist
    layout(location = 2) in vec4 aFiber;    // slot in sheet, seed, gain, width px
    layout(location = 3) in vec4 aExtra;    // kind, wobble freq, wobble amp, speed jitter
    ${FIELD}
    out float vSide;
    out float vE;
    flat out float vKind;

    float twist(float q) {
      return aSheet.w * (q - 0.115 * uT) + aSheet.x
           + 1.15 * sin(2.05 * q - 0.052 * uT + aSheet.x * 1.7);
    }
    float envelope(float q) {
      return 0.72 + 0.48 * sin(2.25 * (q - 0.052 * uT) + aSheet.x * 2.3) * (0.7 + 0.3 * sin(1.1 * q - 0.03 * uT));
    }

    // y (relative to the base line) of this fiber at q, plus how compressed
    // its sheet is there (1 = fully fanned out).
    vec2 fiberY(float q) {
      float kind = aExtra.x;
      float wob = sin(aExtra.y * (q - (0.15 + aExtra.w) * uT) + aFiber.y * TAU);
      if (kind > 1.5) {
        // Stray filaments: long arcs that leave the ribbon and come back.
        float arc = sin(aSheet.w * (q - 0.085 * uT) + aSheet.x)
                  * (0.55 + 0.45 * sin(1.3 * q - 0.04 * uT + aFiber.y * 6.0));
        return vec2(spine(q) + aSheet.y * arc + aExtra.z * 2.5 * wob, 1.0);
      }
      float th = twist(q);
      float c = abs(cos(th));
      float env = envelope(q);
      float spread = aSheet.z * env * (0.2 + 0.8 * c);
      float centre = aSheet.y * env * sin(th);
      float y = spine(q) + centre + aFiber.x * spread * 0.5
              + (0.0018 + aExtra.z * (0.2 + c)) * wob;
      return vec2(y, clamp(aSheet.z * env / max(spread, 1e-4), 1.0, 14.0));
    }

    float pointerPush(float q, float y) {
      float d = (q - uPointer.x) / 0.16;
      float dy = y - uPointer.y;
      return sign(dy) * 0.028 * uPointer.z * exp(-d * d) * exp(-dy * dy / 0.006);
    }

    void main() {
      float u = aCorner.x;
      float q = Q0 - MARGIN + u * (uSpan + 2.0 * MARGIN);
      float h = (uSpan + 2.0 * MARGIN) / ${(SEGMENTS * 2).toFixed(1)};

      vec2 fA = fiberY(q - h);
      vec2 fC = fiberY(q);
      vec2 fB = fiberY(q + h);
      float yA = uBase + uAmp * fA.x;
      float yC = uBase + uAmp * fC.x;
      float yB = uBase + uAmp * fB.x;
      yA += pointerPush(q - h, yA);
      yC += pointerPush(q, yC);
      yB += pointerPush(q + h, yB);

      vec2 pA = toPx(q - h, yA);
      vec2 pB = toPx(q + h, yB);
      vec2 pC = toPx(q, yC);
      vec2 tng = normalize(pB - pA + vec2(1e-5, 0.0));
      vec2 nrm = vec2(-tng.y, tng.x);

      float kind = aExtra.x;
      float comp = fC.y;
      float e = aFiber.z;
      float width;
      if (kind > 0.5 && kind < 1.5) {
        // Haze: one wide, soft strand per sheet slice, the ribbon's volume.
        float th = twist(q);
        float spreadPx = aSheet.z * envelope(q) * (0.2 + 0.8 * abs(cos(th))) * uAmp * uRes.y;
        width = spreadPx * aFiber.w + 10.0 * uDpr;
        e *= 0.55 + 0.45 * sin(1.7 * (q - 0.07 * uT) + aSheet.x * 2.0);
      } else {
        // Light pulses race to the right along every fiber.
        float pulse = pow(0.5 + 0.5 * sin(2.3 * (q - 0.30 * uT) + aFiber.y * 7.0 + aSheet.x), 6.0);
        // Luminous zones shared by every sheet, drifting right with the body.
        float zone = 0.5 + 0.5 * sin(1.7 * (q - 0.07 * uT) + 0.4);
        float broad = (0.3 + 1.1 * zone * zone) * (0.8 + 0.2 * sin(3.1 * q + aSheet.x * 2.0 + aFiber.y));
        float edge = smoothstep(0.80, 0.97, abs(aFiber.x));
        e *= pow(comp, 1.3) * broad * (0.55 + 2.2 * pulse) * (1.0 + 2.4 * edge);
        width = aFiber.w * uDpr;
      }

      // Light where the visitor's pointer touches the ribbon.
      float pd = (q - uPointer.x) / 0.14;
      float py = (yC - uPointer.y) / 0.09;
      e *= 1.0 + 0.9 * uPointer.z * exp(-pd * pd - py * py);

      // Keep very thin fibers anti-aliased: draw them wider, dimmer.
      float drawn = max(width, 1.35);
      e *= width / drawn;

      // Ends fade so nothing pops at the canvas edges.
      e *= smoothstep(Q0 - MARGIN, Q0 - MARGIN * 0.4, q)
         * (1.0 - smoothstep(Q0 + uSpan + MARGIN * 0.4, Q0 + uSpan + MARGIN, q));

      float lit = revealMask(q);
      e = e * lit + (kind < 0.5 ? aFiber.z * 14.0 * revealHead(q) : 0.0);

      vSide = aCorner.y;
      vE = e * uEncode;
      vKind = kind;
      vec2 p = pC + nrm * aCorner.y * drawn * 0.5;
      gl_Position = vec4(p / uRes * 2.0 - 1.0, 0.0, 1.0);
    }
  `;

  const FIBER_FS = /* glsl */`#version 300 es
    precision mediump float;
    in float vSide;
    in float vE;
    flat in float vKind;
    out vec4 outColor;
    void main() {
      float d = abs(vSide);
      float a = (vKind > 0.5 && vKind < 1.5)
        ? exp(-d * d * 3.2) * (1.0 - d)
        : clamp(1.0 - d * d, 0.0, 1.0);
      outColor = vec4(vE * a, 0.0, 0.0, 1.0);
    }
  `;

  const DUST_VS = /* glsl */`#version 300 es
    precision highp float;
    layout(location = 0) in vec4 aP;   // q0, y offset (or sky y), seed, size px
    layout(location = 1) in vec4 aQ;   // speed, kind, energy, twinkle
    ${FIELD}
    out float vE;
    flat out float vKind;
    void main() {
      float kind = aQ.y;
      float span = uSpan + 0.3;
      float q = mod(aP.x + aQ.x * uT, span) + Q0 - 0.15;
      float y;
      if (kind < 0.5) {
        y = uBase + uAmp * (spine(q) + aP.y);
      } else {
        y = aP.y + 0.006 * sin(uT * 0.21 + aP.z * 30.0);
      }
      float tw = 0.55 + 0.45 * sin(uT * aQ.w + aP.z * 41.0);
      float edge = smoothstep(Q0 - 0.12, Q0 + 0.04, q) * (1.0 - smoothstep(Q0 + uSpan - 0.04, Q0 + uSpan + 0.12, q));
      float lit = kind < 0.5 ? revealMask(q) : smoothstep(0.0, 0.6, uReveal);
      vE = aQ.z * tw * edge * lit * uEncode;
      vKind = kind;
      gl_PointSize = aP.w * uDpr;
      vec2 p = toPx(q, y);
      gl_Position = vec4(p / uRes * 2.0 - 1.0, 0.0, 1.0);
    }
  `;

  const DUST_FS = /* glsl */`#version 300 es
    precision mediump float;
    in float vE;
    flat in float vKind;
    out vec4 outColor;
    void main() {
      vec2 p = gl_PointCoord * 2.0 - 1.0;
      float r2 = dot(p, p);
      if (r2 > 1.0) discard;
      float a = vKind > 0.5 && vKind < 1.5
        ? (1.0 - r2) * (1.0 - r2)            // soft bokeh disc
        : exp(-r2 * 7.0);                    // sharp spark
      outColor = vec4(vE * a, 0.0, 0.0, 1.0);
    }
  `;

  const QUAD_VS = /* glsl */`#version 300 es
    precision highp float;
    out vec2 vUv;
    void main() {
      vec2 p = vec2((gl_VertexID << 1) & 2, gl_VertexID & 2);
      vUv = p;
      gl_Position = vec4(p * 2.0 - 1.0, 0.0, 1.0);
    }
  `;

  const DOWN_FS = /* glsl */`#version 300 es
    precision mediump float;
    uniform sampler2D uTex;
    uniform vec2 uTexel;
    in vec2 vUv;
    out vec4 outColor;
    void main() {
      float c = texture(uTex, vUv + uTexel * vec2(-1.0, -1.0)).r
              + texture(uTex, vUv + uTexel * vec2( 1.0, -1.0)).r
              + texture(uTex, vUv + uTexel * vec2(-1.0,  1.0)).r
              + texture(uTex, vUv + uTexel * vec2( 1.0,  1.0)).r;
      outColor = vec4(c * 0.25, 0.0, 0.0, 1.0);
    }
  `;

  // A 9-tap gaussian in 5 bilinear fetches.
  const BLUR_FS = /* glsl */`#version 300 es
    precision mediump float;
    uniform sampler2D uTex;
    uniform vec2 uDir;
    in vec2 vUv;
    out vec4 outColor;
    void main() {
      float c = texture(uTex, vUv).r * 0.2270270270;
      c += texture(uTex, vUv + uDir * 1.3846153846).r * 0.3162162162;
      c += texture(uTex, vUv - uDir * 1.3846153846).r * 0.3162162162;
      c += texture(uTex, vUv + uDir * 3.2307692308).r * 0.0702702703;
      c += texture(uTex, vUv - uDir * 3.2307692308).r * 0.0702702703;
      outColor = vec4(c, 0.0, 0.0, 1.0);
    }
  `;

  const COMPOSITE_FS = /* glsl */`#version 300 es
    precision highp float;
    uniform sampler2D uScene;
    uniform sampler2D uB1;
    uniform sampler2D uB2;
    uniform sampler2D uB3;
    uniform float uDecode;
    uniform float uIntensity;
    uniform float uFrame;
    in vec2 vUv;
    out vec4 outColor;
    void main() {
      float e = texture(uScene, vUv).r;
      float b = texture(uB1, vUv).r * 0.65
              + texture(uB2, vUv).r * 0.85
              + texture(uB3, vUv).r * 1.10;
      float E = (e + b) * uDecode * uIntensity;
      // Energy to colour: red rises first, then blue, then green, so a
      // brighter strand goes crimson -> hot pink -> white.
      vec3 col = 1.0 - exp(-E * vec3(1.45, 0.075, 0.2));
      col = mix(col, vec3(1.0, 0.9, 0.93), 1.0 - exp(-max(E - 1.25, 0.0) * 0.38));
      // Interleaved gradient noise: kills banding in the dark falloff.
      float n = fract(52.9829189 * fract(dot(gl_FragCoord.xy + uFrame * 5.588238, vec2(0.06711056, 0.00583715))));
      col += (n - 0.5) * (1.5 / 255.0);
      col += vec3(0.0118, 0.0118, 0.0157);   // the page's own black, #030304
      outColor = vec4(col, 1.0);
    }
  `;

  // --------------------------------------------------------------- helpers
  function compile(type, source) {
    const shader = gl.createShader(type);
    gl.shaderSource(shader, source);
    gl.compileShader(shader);
    if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
      const log = gl.getShaderInfoLog(shader);
      gl.deleteShader(shader);
      throw new Error(log || 'shader failed');
    }
    return shader;
  }

  function program(vs, fs, names) {
    const p = gl.createProgram();
    gl.attachShader(p, compile(gl.VERTEX_SHADER, vs));
    gl.attachShader(p, compile(gl.FRAGMENT_SHADER, fs));
    gl.linkProgram(p);
    if (!gl.getProgramParameter(p, gl.LINK_STATUS)) {
      throw new Error(gl.getProgramInfoLog(p) || 'link failed');
    }
    const u = {};
    for (const name of names) u[name] = gl.getUniformLocation(p, name);
    return { p, u };
  }

  // Seeded, so the ribbon is the same on every visit.
  function rng(seed) {
    let s = seed >>> 0;
    return () => {
      s = (s + 0x6D2B79F5) >>> 0;
      let t = s;
      t = Math.imul(t ^ (t >>> 15), t | 1);
      t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }
  function gauss(r) {
    return Math.sqrt(-2 * Math.log(Math.max(r(), 1e-6))) * Math.cos(6.2831853 * r());
  }

  // ------------------------------------------------------------- geometry
  const small = Math.min(screen.width, screen.height) < 700 || (navigator.hardwareConcurrency || 8) <= 4;
  const SHEETS = small ? 4 : 5;
  const FIBERS = small ? 36 : 52;
  const STRAYS = small ? 22 : 44;
  const HAZE = 3;

  function buildFibers() {
    const r = rng(7031);
    const data = [];
    const sheets = [];
    for (let j = 0; j < SHEETS; j++) {
      sheets.push({
        phase: j * 1.33 + r() * 0.6,
        radius: 0.03 + r() * 0.045,
        width: 0.07 + r() * 0.07,
        twist: 2.0 + r() * 1.6,
        depth: 0.55 + r() * 0.75,
      });
    }
    for (const s of sheets) {
      for (let i = 0; i < FIBERS; i++) {
        const slot = -1 + 2 * (i + 0.5) / FIBERS + (r() - 0.5) * (1.6 / FIBERS);
        data.push(
          s.phase, s.radius, s.width, s.twist,
          slot, r(), (0.13 + r() * 0.13) * s.depth, 1.0 + r() * 1.0,
          0, 6 + r() * 14, 0.0015 + r() * 0.006, r() * 0.07,
        );
      }
      for (let i = 0; i < HAZE; i++) {
        data.push(
          s.phase, s.radius, s.width, s.twist,
          (i - 1) * 0.45, r(), (0.034 + r() * 0.02) * s.depth, 1.6 + r() * 1.4,
          1, 1, 0, 0,
        );
      }
    }
    for (let i = 0; i < STRAYS; i++) {
      data.push(
        r() * 6.28, (0.04 + r() * 0.17) * (r() < 0.5 ? -1 : 1), 0, 1.8 + r() * 4.2,
        0, r(), 0.08 + r() * 0.14, 0.8 + r() * 0.8,
        2, 4 + r() * 8, 0.002 + r() * 0.004, r() * 0.05,
      );
    }
    return new Float32Array(data);
  }

  function buildDust() {
    const r = rng(1772);
    const data = [];
    const near = small ? 140 : 260;
    const bokeh = small ? 20 : 36;
    const stars = small ? 90 : 170;
    for (let i = 0; i < near; i++) {
      const bright = r() < 0.12;
      data.push(
        r() * 2.6, gauss(r) * 0.095, r(), bright ? 2.6 + r() * 2.2 : 1.4 + r() * 1.6,
        0.035 + r() * 0.09, 0, bright ? 2.4 + r() * 3.0 : 0.45 + r() * 1.0, 0.6 + r() * 2.2,
      );
    }
    for (let i = 0; i < bokeh; i++) {
      data.push(
        r() * 2.6, 0.06 + r() * 0.88, r(), 4 + r() * 10,
        0.006 + r() * 0.018, 1, 0.12 + r() * 0.3, 0.3 + r() * 0.8,
      );
    }
    for (let i = 0; i < stars; i++) {
      data.push(
        r() * 2.6, r(), r(), 0.9 + r() * 1.1,
        0.002 + r() * 0.004, 2, 0.2 + r() * 0.9, 0.5 + r() * 2.5,
      );
    }
    return new Float32Array(data);
  }

  // ---------------------------------------------------------- GL objects
  let fiberProg, dustProg, downProg, blurProg, compProg;
  let fiberVao, dustVao, quadVao;
  let fiberCount = 0, dustCount = 0, indexCount = 0;
  let hdr = true;
  let targets = null;

  const FIELD_UNIFORMS = ['uT', 'uBase', 'uAmp', 'uSpan', 'uRes', 'uDpr', 'uReveal', 'uEncode', 'uPointer'];

  function init() {
    fiberProg = program(FIBER_VS, FIBER_FS, FIELD_UNIFORMS);
    dustProg = program(DUST_VS, DUST_FS, FIELD_UNIFORMS);
    downProg = program(QUAD_VS, DOWN_FS, ['uTex', 'uTexel']);
    blurProg = program(QUAD_VS, BLUR_FS, ['uTex', 'uDir']);
    compProg = program(QUAD_VS, COMPOSITE_FS, ['uScene', 'uB1', 'uB2', 'uB3', 'uDecode', 'uIntensity', 'uFrame']);

    // One fiber's strip: SEGMENTS+1 points, two vertices each.
    const corners = new Float32Array((SEGMENTS + 1) * 4);
    for (let i = 0; i <= SEGMENTS; i++) {
      const u = i / SEGMENTS;
      corners.set([u, -1, u, 1], i * 4);
    }
    const indices = new Uint16Array(SEGMENTS * 6);
    for (let i = 0; i < SEGMENTS; i++) {
      const a = i * 2;
      indices.set([a, a + 1, a + 2, a + 1, a + 3, a + 2], i * 6);
    }
    indexCount = indices.length;

    const fibers = buildFibers();
    fiberCount = fibers.length / 12;
    fiberVao = gl.createVertexArray();
    gl.bindVertexArray(fiberVao);
    const cornerBuf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, cornerBuf);
    gl.bufferData(gl.ARRAY_BUFFER, corners, gl.STATIC_DRAW);
    gl.enableVertexAttribArray(0);
    gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 8, 0);
    const instBuf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, instBuf);
    gl.bufferData(gl.ARRAY_BUFFER, fibers, gl.STATIC_DRAW);
    for (let k = 0; k < 3; k++) {
      gl.enableVertexAttribArray(1 + k);
      gl.vertexAttribPointer(1 + k, 4, gl.FLOAT, false, 48, k * 16);
      gl.vertexAttribDivisor(1 + k, 1);
    }
    const ib = gl.createBuffer();
    gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, ib);
    gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, indices, gl.STATIC_DRAW);
    gl.bindVertexArray(null);

    const dust = buildDust();
    dustCount = dust.length / 8;
    dustVao = gl.createVertexArray();
    gl.bindVertexArray(dustVao);
    const dustBuf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, dustBuf);
    gl.bufferData(gl.ARRAY_BUFFER, dust, gl.STATIC_DRAW);
    gl.enableVertexAttribArray(0);
    gl.vertexAttribPointer(0, 4, gl.FLOAT, false, 32, 0);
    gl.enableVertexAttribArray(1);
    gl.vertexAttribPointer(1, 4, gl.FLOAT, false, 32, 16);
    gl.bindVertexArray(null);

    quadVao = gl.createVertexArray();

    hdr = !!(gl.getExtension('EXT_color_buffer_float') || gl.getExtension('EXT_color_buffer_half_float'));
    gl.disable(gl.DEPTH_TEST);
    gl.disable(gl.CULL_FACE);
  }

  function makeTarget(w, h) {
    const tex = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, tex);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    if (hdr) gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA16F, w, h, 0, gl.RGBA, gl.HALF_FLOAT, null);
    else gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA8, w, h, 0, gl.RGBA, gl.UNSIGNED_BYTE, null);
    const fb = gl.createFramebuffer();
    gl.bindFramebuffer(gl.FRAMEBUFFER, fb);
    gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, tex, 0);
    const ok = gl.checkFramebufferStatus(gl.FRAMEBUFFER) === gl.FRAMEBUFFER_COMPLETE;
    gl.bindFramebuffer(gl.FRAMEBUFFER, null);
    return { tex, fb, w, h, ok };
  }

  function freeTargets() {
    if (!targets) return;
    for (const t of Object.values(targets)) {
      gl.deleteTexture(t.tex);
      gl.deleteFramebuffer(t.fb);
    }
    targets = null;
  }

  function buildTargets(w, h) {
    freeTargets();
    const make = () => {
      const s = (d) => [Math.max(1, Math.round(w / d)), Math.max(1, Math.round(h / d))];
      return {
        scene: makeTarget(w, h),
        a1: makeTarget(...s(4)), b1: makeTarget(...s(4)),
        a2: makeTarget(...s(8)), b2: makeTarget(...s(8)),
        a3: makeTarget(...s(16)), b3: makeTarget(...s(16)),
      };
    };
    targets = make();
    if (hdr && !Object.values(targets).every((t) => t.ok)) {
      hdr = false;           // half-float targets refused: fall back to 8-bit
      freeTargets();
      targets = make();
    }
  }

  // ------------------------------------------------------------- sizing
  let quality = 1;
  let dpr = 1;
  let cssW = 1, cssH = 1;

  function resize() {
    const rect = canvas.getBoundingClientRect();
    cssW = Math.max(1, rect.width);
    cssH = Math.max(1, rect.height);
    dpr = Math.min(window.devicePixelRatio || 1, 1.75);
    let scale = dpr * quality;
    const budget = 2400000;
    if (cssW * cssH * scale * scale > budget) scale = Math.sqrt(budget / (cssW * cssH));
    const w = Math.max(2, Math.round(cssW * scale));
    const h = Math.max(2, Math.round(cssH * scale));
    if (canvas.width !== w || canvas.height !== h || !targets) {
      canvas.width = w;
      canvas.height = h;
      buildTargets(w, h);
    }
    dpr = scale;
    dirty = true;
  }

  // -------------------------------------------------------------- state
  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
  let time = START_TIME;
  let reveal = 0;
  let paused = reduceMotion.matches;
  let hidden = document.hidden;
  let dirty = true;
  let frame = 0;
  let raf = 0;
  let last = 0;
  let avg = 16.7;
  let slowFor = 0, fastFor = 0;
  let lost = false;

  const view = { base: 0.40, intensity: 1, tBase: 0.40, tIntensity: 1 };
  const pointer = { q: 0, y: 0, s: 0, tq: 0, ty: 0, ts: 0 };

  if (paused) reveal = 1;

  function span() {
    const aspect = cssW / cssH;
    return Math.min(Math.max(aspect, 1.15), 2.5) * 1.07;
  }
  function amp() {
    const aspect = cssW / cssH;
    return aspect < 1 ? 0.62 + 0.38 * aspect : 1;
  }

  function setFieldUniforms(u) {
    gl.uniform1f(u.uT, time * SPEED.body);
    gl.uniform1f(u.uBase, view.base);
    gl.uniform1f(u.uAmp, amp());
    gl.uniform1f(u.uSpan, span());
    gl.uniform2f(u.uRes, canvas.width, canvas.height);
    gl.uniform1f(u.uDpr, dpr);
    gl.uniform1f(u.uReveal, easeReveal(reveal));
    gl.uniform1f(u.uEncode, hdr ? 1 : 0.25);
    gl.uniform4f(u.uPointer, pointer.q, pointer.y, pointer.s, 0);
  }

  function easeReveal(x) {
    return x < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2;
  }

  function pass(target, prog, setup) {
    gl.bindFramebuffer(gl.FRAMEBUFFER, target ? target.fb : null);
    gl.viewport(0, 0, target ? target.w : canvas.width, target ? target.h : canvas.height);
    gl.useProgram(prog.p);
    setup(prog.u);
    gl.bindVertexArray(quadVao);
    gl.drawArrays(gl.TRIANGLES, 0, 3);
  }

  function bindTex(unit, tex) {
    gl.activeTexture(gl.TEXTURE0 + unit);
    gl.bindTexture(gl.TEXTURE_2D, tex);
  }

  function blurLevel(src, a, b, radius) {
    pass(a, downProg, (u) => {
      bindTex(0, src.tex);
      gl.uniform1i(u.uTex, 0);
      gl.uniform2f(u.uTexel, 1 / src.w, 1 / src.h);
    });
    for (let i = 0; i < 2; i++) {
      pass(b, blurProg, (u) => {
        bindTex(0, a.tex);
        gl.uniform1i(u.uTex, 0);
        gl.uniform2f(u.uDir, radius / a.w, 0);
      });
      pass(a, blurProg, (u) => {
        bindTex(0, b.tex);
        gl.uniform1i(u.uTex, 0);
        gl.uniform2f(u.uDir, 0, radius / a.h);
      });
    }
  }

  function render() {
    if (lost || !targets) return;
    const t = targets;

    // 1. Energy: fibers and dust, added together.
    gl.bindFramebuffer(gl.FRAMEBUFFER, t.scene.fb);
    gl.viewport(0, 0, t.scene.w, t.scene.h);
    gl.clearColor(0, 0, 0, 1);
    gl.clear(gl.COLOR_BUFFER_BIT);
    if (view.intensity > 0.002) {
      gl.enable(gl.BLEND);
      gl.blendFunc(gl.ONE, gl.ONE);
      gl.useProgram(fiberProg.p);
      setFieldUniforms(fiberProg.u);
      gl.bindVertexArray(fiberVao);
      gl.drawElementsInstanced(gl.TRIANGLES, indexCount, gl.UNSIGNED_SHORT, 0, fiberCount);
      gl.useProgram(dustProg.p);
      setFieldUniforms(dustProg.u);
      gl.bindVertexArray(dustVao);
      gl.drawArrays(gl.POINTS, 0, dustCount);
      gl.disable(gl.BLEND);
    }

    // 2. Bloom at a quarter, an eighth and a sixteenth of the size.
    blurLevel(t.scene, t.a1, t.b1, 1.0);
    blurLevel(t.a1, t.a2, t.b2, 1.25);
    blurLevel(t.a2, t.a3, t.b3, 1.5);

    // 3. Colour.
    pass(null, compProg, (u) => {
      bindTex(0, t.scene.tex);
      bindTex(1, t.a1.tex);
      bindTex(2, t.a2.tex);
      bindTex(3, t.a3.tex);
      gl.uniform1i(u.uScene, 0);
      gl.uniform1i(u.uB1, 1);
      gl.uniform1i(u.uB2, 2);
      gl.uniform1i(u.uB3, 3);
      gl.uniform1f(u.uDecode, hdr ? 1 : 4);
      gl.uniform1f(u.uIntensity, view.intensity);
      gl.uniform1f(u.uFrame, frame % 64);
    });
    gl.bindVertexArray(null);
    frame++;
    dirty = false;
  }

  // Exponential approach: frame-rate independent smoothing.
  function approach(cur, target, dt, tau) {
    return target + (cur - target) * Math.exp(-dt / tau);
  }

  function tick(now) {
    raf = 0;
    if (hidden || lost) return;
    const dt = last ? Math.min((now - last) / 1000, 0.1) : 1 / 60;
    last = now;

    if (!paused) {
      time += dt;
      if (reveal < 1) reveal = Math.min(1, reveal + dt / REVEAL_SECONDS);
    }

    const before = view.base + view.intensity + pointer.s;
    view.base = approach(view.base, view.tBase, dt, 0.22);
    view.intensity = approach(view.intensity, view.tIntensity, dt, 0.22);
    pointer.q = approach(pointer.q, pointer.tq, dt, 0.12);
    pointer.y = approach(pointer.y, pointer.ty, dt, 0.12);
    pointer.s = approach(pointer.s, pointer.ts, dt, 0.35);
    const settling = Math.abs(view.base + view.intensity + pointer.s - before) > 1e-5;

    const visibleNow = view.intensity > 0.002 || view.tIntensity > 0.002;
    if ((!paused && visibleNow) || settling || dirty) {
      render();
      adapt(dt);
    }

    // Keep running while anything moves; sleep otherwise.
    if ((!paused && visibleNow) || settling) schedule();
  }

  // Lower the resolution a little if frames are being dropped.
  function adapt(dt) {
    if (paused) return;
    avg = avg * 0.92 + dt * 1000 * 0.08;
    if (avg > 26) { slowFor += dt; fastFor = 0; }
    else if (avg < 14) { fastFor += dt; slowFor = 0; }
    else { slowFor = 0; fastFor = 0; }
    if (slowFor > 1.2 && quality > 0.5) {
      quality = Math.max(0.5, quality * 0.82);
      slowFor = 0;
      resize();
    } else if (fastFor > 6 && quality < 1) {
      quality = Math.min(1, quality * 1.12);
      fastFor = 0;
      resize();
    }
  }

  function schedule() {
    if (!raf && !hidden && !lost) raf = requestAnimationFrame(tick);
  }

  // -------------------------------------------------------------- events
  document.addEventListener('visibilitychange', () => {
    hidden = document.hidden;
    last = 0;
    if (!hidden) { dirty = true; schedule(); }
  });

  if (window.ResizeObserver) {
    new ResizeObserver(() => { resize(); schedule(); }).observe(canvas);
  } else {
    window.addEventListener('resize', () => { resize(); schedule(); });
  }

  const finePointer = window.matchMedia('(pointer: fine)');
  window.addEventListener('pointermove', (e) => {
    if (!finePointer.matches || paused) return;
    pointer.tq = Q0 + (e.clientX / cssW) * span();
    pointer.ty = 1 - e.clientY / cssH;
    if (pointer.ts === 0) { pointer.q = pointer.tq; pointer.y = pointer.ty; }
    pointer.ts = 1;
    schedule();
  }, { passive: true });
  document.documentElement.addEventListener('pointerleave', () => { pointer.ts = 0; schedule(); });

  canvas.addEventListener('webglcontextlost', (e) => {
    e.preventDefault();
    lost = true;
    if (raf) cancelAnimationFrame(raf);
    raf = 0;
  });
  canvas.addEventListener('webglcontextrestored', () => {
    lost = false;
    targets = null;
    try { init(); resize(); dirty = true; schedule(); } catch (_) { root.classList.add('no-webgl'); }
  });

  // ------------------------------------------------------------------ go
  try {
    init();
    resize();
  } catch (err) {
    root.classList.add('no-webgl');
    if (window.console) console.warn('Stellar silk unavailable:', err.message);
    return;
  }

  api.ready = true;
  api.set = (o) => {
    if (typeof o.base === 'number') view.tBase = o.base;
    if (typeof o.intensity === 'number') view.tIntensity = o.intensity;
    if (o.immediate) { view.base = view.tBase; view.intensity = view.tIntensity; dirty = true; }
    schedule();
  };
  api.setPaused = (p) => {
    paused = !!p;
    if (paused) { pointer.ts = 0; reveal = 1; }
    last = 0;
    dirty = true;
    schedule();
  };
  Object.defineProperty(api, 'paused', { get: () => paused });

  render();
  root.classList.add('silk-on');
  schedule();
})();
