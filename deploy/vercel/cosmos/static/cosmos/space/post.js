/* After the scene: streaks, glow, then light to screen colour.
 *
 *   streaks  a radial blur toward the point we fly at (the wormhole ride)
 *   bloom    the scene halved six times with a 13-tap filter and added back
 *            up with a tent filter: a soft glow from 1/2 down to 1/64 size,
 *            which is what turns Gargantua's disk into a lamp
 *   finish   exposure, a filmic curve (bright light rolls off to white as
 *            on film), a little lift toward indigo in the darks, vignette,
 *            film grain and dithering so the near-black gradients never band
 */
import { FULLSCREEN_VS, startProgram, finishProgram, target, dropTarget, bindTex, draw } from './gl.js';

const DOWN_FS = /* glsl */`#version 300 es
  precision highp float;
  in vec2 vUv;
  out vec4 o;
  uniform sampler2D uSrc;
  uniform vec2 uTexel;
  uniform int uFirst;
  // any NaN or infinity from a scene pass stops here instead of spreading
  vec3 s(vec2 d) {
    vec3 c = texture(uSrc, vUv + d * uTexel).rgb;
    return (any(isnan(c)) || any(isinf(c))) ? vec3(0.0) : min(c, vec3(6.0e4));
  }
  float w(vec3 c) { return 1.0 / (1.0 + max(c.r, max(c.g, c.b)) * 0.25); }
  void main() {
    vec3 a = s(vec2(-2, 2)), b = s(vec2(0, 2)), c = s(vec2(2, 2));
    vec3 d = s(vec2(-2, 0)), e = s(vec2(0, 0)), f = s(vec2(2, 0));
    vec3 g = s(vec2(-2, -2)), h = s(vec2(0, -2)), i = s(vec2(2, -2));
    vec3 j = s(vec2(-1, 1)), k = s(vec2(1, 1)), l = s(vec2(-1, -1)), m = s(vec2(1, -1));
    vec3 r;
    if (uFirst == 1) {
      // Karis average on the first step: no single hot pixel can flicker
      vec3 g0 = (a + b + d + e) * 0.25, g1 = (b + c + e + f) * 0.25;
      vec3 g2 = (d + e + g + h) * 0.25, g3 = (e + f + h + i) * 0.25;
      vec3 g4 = (j + k + l + m) * 0.25;
      float w0 = w(g0), w1 = w(g1), w2 = w(g2), w3 = w(g3), w4 = w(g4);
      r = (g0 * w0 * 0.125 + g1 * w1 * 0.125 + g2 * w2 * 0.125 + g3 * w3 * 0.125 + g4 * w4 * 0.5)
        / (w0 * 0.125 + w1 * 0.125 + w2 * 0.125 + w3 * 0.125 + w4 * 0.5);
    } else {
      r = e * 0.125 + (a + c + g + i) * 0.03125 + (b + d + f + h) * 0.0625 + (j + k + l + m) * 0.125;
    }
    o = vec4(r, 1.0);
  }
`;

const UP_FS = /* glsl */`#version 300 es
  precision highp float;
  in vec2 vUv;
  out vec4 o;
  uniform sampler2D uSrc;
  uniform vec2 uTexel;
  uniform float uScale;
  vec3 s(vec2 d) { return texture(uSrc, vUv + d * uTexel).rgb; }
  void main() {
    vec3 r = s(vec2(0, 0)) * 4.0
      + (s(vec2(-1, 0)) + s(vec2(1, 0)) + s(vec2(0, -1)) + s(vec2(0, 1))) * 2.0
      + s(vec2(-1, -1)) + s(vec2(1, -1)) + s(vec2(-1, 1)) + s(vec2(1, 1));
    o = vec4(r * (uScale / 16.0), 1.0);
  }
`;

const STREAK_FS = /* glsl */`#version 300 es
  precision highp float;
  in vec2 vUv;
  out vec4 o;
  uniform sampler2D uSrc;
  uniform vec2 uCenter;     // uv of the point we fly toward
  uniform float uAmount;    // fraction of the distance to the centre
  uniform float uSeed;
  uniform float uChroma;    // red and blue pulled apart radially (the throat)
  float hash(vec2 p) { return fract(sin(dot(p, vec2(12.9898, 78.233)) + uSeed) * 43758.5453); }
  void main() {
    vec2 d = vUv - uCenter;
    float j = hash(gl_FragCoord.xy);
    vec3 acc = vec3(0.0);
    float wsum = 0.0;
    const int N = 16;
    for (int i = 0; i < N; i++) {
      float t = (float(i) + j) / float(N);
      float k = 1.0 - uAmount * t;
      float w = 1.0 - t * 0.55;
      vec3 s = texture(uSrc, uCenter + d * k).rgb;
      if (uChroma > 0.0) {           // colour fringes: two more reads, only when asked for
        s.r = texture(uSrc, uCenter + d * k * (1.0 + uChroma)).r;
        s.b = texture(uSrc, uCenter + d * k * (1.0 - uChroma)).b;
      }
      acc += s * w;
      wsum += w;
    }
    o = vec4(acc / wsum, 1.0);
  }
`;

const FINISH_FS = /* glsl */`#version 300 es
  precision highp float;
  in vec2 vUv;
  out vec4 o;
  uniform sampler2D uScene;
  uniform sampler2D uBloom;
  uniform float uBloomMix;
  uniform float uExposure;
  uniform float uFade;       // 0 black .. 1 picture
  uniform float uFlash;      // white flash
  uniform float uTime;
  uniform float uGrain;
  uniform float uVignette;
  uniform vec2 uRes;
  uniform vec4 uFlare;       // xy: light on screen (uv), z: strength, w: 1 star, 2 burst
  uniform vec4 uGrade;       // rgb: tint, a: amount (the tunnel's teal light)

  // The camera lens answering a bright light, as in the film's Saturn
  // shots: a hard core with a few thin spikes, and ghosts - soft discs and
  // rings in blue, teal, pink and violet - strung along the line from the
  // light through the centre of the frame.
  vec3 ghost(vec2 uv, vec2 at, float r, vec3 col, float ring) {
    vec2 asp = vec2(uRes.x / uRes.y, 1.0);
    float d = length((uv - at) * asp);
    float disc = smoothstep(r, r * 0.55, d);
    float rim = exp(-pow((d - r) / (r * 0.12), 2.0));
    return col * mix(disc, rim, ring);
  }
  vec3 lensFlare(vec2 uv) {
    vec2 L = uFlare.xy;
    vec2 asp = vec2(uRes.x / uRes.y, 1.0);
    vec2 p = (uv - L) * asp;
    float d = length(p);
    float a = atan(p.y, p.x);
    // a small six-point star, as the film's Sun
    float spikes = pow(abs(cos(a * 3.0)), 160.0) * exp(-d * 55.0)
                 + pow(abs(cos(a * 3.0 + 1.5708)), 220.0) * exp(-d * 80.0) * 0.4;
    vec3 c = vec3(1.0, 0.97, 0.93) * (exp(-d * 420.0) * 3.0 + exp(-d * 60.0) * 0.18 + spikes * 0.9) * min(uFlare.w, 1.0);
    if (uFlare.w > 1.5) {
      // a burst of light: a wide warm core and a long horizontal streak
      float sx = abs(p.x), sy = abs(p.y);
      c += vec3(1.0, 0.9, 0.72) * (exp(-d * 18.0) * 0.5 + exp(-sy * 160.0) * exp(-sx * 2.8) * 1.2);
      c += vec3(0.55, 0.75, 1.0) * exp(-sy * 70.0) * exp(-sx * 1.6) * 0.25;
    }
    vec2 ax = vec2(0.5) - L;
    // the ghosts are faint, as from a good lens
    c += ghost(uv, L + ax * 0.62, 0.026, vec3(0.20, 0.42, 1.00), 0.0) * 0.045;
    c += ghost(uv, L + ax * 0.80, 0.009, vec3(0.30, 1.00, 0.80), 0.0) * 0.04;
    c += ghost(uv, L + ax * 0.38, 0.040, vec3(0.50, 0.30, 1.00), 0.0) * 0.015;
    c += ghost(uv, L + ax * 1.20, 0.014, vec3(1.00, 0.22, 0.52), 0.0) * 0.06;
    c += ghost(uv, L + ax * 1.48, 0.075, vec3(0.80, 0.30, 0.95), 1.0) * 0.02;
    c += ghost(uv, L + ax * 1.95, 0.140, vec3(1.00, 0.40, 0.62), 1.0) * 0.01;
    return c * uFlare.z;
  }

  // ACES filmic fit (Narkowicz), applied per channel after a mild matrix
  vec3 aces(vec3 x) {
    const float a = 2.51, b = 0.03, c = 2.43, d = 0.59, e = 0.14;
    return clamp((x * (a * x + b)) / (x * (c * x + d) + e), 0.0, 1.0);
  }
  float hash(vec3 p) {
    p = fract(p * 0.1031);
    p += dot(p, p.zyx + 31.32);
    return fract((p.x + p.y) * p.z);
  }
  void main() {
    vec3 c = texture(uScene, vUv).rgb;
    if (any(isnan(c)) || any(isinf(c))) c = vec3(0.0);
    vec3 b = texture(uBloom, vUv).rgb;
    // halation: the blurred light added on top, as a film emulsion glows
    c += b * uBloomMix;
    if (uFlare.z > 0.0) c += lensFlare(vUv);
    c *= uExposure * (1.0 + 1.6 * uFlash);      // a surge of light, not a grey veil
    // highlights desaturate toward white before the curve, like film
    float l = dot(c, vec3(0.2126, 0.7152, 0.0722));
    c = mix(c, vec3(l), smoothstep(2.0, 14.0, l) * 0.2);
    c = aces(c);
    // the tunnel's light: a dark teal cast that crushes the shadows
    if (uGrade.a > 0.0) {
      vec3 g = c * uGrade.rgb * (0.75 + 0.25 * smoothstep(0.0, 0.5, l)) + vec3(0.0, 0.0035, 0.0025) * (1.0 - smoothstep(0.0, 0.2, l));
      c = mix(c, g, uGrade.a);
    }
    // vignette
    vec2 q = vUv - 0.5;
    q.x *= uRes.x / uRes.y;
    c *= 1.0 - uVignette * smoothstep(0.35, 1.05, length(q));
    c *= uFade;
    // gamma
    c = pow(max(c, 0.0), vec3(1.0 / 2.2));
    // grain, strongest in the mid-tones, fresh every frame
    float n = hash(vec3(gl_FragCoord.xy, floor(uTime * 24.0) * 7.31)) - 0.5;
    float lm = dot(c, vec3(0.333));
    c += n * uGrain * (0.35 + 0.65 * (4.0 * lm * (1.0 - lm)));
    // dither to 8 bits
    float dn = hash(vec3(gl_FragCoord.yx * 1.37, uTime * 3.7)) + hash(vec3(gl_FragCoord.xy * 0.71, uTime * 1.9)) - 1.0;
    c += dn / 255.0;
    o = vec4(c, 1.0);
  }
`;

export function createPost(gl) {
  const down = startProgram(gl, FULLSCREEN_VS, DOWN_FS, 'bloom-down');
  const up = startProgram(gl, FULLSCREEN_VS, UP_FS, 'bloom-up');
  const streak = startProgram(gl, FULLSCREEN_VS, STREAK_FS, 'streak');
  const finish = startProgram(gl, FULLSCREEN_VS, FINISH_FS, 'finish');
  const programs = [down, up, streak, finish];
  let mips = [];
  let streakT = null;
  let w0 = 0, h0 = 0;

  function resize(w, h) {
    if (w === w0 && h === h0) return;
    w0 = w; h0 = h;
    mips.forEach((m) => dropTarget(gl, m));
    mips = [];
    let w2 = w, h2 = h;
    for (let i = 0; i < 7; i++) {
      w2 = Math.max(1, Math.floor(w2 / 2));
      h2 = Math.max(1, Math.floor(h2 / 2));
      mips.push(target(gl, w2, h2));
      if (w2 <= 8 || h2 <= 8) break;
    }
    dropTarget(gl, streakT);
    streakT = target(gl, w, h);
  }

  return {
    programs,
    setup() { programs.forEach((p) => finishProgram(gl, p)); },
    resize,
    /* Radial streaks: src target -> internal target, returned. */
    streaks(src, center, amount, seed, chroma = 0) {
      gl.bindFramebuffer(gl.FRAMEBUFFER, streakT.fb);
      gl.viewport(0, 0, streakT.w, streakT.h);
      gl.useProgram(streak.p);
      bindTex(gl, 0, src.tex);
      gl.uniform1i(streak.u.uSrc, 0);
      gl.uniform2f(streak.u.uCenter, center[0], center[1]);
      gl.uniform1f(streak.u.uAmount, amount);
      gl.uniform1f(streak.u.uSeed, seed);
      gl.uniform1f(streak.u.uChroma, chroma);
      draw(gl);
      return streakT;
    },
    /* spread: how much each wider level adds (1 = all equal, a wide soft
       halo; lower keeps the glow tight, so a black shadow stays black). */
    bloom(src, spread = 1.0) {
      gl.useProgram(down.p);
      gl.uniform1i(down.u.uSrc, 0);
      let prev = src;
      for (let i = 0; i < mips.length; i++) {
        const m = mips[i];
        gl.bindFramebuffer(gl.FRAMEBUFFER, m.fb);
        gl.viewport(0, 0, m.w, m.h);
        bindTex(gl, 0, prev.tex);
        gl.uniform2f(down.u.uTexel, 1 / prev.w, 1 / prev.h);
        gl.uniform1i(down.u.uFirst, i === 0 ? 1 : 0);
        draw(gl);
        prev = m;
      }
      gl.useProgram(up.p);
      gl.uniform1i(up.u.uSrc, 0);
      gl.enable(gl.BLEND);
      gl.blendFunc(gl.ONE, gl.ONE);
      for (let i = mips.length - 1; i > 0; i--) {
        const from = mips[i], to = mips[i - 1];
        gl.bindFramebuffer(gl.FRAMEBUFFER, to.fb);
        gl.viewport(0, 0, to.w, to.h);
        bindTex(gl, 0, from.tex);
        gl.uniform2f(up.u.uTexel, 1 / from.w, 1 / from.h);
        gl.uniform1f(up.u.uScale, spread);
        draw(gl);
      }
      gl.disable(gl.BLEND);
      return mips[0];
    },
    /* Everything to the canvas. */
    finish(scene, bloom, outW, outH, p) {
      gl.bindFramebuffer(gl.FRAMEBUFFER, null);
      gl.viewport(0, 0, outW, outH);
      gl.useProgram(finish.p);
      bindTex(gl, 0, scene.tex);
      gl.uniform1i(finish.u.uScene, 0);
      bindTex(gl, 1, bloom.tex);
      gl.uniform1i(finish.u.uBloom, 1);
      // the up-sweep adds every level once: divide by the level count
      gl.uniform1f(finish.u.uBloomMix, p.bloom / Math.max(mips.length, 1));
      gl.uniform1f(finish.u.uExposure, p.exposure);
      gl.uniform1f(finish.u.uFade, p.fade);
      gl.uniform1f(finish.u.uFlash, p.flash);
      gl.uniform1f(finish.u.uTime, p.time);
      gl.uniform1f(finish.u.uGrain, p.grain);
      gl.uniform1f(finish.u.uVignette, p.vignette);
      gl.uniform2f(finish.u.uRes, outW, outH);
      gl.uniform4fv(finish.u.uFlare, p.flare || [0, 0, 0, 0]);
      gl.uniform4fv(finish.u.uGrade, p.grade || [1, 1, 1, 0]);
      draw(gl);
    },
    get levels() { return mips.length; },
  };
}
