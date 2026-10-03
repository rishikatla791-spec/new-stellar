/* The tesseract: the lattice from the film's bookshelf scene, where the
 * room is laid out along time - every object stretched into a streak.
 *
 * Geometry: an endless three-dimensional grid of square beams, one family
 * along each axis (a distance field, ray marched). A second, finer grid
 * threads between them. The camera flies down a corridor of the lattice,
 * tilted so the beams run diagonally as in the film.
 *
 * Every beam is a bundle of "worldlines": thin stripes running along it in
 * the film's palette - umber, parchment, gold, a few teal and white - with
 * dark seams, so each face reads like the edges of a thousand books pulled
 * through time. Light leaks from far gaps; a haze swallows the distance.
 */
import { FULLSCREEN_VS, startProgram, finishProgram, draw } from './gl.js';
import { HASH } from './glsl.js';

const FS = /* glsl */`#version 300 es
  precision highp float;
  precision highp int;
  in vec2 vUv;
  out vec4 outColor;

  uniform float uAspect;
  uniform float uTanFov;
  uniform vec3 uCamPos;
  uniform mat3 uCamRot;
  uniform float uTime;
  uniform float uAlpha;
  uniform float uGlow;

  ${HASH}

  const float C1 = 4.0;     // big lattice: cell size
  const float W1 = 0.62;    // big beams: half width
  const float C2 = 1.0;     // fine lattice
  const float W2 = 0.045;

  float box2(vec2 q, float w) {
    vec2 d = abs(q) - w;
    return length(max(d, 0.0)) + min(max(d.x, d.y), 0.0);
  }
  vec2 cellQ(vec2 p, float c) { return p - c * floor(p / c + 0.5); }

  // distance, and which family was hit (0 x-beams, 1 y-beams, 2 z-beams;
  // +3 for the fine lattice)
  vec2 map(vec3 p) {
    float dx = box2(cellQ(p.yz, C1), W1);
    float dy = box2(cellQ(p.xz, C1), W1);
    float dz = box2(cellQ(p.xy, C1), W1);
    // the fine lattice is offset so it threads the gaps
    vec3 pf = p + vec3(0.5);
    float fx = box2(cellQ(pf.yz, C2), W2);
    float fy = box2(cellQ(pf.xz, C2), W2);
    float fz = box2(cellQ(pf.xy, C2), W2 * 1.4);
    // (the corridor the camera flies along, x = y = 2, sits half a cell
    // from every fine beam, so nothing needs carving out)
    float fine = min(fx, min(fy, fz));
    float d = dx; float id = 0.0;
    if (dy < d) { d = dy; id = 1.0; }
    if (dz < d) { d = dz; id = 2.0; }
    if (fine < d) { d = fine; id = 3.0; }
    return vec2(d, id);
  }

  vec3 normalAt(vec3 p) {
    const vec2 e = vec2(0.0015, 0.0);
    return normalize(vec3(
      map(p + e.xyy).x - map(p - e.xyy).x,
      map(p + e.yxy).x - map(p - e.yxy).x,
      map(p + e.yyx).x - map(p - e.yyx).x));
  }

  // The film's palette for the worldlines.
  vec3 worldline(float h) {
    vec3 c;
    if (h < 0.34) c = vec3(0.16, 0.10, 0.07);        // umber
    else if (h < 0.56) c = vec3(0.40, 0.28, 0.19);   // leather
    else if (h < 0.62) c = vec3(0.60, 0.54, 0.46);   // parchment
    else if (h < 0.70) c = vec3(0.42, 0.42, 0.41);   // pewter
    else if (h < 0.80) c = vec3(0.34, 0.10, 0.08);   // oxblood
    else if (h < 0.88) c = vec3(0.80, 0.64, 0.42);   // old gold
    else if (h < 0.94) c = vec3(0.20, 0.50, 0.50);   // teal
    else c = vec3(1.0, 0.95, 0.88);                  // white light
    return pow(c, vec3(2.2));                        // to linear light
  }

  // Stripes along the beam's axis. 'along' runs with the beam, 'across'
  // runs over the face.
  vec3 stripes(float across, float along, float beamId, float scale, out float emit) {
    float s = across * scale;
    float id = floor(s);
    float f = fract(s);
    uint h = pcg(uint(int(id) + 7919 * int(beamId) + 104729));
    float hv = float(h & 65535u) / 65535.0;
    // stripes come in runs of similar widths, like spines on a shelf
    vec3 c = worldline(hv);
    float seam = smoothstep(0.0, 0.08, f) * smoothstep(1.0, 0.92, f);
    // brightness drifts along the stripe, as the object moved through time
    float drift = 0.55 + 0.45 * sin(along * (0.15 + 0.4 * hv) + hv * 40.0);
    // only a few worldlines carry light, and only in stretches
    float on = smoothstep(0.55, 0.95, sin(along * (0.05 + 0.1 * hv) + hv * 17.0));
    emit = hv > 0.965 ? on : (hv > 0.82 && hv < 0.88 ? 0.25 * on : 0.0);
    return c * seam * drift;
  }

  void main() {
    vec2 ndc = vUv * 2.0 - 1.0;
    vec3 rd = normalize(uCamRot * vec3(ndc.x * uAspect * uTanFov, ndc.y * uTanFov, 1.0));
    vec3 ro = uCamPos;

    float t = 0.0;
    vec2 hit = vec2(1e9, -1.0);
    const float TMAX = 70.0;
    for (int i = 0; i < 96; i++) {
      vec2 m = map(ro + rd * t);
      if (m.x < 0.0015 * (1.0 + t)) { hit = vec2(t, m.y); break; }
      t += m.x * 0.92;
      if (t > TMAX) break;
    }

    // the haze: warm near the light, violet in the deep distance
    vec3 hazeNear = vec3(0.075, 0.046, 0.026);
    vec3 hazeFar = vec3(0.022, 0.012, 0.042);
    float along = abs(rd.z);
    vec3 haze = mix(hazeFar, hazeNear, pow(along, 3.0)) * uGlow;
    vec3 col;
    if (hit.y < 0.0) {
      // looked straight down a gap: light pouring in from far away
      col = haze * 1.4 + vec3(1.0, 0.85, 0.6) * pow(along, 60.0) * 3.0 * uGlow;
    } else {
      vec3 p = ro + rd * hit.x;
      vec3 n = normalAt(p);
      float fam = mod(hit.y, 3.0);
      bool fine = hit.y > 2.5;
      // axis of the beam we hit, and the face coordinate across it
      vec3 ax = fam < 0.5 ? vec3(1, 0, 0) : (fam < 1.5 ? vec3(0, 1, 0) : vec3(0, 0, 1));
      float alongC = dot(p, ax);
      vec3 side = normalize(cross(ax, n) + 1e-5);
      float across = dot(p, side);
      // which beam (so neighbouring beams differ)
      vec3 cellId = floor(p / C1 + 0.5);
      float beamId = dot(cellId, vec3(17.0, 59.0, 113.0)) + fam * 7.0;
      float emit;
      vec3 base = stripes(across, alongC, beamId, fine ? 26.0 : 19.0, emit);
      // a second, finer set of lines over the first
      float e2;
      vec3 fineLines = stripes(across + 0.37, alongC * 1.7, beamId + 3.0, fine ? 61.0 : 73.0, e2);
      base = mix(base, fineLines, 0.45);
      emit = max(emit, e2 * 0.4);
      // the beam's faces darken toward their edges, like shelves in shadow
      base *= 0.8 + 0.2 * smoothstep(0.0, 0.25, W1 - abs(fract(across / (2.0 * W1)) * 2.0 * W1 - W1));

      vec3 L = normalize(vec3(0.35, 0.55, 0.75));
      float diff = max(dot(n, L), 0.0);
      float rim = pow(1.0 - max(dot(n, -rd), 0.0), 3.0);
      vec3 lit = base * (0.05 + 1.45 * diff) + base * rim * 0.45;
      lit += base * emit * 5.0;
      // fog
      float fog = 1.0 - exp(-hit.x * 0.06);
      col = mix(lit, haze, fog);
      // light glancing off near beams
      col += vec3(1.0, 0.86, 0.62) * pow(max(dot(reflect(rd, n), L), 0.0), 24.0) * 0.35 * (1.0 - fog);
    }
    // shafts of light along the direction of travel
    float shaft = pow(along, 8.0) * 0.05 * uGlow;
    col += vec3(1.0, 0.82, 0.55) * shaft;
    outColor = vec4(col * uAlpha, uAlpha);
  }
`;

export function createTesseract(gl) {
  const prog = startProgram(gl, FULLSCREEN_VS, FS, 'tesseract');
  return {
    programs: [prog],
    setup() { finishProgram(gl, prog); },
    /* f: { visible 0..1, progress 0..1, glow } - drawn over the current
       target with alpha blending. */
    render(f, aspect, pix, time) {
      const u = prog.u;
      gl.useProgram(prog.p);
      const z = f.progress * 46 + time * 0.35;
      // corridor centre, with a slow sway
      const pos = [2 + Math.sin(z * 0.11) * 0.16, 2 + Math.cos(z * 0.09) * 0.14, z];
      const yaw = 0.42 + 0.08 * Math.sin(time * 0.05);
      const pitch = 0.28 + 0.05 * Math.cos(time * 0.04);
      const roll = 0.5 + f.progress * 0.9;
      gl.uniformMatrix3fv(u.uCamRot, false, rot(yaw, pitch, roll));
      gl.uniform3fv(u.uCamPos, pos);
      gl.uniform1f(u.uAspect, aspect);
      gl.uniform1f(u.uTanFov, Math.tan((f.fov || 56) * Math.PI / 360));
      gl.uniform1f(u.uTime, time);
      gl.uniform1f(u.uAlpha, f.visible);
      gl.uniform1f(u.uGlow, f.glow == null ? 1 : f.glow);
      gl.enable(gl.BLEND);
      gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
      draw(gl);
      gl.disable(gl.BLEND);
    },
  };
}

function rot(yaw, pitch, roll) {
  const cy = Math.cos(yaw), sy = Math.sin(yaw);
  const cp = Math.cos(pitch), sp = Math.sin(pitch);
  const cr = Math.cos(roll), sr = Math.sin(roll);
  const Ry = [cy, 0, -sy, 0, 1, 0, sy, 0, cy];
  const Rx = [1, 0, 0, 0, cp, sp, 0, -sp, cp];
  const Rz = [cr, sr, 0, -sr, cr, 0, 0, 0, 1];
  return mul(mul(Ry, Rx), Rz);
}
function mul(a, b) {
  const o = new Array(9);
  for (let c = 0; c < 3; c++) {
    for (let r = 0; r < 3; r++) {
      o[c * 3 + r] = a[r] * b[c * 3] + a[3 + r] * b[c * 3 + 1] + a[6 + r] * b[c * 3 + 2];
    }
  }
  return o;
}
