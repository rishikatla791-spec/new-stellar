/* The cosmic flow: slow, smooth currents of colour in near-black space.
 *
 * Two renderings of the same idea:
 *   sky    a cube map (the whole sky, by direction) of faint nebula in
 *          midnight indigo, cosmic violet, deep plum and cosmic blue. Every
 *          scene looks it up by direction, so the black hole bends it into
 *          rings like the stars. Redrawn a few times a second; it moves
 *          slowly enough that nobody can tell.
 *   ribbon the footer: the full palette - midnight indigo, deep plum,
 *          cosmic blue, cosmic violet, magenta, ice white and pearl white -
 *          flowing bright, in screen space.
 * Both are domain-warped noise: noise whose input is pushed around by
 * more noise, which is what gives liquid-looking currents.
 */
import { FULLSCREEN_VS, startProgram, finishProgram, draw } from './gl.js';
import { HASH, NOISE } from './glsl.js';

export const PALETTE = {
  midnightIndigo: [0.102, 0.078, 0.275],   // #1a1446
  deepPlum: [0.290, 0.098, 0.259],         // #4a1942
  cosmicBlue: [0.165, 0.357, 1.000],       // #2a5bff
  cosmicViolet: [0.486, 0.302, 1.000],     // #7c4dff
  magenta: [1.000, 0.122, 0.608],          // #ff1f9b
  iceWhite: [0.890, 0.953, 1.000],         // #e3f3ff
  pearlWhite: [0.973, 0.953, 0.918],       // #f8f3ea
};

const glslColor = (c) => `vec3(${c.map((v) => v.toFixed(3)).join(', ')})`;
const P = PALETTE;
const PALETTE_GLSL = /* glsl */`
  // sRGB palette values; converted to linear light where used
  const vec3 C_INDIGO = ${glslColor(P.midnightIndigo)};
  const vec3 C_PLUM = ${glslColor(P.deepPlum)};
  const vec3 C_BLUE = ${glslColor(P.cosmicBlue)};
  const vec3 C_VIOLET = ${glslColor(P.cosmicViolet)};
  const vec3 C_MAGENTA = ${glslColor(P.magenta)};
  const vec3 C_ICE = ${glslColor(P.iceWhite)};
  const vec3 C_PEARL = ${glslColor(P.pearlWhite)};
  vec3 lin(vec3 c) { return pow(c, vec3(2.2)); }
  vec3 hue(vec3 c) { vec3 l = lin(c); return l / max(l.r, max(l.g, l.b)); }
`;

const SKY_FS = /* glsl */`#version 300 es
  precision highp float;
  precision highp int;
  in vec2 vUv;
  out vec4 o;
  uniform int uFace;
  uniform float uTime;
  uniform float uGain;
  ${HASH}
  ${NOISE}
  ${PALETTE_GLSL}

  vec3 faceDirection(int f, vec2 uv) {
    float u = uv.x * 2.0 - 1.0, v = uv.y * 2.0 - 1.0;
    if (f == 0) return vec3(1.0, -v, -u);
    if (f == 1) return vec3(-1.0, -v, u);
    if (f == 2) return vec3(u, 1.0, v);
    if (f == 3) return vec3(u, -1.0, -v);
    if (f == 4) return vec3(u, -v, 1.0);
    return vec3(-u, -v, -1.0);
  }

  void main() {
    vec3 d = normalize(faceDirection(uFace, vUv));
    float t = uTime;
    vec3 p = d * 1.15;
    vec3 drift = vec3(0.011, -0.007, 0.009) * t;
    // first warp: broad currents
    vec3 q = vec3(fbm3(p + drift, 4),
                  fbm3(p + vec3(4.1, 1.7, 9.2) - drift, 4),
                  fbm3(p + vec3(7.3, 3.9, 2.4) + drift.zxy, 4));
    vec3 w = p + 1.45 * q;
    // second warp: the currents carry finer filaments
    float n = fbm3(w * 1.7 + vec3(0.0, 0.013 * t, 0.0), 5);
    float m = fbm3(w * 0.8 - drift * 0.6 + 3.3, 3);

    float veil = smoothstep(-0.1, 0.7, n + 0.35 * m);         // broad glow
    float fil = pow(max(1.0 - abs(n * 1.4 - 0.1), 0.0), 7.0);            // thin bright ridges
    float dark = smoothstep(0.1, -0.5, m);                     // dust lanes

    // hues normalised to unit peak; brightness comes from lum alone
    vec3 col = mix(hue(C_INDIGO), hue(C_VIOLET), smoothstep(-0.4, 0.5, q.x));
    col = mix(col, hue(C_PLUM), smoothstep(0.0, 0.6, q.y) * 0.7);
    col = mix(col, hue(C_BLUE), smoothstep(0.15, 0.7, q.z) * 0.5);
    col = mix(col, hue(C_MAGENTA), smoothstep(0.5, 0.9, q.y + 0.4 * n) * 0.25);

    float lum = 0.004 + 0.018 * veil + 0.055 * fil * veil;
    lum *= 1.0 - 0.8 * dark;
    // a whisper of ice white in the brightest filaments
    vec3 c = col * lum + hue(C_ICE) * 0.02 * pow(fil * veil, 3.0);
    o = vec4(c * uGain, 1.0);
  }
`;

const RIBBON_FS = /* glsl */`#version 300 es
  precision highp float;
  precision highp int;
  in vec2 vUv;
  out vec4 o;
  uniform vec2 uRes;           // target size in pixels
  uniform vec4 uRect;          // footer rect in target pixels: x, y (bottom), w, h
  uniform float uTime;
  uniform float uGain;
  ${HASH}
  ${NOISE}
  ${PALETTE_GLSL}

  // midnight indigo -> deep plum -> magenta -> cosmic violet -> cosmic blue -> back
  vec3 currentColor(float x) {
    vec3 c[5] = vec3[5](hue(C_INDIGO), hue(C_PLUM), hue(C_MAGENTA), hue(C_VIOLET), hue(C_BLUE));
    x = fract(x) * 5.0;
    int i = int(x);
    float f = smoothstep(0.0, 1.0, fract(x));
    return mix(c[i], c[(i + 1) % 5], f);
  }

  void main() {
    vec2 px = gl_FragCoord.xy;
    vec2 q = (px - uRect.xy) / uRect.z;        // x 0..1 across, y 0.. up (in widths)
    float hn = (px.y - uRect.y) / uRect.w;     // 0 at the bottom, 1 at the top
    float t = uTime;
    // currents that drift left to right and curl as they go
    vec3 p = vec3(q * vec2(2.2, 2.6), t * 0.03);
    vec2 w1 = vec2(fbm3(p + vec3(-0.05 * t, 0.0, 0.0), 4), fbm3(p + vec3(5.2, 1.3, 0.0), 4));
    vec3 pw = p + vec3(1.5 * w1, 0.0) + vec3(-0.045 * t, 0.0, 0.0);
    float n = fbm3(pw * 1.25, 5);
    float m = fbm3(pw * 0.6 + 7.1, 3);
    float n2 = fbm3(pw * 2.6 + 3.7, 3);

    // where the light flows: broad luminous currents in the palette
    float flowI = smoothstep(-0.25, 0.65, n + 0.45 * m);
    vec3 col = currentColor(0.9 * n + 0.7 * m + 0.55 * q.x - 0.012 * t);
    vec3 c = col * (0.03 + 0.55 * flowI * flowI);
    // silk: thin bright seams of ice white and pearl along the currents
    float seam = pow(max(1.0 - abs(n * 1.9 - 0.15), 0.0), 14.0) * flowI;
    float seam2 = pow(max(1.0 - abs(n2 * 2.2), 0.0), 18.0) * flowI;
    c += lin(C_ICE) * 1.1 * seam + lin(C_PEARL) * 0.7 * seam2;
    // a faint indigo floor so the darkest parts are deep blue-black, not grey
    c += hue(C_INDIGO) * 0.006;

    // rise out of the page's black: dark at the top of the footer
    float env = smoothstep(1.0, 0.18, hn);
    c *= env * env;
    o = vec4(c * uGain, 1.0);
  }
`;

export function createFlow(gl, size = 256) {
  const sky = startProgram(gl, FULLSCREEN_VS, SKY_FS, 'flow-sky');
  const ribbon = startProgram(gl, FULLSCREEN_VS, RIBBON_FS, 'flow-ribbon');

  const cube = gl.createTexture();
  gl.bindTexture(gl.TEXTURE_CUBE_MAP, cube);
  for (let f = 0; f < 6; f++) {
    gl.texImage2D(gl.TEXTURE_CUBE_MAP_POSITIVE_X + f, 0, gl.RGBA16F, size, size, 0, gl.RGBA, gl.HALF_FLOAT, null);
  }
  gl.texParameteri(gl.TEXTURE_CUBE_MAP, gl.TEXTURE_MIN_FILTER, gl.LINEAR_MIPMAP_LINEAR);
  gl.texParameteri(gl.TEXTURE_CUBE_MAP, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
  gl.texParameteri(gl.TEXTURE_CUBE_MAP, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
  gl.texParameteri(gl.TEXTURE_CUBE_MAP, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
  const fbs = [];
  for (let f = 0; f < 6; f++) {
    const fb = gl.createFramebuffer();
    gl.bindFramebuffer(gl.FRAMEBUFFER, fb);
    gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_CUBE_MAP_POSITIVE_X + f, cube, 0);
    fbs.push(fb);
  }
  gl.bindFramebuffer(gl.FRAMEBUFFER, null);

  let face = 0;
  let drawnAt = -1;

  return {
    programs: [sky, ribbon],
    setup() { finishProgram(gl, sky); finishProgram(gl, ribbon); },
    cube,
    texel: (Math.PI / 2) / size,
    /* Redraw the sky. 'faces' limits the work per frame (all 6 the first
       time); time is the flow's own clock. */
    updateSky(time, faces = 6, gain = 1) {
      gl.useProgram(sky.p);
      gl.viewport(0, 0, size, size);
      gl.uniform1f(sky.u.uTime, time);
      gl.uniform1f(sky.u.uGain, gain);
      for (let i = 0; i < faces; i++) {
        gl.bindFramebuffer(gl.FRAMEBUFFER, fbs[face]);
        gl.uniform1i(sky.u.uFace, face);
        draw(gl);
        face = (face + 1) % 6;
      }
      gl.bindFramebuffer(gl.FRAMEBUFFER, null);
      gl.bindTexture(gl.TEXTURE_CUBE_MAP, cube);
      gl.generateMipmap(gl.TEXTURE_CUBE_MAP);
      drawnAt = time;
    },
    get drawnAt() { return drawnAt; },
    /* Draw the footer ribbon into the bound target, inside rect
       (target pixels, origin bottom-left). */
    drawRibbon(time, res, rect, gain) {
      gl.useProgram(ribbon.p);
      gl.uniform2f(ribbon.u.uRes, res[0], res[1]);
      gl.uniform4f(ribbon.u.uRect, rect[0], rect[1], rect[2], rect[3]);
      gl.uniform1f(ribbon.u.uTime, time);
      gl.uniform1f(ribbon.u.uGain, gain);
      gl.enable(gl.SCISSOR_TEST);
      gl.scissor(Math.floor(rect[0]), Math.floor(Math.max(rect[1], 0)), Math.ceil(rect[2]), Math.ceil(rect[3]));
      // added on top of the sky, so it rises out of it without a seam
      gl.enable(gl.BLEND);
      gl.blendFunc(gl.ONE, gl.ONE);
      draw(gl);
      gl.disable(gl.BLEND);
      gl.disable(gl.SCISSOR_TEST);
    },
  };
}
