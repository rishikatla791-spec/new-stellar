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
      uint h = pcg(uint(c.x + 4096) * 7919u + uint(c.y + 4096) * 104729u + uint(face) * 7u + uint(cells) * 131u + 977u);
      vec2 jit = vec2(float((h >> 10u) & 1023u), float((h >> 20u) & 1023u)) / 1023.0;
      vec3 sd = faceDir(face, ((vec2(c) + 0.25 + 0.5 * jit) / cells) * 2.0 - 1.0);
      // galaxies gather in groups and filaments, with empty voids between
      float web = textureLod(uNoise, sd * 1.3 + vec3(0.7, 0.2, 0.4), 0.0).r;
      float cluster = smoothstep(0.5, 0.74, web);
      if (float(h & 1023u) > density * cluster * 2.2 * 1024.0) continue;
      uint h2 = pcg(h);
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

/* Light seen through a lens, drawn honestly. A pixel does not see a point
 * of the sky but a small patch, and near the wormhole that patch is
 * stretched: squeezed one way, drawn out the other. Each source (a star,
 * a galaxy speck) is a small gaussian on the sky; what a pixel receives is
 * that source spread over the pixel's own footprint, worked out from the
 * screen-space derivatives of the sky direction. So where lensing stretches
 * the sky, one star lights a thin curved arc of pixels - the stretched
 * starlight round the film's wormhole - with its total light conserved.
 * Needs HASH, STARS (cube-face cells) and uNoise. */
export const LENSED = /* glsl */`
  struct Foot { vec3 d; vec3 u; vec3 v; vec2 jx; vec2 jy; float omega; float pix2; };
  // The four nearest cells of each grid, and the two layers of stars and
  // of galaxies: uniforms (always 4 and 2), so the compiler keeps these as
  // loops - one copy of each body - instead of unrolling every copy, which
  // keeps the shaders quick to compile.
  uniform int uTaps;
  uniform int uLayers;

  // d: sky direction of this pixel; ddx, ddy: its change per pixel; pix:
  // how much sky one pixel would cover with no lens.
  Foot footprint(vec3 d, vec3 ddx, vec3 ddy, float pix) {
    Foot F;
    F.d = d;
    F.pix2 = pix * pix;
    vec3 t = ddx - dot(ddx, d) * d;
    if (dot(t, t) < 1e-24) t = cross(d, vec3(0.31, 0.95, 0.07));
    F.u = normalize(t);
    F.v = cross(d, F.u);
    F.jx = vec2(dot(ddx, F.u), dot(ddx, F.v));
    F.jy = vec2(dot(ddy, F.u), dot(ddy, F.v));
    F.omega = abs(F.jx.x * F.jy.y - F.jx.y * F.jy.x);
    return F;
  }

  // A gaussian source at s with sky covariance S (in the footprint's basis),
  // as this pixel sees it. 1.0 at the centre of an unlensed point source.
  // Surface brightness is conserved: where the lens squeezes the sky into a
  // pixel, each source in it is dimmed by the squeeze (the magnification);
  // where it stretches the sky, a source is spread into an arc.
  float lensedBlob(Foot F, vec3 s, mat2 S) {
    vec3 dl = s - F.d;
    vec2 q = vec2(dot(dl, F.u), dot(dl, F.v));
    const float K = 0.3;
    mat2 C = S + K * (outerProduct(F.jx, F.jx) + outerProduct(F.jy, F.jy));
    float det = max(C[0][0] * C[1][1] - C[1][0] * C[0][1], 1e-26);
    vec2 ciq = vec2(C[1][1] * q.x - C[1][0] * q.y, C[0][0] * q.y - C[0][1] * q.x) / det;
    float e = dot(q, ciq);
    if (e > 24.0) return 0.0;
    return F.pix2 * K * exp(-0.5 * e) * inversesqrt(det);
  }

  // The same for an extended source given by its surface brightness (1.0
  // at its centre): lensing keeps surface brightness, so a galaxy squeezed
  // into a few pixels is as bright as ever, only smaller - until it is
  // finer than a pixel, when the pixel shows its average.
  float lensedSB(Foot F, vec3 s, mat2 S) {
    vec3 dl = s - F.d;
    vec2 q = vec2(dot(dl, F.u), dot(dl, F.v));
    const float K = 0.3;
    mat2 C = S + K * (outerProduct(F.jx, F.jx) + outerProduct(F.jy, F.jy));
    float det = max(C[0][0] * C[1][1] - C[1][0] * C[0][1], 1e-26);
    vec2 ciq = vec2(C[1][1] * q.x - C[1][0] * q.y, C[0][0] * q.y - C[0][1] * q.x) / det;
    float e = dot(q, ciq);
    if (e > 24.0) return 0.0;
    float detS = S[0][0] * S[1][1] - S[1][0] * S[0][1];
    return exp(-0.5 * e) * sqrt(max(detS, 1e-30) / det);
  }

  // stars: real points of light with a tiny angular size
  vec3 starLayerJ(Foot F, float cells, float density, float fluxScale, uint salt) {
    int face;
    vec3 f = cubeFace(F.d, face);
    vec2 g = (f.xy / f.z * 0.5 + 0.5) * cells;
    ivec2 c0 = ivec2(floor(g - 0.5));
    const float SS = 0.00032;
    mat2 S = mat2(SS * SS, 0.0, 0.0, SS * SS);
    vec3 acc = vec3(0.0);
    for (int i = 0; i < uTaps; i++) {
      ivec2 c = c0 + ivec2(i & 1, i >> 1);
      uint h = pcg(uint(c.x + 4096) * 6151u + uint(c.y + 4096) * 3079u * 2u + uint(face) * 196613u + salt * 805306457u);
      if (float(h & 1023u) > density * 1024.0) continue;
      uint h2 = pcg(h);
      vec2 jit = vec2(float((h >> 10u) & 1023u), float((h >> 20u) & 1023u)) / 1023.0;
      vec3 sd = faceDir(face, ((vec2(c) + 0.25 + 0.5 * jit) / cells) * 2.0 - 1.0);
      float m = float(h2 & 65535u) / 65535.0;
      float flux = fluxScale * (m * m * m * m * m * m + 0.025);
      acc += starColor(float(h2 >> 16u) / 65535.0) * flux * lensedBlob(F, sd, S);
    }
    return acc;
  }
  // both layers: many faint stars (weight w.x), fewer bright ones (w.y)
  vec3 starsJ(Foot F, vec2 w) {
    vec3 acc = vec3(0.0);
    for (int L = 0; L < uLayers; L++) {
      bool b = L == 1;
      acc += starLayerJ(F, b ? 70.0 : 300.0, b ? 0.26 : 0.11, b ? 2.2 * w.y : 0.32 * w.x, uint(L + 1));
    }
    return acc;
  }

  // galaxies: warm elongated specks, each a bright core in a faint halo,
  // drawn by surface brightness (sb: the cores' brightness)
  // (cells: grid per cube face; a galaxy must stay within about one cell,
  // so larger galaxies use a coarser grid)
  vec3 smallGalaxiesJ(Foot F, float density, float scale, float cells, float sb) {
    int face;
    vec3 f = cubeFace(F.d, face);
    vec2 g = (f.xy / f.z * 0.5 + 0.5) * cells;
    ivec2 c0 = ivec2(floor(g - 0.5));
    vec3 acc = vec3(0.0);
    for (int i = 0; i < uTaps; i++) {
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
      vec2 a2 = vec2(dot(ax, F.u), dot(ax, F.v)), b2 = vec2(dot(ay, F.u), dot(ay, F.v));
      float sz = float((h2 >> 10u) & 255u) / 255.0;
      float size = scale * (0.0012 + 0.0040 * sz * sz);
      float ratio = 0.25 + 0.6 * float((h2 >> 18u) & 255u) / 255.0;
      mat2 core = size * size * 0.06 * (outerProduct(a2, a2) + ratio * ratio * outerProduct(b2, b2));
      mat2 halo = size * size * (outerProduct(a2, a2) + ratio * ratio * outerProduct(b2, b2));
      // old yellow-orange stars in the cores, a few bluer
      float hue = float((h2 >> 26u) & 15u) / 15.0;
      vec3 col = hue > 0.85 ? vec3(0.80, 0.88, 1.0) : mix(vec3(1.0, 0.62, 0.36), vec3(1.0, 0.84, 0.66), hue);
      acc += col * sb * (0.6 + 0.8 * sz) * (lensedSB(F, sd, core) + 0.022 * lensedSB(F, sd, halo));
    }
    return acc;
  }
  // both layers: a few large near galaxies, more small far ones
  vec3 galaxiesJ(Foot F, float density) {
    vec3 acc = vec3(0.0);
    for (int L = 0; L < uLayers; L++) {
      bool b = L == 1;
      acc += smallGalaxiesJ(F, density * (b ? 0.08 : 0.4), b ? 2.2 : 6.0, b ? 34.0 : 9.0, b ? 0.6 : 1.6);
    }
    return acc;
  }

  // knots of young stars in a star-forming cloud: small, very bright,
  // blue-white or pink; resolved, so drawn by surface brightness and still
  // bright where the lens squeezes the cloud into a few pixels
  vec3 knotsJ(Foot F, float cells, float density, float size) {
    int face;
    vec3 f = cubeFace(F.d, face);
    vec2 g = (f.xy / f.z * 0.5 + 0.5) * cells;
    ivec2 c0 = ivec2(floor(g - 0.5));
    vec3 acc = vec3(0.0);
    for (int i = 0; i < uTaps; i++) {
      ivec2 c = c0 + ivec2(i & 1, i >> 1);
      uint h = pcg(uint(c.x + 8192) * 7919u + uint(c.y + 8192) * 15485863u + uint(face) * 104729u + 4242u);
      if (float(h & 1023u) > density * 1024.0) continue;
      uint h2 = pcg(h);
      vec2 jit = vec2(float((h >> 10u) & 1023u), float((h >> 20u) & 1023u)) / 1023.0;
      vec3 sd = faceDir(face, ((vec2(c) + 0.25 + 0.5 * jit) / cells) * 2.0 - 1.0);
      float m = float(h2 & 1023u) / 1023.0;
      float s = size * (0.4 + 0.6 * m);
      float br = 0.25 + 3.0 * m * m * m;
      float hue = float((h2 >> 10u) & 63u) / 63.0;
      vec3 col = hue < 0.6 ? vec3(0.80, 0.88, 1.0) : vec3(1.0, 0.62, 0.72);
      acc += col * br * lensedSB(F, sd, mat2(s * s, 0.0, 0.0, s * s));
    }
    return acc;
  }

  // How far apart two neighbouring pixels are on the sky, at most (for
  // filtering smooth light such as dust: no detail finer than a pixel).
  float footSize(Foot F) {
    return sqrt(max(dot(F.jx, F.jx), dot(F.jy, F.jy)));
  }
`;

/* The far universe - the one the wormhole opens onto and Gargantua lives
 * in - drawn through a pixel footprint (LENSED), so the wormhole and the
 * black hole bend the same sky and the hand-off between them cannot jump.
 * Directions are in the world frame of the arrival camera, which looks
 * roughly along -x. */
export const FARSKY = /* glsl */`
  uniform float uFarGalaxies;   // how many galaxies
  uniform float uGG;            // the great galaxy's brightness

  // The far universe, as the film shows it through the mouth: black space,
  // one great galaxy, one grainy cloud of stars and dust, a scatter of tiny
  // galaxies, faint stars. Everything is light on the sky, so the wormhole's
  // lensing alone curves, squeezes and repeats it.
  // (Directions in the world frame of the arrival camera, which looks
  // along about -x with +z to its left. Seen through the mouth, the far sky
  // is laid out by angle from the way ahead: the centre of the sphere shows
  // 0 degrees, half its radius ~86, 0.6 ~107, 0.7 ~131, 0.75 ~145, 0.85
  // straight behind; beyond that the whole sky again, squeezed. So these
  // sit where the film has them in the sphere, and are out of the arrival
  // view.)
  // 137 degrees from the way ahead, to the left, a little up: the great
  // galaxy, near three quarters of the way out on the sphere's left. The
  // lens stretches it ~5x along the sphere's edge there, so to look as it
  // does in the film it is long the other way: its long axis points back
  // toward straight behind, and it ends well short of that point, where
  // anything is smeared round the whole sphere (a ring).
  const vec3 G_DIR = vec3(0.731, 0.17, 0.6611);
  const vec3 G_AXIS = vec3(0.0, 0.9686, -0.2491);     // its short axis on the sky
  // 90 degrees from the way ahead, below and a little left: the cluster,
  // halfway out on the sphere, low
  const vec3 C_DIR = vec3(0.0, -0.9659, 0.2588);

  // (km: where its knots shine, and how brightly)
  vec3 greatGalaxy(Foot F, out float km) {
    km = 0.0;
    vec3 x = F.d - G_DIR * dot(F.d, G_DIR);
    vec3 ay = normalize(G_AXIS - G_DIR * dot(G_AXIS, G_DIR));
    vec3 ax = cross(ay, G_DIR);
    vec2 p = vec2(dot(x, ax), dot(x, ay)) / 0.34;          // ~45 degrees long: a near neighbour
    p.y /= 0.35;                                            // seen at a tilt
    float r = length(p);
    if (r > 2.2 || dot(F.d, G_DIR) < 0.0) return vec3(0.0);
    float blur = clamp(footSize(F) / 0.02, 0.0, 1.0);       // finer than a pixel: smooth it
    float th = atan(p.y, p.x);
    // two arms wound in a log spiral, ragged with star-forming knots
    vec4 n = textureLod(uNoise, vec3(p * 1.4, 0.31), 0.0);
    vec4 n2 = textureLod(uNoise, vec3(p * 4.0, 0.57), 0.0);
    float sw = th + 3.2 * log(r + 0.05) + 0.6 * (n.r - 0.5);
    float arms0 = pow(0.5 + 0.5 * cos(2.0 * sw), 3.0);
    float arms = mix(arms0, 0.3, blur);
    float disk = exp(-r * 1.5) * (1.0 - smoothstep(1.2, 2.1, r));
    float bulge = exp(-r * r / 0.02) * 3.0 + exp(-r * r / 0.12) * 0.6;
    // warm, as in the film: a gold core, peach arms, pink knots
    vec3 armCol = mix(vec3(1.0, 0.78, 0.62), vec3(0.92, 0.74, 0.78), smoothstep(0.4, 1.4, r));
    vec3 c = armCol * disk * (0.08 + 1.1 * arms * (0.6 + 0.8 * n2.b)) * 0.6;
    // dark dust lanes winding with the arms, on the near side of the bulge
    float lane = smoothstep(0.55, 0.8, 0.5 + 0.5 * cos(2.0 * sw + 1.3)) * smoothstep(0.08, 0.5, r) * (0.6 + 0.4 * n.g);
    float clear = 1.0 - 0.75 * mix(lane, 0.3 * lane, blur);
    // the star-forming knots strung along the arms (drawn in farLight)
    km = smoothstep(0.2, 0.7, arms0) * (0.1 + 1.4 * disk) * (1.0 - smoothstep(1.4, 1.9, r)) * clear;
    return c * clear + vec3(1.0, 0.74, 0.44) * bulge * 0.6;
  }

  // The cluster, low in the sphere: a ragged cloud of young stars and
  // glowing gas some forty degrees long, drawn out on a diagonal and torn
  // by dust - lit by its knots rather than by a glow of its own.
  // (km: where its knots shine, and how brightly)
  vec3 cluster(Foot F, float cd, out float km) {
    km = 0.0;
    if (cd < 0.75) return vec3(0.0);
    vec3 cx = normalize(cross(C_DIR, vec3(0.0, 0.0, 1.0)));
    vec3 cy = cross(C_DIR, cx);
    vec2 p = mat2(0.8, 0.6, -0.6, 0.8) * vec2(dot(F.d, cx), dot(F.d, cy)) / 0.27;
    vec4 n1 = textureLod(uNoise, vec3(p * 0.3, 0.73), 0.0);
    vec4 n2 = textureLod(uNoise, vec3(p * 0.3 + (n1.rg - 0.5) * 0.3, 0.41), 0.0);
    vec2 w = (p + 1.6 * (n1.rg - 0.5)) * vec2(0.6, 1.25);
    float body = exp(-dot(w, w));
    float blur = clamp(footSize(F) / 0.012, 0.0, 1.0);    // finer than a pixel: smooth it
    float gas = body * mix(smoothstep(0.35, 0.8, n2.b * 0.6 + n2.a * 0.4), 0.35, blur);
    float dust = smoothstep(0.52, 0.72, n2.g) * (1.0 - blur);
    vec3 glow = mix(vec3(0.82, 0.50, 0.62), vec3(0.50, 0.64, 0.84), n1.b) * gas * 0.14;
    float clear = 1.0 - 0.85 * dust * smoothstep(0.1, 0.4, body);
    km = 0.7 * smoothstep(0.15, 0.7, body * (0.3 + 1.2 * n2.b * n2.b)) * clear;
    return glow * clear;
  }

  // the far universe's stars: fainter than ours
  const vec2 FAR_STARS = vec2(0.2, 0.45);

  // Everything in the far universe but its stars.
  vec3 farLight(Foot F) {
    // Mostly black, as in the film: a scatter of galaxies - a few large
    // near ones, more small far ones - the great galaxy, the cluster, and
    // faint stars (farSky). (Stars are points and fade where the lens
    // squeezes them; the galaxies and knots keep their brightness, only
    // shrink.)
    float cd = dot(F.d, C_DIR);
    float gk, ck;
    vec3 gg = greatGalaxy(F, gk) * uGG;
    vec3 c = galaxiesJ(F, uFarGalaxies) + cluster(F, cd, ck);
    // The knots of both, from one grid (the two are ninety degrees apart):
    // resolved sources, so they stay sharp and bright where the lens
    // squeezes them, and stretch into arcs along the sphere's edge.
    if (gk + ck > 0.0) {
      bool inG = gk > 0.0;
      vec3 kn = knotsJ(F, inG ? 140.0 : 80.0, inG ? 0.8 : 0.4, inG ? 0.0035 : 0.005);
      if (inG) gg += kn * vec3(1.0, 0.72, 0.68) * gk * uGG;
      else c += kn * vec3(1.0, 0.86, 0.84) * ck;
    }
    c += gg;
    // a faint veil of cold dust across part of the sky
    vec4 n = textureLod(uNoise, F.d * 1.7 + vec3(0.31, 0.17, 0.53), 0.0);
    c += vec3(0.30, 0.42, 0.46) * smoothstep(0.62, 0.9, n.r * 0.6 + n.a * 0.4) * 0.007;
    // Where the lens squeezes a large patch of this sky into one pixel (the
    // repeated images just inside the wormhole's edge), the pixel shows the
    // patch's average light - this sky's mean, cool grey - rather than
    // whatever lies at its centre. (The film's glassy inner band.)
    float fs = footSize(F);
    float wide = smoothstep(0.02, 0.08, fs);
    if (wide > 0.0) {
      // The average light of a large patch follows the sky's large-scale
      // structure: brighter where galaxies crowd (and over the great galaxy
      // and the cluster), dark in the voids. Squeezed into the band, that
      // variation comes out as irregular arcs and gaps, not a ring; the
      // more of the sky a pixel holds, the more it tends to the sky's
      // average, a cool glassy grey (the film's band measures ~RGB 20-50).
      vec4 wn = textureLod(uNoise, F.d * 0.7 + vec3(0.7, 0.2, 0.4), 0.0);
      float group = smoothstep(0.38, 0.8, wn.r * 0.6 + wn.g * 0.4);
      // (a pixel holding more than a radian of sky - deep in the nested
      // images as we enter - holds mostly black: there it goes dark)
      float avg = smoothstep(0.3, 1.4, fs);
      vec3 mean = vec3(0.0140, 0.0156, 0.0172) * mix(0.15 + 2.1 * group * group, 0.1, avg)
                + vec3(0.012, 0.010, 0.011) * smoothstep(0.75, 0.97, cd) + gg * 0.6;
      c = mix(c, mean, wide);
    }
    return c;
  }

  vec3 farSky(Foot F) {
    return farLight(F) + starsJ(F, FAR_STARS);
  }
`;
