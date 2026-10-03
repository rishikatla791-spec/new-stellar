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
  float hash(vec2 p) { return fract(sin(dot(p, vec2(12.9898, 78.233)) + uSeed) * 43758.5453); }
  void main() {
    vec2 d = vUv - uCenter;
    float j = hash(gl_FragCoord.xy);
    vec3 acc = vec3(0.0);
    float wsum = 0.0;
    const int N = 20;
    for (int i = 0; i < N; i++) {
      float t = (float(i) + j) / float(N);
      vec2 uv = uCenter + d * (1.0 - uAmount * t);
      float w = 1.0 - t * 0.55;
      acc += texture(uSrc, uv).rgb * w;
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
    // the glow's faint outskirts lean rose rather than brown
    float bl = dot(b, vec3(0.2126, 0.7152, 0.0722));
    b = mix(b, bl * vec3(1.05, 0.86, 0.98), 0.5 * (1.0 - smoothstep(0.02, 0.6, bl)));
    // halation: the blurred light added on top, as a film emulsion glows
    c += b * uBloomMix;
    c *= uExposure * (1.0 + 2.5 * uFlash);      // a surge of light, not a grey veil
    // highlights desaturate toward white before the curve, like film
    float l = dot(c, vec3(0.2126, 0.7152, 0.0722));
    c = mix(c, vec3(l), smoothstep(1.5, 12.0, l) * 0.35);
    c = aces(c);
    // vignette
    vec2 q = vUv - 0.5;
    q.x *= uRes.x / uRes.y;
    c *= 1.0 - uVignette * smoothstep(0.35, 1.05, length(q));
    // the deepest shadow is not grey but the faintest indigo (~#040309 at most)
    c += vec3(0.00012, 0.00008, 0.00042) * (1.0 - smoothstep(0.0, 0.02, l));
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
    streaks(src, center, amount, seed) {
      gl.bindFramebuffer(gl.FRAMEBUFFER, streakT.fb);
      gl.viewport(0, 0, streakT.w, streakT.h);
      gl.useProgram(streak.p);
      bindTex(gl, 0, src.tex);
      gl.uniform1i(streak.u.uSrc, 0);
      gl.uniform2f(streak.u.uCenter, center[0], center[1]);
      gl.uniform1f(streak.u.uAmount, amount);
      gl.uniform1f(streak.u.uSeed, seed);
      draw(gl);
      return streakT;
    },
    bloom(src) {
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
        gl.uniform1f(up.u.uScale, 1.0);
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
      draw(gl);
    },
    get levels() { return mips.length; },
  };
}
