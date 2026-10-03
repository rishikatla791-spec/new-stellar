/* GLSL shared by the passes: hashing, noise, the star field, colour. */

export const HASH = /* glsl */`
  // Integer hashes (no sin), stable across GPUs.
  uint pcg(uint v) {
    uint s = v * 747796405u + 2891336453u;
    uint w = ((s >> ((s >> 28u) + 4u)) ^ s) * 277803737u;
    return (w >> 22u) ^ w;
  }
  float h1(uint v) { return float(pcg(v)) * (1.0 / 4294967296.0); }
  uint hseed(ivec3 c) {
    return pcg(uint(c.x) * 73856093u ^ pcg(uint(c.y) * 19349663u ^ pcg(uint(c.z) * 83492791u)));
  }
`;

export const NOISE = /* glsl */`
  // 3D gradient noise (Perlin-style quintic), roughly in [-1, 1].
  vec3 grad3(ivec3 c) {
    uint h = hseed(c);
    float z = float(h & 1023u) * (2.0 / 1023.0) - 1.0;
    float a = float(h >> 10u) * (6.2831853 / 4194304.0);
    float s = sqrt(max(1.0 - z * z, 0.0));
    return vec3(s * cos(a), s * sin(a), z);
  }
  float gnoise(vec3 p) {
    ivec3 i = ivec3(floor(p));
    vec3 f = fract(p);
    vec3 w = f * f * f * (f * (f * 6.0 - 15.0) + 10.0);
    float n000 = dot(grad3(i), f);
    float n100 = dot(grad3(i + ivec3(1, 0, 0)), f - vec3(1, 0, 0));
    float n010 = dot(grad3(i + ivec3(0, 1, 0)), f - vec3(0, 1, 0));
    float n110 = dot(grad3(i + ivec3(1, 1, 0)), f - vec3(1, 1, 0));
    float n001 = dot(grad3(i + ivec3(0, 0, 1)), f - vec3(0, 0, 1));
    float n101 = dot(grad3(i + ivec3(1, 0, 1)), f - vec3(1, 0, 1));
    float n011 = dot(grad3(i + ivec3(0, 1, 1)), f - vec3(0, 1, 1));
    float n111 = dot(grad3(i + ivec3(1, 1, 1)), f - vec3(1, 1, 1));
    return 1.6 * mix(mix(mix(n000, n100, w.x), mix(n010, n110, w.x), w.y),
                     mix(mix(n001, n101, w.x), mix(n011, n111, w.x), w.y), w.z);
  }
  float fbm3(vec3 p, int oct) {
    float s = 0.0, a = 0.5;
    for (int i = 0; i < 6; i++) {
      if (i >= oct) break;
      s += a * gnoise(p);
      p = p * 2.03 + vec3(1.7, 9.2, 3.1);
      a *= 0.5;
    }
    return s;
  }
`;

/* Stars on the sky, looked up by direction. Each star is a point of light
 * with a flux; a pixel shows the light of whatever patch of sky it covers.
 * Where gravity squeezes a lot of sky into one pixel (near the shadow) the
 * stars get fainter, and where it magnifies they get brighter, exactly as
 * lensing does: the footprint (sky area per pixel) comes from screen-space
 * derivatives of the looked-up direction. */
export const STARS = /* glsl */`
  vec3 starColor(float t) {
    // cool red-orange .. white .. blue-white
    vec3 warm = vec3(1.0, 0.72, 0.48);
    vec3 white = vec3(1.0, 0.97, 0.94);
    vec3 cool = vec3(0.70, 0.80, 1.0);
    return t < 0.5 ? mix(warm, white, t * 2.0) : mix(white, cool, t * 2.0 - 1.0);
  }

  // Cube-face cell grid: face id, cell coordinate, position in the face.
  vec3 cubeFace(vec3 d, out int face) {
    vec3 a = abs(d);
    if (a.x >= a.y && a.x >= a.z) { face = d.x > 0.0 ? 0 : 1; return vec3(d.y, d.z, a.x); }
    if (a.y >= a.z) { face = d.y > 0.0 ? 2 : 3; return vec3(d.x, d.z, a.y); }
    face = d.z > 0.0 ? 4 : 5; return vec3(d.x, d.y, a.z);
  }
  vec3 faceDir(int face, vec2 uv) {
    if (face == 0) return normalize(vec3(1.0, uv.x, uv.y));
    if (face == 1) return normalize(vec3(-1.0, uv.x, uv.y));
    if (face == 2) return normalize(vec3(uv.x, 1.0, uv.y));
    if (face == 3) return normalize(vec3(uv.x, -1.0, uv.y));
    if (face == 4) return normalize(vec3(uv.x, uv.y, 1.0));
    return normalize(vec3(uv.x, uv.y, -1.0));
  }

  // One layer of stars, at most one per cell, kept away from the cell's
  // edges so only the 2x2 cells nearest the point can reach it.
  // sig: angular footprint of this pixel on the sky (radians); pix: the
  // footprint of an unlensed pixel.
  vec3 starLayer(vec3 d, float cells, float density, float fluxScale, float sig, float pix, uint salt) {
    int face;
    vec3 f = cubeFace(d, face);
    vec2 g = (f.xy / f.z * 0.5 + 0.5) * cells;
    ivec2 c0 = ivec2(floor(g - 0.5));
    float s2 = sig * sig;
    float k = min(pix * pix / s2, 2.5);   // magnified stars brighten, within reason
    vec3 acc = vec3(0.0);
    for (int i = 0; i < 4; i++) {
      ivec2 c = c0 + ivec2(i & 1, i >> 1);
      uint h = pcg(uint(c.x + 4096) * 6151u + uint(c.y + 4096) * 3079u * 2u + uint(face) * 196613u + salt * 805306457u);
      if (float(h & 1023u) > density * 1024.0) continue;
      uint h2 = pcg(h);
      vec2 jit = vec2(float((h >> 10u) & 1023u), float((h >> 20u) & 1023u)) / 1023.0;
      vec2 suv = ((vec2(c) + 0.25 + 0.5 * jit) / cells) * 2.0 - 1.0;
      vec3 sd = faceDir(face, suv);
      vec3 dd = d - sd;
      float m = float(h2 & 65535u) / 65535.0;
      float flux = fluxScale * (m * m * m * m * m * m + 0.025);
      vec3 col = starColor(float(h2 >> 16u) / 65535.0);
      // flux spread over the pixel's footprint: brightness ~ magnification
      acc += col * flux * k * exp(-0.5 * dot(dd, dd) / s2);
    }
    return acc;
  }

  vec3 starField(vec3 d, float sig, float pix) {
    return starLayer(d, 300.0, 0.11, 0.32, sig, pix, 1u)
         + starLayer(d, 70.0, 0.26, 2.2, sig, pix, 2u);
  }
`;

export const COLOR = /* glsl */`
  vec3 srgbToLinear(vec3 c) { return pow(c, vec3(2.2)); }
`;

/* The far galaxy seen through the wormhole: the cosmic flow's brightness
 * mapped onto plum -> magenta -> violet -> pearl, so it reads as a lit,
 * dusty galaxy rather than a blue fog. */
export const FAR = /* glsl */`
  vec3 farGalaxy(vec3 f) {
    float l = dot(f, vec3(0.30, 0.40, 0.30));
    vec3 plum = vec3(0.075, 0.012, 0.052);
    vec3 mag = vec3(1.0, 0.014, 0.33);
    vec3 vio = vec3(0.20, 0.072, 1.0);
    vec3 pearl = vec3(0.94, 0.90, 0.84);
    float x = clamp(l * 3.6, 0.0, 1.6);
    vec3 c = x < 0.35 ? mix(vec3(0.0), plum, x / 0.35)
           : x < 0.75 ? mix(plum, mag * 0.55, (x - 0.35) / 0.4)
           : x < 1.15 ? mix(mag * 0.55, vio * 0.9, (x - 0.75) / 0.4)
           : mix(vio * 0.9, pearl * 1.6, (x - 1.15) / 0.45);
    // keep a little of the flow's own hue so it is not one flat ramp
    vec3 hue = f / max(max(f.r, f.g), max(f.b, 1e-4));
    return mix(c, c * hue * 1.6, 0.25) * 1.4;
  }
`;
