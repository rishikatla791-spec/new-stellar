/* Stellar silk: procedural ribbons of light that flow left to right.
 *
 * Nothing is sampled from an image. Six silk ribbons wind around one
 * travelling path, on two depth layers:
 *
 *   near  full resolution, sharp, bright, moving at full speed
 *   far   half resolution and blurred (out of focus), dimmer, flatter and
 *         slower, so the scene has parallax
 *
 * Each ribbon is drawn three ways:
 *
 *   veil    a smooth, translucent curtain from edge to edge, lit from the
 *           edge that faces the light and fading across; it swaps sides
 *           as the ribbon twists, and burns bright where it folds edge-on
 *   strands a handful of soft threads (a bright core in a glowing halo)
 *           that wander across the sheet, weave together and apart, and
 *           thicken, thin and fade along their length
 *   vanes   the feathered fringe of every strand: fine curved barbs that
 *           leave it at a shallow angle, lean forward, each with its own
 *           length, sitting on one side then the other, sliding right
 *   wisps   a few longer barbs that peel right away, curving as they fade
 *
 * Every wave is a function of (q - speed * t), so the body, the folds, the
 * texture, the wisps and the light pulses all travel right, each at its own
 * steady speed.
 *
 * Pipeline: everything is drawn additively as "energy" into half-float
 * buffers. Four blurred copies make the glow; the widest is broken up by
 * domain-warped noise into smoke. A final pass turns energy into colour
 * (crimson -> hot pink -> warm white) and adds film grain.
 *
 * The page talks to it through window.stellarSilk; see the bottom.
 */
(() => {
  'use strict';

  const canvas = document.getElementById('silk');
  if (!canvas) return;
  const root = document.documentElement;

  const SEGMENTS = 220;          // points along every veil and strand
  const BARB_SEGMENTS = 10;      // points along every wisp
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

    const float Q0 = ${Q0.toFixed(3)};
    const float MARGIN = 0.16;      // strips start and end off-canvas
    const float TAU = 6.2831853;

    // The shared path every ribbon winds around. Five waves, all moving
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
      return 0.2 + 2.4 * a * a * (0.4 + 0.6 * b);
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
  `;

  // Per-strand path. Needs aStrand declared first: slot, seed, gain, width.
  const STRAND = /* glsl */`
    // Where a strand sits across its sheet. It drifts, so strands weave
    // together and apart instead of running side by side.
    float strandSlot(float q) {
      float t = uT * rspeed();
      float sd = aStrand.y * 37.0;
      float drift = 0.62 * (noise1(q * 2.1 - t * 0.09 + sd) - 0.5)
                  + 0.16 * sin(q * 4.9 + sd * 1.7 - t * 0.13);
      return clamp(aStrand.x + drift, -1.1, 1.1);
    }
    float strandY(float q) {
      vec3 sh = sheet(q);
      float t = uT * rspeed();
      float wob = (noise1(q * 8.0 - t * 0.28 + aStrand.y * 53.0) - 0.5) * 0.005;
      return sh.x + strandSlot(q) * sh.y * 0.5 + wob;
    }
    // Strands come and go along their length.
    float strandLum(float q) {
      float t = uT * rspeed();
      return smoothstep(0.2, 0.66, noise1(q * 1.45 + aStrand.y * 29.0 - t * 0.05));
    }
  `;

  const VEIL_VS = /* glsl */`#version 300 es
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
    out float vLit;
    flat out float vSeed;
    void main() {
      float q = Q0 - MARGIN + aCorner.x * (uSpan + 2.0 * MARGIN);
      vec3 sh = sheet(q);
      float s = aCorner.y * aMem.z;
      float y = uBase + uAmp * (sh.x + s * sh.y * 0.5);
      // Veils thin out and thicken along the ribbon rather than running
      // unbroken from edge to edge.
      float t = uT * rspeed();
      float vis = 0.25 + 0.75 * smoothstep(0.18, 0.7, noise1(q * 1.1 + aMem.y * 7.0 - t * 0.045));
      vQS = vec2(q, s);
      vComp = sh.z;
      vSeed = aMem.y;
      vLit = cos(twist(q));
      vE = aMem.x * zone(q) * vis * revealMask(q) * edgeFade(q) * uEncode;
      gl_Position = vec4(toPx(q, y) / uRes * 2.0 - 1.0, 0.0, 1.0);
    }
  `;

  const VEIL_FS = /* glsl */`#version 300 es
    precision highp float;
    uniform float uT;
    in vec2 vQS;
    in float vComp;
    in float vE;
    in float vLit;
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
      for (int i = 0; i < 3; i++) {
        s += a * vn(p);
        p = p * 2.03 + vec2(17.1, 9.2);
        a *= 0.5;
      }
      return s;
    }

    void main() {
      float s = vQS.y;
      // Lit from the edge that faces the light (it swaps as the sheet
      // twists), fading across the curtain, with a soft bright rim.
      float w = 0.5 + 0.5 * clamp(vLit * 2.5, -1.0, 1.0);
      float gradient = mix(exp(-(1.0 + s) * 1.8), exp(-(1.0 - s) * 1.8), w);
      float rim = mix(exp(-pow((1.0 + s) * 6.5, 2.0)), exp(-pow((1.0 - s) * 6.5, 2.0)), w)
                * (1.0 - smoothstep(1.02, 1.2, abs(s)));
      float edge = 1.0 - smoothstep(0.86, 1.08, abs(s));
      // Gentle texture that streams right with the light.
      float q = vQS.x - uT * 0.17;
      float n = fbm(vec2(q * 1.7, s * 1.6 + vSeed * 13.0));
      float fine = vn(vec2(q * 7.0, s * 14.0 + vSeed * 5.0));
      float tex = 0.4 + 1.2 * n * n + 0.25 * fine;
      float e = vE * pow(vComp, 0.95) * (mix(0.12, 1.0, gradient) * tex * edge + rim * 1.1 * (0.6 + n));
      outColor = vec4(e, 0.0, 0.0, 1.0);
    }
  `;

  // Soft threads, and stray arcs that leave the ribbon and come back.
  const STRAND_VS = /* glsl */`#version 300 es
    precision highp float;
    layout(location = 0) in vec2 aCorner;   // u along, side -1/+1
    layout(location = 1) in vec4 aRibA;
    layout(location = 2) in vec4 aRibB;
    layout(location = 3) in vec4 aStrand;   // slot (arc for strays), seed, gain, half-width px
    layout(location = 4) in vec4 aExtra;    // kind (0 strand, 1 stray), core px, unused, unused
    ${COMMON}
    ${RIBBON}
    ${STRAND}
    out float vSide;
    out float vE;
    out float vCore;

    float pathY(float q) {
      if (aExtra.x > 0.5) {
        float t = uT * rspeed();
        float arc = sin(aRibB.y * 1.3 * (q - 0.085 * t) + aStrand.y * TAU)
                  * (0.55 + 0.45 * sin(1.3 * q - 0.04 * t + aStrand.y * 6.0));
        return ribbonCentre(q) + aStrand.x * arc
             + (noise1(q * 6.0 - t * 0.2 + aStrand.y * 40.0) - 0.5) * 0.01;
      }
      return strandY(q);
    }

    void main() {
      float u = aCorner.x;
      float q = Q0 - MARGIN + u * (uSpan + 2.0 * MARGIN);
      float h = (uSpan + 2.0 * MARGIN) / ${(SEGMENTS * 2).toFixed(1)};
      float yA = uBase + uAmp * pathY(q - h);
      float yC = uBase + uAmp * pathY(q);
      float yB = uBase + uAmp * pathY(q + h);
      vec2 pA = toPx(q - h, yA);
      vec2 pB = toPx(q + h, yB);
      vec2 pC = toPx(q, yC);
      vec2 tng = normalize(pB - pA + vec2(1e-5, 0.0));
      vec2 nrm = vec2(-tng.y, tng.x);

      bool stray = aExtra.x > 0.5;
      float lum = stray ? 0.35 + 0.65 * strandLum(q) : strandLum(q);
      float comp = stray ? 1.0 : sheet(q).z;
      float t = uT * rspeed();
      float pulse = pow(0.5 + 0.5 * sin(2.3 * (q - 0.30 * t) + aStrand.y * 7.0 + aRibA.z), 5.0);
      float fibre = 0.72 + 0.56 * noise1((q - 0.30 * t) * 14.0 + aStrand.y * 91.0);
      float e = aStrand.z * pow(comp, 0.9) * zone(q) * lum * (0.55 + 1.8 * pulse) * fibre;

      // Thicker where it is bright, thinner where it fades out.
      float halfW = aStrand.w * uDpr * (0.5 + 0.5 * lum);
      float drawn = max(halfW, 1.5);
      vCore = clamp(aExtra.y * uDpr * (0.6 + 0.4 * lum) / drawn, 0.06, 1.0);
      e *= edgeFade(q);
      e = e * revealMask(q) + (stray ? 0.0 : aStrand.z * 5.0 * revealHead(q));

      vSide = aCorner.y;
      vE = e * uEncode;
      gl_Position = vec4((pC + nrm * aCorner.y * drawn) / uRes * 2.0 - 1.0, 0.0, 1.0);
    }
  `;

  const STRAND_FS = /* glsl */`#version 300 es
    precision mediump float;
    in float vSide;
    in float vE;
    in float vCore;
    out vec4 outColor;
    void main() {
      float v = abs(vSide);
      float core = exp(-(v * v) / (vCore * vCore) * 2.5);
      float halo = exp(-v * v * 3.5) * (1.0 - v);
      outColor = vec4(vE * (core + 0.4 * halo), 0.0, 0.0, 1.0);
    }
  `;

  // Wisps fraying off a strand, like the barbs of a feather.
  const BARB_VS = /* glsl */`#version 300 es
    precision highp float;
    layout(location = 0) in vec2 aCorner;   // u along the wisp, side -1/+1
    layout(location = 1) in vec4 aRibA;
    layout(location = 2) in vec4 aRibB;
    layout(location = 3) in vec4 aStrand;   // parent's slot and seed, gain, half-width px
    layout(location = 4) in vec4 aBarb;     // root q, length (negative: trails back), spread (signed), speed
    ${COMMON}
    ${RIBBON}
    ${STRAND}
    out float vSide;
    out float vE;

    // Where the wisp leaves its strand. It slides along with the flow and
    // wraps around off-canvas, where it cannot be seen.
    float barbRoot() {
      float span = uSpan + 2.0 * MARGIN;
      return Q0 - MARGIN + mod(aBarb.x + uT * rspeed() * aBarb.w, span);
    }
    // It follows the strand's curve while peeling away from it: slowly at
    // first, then faster, with a little curl.
    vec2 barbAt(float d, float rootQ) {
      float q = rootQ + d * aBarb.y;
      float y = strandY(q) + aBarb.z * pow(d, 1.8);
      return vec2(q, uBase + uAmp * y);
    }

    void main() {
      float rootQ = barbRoot();
      float d = aCorner.x;
      float h = 1.0 / ${(BARB_SEGMENTS * 2).toFixed(1)};
      vec2 A = barbAt(max(d - h, 0.0), rootQ);
      vec2 C = barbAt(d, rootQ);
      vec2 B = barbAt(min(d + h, 1.0), rootQ);
      vec2 pA = toPx(A.x, A.y);
      vec2 pB = toPx(B.x, B.y);
      vec2 pC = toPx(C.x, C.y);
      vec2 tng = normalize(pB - pA + vec2(1e-5, 0.0));
      vec2 nrm = vec2(-tng.y, tng.x);

      // Bright at the root, tapering and fading to nothing at the tip; each
      // wisp flickers on its own clock.
      float flicker = 0.6 + 0.4 * sin(uT * (0.9 + aStrand.y * 1.7) + aBarb.x * 40.0);
      float e = aStrand.z * pow(1.0 - d, 1.3) * smoothstep(0.0, 0.15, d)
              * zone(C.x) * strandLum(rootQ) * flicker;
      float halfW = aStrand.w * uDpr * (1.0 - 0.55 * d);
      float drawn = max(halfW, 1.0);
      e *= halfW / drawn;
      e *= edgeFade(C.x) * revealMask(C.x);

      vSide = aCorner.y;
      vE = e * uEncode;
      gl_Position = vec4((pC + nrm * aCorner.y * drawn) / uRes * 2.0 - 1.0, 0.0, 1.0);
    }
  `;

  const BARB_FS = /* glsl */`#version 300 es
    precision mediump float;
    in float vSide;
    in float vE;
    out vec4 outColor;
    void main() {
      float d = abs(vSide);
      outColor = vec4(vE * clamp(1.0 - d * d, 0.0, 1.0), 0.0, 0.0, 1.0);
    }
  `;

  // The feathered fringe of a strand. Drawn on a wide strip around it, the
  // barbs are painted per pixel: fine curved lines that leave the shaft at
  // a shallow angle and lean forward, each with its own length and
  // brightness, some missing. The vane sits on one side of the strand,
  // then the other, along its length, and slides right with the flow.
  const VANE_VS = /* glsl */`#version 300 es
    precision highp float;
    layout(location = 0) in vec2 aCorner;   // u along, side -1/+1
    layout(location = 1) in vec4 aRibA;
    layout(location = 2) in vec4 aRibB;
    layout(location = 3) in vec4 aStrand;   // slot, seed, gain, half-width px
    layout(location = 4) in vec4 aExtra;    // kind, core px, vane reach px, vane gain
    ${COMMON}
    ${RIBBON}
    ${STRAND}
    out float vSide;
    out float vA;
    out float vE;
    flat out float vSeed;
    flat out float vReach;
    void main() {
      float u = aCorner.x;
      float q = Q0 - MARGIN + u * (uSpan + 2.0 * MARGIN);
      float h = (uSpan + 2.0 * MARGIN) / ${(SEGMENTS * 2).toFixed(1)};
      vec2 pA = toPx(q - h, uBase + uAmp * strandY(q - h));
      vec2 pB = toPx(q + h, uBase + uAmp * strandY(q + h));
      vec2 pC = toPx(q, uBase + uAmp * strandY(q));
      vec2 tng = normalize(pB - pA + vec2(1e-5, 0.0));
      vec2 nrm = vec2(-tng.y, tng.x);
      float lum = strandLum(q);
      float t = uT * rspeed();
      float reach = aExtra.z * uDpr * (0.45 + 0.55 * lum);
      vReach = aExtra.z * uDpr;
      vA = (q - 0.13 * t) * (uRes.x / uSpan);
      vE = aStrand.z * aExtra.w * lum * zone(q) * edgeFade(q) * revealMask(q) * uEncode;
      vSeed = aStrand.y;
      vSide = aCorner.y;
      gl_Position = vec4((pC + nrm * aCorner.y * reach) / uRes * 2.0 - 1.0, 0.0, 1.0);
    }
  `;

  const VANE_FS = /* glsl */`#version 300 es
    precision highp float;
    uniform float uDpr;
    in float vSide;
    in float vA;
    in float vE;
    flat in float vSeed;
    flat in float vReach;
    out vec4 outColor;
    float h11(float p) {
      p = fract(p * 0.1031);
      p *= p + 33.33;
      p *= p + p;
      return fract(p);
    }
    float n11(float x) {
      float i = floor(x), f = fract(x);
      return mix(h11(i), h11(i + 1.0), f * f * (3.0 - 2.0 * f));
    }
    void main() {
      float v = vSide;
      float av = abs(v);
      // Which side the vane is on drifts along the strand.
      float sideField = sin(vA / (900.0 * uDpr) * 5.65 + vSeed * 20.0);
      float sideW = smoothstep(-0.35, 0.55, v > 0.0 ? sideField : -sideField);
      // Curved barbs, leaning forward, a little wavy, packed tighter in
      // some places than others.
      float spacing = 3.6 * uDpr * (0.7 + 0.9 * n11(vA * 0.006 / uDpr + vSeed * 13.0));
      float wav = (n11(vA * 0.03 / uDpr + vSeed * 50.0) - 0.5) * 0.9
                + (n11(vA * 0.05 / uDpr + av * 1.2 + vSeed * 9.0) - 0.5) * 0.35;
      float phase = (vA - 2.8 * vReach * pow(av, 1.8)) / spacing + wav;
      float id = floor(phase);
      float f = fract(phase);
      float dist = min(f, 1.0 - f);
      float line = 1.0 - smoothstep(0.0, max(fwidth(phase), 0.04) * 1.3, dist);
      float r1 = h11(id * 1.37 + vSeed * 91.0);
      float r2 = h11(id * 2.11 + vSeed * 17.0 + 3.0);
      float reach = 0.3 + 0.7 * r1 * r1;            // mostly short, a few long
      float fade = 1.0 - smoothstep(reach * 0.5, reach, av);
      float root = smoothstep(0.02, 0.12, av);      // the shaft itself is the strand's
      // Barbs come in tufts with gaps between them.
      float tuft = n11(id * 0.13 + vSeed * 31.0);
      float present = step(0.62 - 0.55 * tuft, h11(id * 3.7 + vSeed * 7.0));
      float e = vE * line * fade * root * sideW * present * (0.35 + 0.65 * r2);
      outColor = vec4(e, 0.0, 0.0, 1.0);
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

  // The shape of a fold: shared by the folds and the glints that ride them.
  // Needs aL (depth, sky, seed, gain) and aM (amplitude, frequency, speed,
  // opacity) declared first.
  const FOLD = /* glsl */`
    float horizon() { return uBase + 0.18; }
    bool isSky() { return aL.y > 0.5; }
    float foldTime() { return uT * (0.3 + 0.7 * (1.0 - aL.x)) * aM.z; }
    // A crest line: long sweeping waves near the viewer, finer ones toward
    // the horizon, sharp where the fold turns, all drifting right. The
    // sky's edges are more ragged.
    float ridge(float q, float t) {
      float f = aM.y * (0.55 + 2.6 * aL.x);
      float sd = aL.z * 40.0;
      float x = q * f;
      float r = 0.62 * sin(2.1 * (x - 0.05 * t) + sd)
              + 0.28 * sin(4.3 * (x - 0.08 * t) + sd * 1.7)
              + 0.12 * sin(8.7 * (x - 0.11 * t) + sd * 2.3)
              + 0.30 * (noise1(x * 2.4 - t * 0.2 + sd) - 0.5)
              - 0.32 * abs(sin(1.45 * (x - 0.045 * t) + sd * 0.7)) + 0.2;
      if (isSky()) r += 0.45 * (noise1(x * 9.0 - t * 0.5 + sd * 3.0) - 0.5);
      return r;
    }
    float crestY(float q, float t) {
      float near = 1.0 - aL.x;
      float yH = horizon();
      float base = isSky() ? yH + 0.6 * pow(near, 1.3) : yH - 0.72 * pow(near, 1.6);
      float amp = (isSky() ? 0.01 + 0.06 * pow(near, 1.3) : 0.012 + 0.16 * pow(near, 1.4)) * aM.x;
      return base + amp * ridge(q, t);
    }
  `;

  // ------------------------------------------------- the light field
  // The laptop hero: a sea of light seen from low down. Layers of rolling
  // crests stack toward a bright horizon; above it the same folds become
  // smoky clouds lit from below. Each layer is a curtain hanging from its
  // crest (rising from it, in the sky): a thin bright rim, a sheen just
  // under it, a soft glow, and a body that partly hides the layers behind,
  // which is what makes the troughs dark. Drawn far to near, so nearer
  // folds pass in front. Nearer folds are taller and move faster.
  const FIELD_VS = /* glsl */`#version 300 es
    precision highp float;
    layout(location = 0) in vec2 aCorner;   // u along, side: +1 crest, -1 far end of the curtain
    layout(location = 1) in vec4 aL;        // depth (0 near .. 1 far), sky, seed, gain
    layout(location = 2) in vec4 aM;        // amplitude, frequency, speed, opacity
    ${COMMON}
    ${FOLD}
    out float vD;
    out float vHpx;
    out float vE;
    out float vHot;
    out float vA;
    out vec2 vTex;
    flat out float vSky;

    void main() {
      float z = aL.x;
      float near = 1.0 - z;
      bool sky = aL.y > 0.5;
      float q = Q0 - MARGIN + aCorner.x * (uSpan + 2.0 * MARGIN);
      float t = foldTime();
      float crest = crestY(q, t);
      float height = sky ? 0.06 + 0.2 * near : 0.08 + 0.45 * near;
      float d = 0.5 - 0.5 * aCorner.y;        // 0 at the crest, 1 at the far end
      float y = sky ? crest + d * height : crest - d * height;

      float sd = aL.z * 40.0;
      // Rare, long white-hot streaks along crests nearest the horizon.
      float streak = smoothstep(0.64, 0.95, noise1(q * 1.5 * (1.0 + z) - t * 0.04 + sd));
      float glint = smoothstep(0.45, 1.0, noise1(q * 6.0 - t * 0.2 + sd * 3.0));
      float hotBand = sky ? 0.35 : smoothstep(0.25, 0.75, z);
      vHot = 1.0 + 14.0 * streak * (0.4 + 0.6 * glint) * hotBand;
      // Light gathers toward the horizon and varies along each crest.
      float depthLight = sky ? mix(0.12, 0.8, pow(z, 1.6)) : mix(0.08, 1.8, pow(z, 1.15));
      float low = smoothstep(0.03, 0.5, crest);
      depthLight *= low * sqrt(low);
      float along = 0.55 + 0.9 * noise1(q * 1.2 + sd - t * 0.03);
      float shown = revealMask(q) * edgeFade(q);
      vE = aL.w * depthLight * along * shown * uEncode;
      vA = aM.w * shown;
      vD = d;
      vHpx = height * uRes.y;
      vTex = vec2((q - 0.1 * t) * 5.0 * (0.6 + z), sd);
      vSky = sky ? 1.0 : 0.0;
      gl_Position = vec4(toPx(q, y) / uRes * 2.0 - 1.0, 0.0, 1.0);
    }
  `;

  const FIELD_FS = /* glsl */`#version 300 es
    precision highp float;
    uniform float uDpr;
    in float vD;
    in float vHpx;
    in float vE;
    in float vHot;
    in float vA;
    in vec2 vTex;
    flat in float vSky;
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
    void main() {
      float px = vD * vHpx;
      bool sky = vSky > 0.5;
      float rim = exp(-px / ((sky ? 5.0 : 1.5) * uDpr));         // the bright crest line
      float sheen = exp(-px / (vHpx * 0.1 + 4.0 * uDpr));        // light caught just under it
      float glow = exp(-vD * 2.6);                                // the lit face, fading down
      float n = vn(vTex + vec2(0.0, vD * 2.5)) * 0.65 + vn(vTex * 2.3 + vec2(7.0, vD * 5.0)) * 0.35;
      float e = vE * (rim * (sky ? 0.7 : 0.8) * vHot + sheen * 0.7 * vHot + glow * (sky ? 0.3 : 0.55) * (0.35 + n));
      // The body hides what is behind it, so the troughs go dark.
      float body = smoothstep(0.0, 0.03, vD) * (1.0 - smoothstep(0.7, 1.0, vD));
      float a = vA * body * (sky ? (0.08 + 0.18 * n) : (0.78 + 0.18 * n));
      outColor = vec4(e, 0.0, 0.0, clamp(a, 0.0, 1.0));
    }
  `;

  // Behind the folds: a luminous band along the horizon and, above it, a
  // sky of smoke lit from below, with fine bright veins where it folds.
  // Both drift right.
  const HAZE_FS = /* glsl */`#version 300 es
    precision highp float;
    uniform float uT;
    uniform float uHorizon;
    uniform float uAspect;
    uniform float uReveal;
    uniform float uEncode;
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
      for (int i = 0; i < 4; i++) {
        s += a * vn(p);
        p = p * 2.03 + vec2(13.1, 7.7);
        a *= 0.5;
      }
      return s;
    }
    void main() {
      float above = vUv.y - uHorizon;
      float dy = above / 0.07;
      float band = exp(-dy * dy) + 0.3 * exp(-dy * dy * 0.12);
      vec2 p = vec2(vUv.x * uAspect * 1.1 - uT * 0.04, vUv.y * 15.0);
      float n = fbm(p + vec2(fbm(p * vec2(0.5, 0.3) + uT * 0.02) * 1.2, 0.0));
      float haze = band * (0.18 + 1.6 * n * n * n);

      // Smoke above the horizon, stretched along the flow.
      vec2 c = vec2(vUv.x * uAspect * 1.4 - uT * 0.03, above * 3.4);
      vec2 w = vec2(fbm(c * 0.7 + vec2(0.0, uT * 0.015)), fbm(c * 0.7 + vec2(3.1, 1.7) - uT * 0.01));
      float cloud = fbm(c + w * 1.7);
      float vein = 1.0 - abs(2.0 * fbm(c * vec2(1.6, 2.6) + w * 2.2) - 1.0);
      vein = pow(vein, 7.0);
      float lift = exp(-max(above, 0.0) / 0.3) * smoothstep(-0.04, 0.03, above);
      float skyLight = lift * (0.35 + 1.1 * smoothstep(0.3, 0.85, cloud) + 2.4 * vein * smoothstep(0.25, 0.7, cloud));
      skyLight *= 1.0 - smoothstep(0.72, 1.0, vUv.y) * 0.85;

      float front = mix(-0.3, 1.6, uReveal);
      float shown = 1.0 - smoothstep(front - 0.25, front, vUv.x);
      outColor = vec4((haze * 1.35 + skyLight) * shown * uEncode, 0.0, 0.0, 0.0);
    }
  `;

  // White-hot glints: short stretches of a crest near the horizon that
  // burn white, come and go, and drift with their fold. They are drawn on
  // top of the folds so nothing dims them.
  const GLINT_VS = /* glsl */`#version 300 es
    precision highp float;
    layout(location = 0) in vec2 aCorner;   // u along the glint, side -1/+1
    layout(location = 1) in vec4 aL;        // the fold's depth, sky, seed, gain
    layout(location = 2) in vec4 aM;        // the fold's amplitude, frequency, speed, opacity
    layout(location = 3) in vec4 aG;        // start q, length, lifetime phase, gain
    ${COMMON}
    ${FOLD}
    out float vSide;
    out float vE;
    void main() {
      float t = foldTime();
      float span = uSpan + 2.0 * MARGIN;
      float start = Q0 - MARGIN + mod(aG.x + uT * 0.025, span);
      float u = aCorner.x;
      float q = start + u * aG.y;
      float h = aG.y / 64.0;
      vec2 pA = toPx(q - h, crestY(q - h, t));
      vec2 pB = toPx(q + h, crestY(q + h, t));
      vec2 pC = toPx(q, crestY(q, t));
      vec2 tng = normalize(pB - pA + vec2(1e-5, 0.0));
      vec2 nrm = vec2(-tng.y, tng.x);
      // Tapered ends, a hotter middle, and a slow life cycle.
      float taper = pow(sin(3.14159 * u), 1.5);
      float life = pow(0.5 + 0.5 * sin(uT * 0.16 + aG.z * 6.2831), 2.0);
      float e = aG.w * aL.w * taper * life * revealMask(q) * edgeFade(q);
      float halfW = (1.2 + 2.2 * taper) * uDpr;
      vSide = aCorner.y;
      vE = e * uEncode;
      gl_Position = vec4((pC + nrm * aCorner.y * halfW) / uRes * 2.0 - 1.0, 0.0, 1.0);
    }
  `;

  const GLINT_FS = /* glsl */`#version 300 es
    precision mediump float;
    in float vSide;
    in float vE;
    out vec4 outColor;
    void main() {
      float d = abs(vSide);
      outColor = vec4(vE * exp(-d * d * 4.0), 0.0, 0.0, 0.0);
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
    uniform float uStretch;
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
      vec2 p = vec2(vUv.x * uAspect, vUv.y * uStretch) * 2.4;
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
      float glow = texture(uB1, vUv).r * 0.55
                 + texture(uB2, vUv).r * 0.58
                 + texture(uB3, vUv).r * 0.40
                 + texture(uB4, vUv).r * 0.26;
      float smoke = texture(uSmoke, vUv).r;
      float E = (e + glow + smoke * 0.45) * uDecode * uIntensity;
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

  // a: offset, deviation, phase, frequency | b: width, twist, twist phase, depth
  const RIBBONS = [
    { a: [0.000, 1.10, 0.4, 1.00], b: [0.150, 2.6, 0.3, 0.00], gain: 1.30, strands: 9 },
    { a: [0.070, 1.45, 2.1, 1.15], b: [0.105, 3.4, 2.2, 0.15], gain: 1.10, strands: 7 },
    { a: [-0.065, 1.30, 1.2, 1.30], b: [0.090, 4.2, 3.7, 0.30], gain: 1.00, strands: 6 },
    { a: [-0.100, 1.00, 4.0, 0.90], b: [0.150, 2.2, 4.4, 0.70], gain: 0.80, strands: 6 },
    { a: [0.115, 1.10, 5.3, 1.05], b: [0.120, 3.0, 1.1, 0.85], gain: 0.65, strands: 5 },
    { a: [-0.012, 1.25, 3.1, 0.80], b: [0.240, 1.5, 5.6, 1.00], gain: 0.32, strands: 3 },
  ];
  const isFar = (rb) => rb.b[3] > 0.5;

  // Veils, strands and the wisps of each strand for one depth layer.
  function buildLayer(far) {
    const r = rng(far ? 4409 : 7031);
    const veils = [];
    const strands = [];
    const barbs = [];
    RIBBONS.forEach((rb, i) => {
      if (isFar(rb) !== far) return;
      veils.push(...rb.a, ...rb.b, (far ? 0.3 : 0.5) * rb.gain, i * 1.618 + 0.3, 1.22, 0);
      const count = Math.max(2, Math.round(rb.strands * density));
      for (let k = 0; k < count; k++) {
        const slot = (r() * 2 - 1) * 0.95;
        const seed = r();
        const lead = r() < 0.3;              // a few strong strands, more faint ones
        const gain = (lead ? 0.5 + r() * 0.3 : 0.18 + r() * 0.2) * rb.gain;
        const half = lead ? 6 + r() * 3 : 3.5 + r() * 2.5;
        const core = lead ? 1.3 + r() * 0.6 : 0.9 + r() * 0.5;
        const vaneReach = lead ? 32 + r() * 16 : 16 + r() * 10;
        const vaneGain = lead ? 1.0 : 0.6;
        strands.push(...rb.a, ...rb.b, slot, seed, gain, half, 0, core, vaneReach, vaneGain);
        // Its wisps, packed like the barbs of a feather: short, curving away
        // as they fade, and grouped into vanes that alternate sides along
        // the strand (a few stray to the other side). Most reach forward
        // with the flow; some trail back.
        const wisps = Math.round((lead ? 10 : 4) * density * (far ? 0.5 : 1));
        const speed = 0.12 + r() * 0.04;
        for (let j = 0; j < wisps; j++) {
          const at = r() * 3.0;
          const vane = Math.sin(at * 9.0 + seed * 20.0);
          const side = (vane >= 0 ? 1 : -1) * (r() < 0.22 ? -1 : 1);
          const angle = 0.6 + 0.4 * Math.abs(Math.sin(at * 5.0 + seed * 11.0));
          const length = (0.03 + 0.08 * Math.pow(r(), 1.3)) * (r() < 0.8 ? 1 : -1);
          const spread = side * (0.008 + 0.025 * r()) * angle;
          barbs.push(...rb.a, ...rb.b, slot, seed, gain * (0.3 + r() * 0.3), 0.45 + r() * 0.25,
                     at, length, spread, speed + (r() - 0.5) * 0.012);
        }
      }
    });
    return {
      veils: new Float32Array(veils),
      strands: new Float32Array(strands),
      barbs: new Float32Array(barbs),
    };
  }

  // The light field's layers, far to near so nearer folds are drawn last.
  // The light field's folds, far to near so nearer folds are drawn last,
  // and the glints that ride some of the far ones.
  function buildField() {
    const r = rng(2203);
    const folds = [];
    const ground = Math.round(16 * density);
    const sky = Math.round(4 * density);
    for (let i = 0; i < ground; i++) {
      const z = Math.pow((i + 0.5) / ground, 0.5);
      folds.push([1 - z, 0, r(), 0.75 + 0.5 * r(),
                  0.75 + 0.5 * r(), 0.8 + 0.5 * r(), 0.85 + 0.3 * r(), 0.95]);
    }
    for (let i = 0; i < sky; i++) {
      const z = Math.pow((i + 0.5) / sky, 0.7);
      folds.push([1 - z, 1, r(), 0.45 + 0.3 * r(),
                  0.7 + 0.6 * r(), 0.8 + 0.6 * r(), 0.85 + 0.3 * r(), 1.0]);
    }
    folds.sort((a, b) => b[0] - a[0]);
    const glints = [];
    const candidates = folds.filter((f) => f[1] === 0 && f[0] > 0.45 && f[0] < 0.92);
    for (let i = 0; i < 11; i++) {
      const f = candidates[i % candidates.length];
      glints.push(...f, r() * 3.0, 0.18 + r() * 0.34, r(), 8 + r() * 10);
    }
    const skyFolds = folds.filter((f) => f[1] === 1 && f[0] > 0.3);
    for (let i = 0; i < 3 && skyFolds.length; i++) {
      const f = skyFolds[i % skyFolds.length];
      glints.push(...f, r() * 3.0, 0.2 + r() * 0.3, r(), 5 + r() * 6);
    }
    return { folds: new Float32Array(folds.flat()), glints: new Float32Array(glints) };
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
  let veilProg, strandProg, vaneProg, barbProg, dustProg, downProg, blurProg, smokeProg, compProg;
  let fieldProg, hazeProg, glintProg, fieldPart, glintPart;
  const layers = { near: {}, far: {} };
  let dustVao, quadVao, dustCount = 0;
  let hdr = true;
  let targets = null;

  const FIELD_UNIFORMS = ['uT', 'uBase', 'uAmp', 'uSpan', 'uRes', 'uDpr', 'uReveal', 'uEncode'];

  // A strip of `segments` quads: (u, side) per vertex, indexed triangles.
  function makeStrip(segments) {
    const corners = new Float32Array((segments + 1) * 4);
    for (let i = 0; i <= segments; i++) {
      const u = i / segments;
      corners.set([u, -1, u, 1], i * 4);
    }
    const indices = new Uint16Array(segments * 6);
    for (let i = 0; i < segments; i++) {
      const a = i * 2;
      indices.set([a, a + 1, a + 2, a + 1, a + 3, a + 2], i * 6);
    }
    const cornerBuf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, cornerBuf);
    gl.bufferData(gl.ARRAY_BUFFER, corners, gl.STATIC_DRAW);
    const indexBuf = gl.createBuffer();
    gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, indexBuf);
    gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, indices, gl.STATIC_DRAW);
    return { cornerBuf, indexBuf, indexCount: indices.length };
  }

  function stripVao(strip, instances, attribs) {
    const stride = attribs * 16;
    const vao = gl.createVertexArray();
    gl.bindVertexArray(vao);
    gl.bindBuffer(gl.ARRAY_BUFFER, strip.cornerBuf);
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
    gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, strip.indexBuf);
    gl.bindVertexArray(null);
    return { vao, count: instances.length / (stride / 4), indexCount: strip.indexCount };
  }

  function init() {
    veilProg = program(VEIL_VS, VEIL_FS, FIELD_UNIFORMS);
    strandProg = program(STRAND_VS, STRAND_FS, FIELD_UNIFORMS);
    vaneProg = program(VANE_VS, VANE_FS, FIELD_UNIFORMS);
    barbProg = program(BARB_VS, BARB_FS, FIELD_UNIFORMS);
    dustProg = program(DUST_VS, DUST_FS, FIELD_UNIFORMS);
    downProg = program(QUAD_VS, DOWN_FS, ['uTex', 'uTex2', 'uTexel', 'uMix2']);
    blurProg = program(QUAD_VS, BLUR_FS, ['uTex', 'uDir']);
    smokeProg = program(QUAD_VS, SMOKE_FS, ['uD1', 'uD2', 'uT', 'uAspect', 'uStretch']);
    compProg = program(QUAD_VS, COMPOSITE_FS,
      ['uScene', 'uSoft', 'uB1', 'uB2', 'uB3', 'uB4', 'uSmoke', 'uDecode', 'uIntensity', 'uFrame']);

    fieldProg = program(FIELD_VS, FIELD_FS, FIELD_UNIFORMS);
    glintProg = program(GLINT_VS, GLINT_FS, FIELD_UNIFORMS);
    hazeProg = program(QUAD_VS, HAZE_FS, ['uT', 'uHorizon', 'uAspect', 'uReveal', 'uEncode']);

    const long = makeStrip(SEGMENTS);
    const field = buildField();
    fieldPart = stripVao(long, field.folds, 2);
    glintPart = stripVao(makeStrip(64), field.glints, 3);
    const short = makeStrip(BARB_SEGMENTS);
    for (const name of ['near', 'far']) {
      const built = buildLayer(name === 'far');
      layers[name].veil = stripVao(long, built.veils, 3);
      layers[name].strand = stripVao(long, built.strands, 4);
      layers[name].barb = stripVao(short, built.barbs, 4);
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

  // scene: 'ribbon' (silk ribbons) or 'field' (the laptop hero). A change
  // waits until the light has faded out, so it is never seen happening.
  const view = { base: 0.37, intensity: 1, tBase: 0.37, tIntensity: 1, scene: 'ribbon', tScene: 'ribbon' };

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

  function drawStrips(prog, part, target, scale) {
    if (!part.count) return;
    gl.useProgram(prog.p);
    setFieldUniforms(prog.u, target, scale);
    gl.bindVertexArray(part.vao);
    gl.drawElementsInstanced(gl.TRIANGLES, part.indexCount, gl.UNSIGNED_SHORT, 0, part.count);
  }

  function drawLayer(layer, target, scale) {
    gl.bindFramebuffer(gl.FRAMEBUFFER, target.fb);
    gl.viewport(0, 0, target.w, target.h);
    gl.clearColor(0, 0, 0, 1);
    gl.clear(gl.COLOR_BUFFER_BIT);
    if (view.intensity <= 0.002) return;
    gl.enable(gl.BLEND);
    gl.blendFunc(gl.ONE, gl.ONE);
    drawStrips(veilProg, layer.veil, target, scale);
    drawStrips(strandProg, layer.strand, target, scale);
    drawStrips(vaneProg, layer.strand, target, scale);
    drawStrips(barbProg, layer.barb, target, scale);
    if (layer === layers.near) {
      gl.useProgram(dustProg.p);
      setFieldUniforms(dustProg.u, target, scale);
      gl.bindVertexArray(dustVao);
      gl.drawArrays(gl.POINTS, 0, dustCount);
    }
    gl.disable(gl.BLEND);
  }

  // The light field: the smoky sky and horizon haze at half size (they
  // are soft, and seen through as atmosphere), then the folds far to near
  // (each adds its light and hides part of what is behind it), the
  // glints, and glitter.
  function drawField(target, hazeTarget) {
    gl.bindFramebuffer(gl.FRAMEBUFFER, hazeTarget.fb);
    gl.viewport(0, 0, hazeTarget.w, hazeTarget.h);
    gl.clearColor(0, 0, 0, 0);
    gl.clear(gl.COLOR_BUFFER_BIT);
    gl.bindFramebuffer(gl.FRAMEBUFFER, target.fb);
    gl.viewport(0, 0, target.w, target.h);
    gl.clear(gl.COLOR_BUFFER_BIT);
    if (view.intensity <= 0.002) return;
    gl.bindFramebuffer(gl.FRAMEBUFFER, hazeTarget.fb);
    gl.viewport(0, 0, hazeTarget.w, hazeTarget.h);
    gl.useProgram(hazeProg.p);
    gl.uniform1f(hazeProg.u.uT, time);
    gl.uniform1f(hazeProg.u.uHorizon, view.base + 0.18);
    gl.uniform1f(hazeProg.u.uAspect, cssW / cssH);
    gl.uniform1f(hazeProg.u.uReveal, easeReveal(reveal));
    gl.uniform1f(hazeProg.u.uEncode, hdr ? 1 : 0.25);
    gl.bindVertexArray(quadVao);
    gl.drawArrays(gl.TRIANGLES, 0, 3);
    gl.bindFramebuffer(gl.FRAMEBUFFER, target.fb);
    gl.viewport(0, 0, target.w, target.h);
    gl.enable(gl.BLEND);
    gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
    drawStrips(fieldProg, fieldPart, target, 1);
    gl.blendFunc(gl.ONE, gl.ONE);
    drawStrips(glintProg, glintPart, target, 1);
    gl.useProgram(dustProg.p);
    setFieldUniforms(dustProg.u, target, 1);
    gl.bindVertexArray(dustVao);
    gl.drawArrays(gl.POINTS, 0, dustCount);
    gl.disable(gl.BLEND);
  }

  function render() {
    if (lost || !targets) return;
    const t = targets;

    if (view.scene === 'field') {
      drawField(t.scene, t.soft);
    } else {
      // 1. Far layer at half size, then out of focus.
      drawLayer(layers.far, t.soft, 0.5);
      blur(t.soft, t.soft2, 1.1, 1);
      // 2. Near layer, sharp.
      drawLayer(layers.near, t.scene, 1);
    }

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
      gl.uniform1f(u.uStretch, view.scene === 'field' ? 4.0 : 1.0);
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

    if (view.scene !== view.tScene && view.intensity < 0.03) {
      view.scene = view.tScene;
      dirty = true;
    }
    const before = view.base + view.intensity;
    view.base = approach(view.base, view.tBase, dt, 0.16);
    view.intensity = approach(view.intensity, view.scene === view.tScene ? view.tIntensity : 0, dt, 0.22);
    const settling = Math.abs(view.base + view.intensity - before) > 1e-5 || view.scene !== view.tScene;

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
    if (o.scene === 'ribbon' || o.scene === 'field') view.tScene = o.scene;
    if (o.immediate) {
      view.base = view.tBase;
      view.intensity = view.tIntensity;
      view.scene = view.tScene;
      dirty = true;
    }
    schedule();
  };
  api.setPaused = (p) => {
    paused = !!p;
    if (paused) reveal = 1;
    last = 0;
    dirty = true;
    schedule();
  };
  Object.defineProperty(api, 'paused', { get: () => paused });

  render();
  root.classList.add('silk-on');
  schedule();
})();
