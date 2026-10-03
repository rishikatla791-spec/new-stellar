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

/* The far galaxy - what the film shows through the wormhole: a band of
 * dusty starlight with dark lanes, clumps of star clouds and a bright core,
 * in dusty rose, plum, cream and dusk violet. Needs a sampler3D uNoise
 * (noise3d.js). w is a direction in the destination's sky. */
export const GALAXY = /* glsl */`
  const vec3 G_POLE = vec3(0.3092, 0.9276, -0.2061);
  const vec3 G_CORE = vec3(-0.8150, 0.2445, 0.5253);
  float galaxyBand(vec3 w) {
    float h = dot(w, G_POLE);
    return exp(-h * h / 0.010);
  }
  vec3 galaxy(vec3 w) {
    float h = dot(w, G_POLE);
    float band = exp(-h * h / 0.010);
    float wide = exp(-h * h / 0.07);
    float cb = max(dot(w, G_CORE), 0.0);
    float bulge = pow(cb, 30.0) * 1.0 + pow(cb, 6.0) * 0.07;
    vec4 n = texture(uNoise, w * 1.15 + vec3(0.37, 0.11, 0.73));
    vec4 m = texture(uNoise, w * 3.1 + vec3(0.19, 0.61, 0.07));
    float clump = smoothstep(0.38, 0.82, n.r * 0.5 + n.g * 0.3 + m.b * 0.2 + m.a * 0.1);
    float lanes = smoothstep(0.46, 0.72, m.r * 0.55 + n.b * 0.45) * exp(-h * h / 0.0035);
    float lum = (band * (0.10 + 1.5 * clump * clump)
               + wide * 0.05 * (0.5 + n.g)
               + bulge * (0.6 + 0.6 * clump)) * (1.0 - 0.9 * lanes);
    vec3 rose = vec3(1.0, 0.56, 0.50);
    vec3 cream = vec3(1.0, 0.86, 0.72);
    vec3 dusk = vec3(0.56, 0.42, 0.90);
    vec3 plum = vec3(0.62, 0.26, 0.45);
    vec3 col = mix(dusk, rose, smoothstep(0.05, 0.6, band + bulge));
    col = mix(col, plum, smoothstep(0.55, 0.85, m.g) * 0.45);
    col = mix(col, cream, smoothstep(0.35, 1.2, bulge + clump * band * 0.6));
    return col * lum * 0.2;
  }
`;

/* The universe on the far side of the wormhole, as the film shows it through
 * the mouth: billowing emission clouds in rose, salmon and cream with
 * bright cores, cut by dark dust lanes, slate-grey dust at their edges,
 * and small distant galaxies scattered across black space. Needs a
 * sampler3D uNoise (noise3d.js) and the STARS chunk (cube-face cells).
 * w: a direction in the far universe. */
export const NEBULA = /* glsl */`
  // In the frame of a traveller heading through (+z ahead, +y up): one
  // great complex 20 degrees above the way ahead, so it fills the upper
  // part of the sphere, and a fainter one low on the right.
  const vec3 NB_C = vec3(0.1219, 0.3256, 0.9376);
  const vec3 NB_C2 = vec3(0.6623, -0.3974, 0.6352);
  // Fractal noise from the smooth channel of the noise volume: each octave
  // a fresh, rotated lookup, half as strong as the one before.
  float nbm(vec3 p, int oct) {
    float s = 0.0, a = 0.5;
    for (int i = 0; i < 6; i++) {
      if (i >= oct) break;
      s += a * (textureLod(uNoise, p, 0.0).r - 0.5);
      p = vec3(p.y * 1.6 + p.z * 1.2, p.z * 1.6 - p.x * 1.2, p.x * 1.6 + p.y * 1.2) * 1.03 + 0.37;
      a *= 0.5;
    }
    return s * 2.0;     // about -1..1
  }
  // Turbulence: the same octaves, folded (|n|), which gives billows - puffy
  // bright shapes with dark creases between them, like real gas clouds.
  float nbt(vec3 p, int oct) {
    float s = 0.0, a = 0.5, n = 0.0;
    for (int i = 0; i < 6; i++) {
      if (i >= oct) break;
      s += a * abs(textureLod(uNoise, p, 0.0).r * 2.0 - 1.0);
      n += a;
      p = vec3(p.y * 1.6 + p.z * 1.2, p.z * 1.6 - p.x * 1.2, p.x * 1.6 + p.y * 1.2) * 1.03 + 0.37;
      a *= 0.5;
    }
    return s / n;       // 0..1, mean about 0.45
  }
  vec3 nebula(vec3 w) {
    float m1 = dot(w, NB_C), m2 = dot(w, NB_C2);
    float edge = nbm(w * 0.9 + 5.3, 2);
    // the far universe is thick with it: it covers most of the sky ahead
    float region = max(smoothstep(-0.35, 0.85, m1 + 0.3 * edge), 0.5 * smoothstep(0.4, 0.95, m2 + 0.2 * edge));
    if (region <= 0.0) return vec3(0.0);
    float heart = smoothstep(0.80, 0.99, m1 + 0.12 * edge);   // the bright core of the complex
    vec3 p = w * 0.45;          // big: the mouth shrinks the whole sky into one disc
    vec3 q = vec3(nbm(p + 1.7, 2), nbm(p + 9.2, 2), 0.0);
    // big patches of cloud with black space between them
    float mask = smoothstep(-0.5, 0.35, nbm(p * 0.7 + 3.1, 3) + 0.6 * (region - 0.6) + 0.5 * heart);
    // billows inside the patches
    float bill = nbt(p * 2.4 + 0.3 * q, 5);
    float dens = mask * smoothstep(0.08, 0.62, bill);
    // dust: dark creases and lanes across the clouds
    float dl = 1.0 - nbt(p * 3.6 + 0.5 * q + 11.0, 3);
    float dust = smoothstep(0.62, 0.9, dl) * 0.85;
    float lum = pow(dens, 1.4) * region * (0.6 + 1.0 * heart);
    float hot = smoothstep(0.5, 1.1, lum);
    vec3 rose = vec3(0.70, 0.34, 0.32);       // dusky rose, like the film's
    vec3 salmon = vec3(1.0, 0.56, 0.48);
    vec3 cream = vec3(1.0, 0.88, 0.76);
    vec3 slate = vec3(0.42, 0.45, 0.52);
    vec3 col = mix(rose, salmon, smoothstep(0.1, 0.55, lum));
    col = mix(col, cream, hot);
    col = mix(col, slate, smoothstep(0.05, 0.45, q.x) * 0.5 * (1.0 - hot));
    lum *= 1.0 - dust;
    return col * (0.5 * lum + 0.9 * hot * lum);
  }

`;

/* The far universe, as the film shows it through and inside the wormhole:
 * black space, dense faint stars, galaxies only as tiny warm specks, and
 * faint grey dust clouds made of crowded stars. Restrained: no big
 * spirals. Looked up by direction, so the wormhole's lensing bends it.
 * Needs a sampler3D uNoise and the STARS chunk. */
export const GALAXIES = /* glsl */`
  // Star clouds: patches where faint stars crowd together, sparkling.
  vec3 starClouds(vec3 d, float sig, float pix) {
    int face;
    vec3 f = cubeFace(d, face);
    const float cells = 700.0;
    vec2 g = (f.xy / f.z * 0.5 + 0.5) * cells;
    ivec2 c0 = ivec2(floor(g - 0.5));
    float s2 = sig * sig;
    float k = min(pix * pix / s2, 2.5);
    vec3 acc = vec3(0.0);
    for (int i = 0; i < 4; i++) {
      ivec2 c = c0 + ivec2(i & 1, i >> 1);
      uint h = pcg(uint(c.x + 8192) * 7919u + uint(c.y + 8192) * 15485863u + uint(face) * 104729u + 4242u);
      vec2 jit = vec2(float((h >> 10u) & 1023u), float((h >> 20u) & 1023u)) / 1023.0;
      vec3 sd = faceDir(face, ((vec2(c) + 0.25 + 0.5 * jit) / cells) * 2.0 - 1.0);
      if (float(h & 1023u) > 260.0) continue;
      vec3 dd = d - sd;
      float br = 0.06 + 0.4 * pow(float(pcg(h) & 1023u) / 1023.0, 4.0);
      vec3 col = mix(vec3(0.75, 0.82, 1.0), vec3(1.0, 0.85, 0.7), float((h >> 4u) & 63u) / 63.0);
      acc += col * br * k * exp(-0.5 * dot(dd, dd) / s2);
    }
    return acc;
  }

  // Small distant galaxies: elongated smudges, one per cell at most.
  vec3 smallGalaxies(vec3 d, float sig, float density) {
    int face;
    vec3 f = cubeFace(d, face);
    const float cells = 34.0;
    vec2 g = (f.xy / f.z * 0.5 + 0.5) * cells;
    ivec2 c0 = ivec2(floor(g - 0.5));
    vec3 acc = vec3(0.0);
    for (int i = 0; i < 4; i++) {
      ivec2 c = c0 + ivec2(i & 1, i >> 1);
      uint h = pcg(uint(c.x + 4096) * 7919u + uint(c.y + 4096) * 104729u + uint(face) * 7u + 977u);
      if (float(h & 1023u) > density * 1024.0) continue;
      uint h2 = pcg(h);
      vec2 jit = vec2(float((h >> 10u) & 1023u), float((h >> 20u) & 1023u)) / 1023.0;
      vec3 sd = faceDir(face, ((vec2(c) + 0.25 + 0.5 * jit) / cells) * 2.0 - 1.0);
      vec3 t1 = normalize(cross(sd, abs(sd.y) < 0.9 ? vec3(0.0, 1.0, 0.0) : vec3(1.0, 0.0, 0.0)));
      vec3 t2 = cross(sd, t1);
      float ang = float(h2 & 1023u) * (6.2831853 / 1024.0);
      vec3 ax = cos(ang) * t1 + sin(ang) * t2, ay = -sin(ang) * t1 + cos(ang) * t2;
      vec3 dd = d - sd;
      float size = 0.0016 + 0.0038 * float((h2 >> 10u) & 255u) / 255.0;
      float ratio = 0.25 + 0.6 * float((h2 >> 18u) & 255u) / 255.0;
      float sx = max(size, sig), sy = max(size * ratio, sig);
      float q = pow(dot(dd, ax) / sx, 2.0) + pow(dot(dd, ay) / sy, 2.0);
      float core = exp(-q * 6.0) * 1.5 + exp(-q * 1.2) * 0.35;
      vec3 col = mix(vec3(1.0, 0.70, 0.45), vec3(1.0, 0.88, 0.74), float((h2 >> 26u) & 15u) / 15.0);
      acc += col * core * (size * size) / (sx * sy) * 0.12;
    }
    return acc;
  }

  // The far universe in direction w. sig/pix/aniso as for the star field.
  // specks: how many tiny galaxies (fraction of 1.7-degree cells).
  vec3 farSpace(vec3 w, float sig, float pix, float aniso, float specks) {
    vec3 c = starField(w, sig, pix) * (0.9 / (aniso * aniso));
    c += smallGalaxies(w, sig, specks) * 1.3;
    // faint dust clouds: grey, a little warm in their hearts, grainy
    vec4 n = textureLod(uNoise, w * 0.9 + vec3(0.31, 0.17, 0.53), 0.0);
    vec4 m = textureLod(uNoise, w * 2.6 + vec3(0.12, 0.66, 0.29), 0.0);
    float cloud = smoothstep(0.56, 0.86, n.r * 0.55 + n.g * 0.3 + m.r * 0.15);
    float grain = 0.5 + 0.9 * m.b * m.g;
    c += mix(vec3(0.55, 0.62, 0.62), vec3(0.85, 0.72, 0.6), m.a) * cloud * grain * 0.014;
    c += starClouds(w, sig, pix) * (cloud * 0.9 / aniso);
    return c;
  }
`;
