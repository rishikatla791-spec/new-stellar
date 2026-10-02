/* Stellar silk: procedural ribbons of light that flow left to right.
 *
 * Nothing is sampled from an image. Five silk ribbons wind around one
 * travelling path, on two depth layers:
 *
 *   near  drawn at full resolution, sharp, bright, moving at full speed
 *   far   drawn at half resolution and blurred (out of focus), dimmer,
 *         flatter and slower, so the scene has parallax
 *
 * Each ribbon is a twisting sheet. Its "membrane" is a translucent surface
 * with a streaky noise texture that streams right; where the sheet turns
 * edge-on it is compressed into a bright fold, and its edges are rim-lit.
 * Fine fibers with broken, grainy light run inside it, stray filaments
 * peel off, and glitter rides along the stream.
 *
 * Every wave is a function of (q - speed * t), so the body, the folds, the
 * texture and the light pulses all travel right, each at its own steady
 * speed.
 *
 * Pipeline: everything is drawn additively as "energy" into half-float
 * buffers. Four blurred copies make the glow; the widest is broken up by a
 * domain-warped noise field into smoke. A final pass turns energy into
 * colour (crimson -> hot pink -> warm white) and adds film grain.
 *
 * The page talks to it through window.stellarSilk; see the bottom.
 */
(() => {
  'use strict';

  const canvas = document.getElementById('silk');
  if (!canvas) return;
  const root = document.documentElement;

  const SEGMENTS = 220;          // points along every strip
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
  const COMMON = /* glsl */`
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
    const float MARGIN = 0.16;      // strips start and end off-canvas
    const float TAU = 6.2831853;

    // The shared path every ribbon winds around. Four waves, all moving
    // right, at speeds that differ just enough to keep the shape changing.
    float spine(float q, float t) {
      return 0.068 * sin(3.1  * (q - 0.046 * t) + 0.9)
           + 0.040 * sin(5.6  * (q - 0.064 * t) + 2.4)
           + 0.020 * sin(7.6  * (q - 0.078 * t) + 1.3)
           + 0.022 * sin(10.1 * (q - 0.090 * t) + 4.2)
           + 0.034 * sin(1.55 * (q - 0.031 * t) + 5.1);
    }

    // Stretches where the light runs hot, shared by every ribbon and
    // drifting right with the body.
    float zone(float q) {
      float a = 0.5 + 0.5 * sin(4.0 * (q - 0.07 * uT) + 0.4);
      float b = 0.5 + 0.5 * sin(1.6 * (q - 0.05 * uT) + 2.0);
      return 0.2 + 1.9 * a * a * (0.4 + 0.6 * b);
    }

    vec2 toPx(float q, float y) {
      return vec2((q - Q0) / uSpan * uRes.x, y * uRes.y);
    }

    float revealFront() { return mix(Q0 - 0.35, Q0 + uSpan + 0.6, uReveal); }
    float revealMask(float q) {
      float f = revealFront();
      return 1.0 - smoothstep(f - 0.42, f, q);
    }
    float revealHead(float q) {
      float d = (q - revealFront() + 0.08) / 0.07;
      return exp(-d * d) * (1.0 - smoothstep(0.82, 1.0, uReveal));
    }
    float edgeFade(float q) {
      return smoothstep(Q0 - MARGIN, Q0 - MARGIN * 0.4, q)
           * (1.0 - smoothstep(Q0 + uSpan + MARGIN * 0.4, Q0 + uSpan + MARGIN, q));
    }

    float hash11(float p) {
      p = fract(p * 0.1031);
      p *= p + 33.33;
      p *= p + p;
      return fract(p);
    }
    float noise1(float x) {
      float i = floor(x), f = fract(x);
      return mix(hash11(i), hash11(i + 1.0), f * f * (3.0 - 2.0 * f));
    }
  `;

  // Per-ribbon shape. Needs aRibA / aRibB declared first.
  const RIBBON = /* glsl */`
    // aRibA: offset from the path, deviation amplitude, phase, frequency
    // aRibB: sheet width, twist rate, twist phase, depth (0 near .. 1 far)
    float rspeed() { return mix(1.0, 0.68, aRibB.w); }

    float ribbonCentre(float q) {
      float t = uT * rspeed();
      float f = aRibA.w, p = aRibA.z;
      float dev = 0.044 * sin(2.3 * f * (q - 0.050 * t) + p)
                + 0.022 * sin(4.7 * f * (q - 0.075 * t) + p * 1.9 + 1.1)
                + 0.011 * sin(8.3 * f * (q - 0.100 * t) + p * 2.7 + 2.0);
      return spine(q, t) * mix(1.0, 0.78, aRibB.w)
           + aRibA.x * (0.8 + 0.2 * sin(1.3 * q + p))
           + aRibA.y * dev;
    }
    float twist(float q) {
      float t = uT * rspeed();
      return aRibB.y * (q - 0.115 * t) + aRibB.z
           + 1.15 * sin(2.05 * q - 0.052 * t + aRibB.z * 1.7);
    }
    float envelope(float q) {
      float t = uT * rspeed();
      return 0.70 + 0.5 * sin(2.25 * (q - 0.052 * t) + aRibB.z * 2.3)
                  * (0.7 + 0.3 * sin(1.1 * q - 0.03 * t));
    }
    // x: centre line, y: width across the sheet, z: compression (1 = flat on)
    vec3 sheet(float q) {
      float th = twist(q);
      float env = envelope(q);
      float spread = aRibB.x * env * (0.14 + 0.86 * abs(cos(th)));
      float centre = ribbonCentre(q) + 0.22 * aRibB.x * env * sin(th);
      return vec3(centre, spread, clamp(aRibB.x * env / max(spread, 1e-4), 1.0, 7.2));
    }
    float pointerPush(float q, float y) {
      float d = (q - uPointer.x) / 0.16;
      float dy = y - uPointer.y;
      return sign(dy) * 0.028 * uPointer.z * exp(-d * d) * exp(-dy * dy / 0.006);
    }
    float pointerGlow(float q, float y) {
      float d = (q - uPointer.x) / 0.14;
      float dy = (y - uPointer.y) / 0.09;
      return 1.0 + 0.9 * uPointer.z * exp(-d * d - dy * dy);
    }
  `;

  // The translucent surface of a ribbon, one strip from edge to edge.
  const MEMBRANE_VS = /* glsl */`#version 300 es
    precision highp float;
    layout(location = 0) in vec2 aCorner;   // u along, side -1/+1 (edge)
    layout(location = 1) in vec4 aRibA;
    layout(location = 2) in vec4 aRibB;
    layout(location = 3) in vec4 aMem;      // gain, seed, edge padding, unused
    ${COMMON}
    ${RIBBON}
    out vec2 vQS;
    out float vComp;
    out float vE;
    flat out float vSeed;
    void main() {
      float q = Q0 - MARGIN + aCorner.x * (uSpan + 2.0 * MARGIN);
      vec3 sh = sheet(q);
      float s = aCorner.y * aMem.z;
      float y = uBase + uAmp * (sh.x + s * sh.y * 0.5);
      y += pointerPush(q, y);
      vQS = vec2(q, s);
      vComp = sh.z;
      vSeed = aMem.y;
      vE = aMem.x * zone(q) * revealMask(q) * edgeFade(q) * pointerGlow(q, y) * uEncode;
      vec2 p = toPx(q, y);
      gl_Position = vec4(p / uRes * 2.0 - 1.0, 0.0, 1.0);
    }
  `;

  const MEMBRANE_FS = /* glsl */`#version 300 es
    precision highp float;
    uniform float uT;
    in vec2 vQS;
    in float vComp;
    in float vE;
    flat in float vSeed;
    out vec4 outColor;

    float h21(vec2 p) {
      p = fract(p * vec2(123.34, 456.21));
      p += dot(p, p + 45.32);
      return fract(p.x * p.y);
    }
    float vn(vec2 p) {
      vec2 i = floor(p), f = fract(p);
      vec2 u = f * f * (3.0 - 2.0 * f);
      return mix(mix(h21(i), h21(i + vec2(1.0, 0.0)), u.x),
                 mix(h21(i + vec2(0.0, 1.0)), h21(i + vec2(1.0, 1.0)), u.x), u.y);
    }
    float fbm(vec2 p) {
      float a = 0.5, s = 0.0;
      for (int i = 0; i < 4; i++) {
        s += a * vn(p);
        p = p * 2.03 + vec2(17.1, 9.2);
        a *= 0.5;
      }
      return s;
    }

    void main() {
      float s = vQS.y;
      float as = abs(s);
      // Soft falloff just past the edge, and a rim of light along it.
      float body = 1.0 - smoothstep(0.8, 1.08, as);
      float rim = smoothstep(0.72, 0.98, as) * (1.0 - smoothstep(0.98, 1.08, as));
      // Silky striations: long along the flow, fine across it, streaming
      // right with the light.
      float q = vQS.x - uT * 0.17;
      float wave = sin(vQS.x * 3.1 + vSeed * 4.0) * 1.3;
      float streak = fbm(vec2(q * 2.1, s * 8.0 + vSeed * 31.0 + wave));
      float fine = vn(vec2(q * 10.0, s * 34.0 + vSeed * 7.0 + wave * 2.0));
      float tex = 0.04 + 2.6 * streak * streak * streak + 0.5 * fine * streak * streak;
      float e = vE * pow(vComp, 0.95) * (body * tex + rim * 2.6 * (0.4 + streak));
      outColor = vec4(e, 0.0, 0.0, 1.0);
    }
  `;

  // Fine fibers inside the ribbons, and stray filaments that peel away.
  const FIBER_VS = /* glsl */`#version 300 es
    precision highp float;
    layout(location = 0) in vec2 aCorner;   // u along, side -1/+1
    layout(location = 1) in vec4 aRibA;
    layout(location = 2) in vec4 aRibB;
    layout(location = 3) in vec4 aFiber;    // slot (or arc for strays), seed, gain, width px
    layout(location = 4) in vec4 aExtra;    // kind (0 fiber, 1 rim, 2 stray), wobble freq, wobble amp, speed jitter
    ${COMMON}
    ${RIBBON}
    out float vSide;
    out float vE;

    vec2 fiberY(float q) {
      float t = uT * rspeed();
      float wob = sin(aExtra.y * (q - (0.15 + aExtra.w) * t) + aFiber.y * TAU);
      if (aExtra.x > 1.5) {
        float arc = sin(aRibB.y * 1.3 * (q - 0.085 * t) + aFiber.y * TAU)
                  * (0.55 + 0.45 * sin(1.3 * q - 0.04 * t + aFiber.y * 6.0));
        return vec2(ribbonCentre(q) + aFiber.x * arc + aExtra.z * 2.5 * wob, 1.0);
      }
      vec3 sh = sheet(q);
      float loose = 1.0 / sh.z;            // fibers wander more where the sheet fans out
      return vec2(sh.x + aFiber.x * sh.y * 0.5 + (0.0012 + aExtra.z * (0.2 + loose)) * wob, sh.z);
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

      float t = uT * rspeed();
      // Pulses race right along every fiber; the light along a fiber is
      // broken and grainy rather than a clean line.
      float pulse = pow(0.5 + 0.5 * sin(2.3 * (q - 0.30 * t) + aFiber.y * 7.0 + aRibA.z), 6.0);
      float grain = 0.3 + 1.4 * noise1((q - 0.30 * t) * 24.0 + aFiber.y * 97.0)
                              * noise1((q - 0.12 * t) * 7.0 + aFiber.y * 31.0);
      float e = aFiber.z * pow(fC.y, 1.1) * zone(q) * (0.5 + 2.1 * pulse) * grain;
      e *= pointerGlow(q, yC);

      float width = aFiber.w * uDpr;
      float drawn = max(width, 1.35);
      e *= width / drawn;
      e *= edgeFade(q);
      e = e * revealMask(q) + (aExtra.x < 1.5 ? aFiber.z * 12.0 * revealHead(q) : 0.0);

      vSide = aCorner.y;
      vE = e * uEncode;
      vec2 p = pC + nrm * aCorner.y * drawn * 0.5;
      gl_Position = vec4(p / uRes * 2.0 - 1.0, 0.0, 1.0);
    }
  `;

  const FIBER_FS = /* glsl */`#version 300 es
    precision mediump float;
    in float vSide;
    in float vE;
    out vec4 outColor;
    void main() {
      float d = abs(vSide);
      outColor = vec4(vE * clamp(1.0 - d * d, 0.0, 1.0), 0.0, 0.0, 1.0);
    }
  `;

  // Glitter in the stream, soft bokeh in the distance, and stars.
  const DUST_VS = /* glsl */`#version 300 es
    precision highp float;
    layout(location = 0) in vec4 aP;   // q0, y offset (or sky y), seed, size px
    layout(location = 1) in vec4 aQ;   // speed, kind, energy, twinkle
    ${COMMON}
    out float vE;
    flat out float vKind;
    void main() {
      float kind = aQ.y;
      float span = uSpan + 0.3;
      float q = mod(aP.x + aQ.x * uT, span) + Q0 - 0.15;
      float y;
      if (kind < 0.5) {
        y = uBase + uAmp * (spine(q, uT) + aP.y);
      } else {
        y = aP.y + 0.006 * sin(uT * 0.21 + aP.z * 30.0);
      }
      // Sharp twinkle for glitter, slow breathing for the rest.
      float tw = kind < 0.5
        ? pow(0.5 + 0.5 * sin(uT * aQ.w * 2.2 + aP.z * 41.0), 3.0) * 1.6 + 0.15
        : 0.55 + 0.45 * sin(uT * aQ.w + aP.z * 41.0);
      float edge = smoothstep(Q0 - 0.12, Q0 + 0.04, q) * (1.0 - smoothstep(Q0 + uSpan - 0.04, Q0 + uSpan + 0.12, q));
      float lit = kind < 0.5 ? revealMask(q) * zone(q) : smoothstep(0.0, 0.6, uReveal);
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

  // Four bilinear taps of the sharp layer, plus the soft layer.
  const DOWN_FS = /* glsl */`#version 300 es
    precision mediump float;
    uniform sampler2D uTex;
    uniform sampler2D uTex2;
    uniform vec2 uTexel;
    uniform float uMix2;
    in vec2 vUv;
    out vec4 outColor;
    void main() {
      float c = texture(uTex, vUv + uTexel * vec2(-1.0, -1.0)).r
              + texture(uTex, vUv + uTexel * vec2( 1.0, -1.0)).r
              + texture(uTex, vUv + uTexel * vec2(-1.0,  1.0)).r
              + texture(uTex, vUv + uTexel * vec2( 1.0,  1.0)).r;
      outColor = vec4(c * 0.25 + texture(uTex2, vUv).r * uMix2, 0.0, 0.0, 1.0);
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

  // Smoke: the widest glow, broken up by domain-warped noise that drifts
  // right with the ribbons and slowly churns.
  const SMOKE_FS = /* glsl */`#version 300 es
    precision highp float;
    uniform sampler2D uD1;
    uniform sampler2D uD2;
    uniform float uT;
    uniform float uAspect;
    in vec2 vUv;
    out vec4 outColor;
    float h21(vec2 p) {
      p = fract(p * vec2(233.34, 851.73));
      p += dot(p, p + 23.45);
      return fract(p.x * p.y);
    }
    float vn(vec2 p) {
      vec2 i = floor(p), f = fract(p);
      vec2 u = f * f * (3.0 - 2.0 * f);
      return mix(mix(h21(i), h21(i + vec2(1.0, 0.0)), u.x),
                 mix(h21(i + vec2(0.0, 1.0)), h21(i + vec2(1.0, 1.0)), u.x), u.y);
    }
    float fbm(vec2 p) {
      float a = 0.5, s = 0.0;
      for (int i = 0; i < 5; i++) {
        s += a * vn(p);
        p = p * 2.02 + vec2(11.3, 5.7);
        a *= 0.5;
      }
      return s;
    }
    void main() {
      float d = texture(uD1, vUv).r * 0.7 + texture(uD2, vUv).r;
      vec2 p = vec2(vUv.x * uAspect, vUv.y) * 2.4;
      p.x -= uT * 0.05;
      vec2 w = vec2(fbm(p + vec2(0.0, uT * 0.03)), fbm(p + vec2(5.2, 1.3) - vec2(0.0, uT * 0.025)));
      float n = fbm(p * 1.7 + w * 2.1);
      float smoke = smoothstep(0.28, 0.82, n);
      float wisps = smoothstep(0.55, 0.9, fbm(p * 4.3 + w * 3.0 - vec2(uT * 0.08, 0.0)));
      outColor = vec4(d * (0.02 + 2.3 * smoke * smoke * smoke + 1.1 * wisps), 0.0, 0.0, 1.0);
    }
  `;

  const COMPOSITE_FS = /* glsl */`#version 300 es
    precision highp float;
    uniform sampler2D uScene;
    uniform sampler2D uSoft;
    uniform sampler2D uB1;
    uniform sampler2D uB2;
    uniform sampler2D uB3;
    uniform sampler2D uB4;
    uniform sampler2D uSmoke;
    uniform float uDecode;
    uniform float uIntensity;
    uniform float uFrame;
    in vec2 vUv;
    out vec4 outColor;
    float h21(vec2 p) {
      vec3 p3 = fract(vec3(p.xyx) * 0.1031);
      p3 += dot(p3, p3.yzx + 33.33);
      return fract((p3.x + p3.y) * p3.z);
    }
    void main() {
      float e = texture(uScene, vUv).r + texture(uSoft, vUv).r;
      float glow = texture(uB1, vUv).r * 0.50
                 + texture(uB2, vUv).r * 0.50
                 + texture(uB3, vUv).r * 0.34
                 + texture(uB4, vUv).r * 0.22;
      float smoke = texture(uSmoke, vUv).r;
      float E = (e + glow + smoke * 0.42) * uDecode * uIntensity;
      // Energy to colour: red rises first, so faint light is crimson; the
      // hottest lines bleach to a warm white.
      vec3 col = 1.0 - exp(-E * vec3(1.45, 0.075, 0.2));
      col = mix(col, vec3(1.0, 0.9, 0.93), 1.0 - exp(-max(E - 1.2, 0.0) * 0.36));
      // Film grain, stronger in the light, fresh every frame.
      float g = h21(gl_FragCoord.xy + uFrame * vec2(37.0, 17.0)) - 0.5;
      float lum = dot(col, vec3(0.45, 0.35, 0.2));
      col += g * (0.012 + 0.11 * lum * (1.0 - lum * 0.6));
      col += vec3(0.0118, 0.0118, 0.0157);   // the page's own black, #030304
      outColor = vec4(max(col, 0.0), 1.0);
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

  // Seeded, so the ribbons are the same on every visit.
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
  const density = small ? 0.65 : 1;

  // yo, dev amp, phase, freq | width, twist, twist phase, depth | gain
  const RIBBONS = [
    { a: [0.000, 1.00, 0.4, 1.00], b: [0.120, 3.0, 0.3, 0.00], gain: 1.25, fibers: 44 },
    { a: [0.050, 1.40, 2.1, 1.15], b: [0.085, 3.8, 2.2, 0.15], gain: 1.05, fibers: 32 },
    { a: [-0.045, 1.20, 1.2, 1.30], b: [0.075, 4.4, 3.7, 0.30], gain: 1.00, fibers: 28 },
    { a: [0.020, 1.60, 5.9, 1.45], b: [0.060, 5.0, 0.9, 0.40], gain: 0.90, fibers: 22 },
    { a: [-0.075, 0.90, 4.0, 0.90], b: [0.130, 2.4, 4.4, 0.70], gain: 0.75, fibers: 26 },
    { a: [0.090, 1.00, 5.3, 1.05], b: [0.100, 3.1, 1.1, 0.85], gain: 0.60, fibers: 22 },
    { a: [-0.012, 1.15, 3.1, 0.80], b: [0.210, 1.6, 5.6, 1.00], gain: 0.30, fibers: 14 },
  ];
  const isFar = (rb) => rb.b[3] > 0.5;

  function buildMembranes(far) {
    const data = [];
    RIBBONS.forEach((rb, i) => {
      if (isFar(rb) !== far) return;
      data.push(...rb.a, ...rb.b, (far ? 0.20 : 0.30) * rb.gain, i * 1.618 + 0.3, 1.08, 0);
    });
    return new Float32Array(data);
  }

  function buildFibers(far) {
    const r = rng(far ? 4409 : 7031);
    const data = [];
    const push = (rb, fiber, extra) => data.push(...rb.a, ...rb.b, ...fiber, ...extra);
    for (const rb of RIBBONS) {
      if (isFar(rb) !== far) continue;
      const n = Math.round(rb.fibers * density);
      for (let i = 0; i < n; i++) {
        const slot = -1 + 2 * (i + 0.5) / n + (r() - 0.5) * (1.6 / n);
        push(rb, [slot, r(), (0.11 + r() * 0.13) * rb.gain, 0.9 + r() * 0.9],
                 [0, 6 + r() * 14, 0.0012 + r() * 0.005, r() * 0.07]);
      }
      // Rim lines: the bright edges that catch the light where it folds.
      for (const side of [-1, 1]) {
        push(rb, [side * 0.97, r(), 0.34 * rb.gain, 1.5], [1, 5 + r() * 4, 0.0008, 0]);
      }
    }
    if (!far) {
      const strays = Math.round(30 * density);
      for (let i = 0; i < strays; i++) {
        const rb = RIBBONS[i % 2];
        push(rb, [(0.05 + r() * 0.17) * (r() < 0.5 ? -1 : 1), r(), 0.07 + r() * 0.12, 0.7 + r() * 0.8],
                 [2, 4 + r() * 8, 0.002 + r() * 0.004, r() * 0.05]);
      }
    }
    return new Float32Array(data);
  }

  function buildDust() {
    const r = rng(1772);
    const data = [];
    const glitter = Math.round(1600 * density);
    const bokeh = Math.round(36 * density);
    const stars = Math.round(170 * density);
    for (let i = 0; i < glitter; i++) {
      const bright = r() < 0.08;
      data.push(
        r() * 2.6, gauss(r) * 0.12, r(), bright ? 2.4 + r() * 2.4 : 1.3 + r() * 1.4,
        0.04 + r() * 0.1, 0, bright ? 3.5 + r() * 4.0 : 0.8 + r() * 1.4, 0.5 + r() * 2.5,
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
  let memProg, fiberProg, dustProg, downProg, blurProg, smokeProg, compProg;
  const layers = { near: {}, far: {} };
  let dustVao, quadVao, dustCount = 0, indexCount = 0;
  let hdr = true;
  let targets = null;

  const FIELD_UNIFORMS = ['uT', 'uBase', 'uAmp', 'uSpan', 'uRes', 'uDpr', 'uReveal', 'uEncode', 'uPointer'];

  function stripVao(cornerBuf, indexBuf, instances, stride, attribs) {
    const vao = gl.createVertexArray();
    gl.bindVertexArray(vao);
    gl.bindBuffer(gl.ARRAY_BUFFER, cornerBuf);
    gl.enableVertexAttribArray(0);
    gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 8, 0);
    const buf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buf);
    gl.bufferData(gl.ARRAY_BUFFER, instances, gl.STATIC_DRAW);
    for (let k = 0; k < attribs; k++) {
      gl.enableVertexAttribArray(1 + k);
      gl.vertexAttribPointer(1 + k, 4, gl.FLOAT, false, stride, k * 16);
      gl.vertexAttribDivisor(1 + k, 1);
    }
    gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, indexBuf);
    gl.bindVertexArray(null);
    return { vao, count: instances.length / (stride / 4) };
  }

  function init() {
    memProg = program(MEMBRANE_VS, MEMBRANE_FS, FIELD_UNIFORMS);
    fiberProg = program(FIBER_VS, FIBER_FS, FIELD_UNIFORMS);
    dustProg = program(DUST_VS, DUST_FS, FIELD_UNIFORMS);
    downProg = program(QUAD_VS, DOWN_FS, ['uTex', 'uTex2', 'uTexel', 'uMix2']);
    blurProg = program(QUAD_VS, BLUR_FS, ['uTex', 'uDir']);
    smokeProg = program(QUAD_VS, SMOKE_FS, ['uD1', 'uD2', 'uT', 'uAspect']);
    compProg = program(QUAD_VS, COMPOSITE_FS,
      ['uScene', 'uSoft', 'uB1', 'uB2', 'uB3', 'uB4', 'uSmoke', 'uDecode', 'uIntensity', 'uFrame']);

    // One strip: SEGMENTS+1 points, two vertices each.
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
    const cornerBuf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, cornerBuf);
    gl.bufferData(gl.ARRAY_BUFFER, corners, gl.STATIC_DRAW);
    const indexBuf = gl.createBuffer();
    gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, indexBuf);
    gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, indices, gl.STATIC_DRAW);

    for (const name of ['near', 'far']) {
      const far = name === 'far';
      layers[name].mem = stripVao(cornerBuf, indexBuf, buildMembranes(far), 48, 3);
      layers[name].fib = stripVao(cornerBuf, indexBuf, buildFibers(far), 64, 4);
    }

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
        soft: makeTarget(...s(2)), soft2: makeTarget(...s(2)),
        a1: makeTarget(...s(4)), b1: makeTarget(...s(4)),
        a2: makeTarget(...s(8)), b2: makeTarget(...s(8)),
        a3: makeTarget(...s(16)), b3: makeTarget(...s(16)),
        a4: makeTarget(...s(32)), b4: makeTarget(...s(32)),
        smoke: makeTarget(...s(6)),
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

  const view = { base: 0.37, intensity: 1, tBase: 0.37, tIntensity: 1 };
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

  function easeReveal(x) {
    return x < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2;
  }

  function setFieldUniforms(u, target, scale) {
    gl.uniform1f(u.uT, time);
    gl.uniform1f(u.uBase, view.base);
    gl.uniform1f(u.uAmp, amp());
    gl.uniform1f(u.uSpan, span());
    gl.uniform2f(u.uRes, target.w, target.h);
    gl.uniform1f(u.uDpr, dpr * scale);
    gl.uniform1f(u.uReveal, easeReveal(reveal));
    gl.uniform1f(u.uEncode, hdr ? 1 : 0.25);
    gl.uniform4f(u.uPointer, pointer.q, pointer.y, pointer.s, 0);
  }

  function bindTex(unit, tex) {
    gl.activeTexture(gl.TEXTURE0 + unit);
    gl.bindTexture(gl.TEXTURE_2D, tex);
  }

  function pass(target, prog, setup) {
    gl.bindFramebuffer(gl.FRAMEBUFFER, target ? target.fb : null);
    gl.viewport(0, 0, target ? target.w : canvas.width, target ? target.h : canvas.height);
    gl.useProgram(prog.p);
    setup(prog.u);
    gl.bindVertexArray(quadVao);
    gl.drawArrays(gl.TRIANGLES, 0, 3);
  }

  function blur(a, b, radius, times) {
    for (let i = 0; i < times; i++) {
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

  function down(src, dst, extra, mix) {
    pass(dst, downProg, (u) => {
      bindTex(0, src.tex);
      bindTex(1, extra ? extra.tex : src.tex);
      gl.uniform1i(u.uTex, 0);
      gl.uniform1i(u.uTex2, 1);
      gl.uniform2f(u.uTexel, 1 / src.w, 1 / src.h);
      gl.uniform1f(u.uMix2, extra ? mix : 0);
    });
  }

  function drawLayer(layer, target, scale) {
    gl.bindFramebuffer(gl.FRAMEBUFFER, target.fb);
    gl.viewport(0, 0, target.w, target.h);
    gl.clearColor(0, 0, 0, 1);
    gl.clear(gl.COLOR_BUFFER_BIT);
    if (view.intensity <= 0.002) return;
    gl.enable(gl.BLEND);
    gl.blendFunc(gl.ONE, gl.ONE);
    gl.useProgram(memProg.p);
    setFieldUniforms(memProg.u, target, scale);
    gl.bindVertexArray(layer.mem.vao);
    gl.drawElementsInstanced(gl.TRIANGLES, indexCount, gl.UNSIGNED_SHORT, 0, layer.mem.count);
    gl.useProgram(fiberProg.p);
    setFieldUniforms(fiberProg.u, target, scale);
    gl.bindVertexArray(layer.fib.vao);
    gl.drawElementsInstanced(gl.TRIANGLES, indexCount, gl.UNSIGNED_SHORT, 0, layer.fib.count);
    if (layer === layers.near) {
      gl.useProgram(dustProg.p);
      setFieldUniforms(dustProg.u, target, scale);
      gl.bindVertexArray(dustVao);
      gl.drawArrays(gl.POINTS, 0, dustCount);
    }
    gl.disable(gl.BLEND);
  }

  function render() {
    if (lost || !targets) return;
    const t = targets;

    // 1. Far layer at half size, then out of focus.
    drawLayer(layers.far, t.soft, 0.5);
    blur(t.soft, t.soft2, 1.1, 1);

    // 2. Near layer, sharp.
    drawLayer(layers.near, t.scene, 1);

    // 3. Glow at a quarter, an eighth, a sixteenth and a thirty-second.
    down(t.scene, t.a1, t.soft, 1.0);
    blur(t.a1, t.b1, 1.0, 2);
    down(t.a1, t.a2);
    blur(t.a2, t.b2, 1.25, 2);
    down(t.a2, t.a3);
    blur(t.a3, t.b3, 1.5, 2);
    down(t.a3, t.a4);
    blur(t.a4, t.b4, 1.5, 2);

    // 4. Smoke from the widest glow.
    pass(t.smoke, smokeProg, (u) => {
      bindTex(0, t.a3.tex);
      bindTex(1, t.a4.tex);
      gl.uniform1i(u.uD1, 0);
      gl.uniform1i(u.uD2, 1);
      gl.uniform1f(u.uT, time);
      gl.uniform1f(u.uAspect, cssW / cssH);
    });

    // 5. Colour.
    pass(null, compProg, (u) => {
      const texs = [t.scene, t.soft, t.a1, t.a2, t.a3, t.a4, t.smoke];
      const names = ['uScene', 'uSoft', 'uB1', 'uB2', 'uB3', 'uB4', 'uSmoke'];
      texs.forEach((tx, i) => { bindTex(i, tx.tex); gl.uniform1i(u[names[i]], i); });
      gl.uniform1f(u.uDecode, hdr ? 1 : 4);
      gl.uniform1f(u.uIntensity, view.intensity);
      gl.uniform1f(u.uFrame, frame % 997);
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
    view.base = approach(view.base, view.tBase, dt, 0.16);
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
