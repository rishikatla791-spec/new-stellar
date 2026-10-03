/* The tesseract: the lattice from the film's bookshelf scene, where the
 * room is laid out along time - every object stretched into a streak.
 *
 * Geometry (a distance field, ray marched): an endless three-dimensional
 * grid of walls, one family running along each axis, crossing every 4
 * units, so every corridor is lined with square frames receding to
 * infinity. Each wall is a bundle of thin slats - worldlines - of varied
 * thickness with gaps between them, so light falls into the cracks and the
 * walls have real depth (only the outer two layers are slats; the core is
 * solid, which keeps the march short).
 *
 * Shading: each slat has its own colour from the film's palette - mostly
 * dark walnut and umber, then tan, parchment, a few cream-white and teal
 * strands, rare oxblood - and its brightness drifts along its length as
 * the object it came from moved through time. Warm light runs down the
 * corridors, slats glint, the cracks between them are dark, and an amber
 * haze swallows the distance.
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

  const float C = 4.0;      // lattice cell
  const float W = 0.7;      // half width of a wall
  const float S = 0.058;    // slat pitch

  float box2(vec2 q, vec2 w) {
    vec2 d = abs(q) - w;
    return length(max(d, 0.0)) + min(max(d.x, d.y), 0.0);
  }

  // One wall family. q: position across the wall (2D), cid: which wall.
  // Returns distance and the slat's hash.
  vec2 wall(vec2 q, vec2 cid, uint fam) {
    float dOut = box2(q, vec2(W));
    if (dOut > 0.25) return vec2(dOut, 0.0);          // far outside: skip the slats
    float dCore = box2(q, vec2(W - 2.0 * S));         // solid core
    vec2 si = floor(q / S);
    vec2 sq = q - (si + 0.5) * S;
    uint h = pcg(uint(int(si.x) + 512) * 7919u + uint(int(si.y) + 512) * 104729u
               + uint(int(cid.x) + 4096) * 31u + uint(int(cid.y) + 4096) * 1543u + fam * 92821u);
    float r = float(h & 255u) / 255.0;
    float present = step(0.2, float((h >> 8u) & 255u) / 255.0);
    float hw = S * (0.2 + 0.28 * r);
    float dS = present > 0.5 ? box2(sq, vec2(hw))
                             : (S * 0.5 - max(abs(sq.x), abs(sq.y))) + S * 0.02;
    float d = min(dCore, max(dOut, dS));
    return vec2(d, float(h >> 16u));
  }

  // x: distance, y: family (0 x, 1 y, 2 z), z: slat hash
  vec3 mapT(vec3 p) {
    vec2 cx = floor(p.yz / C + 0.5);
    vec2 a = wall(p.yz - C * cx, cx, 1u);
    vec2 cy = floor(p.xz / C + 0.5);
    vec2 b = wall(p.xz - C * cy, cy, 2u);
    vec2 cz = floor(p.xy / C + 0.5);
    vec2 c = wall(p.xy - C * cz, cz, 3u);
    vec3 m = vec3(a.x, 0.0, a.y);
    if (b.x < m.x) m = vec3(b.x, 1.0, b.y);
    if (c.x < m.x) m = vec3(c.x, 2.0, c.y);
    return m;
  }

  // Every surface is a face of an axis-aligned square (a slat, the core or
  // the wall's outline), so the normal is exact and cheap: the face of the
  // square nearest the point, in the wall's cross-section.
  vec2 squareNormal(vec2 r, float w) {
    vec2 a = abs(r);
    return a.x > a.y ? vec2(sign(r.x), 0.0) : vec2(0.0, sign(r.y));
  }
  vec2 wallNormal(vec2 q) {
    float dCore = box2(q, vec2(W - 2.0 * S));
    vec2 si = floor(q / S);
    vec2 sq = q - (si + 0.5) * S;
    float dOut = box2(q, vec2(W));
    float dSlat = max(abs(sq.x), abs(sq.y));
    if (dCore < 0.004) return squareNormal(q, W - 2.0 * S);
    if (dOut > -0.004) return squareNormal(q, W);
    return squareNormal(sq, dSlat);
  }

  // The film's palette for the worldlines (linear light).
  vec3 worldline(float h) {
    vec3 c;
    if (h < 0.22) c = vec3(0.16, 0.10, 0.06);        // dark walnut
    else if (h < 0.42) c = vec3(0.34, 0.22, 0.13);   // umber
    else if (h < 0.58) c = vec3(0.55, 0.41, 0.27);   // tan
    else if (h < 0.74) c = vec3(0.78, 0.68, 0.54);   // parchment
    else if (h < 0.86) c = vec3(0.96, 0.92, 0.84);   // cream-white
    else if (h < 0.93) c = vec3(0.34, 0.62, 0.60);   // teal
    else if (h < 0.96) c = vec3(0.46, 0.14, 0.10);   // oxblood
    else c = vec3(1.0, 0.97, 0.92);                  // white light
    return pow(c, vec3(2.2));
  }

  void main() {
    vec2 ndc = vUv * 2.0 - 1.0;
    vec3 rd = normalize(uCamRot * vec3(ndc.x * uAspect * uTanFov, ndc.y * uTanFov, 1.0));
    vec3 ro = uCamPos;

    float t = 0.0;
    vec3 hit = vec3(-1.0);
    const float TMAX = 80.0;
    for (int i = 0; i < 120; i++) {
      vec3 m = mapT(ro + rd * t);
      if (m.x < 0.0008 * (1.0 + t)) { hit = vec3(t, m.y, m.z); break; }
      t += m.x * 0.85;
      if (t > TMAX) break;
    }

    // amber haze, brighter down the corridor ahead, dusk at the sides
    float ahead = max(rd.z, 0.0);
    vec3 haze = mix(vec3(0.018, 0.012, 0.008), vec3(0.16, 0.10, 0.055), pow(ahead, 6.0)) * uGlow;
    vec3 col;
    if (hit.x < 0.0) {
      col = haze + vec3(1.0, 0.85, 0.62) * pow(ahead, 120.0) * 2.0 * uGlow;
    } else {
      vec3 p = ro + rd * hit.x;
      int fam = int(hit.y + 0.5);
      // along the slat (its time axis) and how deep in the wall it sits
      float along = fam == 0 ? p.x : (fam == 1 ? p.y : p.z);
      vec2 q = fam == 0 ? p.yz : (fam == 1 ? p.xz : p.xy);
      q -= C * floor(q / C + 0.5);
      vec2 n2 = wallNormal(q);
      vec3 n = fam == 0 ? vec3(0.0, n2) : (fam == 1 ? vec3(n2.x, 0.0, n2.y) : vec3(n2, 0.0));
      if (dot(n, rd) > 0.0) n = -n;
      float depth = clamp((W - max(abs(q.x), abs(q.y))) / (2.0 * S), 0.0, 1.0);
      uint h = uint(hit.z);
      float hv = float(h & 1023u) / 1023.0;
      vec3 base = worldline(hv);
      // the object moved through time: its brightness drifts along the slat
      float drift = 0.55 + 0.45 * sin(along * (0.12 + 0.35 * float((h >> 10u) & 63u) / 63.0) + hv * 40.0);
      float streaks = 0.75 + 0.25 * sin(along * 7.0 + hv * 90.0) * sin(along * 2.3 + hv * 13.0);
      base *= drift * streaks;
      // light: warm, from down the corridors; a little teal fill
      vec3 L = normalize(vec3(0.25, 0.45, 0.86));
      float diff = max(dot(n, L), 0.0);
      float fill = max(dot(n, normalize(vec3(-0.6, -0.3, 0.4))), 0.0);
      float spec = pow(max(dot(reflect(rd, n), L), 0.0), 40.0);
      float ao = 1.0 - 0.85 * depth;                  // the cracks are dark
      vec3 lit = base * (0.02 + 1.8 * diff * vec3(1.0, 0.9, 0.75) + 0.22 * fill * vec3(0.55, 0.8, 0.85)) * ao;
      // glossy: a sharp glint along each slat's edge
      lit += vec3(1.0, 0.92, 0.8) * spec * 1.2 * ao * (0.3 + hv);
      // the white strands glow faintly on their own
      if (hv > 0.97) lit += base * 1.5 * drift;
      // far away the slats are finer than a pixel: fade them toward their
      // average so they do not shimmer
      float tiny = smoothstep(6.0, 22.0, hit.x * uTanFov * 1000.0 / 1080.0 * 18.0);
      lit = mix(lit, worldline(0.5) * (0.05 + 0.8 * diff) * 0.6, tiny * 0.6);
      float fog = 1.0 - exp(-hit.x * 0.035);
      col = mix(lit, haze, fog);
    }
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
      const pos = [2 + Math.sin(z * 0.11) * 0.22, 2 + Math.cos(z * 0.09) * 0.2, z];
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
