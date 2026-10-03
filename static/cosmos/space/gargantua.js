/* Gargantua: a non-spinning black hole with a thin accretion disk,
 * ray traced per pixel through the precomputed geodesic table.
 *
 * For each pixel:
 *   1. the camera ray's impact parameter b (as measured by a camera
 *      hovering at rest at distance r_c: b = r_c sin(a) / sqrt(1 - 1/r_c));
 *   2. where the camera sits on that orbit, psi_c (8-point Gauss-Legendre);
 *   3. the swept angles at which the ray crosses the disk plane - exact,
 *      from the orientation of the ray's plane - and the radius at each
 *      crossing from the table. The first crossing is the disk in front
 *      of the hole; the second is the far side bent over the top and the
 *      underside bent below; the third is the thin ring hugging the shadow;
 *   4. where the ray ends up: in the hole (black), or out on the sky,
 *      looked up in the escape direction - so the stars and the cosmic
 *      flow behind the hole are lensed into rings too.
 *
 * The disk is lit by its own turbulent gas: filaments in co-rotating
 * coordinates, inner parts orbiting faster (Kepler, ~r^-1.5). As in the
 * film, Doppler beaming is kept subtle (the physical value would make one
 * side ~30x brighter than the other).
 */
import { FULLSCREEN_VS, startProgram, finishProgram, target, bindTex, draw } from './gl.js';
import { HASH, STARS, GALAXIES } from './glsl.js';
import { tableGLSL } from './geodesics.js';

const DISK_TEX_FS = /* glsl */`#version 300 es
  precision highp float;
  precision highp int;
  in vec2 vUv;
  out vec4 o;
  ${HASH}
  // Gradient noise on a lattice that wraps with period 'per', so the
  // texture tiles: azimuth (x) all the way round, radius (y) too.
  float pnoise(vec2 p, ivec2 per, uint salt) {
    ivec2 i = ivec2(floor(p));
    vec2 f = fract(p);
    vec2 w = f * f * f * (f * (f * 6.0 - 15.0) + 10.0);
    float v[4];
    for (int k = 0; k < 4; k++) {
      ivec2 o2 = ivec2(k & 1, k >> 1);
      ivec2 c = ((i + o2) % per + per) % per;
      uint h = pcg(uint(c.x) * 1973u + pcg(uint(c.y) * 9277u + salt));
      float a = float(h) * (6.2831853 / 4294967296.0);
      v[k] = dot(vec2(cos(a), sin(a)), f - vec2(o2));
    }
    return 1.414 * mix(mix(v[0], v[1], w.x), mix(v[2], v[3], w.x), w.y);
  }
  float pfbm(vec2 uv, ivec2 base, int oct, uint salt, float gain) {
    float s = 0.0, a = 0.5, n = 0.0;
    ivec2 per = base;
    for (int i = 0; i < 6; i++) {
      if (i >= oct) break;
      s += a * pnoise(uv * vec2(per), per, salt + uint(i) * 101u);
      n += a;
      per *= 2;
      a *= gain;
    }
    return s / n;
  }
  void main() {
    vec2 uv = vUv;
    // A slow, periodic warp curls the strands like flames.
    vec2 wq = vec2(pfbm(uv, ivec2(3, 6), 3, 11u, 0.5), pfbm(uv + 0.37, ivec2(3, 6), 3, 23u, 0.5));
    vec2 uw = uv + vec2(0.035, 0.030) * wq;
    float fil = pfbm(uw, ivec2(8, 56), 4, 3u, 0.6);            // long filaments
    // fibres: thin bright threads running along the orbit (cells long in
    // azimuth, thin in radius), ridged so they come out as lines
    float fib = 1.0 - abs(pfbm(uw * vec2(1.0, 1.0) + 0.21, ivec2(5, 140), 3, 7u, 0.55));
    fib = fib * fib * fib;
    float clump = pfbm(uv, ivec2(4, 9), 3, 13u, 0.55);         // large clumps
    float alt = pfbm(uw + 0.11, ivec2(10, 88), 4, 17u, 0.6);    // second strand set
    o = vec4(0.5 + 0.5 * fil, fib, 0.5 + 0.5 * clump, 0.5 + 0.5 * alt);
  }
`;

const gargFS = (pass) => /* glsl */`#version 300 es
  #define ${pass}
  precision highp float;
  precision highp int;
  precision highp sampler2D;
  precision highp sampler3D;
  in vec2 vUv;
#ifdef SMOKE_PASS
  layout(location = 0) out vec4 outFront;   // smoke before the disk's first crossing
  layout(location = 1) out vec4 outBack;    // and after it
#else
  out vec4 outColor;
  uniform sampler2D uSmokeFront;
  uniform sampler2D uSmokeBack;
  uniform float uSmokeOn;
#endif

  uniform float uAspect;        // screen width / height
  uniform vec2 uPix;            // render target size
  uniform vec4 uFrame;          // ndc window drawn into this target
  uniform vec3 uCamPos;
  uniform vec3 uCamRight;
  uniform vec3 uCamUp;
  uniform vec3 uCamFwd;
  uniform float uTanFov;
  uniform vec2 uShift;
  uniform float uTime;
  uniform float uHole;
  uniform float uDiskGain;
  uniform float uBeaming;
  uniform float uSkyGain;
  uniform float uSkyContrast;   // how much of the wormhole's far universe is still around us
  uniform float uFarDens;       // its galaxy density, as the wormhole drew it
  uniform float uFarG;          // and its brightness
  uniform float uStarGain;
  uniform vec2 uDiskR;
  uniform sampler2D uTable;
  uniform sampler2D uDiskTex;
  uniform samplerCube uFlow;
  uniform float uFlowTexel;     // angular size of a flow cubemap texel
  uniform sampler3D uNoise;     // tiling 3D noise (noise3d.js)
  uniform float uSmoke;         // density of the smoke around the hole
  uniform float uSmokeLight;    // how brightly the disk lights it

  const float PI = 3.14159265;
  const float TAU = 6.2831853;
  ${tableGLSL()}
  ${HASH}
  ${STARS}
  ${GALAXIES}

  // ------------------------------------------------------------- orbits
  // One table row: u at sweep psi on the orbit stored there.
  // state: 0 on the orbit, 1 escaped before psi, 2 captured before psi.
  float rowU(int row, float psi, bool above, out float psiEnd, out int state, out float outgoing) {
    psiEnd = texelFetch(uTable, ivec2(0, row), 0).g;
    float x = psi / psiEnd;
    state = 0;
    outgoing = 0.0;
    if (above) {
      if (x >= 2.0) { state = 1; return 0.0; }
      if (x > 1.0) { x = 2.0 - x; outgoing = 1.0; }
    } else if (x >= 1.0) {
      state = 2; return 1.0;
    }
    float c = x * float(TW - 1);
    int c0 = min(int(c), TW - 2);
    float f = c - float(c0);
    float u0 = texelFetch(uTable, ivec2(c0, row), 0).r;
    float u1 = texelFetch(uTable, ivec2(c0 + 1, row), 0).r;
    return mix(u0, u1, f);
  }

  // u at sweep psi for impact parameter b, between the two nearest rows.
  float orbitU(float b, float psi, out int state, out float outgoing, out float psiEnd) {
    bool above = b > BC;
    int r0;
    float fr;
    if (above) {
      float y = clamp((log(max(b - BC, 1e-30)) - S_MIN) / (S_MAX_A - S_MIN), 0.0, 1.0) * float(TH1 - 1);
      r0 = min(int(y), TH1 - 2);
      fr = y - float(r0);
    } else {
      float y = clamp((log(max(BC - b, 1e-30)) - S_MIN) / (S_MAX_B - S_MIN), 0.0, 1.0) * float(TH2 - 1);
      int rr = min(int(y), TH2 - 2);
      fr = y - float(rr);
      r0 = TH1 + rr;
    }
    float pe0, pe1, o0, o1;
    int s0, s1;
    float u0 = rowU(r0, psi, above, pe0, s0, o0);
    float u1 = rowU(r0 + 1, psi, above, pe1, s1, o1);
    psiEnd = mix(pe0, pe1, fr);
    if (s0 == s1) {
      state = s0;
      outgoing = fr < 0.5 ? o0 : o1;
      return mix(u0, u1, fr);
    }
    if (fr < 0.5) { state = s0; outgoing = o0; return u0; }
    state = s1; outgoing = o1; return u1;
  }

  // ---------------------------------------------------------------- sky
  // aniso: how much lensing stretches the sky here (1 = not at all)
  vec3 sky(vec3 d, float sig, float pix, float aniso) {
    float lod = log2(max(sig / uFlowTexel, 1.0));
    vec3 c = textureLod(uFlow, d, lod).rgb * uSkyGain;
    // Coming out of the wormhole the sky is the far universe exactly as the
    // wormhole drew it - a crowd of galaxies and star clouds - and it thins
    // out to Gargantua's black sky as we fly on (far: 1 -> 0). The few
    // galaxies of the black sky are among the crowd, so nothing jumps.
    float far = uSkyContrast;
    float stars = mix(uStarGain, 1.1, far);
    // A star is a point: where lensing would smear it into a streak, let it
    // fade instead, so the sky near the ring stays clean as in the film.
    if (stars > 0.0) c += starField(d, sig, pix) * (stars / (aniso * aniso));
    float keepG = mix(0.5 * uStarGain / aniso, uFarG, far);
    if (keepG > 0.0) {
      c += galaxyField(d, sig, far > 0.0 ? uFarDens : 0.08, 0.08, keepG > 0.0 ? uFarG * far / keepG : 0.0) * keepG;
    }
    if (far > 0.0) {
      c += (smallGalaxies(d, sig) * (1.6 * uFarG) + starClouds(d, sig, pix) * (uFarG * 1.5 / aniso)) * far;
    }
    return c;
  }

  // --------------------------------------------------------------- disk
  vec3 diskColor(float t) {
    // fire: deep ember, orange, gold, pale gold, white heat
    // (the reference's oranges are peachy: blue at half the red, not less)
    vec3 c0 = vec3(0.30, 0.035, 0.008);
    vec3 c1 = vec3(1.00, 0.20, 0.025);
    vec3 c2 = vec3(1.00, 0.42, 0.07);
    vec3 c3 = vec3(1.00, 0.72, 0.36);
    vec3 c4 = vec3(1.00, 0.95, 0.86);
    t = clamp(t, 0.0, 1.0);
    if (t < 0.25) return mix(c0, c1, t / 0.25);
    if (t < 0.5) return mix(c1, c2, (t - 0.25) / 0.25);
    if (t < 0.75) return mix(c2, c3, (t - 0.5) / 0.25);
    return mix(c3, c4, (t - 0.75) / 0.25);
  }

  vec4 sampleDisk(vec2 uv, vec2 gx, vec2 gy) {
    // a slightly softer filter than the footprint, so fibres seen edge on
    // average out instead of beating into moire
    return textureGrad(uDiskTex, uv, gx * 1.7, gy * 1.7);
  }

  // Half-thickness of the gas layer: it flares with radius and boils with
  // the same turbulence that lights it.
  float slabHeight(vec2 P, float r) {
    float th = atan(P.y, P.x);
    float om = 0.5 * pow(uDiskR.x / r, 1.5);
    float lr = log(r / uDiskR.x);
    vec2 uv = vec2((th - om * mod(uTime, 8.0)) * (3.0 / TAU), lr * 0.9 + 0.37);
    vec4 n = textureLod(uDiskTex, uv, 2.5);
    float boil = smoothstep(0.35, 0.85, n.b * 0.6 + n.r * 0.6 - 0.1);
    float flare = smoothstep(uDiskR.x * 1.5, uDiskR.y, r);
    return r * (0.006 + flare * (0.016 + 0.07 * boil));
  }

  // Emission (rgb) and opacity (a) where a ray crosses the disk.
  vec4 diskShade(vec2 P, vec2 dPx, vec2 dPy, float cosInc, float g, float layer) {
    float r = length(P);
    float rin = uDiskR.x, rout = uDiskR.y;
    if (r < rin * 0.9 || r > rout * 1.6) return vec4(0.0);
    float th = atan(P.y, P.x);
    float r2 = r * r;
    vec2 dThx = vec2(P.x * dPx.y - P.y * dPx.x, P.x * dPy.y - P.y * dPy.x) / r2;
    vec2 dLr = vec2(dot(P, dPx), dot(P, dPy)) / r2;
    float lr = log(r / rin);

    // Co-rotating texture, two phases cross-faded so the shear never
    // winds the pattern into rings.
    // the inner edge turns once in ~12 s, the outer disk far slower
    float om = 0.5 * pow(rin / r, 1.5);
    const float PER = 8.0;
    float ph = uTime / PER;
    float f1 = fract(ph), f2 = fract(ph + 0.5);
    float w1 = 1.0 - abs(2.0 * f1 - 1.0);
    float ky = 0.56;
    const float KA = 2.0 / TAU;   // the pattern repeats twice per turn
    vec2 gx = vec2(dThx.x * KA, dLr.x * ky);
    vec2 gy = vec2(dThx.y * KA, dLr.y * ky);
    vec2 uvA = vec2((th - om * f1 * PER) * KA + layer * 0.31, lr * ky + 0.13 + layer * 0.047);
    vec2 uvB = vec2((th - om * f2 * PER) * KA + 0.5 + layer * 0.31, lr * ky + 0.61 + layer * 0.047);
    vec4 n = sampleDisk(uvA, gx, gy) * w1 + sampleDisk(uvB, gx, gy) * (1.0 - w1);

    float fil = n.r, fib = n.g, clump = n.b, alt = n.a;
    float x = clamp((r - rin) / (rout - rin), 0.0, 1.0);
    // finer fibres still, from the same texture at three times the scale
    vec4 n2 = sampleDisk(uvA * vec2(3.0, 2.0) + 0.37, gx * vec2(3.0, 2.0), gy * vec2(3.0, 2.0));

    // A sharp inner edge; an outer edge that frays where the noise allows.
    float edgeIn = smoothstep(rin * 0.985, rin * 1.08, r);
    float fray = rout * (0.55 + 0.6 * clump + 0.25 * (alt - 0.5));
    float edgeOut = 1.0 - smoothstep(fray * 0.4, fray, r);
    // The gas is made of strands: bright fibres running with the flow,
    // dark gaps between them, more broken further out.
    float strands = (0.10 + 1.1 * fib + 0.6 * n2.g) * (0.3 + 0.95 * fil);
    float flame = smoothstep(0.30, 0.84, fil * 0.7 + alt * 0.45 - 0.08);
    // strands everywhere, even in the white heat by the inner edge
    float tex = (0.04 + 1.3 * strands * strands) * mix(0.8, 0.1 + 1.25 * flame, smoothstep(0.05, 0.4, x));
    // The gas thins out with radius: seen face on (the arcs over and under
    // the hole) the outer disk is faint, seen edge on (the band across
    // the front) its long path through the gas makes it bright.
    float dens = edgeIn * edgeOut * tex * pow(rin / r, 1.25);

    // white heat only near the inner edge; orange strands further out
    float S = uDiskGain * pow(rin / r, 1.45) * (0.05 + tex);
    // white heat by the inner edge; further out the strands burn orange and
    // the gaps between them glow deep red
    float temp = 1.15 * pow(rin / r, 0.95) * (0.55 + 0.6 * clamp(strands, 0.0, 1.2) + 0.15 * (alt - 0.5));
    // Soot: dense, cooler gas in the outer disk that blocks more light than
    // it gives - the dark smoky lanes of the film's close-ups.
    float soot = smoothstep(0.50, 0.80, alt * 0.7 + clump * 0.4) * smoothstep(0.15, 0.5, x);
    S *= 1.0 - 0.8 * soot;
    temp *= 1.0 - 0.25 * soot;
    dens += edgeIn * edgeOut * soot * 0.55 * pow(rin / r, 0.8);

    // Doppler and gravitational shift, at the strength the film allows.
    S *= pow(g, 3.0);
    temp *= g;

    float tau = dens * 1.6 / max(abs(cosInc), 0.03);
    float a = 1.0 - exp(-tau);
    return vec4(diskColor(temp) * S * a, a);
  }

  // -------------------------------------------------------------- smoke
  // A thick, flared torus of dusty gas around the disk, out to SMOKE_R,
  // churned by 3D noise and swirled round the hole (inner parts faster),
  // lit by the inner disk: warm near it, cooling to dusk violet further out.
  // Returns emission per unit length (rgb) and extinction per unit length.
  const float SMOKE_R = 21.0;
  vec4 smokeAt(vec3 P) {
    float rr = length(P.xz);
    float R = length(P);
    float H = 0.9 + 0.11 * rr;
    float env = exp(-P.y * P.y / (H * H)) * smoothstep(2.8, 5.5, rr)
              * (1.0 - smoothstep(SMOKE_R * 0.35, SMOKE_R, R));
    if (env < 0.003) return vec4(0.0);
    // a gentle swirl (inner parts a little faster) and a slow boil: strong
    // differential rotation would wind the noise into thin rings
    float om = 0.06 + 0.09 * pow(9.0 / max(rr, 3.0), 0.8);
    const float SPER = 16.0;
    float ph = uTime / SPER;
    float f1 = fract(ph), f2 = fract(ph + 0.5);
    float w1 = 1.0 - abs(2.0 * f1 - 1.0);
    float a1 = om * f1 * SPER, a2 = om * f2 * SPER;
    float boil = uTime * 0.012;
    float c1 = cos(a1), s1 = sin(a1), c2 = cos(a2), s2 = sin(a2);
    vec3 q1 = vec3(c1 * P.x + s1 * P.z, P.y * 1.6, -s1 * P.x + c1 * P.z);
    vec3 q2 = vec3(c2 * P.x + s2 * P.z, P.y * 1.6, -s2 * P.x + c2 * P.z);
    vec4 n = textureLod(uNoise, q1 * 0.034 + vec3(0.13, 0.52 + boil, 0.71), 0.0) * w1
           + textureLod(uNoise, q2 * 0.034 + vec3(0.64, 0.27 + boil, 0.09), 0.0) * (1.0 - w1);
    float f = n.r * 0.55 + n.g * 0.3 + n.b * 0.15;
    // wisps: only the upper part of the noise becomes smoke
    float d = env * smoothstep(0.54, 0.84, f) * uSmoke * 0.45;
    float light = uSmokeLight / (1.0 + R * R * 0.04);
    // fire-lit near the disk, rose further out, dusk violet at the fringes
    vec3 lc = mix(vec3(1.0, 0.50, 0.26), vec3(0.95, 0.46, 0.46), smoothstep(4.0, 9.0, R));
    lc = mix(lc, vec3(0.55, 0.40, 0.80), smoothstep(9.0, 17.0, R));
    // the light has to get into the smoke too: thick clumps are sooty inside
    float self = mix(1.0, 0.15, smoothstep(0.64, 0.9, f));
    return vec4(lc * light * self * d, d * 0.9);
  }

#ifndef SMOKE_PASS
  void main() {
    vec2 ndc = mix(uFrame.xy, uFrame.zw, vUv);
    vec3 dir = normalize(uCamRight * (ndc.x * uAspect * uTanFov + uShift.x)
                       + uCamUp * (ndc.y * uTanFov + uShift.y) + uCamFwd);
    float pix = 2.0 * uTanFov * (uFrame.w - uFrame.y) * 0.5 / uPix.y;

    // The sky is evaluated once, at the end, in whichever direction the
    // light came from (the shader stays small enough to compile quickly).
    vec3 skyDir = dir;
    float sSig = pix * 0.8, sAniso = 1.0;
    vec3 acc = vec3(0.0);
    float T = 1.0;
    bool captured = false;
    if (uHole > 0.0) {
    float rc = length(uCamPos);
    vec3 e1 = uCamPos / rc;
    float cosA = dot(dir, e1);
    vec3 perp = dir - cosA * e1;
    float sinA = length(perp);
    vec3 e2 = sinA > 1e-7 ? perp / sinA : normalize(cross(e1, vec3(0.31, 0.95, 0.07)));
    float uc = 1.0 / rc;
    float b = max(rc * sinA / sqrt(1.0 - uc), 1e-4);
    bool inward = cosA < 0.0;

    // Sweep from infinity to the camera along this orbit.
    float ib2 = 1.0 / (b * b);
    const float GX[8] = float[8](0.0198550717512319, 0.1016667612931866, 0.2372337950418355, 0.4082826787521751,
                                 0.5917173212478249, 0.7627662049581645, 0.8983332387068134, 0.9801449282487681);
    const float GW[8] = float[8](0.1012285362903763, 0.2223810344533745, 0.3137066458778873, 0.3626837833783620,
                                 0.3626837833783620, 0.3137066458778873, 0.2223810344533745, 0.1012285362903763);
    float psiC = 0.0;
    for (int i = 0; i < 8; i++) {
      float u = uc * GX[i];
      psiC += GW[i] * inversesqrt(max(ib2 - u * u + u * u * u, 1e-14));
    }
    psiC *= uc * 0.5;

    // The ray's height above the disk is |r| (e1.y cos(phi) + e2.y sin(phi));
    // it crosses the plane every half turn from phi0.
    float phi0 = mod(atan(-e1.y, e2.y), PI);
    if (phi0 < 1e-4) phi0 += PI;
    float nY = cross(e1, e2).y;

    // Each disk crossing's light and opacity, and where along the ray it is,
    // so the smoke can be laid in between them in the right order.
    vec3 dCol[3];
    float dA[3];
    float dPhi[3];
    bool alive = true;
    float psiT = 0.0;
    for (int k = 0; k < 3; k++) {
      float phi = phi0 + float(k) * PI;
      int st;
      float og, pe;
      float u = orbitU(b, inward ? psiC + phi : 1e9, st, og, pe);
      if (k == 0) psiT = pe;
      float r = 1.0 / max(u, 1e-6);
      float ty = -e1.y * sin(phi) + e2.y * cos(phi);
      vec2 P = r * (cos(phi) * e1.xz + sin(phi) * e2.xz);
      // The front of the disk has thickness: seen edge on, a ray passes
      // through a deep layer of gas, so it is sampled three times on its
      // way through - the band shows stacked strands, and swells and frays
      // where the layer is puffed up.
      float dphiV = 0.0;
      if (k == 0) {
        float hgt = slabHeight(P, r);
        dphiV = min(hgt / (r * max(abs(ty), 1e-3)), min(0.5, 0.8 * phi));
      }
      vec2 dPx = dFdx(P), dPy = dFdy(P);
      dCol[k] = vec3(0.0);
      dA[k] = 0.0;
      dPhi[k] = 1e9;
      if (alive) {
        if (st == 2) { alive = false; captured = true; }
        else if (st == 1) { alive = false; }
        else {
          dPhi[k] = phi - dphiV;
          if (r > uDiskR.x * 0.9 && r < uDiskR.y * 1.6) {
            // angle between the ray and the disk's normal at the crossing
            float slope2 = max(ib2 - u * u + u * u * u, 0.0) / (u * u);
            float cosInc = ty * inversesqrt(1.0 + slope2);
            // redshift factor for gas on circular orbits
            float om = sqrt(0.5 / (r * r * r));
            float g = sqrt(max(1.0 - 1.5 / r, 0.05)) / max(1.0 - om * b * nY, 0.05);
            g = mix(1.0, g, uBeaming);
            if (k == 0 && dphiV > 0.0) {
              // entry surface, one third and two thirds of the way in
              vec3 acc0 = vec3(0.0);
              float T0 = 1.0;
              for (int L = 0; L < 2; L++) {
                float dp = dphiV * (1.0 - float(L) / 2.0);
                int stL;
                float ogL, peL;
                float uL = orbitU(b, psiC + phi - dp, stL, ogL, peL);
                if (stL != 0) continue;
                float rL = 1.0 / max(uL, 1e-6);
                vec2 PL = rL * (cos(phi - dp) * e1.xz + sin(phi - dp) * e2.xz);
                vec4 eL = diskShade(PL, dPx, dPy, cosInc * 2.0, g, float(L));
                acc0 += T0 * eL.rgb;
                T0 *= 1.0 - eL.a;
              }
              dCol[k] = acc0;
              dA[k] = 1.0 - T0;
            } else {
              vec4 e = diskShade(P, dPx, dPy, cosInc, g, float(k) * 1.7);
              dCol[k] = e.rgb;
              dA[k] = e.a;
            }
          }
        }
      }
    }
    if (alive) captured = inward && b <= BC;

    // Where the ray goes in the end.
    float phiEsc = inward ? 2.0 * psiT - psiC : psiC;
    vec3 dEsc = cos(phiEsc) * e1 + sin(phiEsc) * e2;
    vec3 ddx = dFdx(dEsc), ddy = dFdy(dEsc);
    float area = max(length(cross(ddx, ddy)), 1e-14);
    float sig = clamp(sqrt(area), pix * 0.55, 0.5);
    float aniso = clamp((dot(ddx, ddx) + dot(ddy, ddy)) / (2.0 * area), 1.0, 50.0);

    // The smoke comes from its own half-resolution pass (it is soft): what
    // lies in front of the disk's first crossing, and what lies behind it.
    vec4 sf = vec4(0.0, 0.0, 0.0, 1.0), sb = vec4(0.0, 0.0, 0.0, 1.0);
    if (uSmokeOn > 0.5) {
      vec2 tx = 1.0 / vec2(textureSize(uSmokeFront, 0));
      sf = vec4(0.0); sb = vec4(0.0);
      for (int i = 0; i < 4; i++) {
        vec2 o = vec2(float(i & 1) - 0.5, float(i >> 1) - 0.5) * 1.5 * tx;
        sf += texture(uSmokeFront, vUv + o);
        sb += texture(uSmokeBack, vUv + o);
      }
      sf *= 0.25; sb *= 0.25;
    }
    acc = sf.rgb;
    T = sf.a;
    acc += T * dCol[0];
    T *= 1.0 - dA[0];
    acc += T * sb.rgb;
    T *= sb.a;
    acc += T * dCol[1];
    T *= 1.0 - dA[1];
    acc += T * dCol[2];
    T *= 1.0 - dA[2];
    // fading in (uHole < 1): the bending, the disk and the shadow grow
    // from nothing
    acc *= uHole;
    T = mix(1.0, T, uHole);
    if (!captured) {
      skyDir = uHole < 1.0 ? normalize(mix(dir, dEsc, uHole)) : dEsc;
      sSig = sig;
      sAniso = aniso;
    }
    }
    vec3 bg = sky(skyDir, sSig, pix, sAniso);
    vec3 col = acc + (captured ? (1.0 - uHole) : T) * bg;
    outColor = vec4(col, 1.0);
  }
#endif

#ifdef SMOKE_PASS
  // March the smoke along the bent ray itself (the same orbit, sampled in
  // sweep angle), split where the ray first crosses the disk plane.
  void main() {
    outFront = vec4(0.0, 0.0, 0.0, 1.0);
    outBack = vec4(0.0, 0.0, 0.0, 1.0);
    if (uSmoke <= 0.0 || uHole <= 0.0) return;
    vec2 ndc = mix(uFrame.xy, uFrame.zw, vUv);
    vec3 dir = normalize(uCamRight * (ndc.x * uAspect * uTanFov + uShift.x)
                       + uCamUp * (ndc.y * uTanFov + uShift.y) + uCamFwd);
    float rc = length(uCamPos);
    vec3 e1 = uCamPos / rc;
    float cosA = dot(dir, e1);
    if (cosA >= 0.0) return;                      // heading away from the hole
    vec3 perp = dir - cosA * e1;
    float sinA = length(perp);
    vec3 e2 = sinA > 1e-7 ? perp / sinA : normalize(cross(e1, vec3(0.31, 0.95, 0.07)));
    float uc = 1.0 / rc;
    float b = max(rc * sinA / sqrt(1.0 - uc), 1e-4);
    if (b >= SMOKE_R) return;
    // paths that stay well above or below the layer meet no smoke
    float cd = dot(uCamPos, dir);
    float disc = cd * cd - (rc * rc - SMOKE_R * SMOKE_R);
    if (disc <= 0.0) return;
    float sq = sqrt(disc);
    float y1 = uCamPos.y + (-cd - sq) * dir.y;
    float y2 = uCamPos.y + (-cd + sq) * dir.y;
    if (y1 * y2 > 0.0 && min(abs(y1), abs(y2)) > 12.0) return;

    float ib2 = 1.0 / (b * b);
    const float GX[8] = float[8](0.0198550717512319, 0.1016667612931866, 0.2372337950418355, 0.4082826787521751,
                                 0.5917173212478249, 0.7627662049581645, 0.8983332387068134, 0.9801449282487681);
    const float GW[8] = float[8](0.1012285362903763, 0.2223810344533745, 0.3137066458778873, 0.3626837833783620,
                                 0.3626837833783620, 0.3137066458778873, 0.2223810344533745, 0.1012285362903763);
    float psiC = 0.0;
    for (int i = 0; i < 8; i++) {
      float u = uc * GX[i];
      psiC += GW[i] * inversesqrt(max(ib2 - u * u + u * u * u, 1e-14));
    }
    psiC *= uc * 0.5;
    float phi0 = mod(atan(-e1.y, e2.y), PI);
    if (phi0 < 1e-4) phi0 += PI;
    int st0;
    float og0, psiT;
    orbitU(b, psiC + phi0, st0, og0, psiT);

    float psiIn = asin(min(b / SMOKE_R, 1.0));        // entering the smoke (flat-space estimate)
    float psiA = max(psiC, psiIn);
    float psiB = b > BC ? 2.0 * psiT - psiIn : psiT;  // leaving it, or the horizon
    if (psiB <= psiA) return;
    const int NS = 14;
    float jit = fract(52.9829189 * fract(dot(gl_FragCoord.xy, vec2(0.06711056, 0.00583715))));
    float dpsi = (psiB - psiA) / float(NS);
    vec3 accF = vec3(0.0), accB = vec3(0.0);
    float TF = 1.0, TB = 1.0;
    for (int j = 0; j < NS; j++) {
      float psi = psiA + (float(j) + jit) * dpsi;
      float phiS = psi - psiC;
      int st;
      float og, pe;
      float u = orbitU(b, psi, st, og, pe);
      if (st != 0) break;
      float r = 1.0 / max(u, 1e-6);
      vec3 P = r * (cos(phiS) * e1 + sin(phiS) * e2);
      float slope2 = max(ib2 - u * u + u * u * u, 0.0) / (u * u);
      float ds = dpsi * r * sqrt(1.0 + slope2);
      vec4 sm = smokeAt(P);
      if (sm.a > 0.0) {
        float tr = exp(-sm.a * ds);
        vec3 e = sm.rgb / sm.a * (1.0 - tr);
        if (phiS < phi0) { accF += TF * e; TF *= tr; }
        else { accB += TB * e; TB *= tr; }
      }
      if (TF * TB < 0.02) break;
    }
    outFront = vec4(accF, TF);
    outBack = vec4(accB, TB);
  }
#endif
`;

export function createGargantua(gl) {
  const prog = startProgram(gl, FULLSCREEN_VS, gargFS('MAIN_PASS'), 'gargantua');
  const sprog = startProgram(gl, FULLSCREEN_VS, gargFS('SMOKE_PASS'), 'gargantua-smoke');
  const gen = startProgram(gl, FULLSCREEN_VS, DISK_TEX_FS, 'disktex');
  const size = 1024;
  let disk = null;

  // Once compiled: the disk turbulence texture, generated on the GPU and
  // mipmapped.
  function setup() {
    finishProgram(gl, prog);
    finishProgram(gl, sprog);
    finishProgram(gl, gen);
    disk = target(gl, size, size, {
      internal: gl.RGBA8, format: gl.RGBA, type: gl.UNSIGNED_BYTE,
      filter: gl.LINEAR_MIPMAP_LINEAR, wrap: gl.REPEAT,
    });
    gl.bindFramebuffer(gl.FRAMEBUFFER, disk.fb);
    gl.viewport(0, 0, size, size);
    gl.useProgram(gen.p);
    draw(gl);
    gl.bindFramebuffer(gl.FRAMEBUFFER, null);
    gl.bindTexture(gl.TEXTURE_2D, disk.tex);
    gl.generateMipmap(gl.TEXTURE_2D);
    const aniso = gl.getExtension('EXT_texture_filter_anisotropic');
    if (aniso) {
      const max = gl.getParameter(aniso.MAX_TEXTURE_MAX_ANISOTROPY_EXT);
      gl.texParameterf(gl.TEXTURE_2D, aniso.TEXTURE_MAX_ANISOTROPY_EXT, Math.min(8, max));
    }
    gl.deleteProgram(gen.p);
  }

  let noiseTex = null;
  return {
    prog,
    programs: [prog, sprog, gen],
    setup,
    setNoise(tex) { noiseTex = tex; },
    /* Draw into the currently bound target.
       cam: { pos, right, up, fwd, tanFov, shift:[x,y], aspect }
       frame: [x0, y0, x1, y1] ndc window (default whole screen). */
    _set(P, state, cam, pix, frame, tableTex, flowCube, flowTexel) {
      const u = P.u;
      gl.useProgram(P.p);
      gl.uniform1f(u.uAspect, cam.aspect);
      gl.uniform2f(u.uPix, pix[0], pix[1]);
      gl.uniform4fv(u.uFrame, frame || [-1, -1, 1, 1]);
      gl.uniform3fv(u.uCamPos, cam.pos);
      gl.uniform3fv(u.uCamRight, cam.right);
      gl.uniform3fv(u.uCamUp, cam.up);
      gl.uniform3fv(u.uCamFwd, cam.fwd);
      gl.uniform1f(u.uTanFov, cam.tanFov);
      gl.uniform2fv(u.uShift, cam.shift);
      gl.uniform1f(u.uTime, state.time);
      gl.uniform1f(u.uHole, state.hole);
      gl.uniform1f(u.uDiskGain, state.diskGain);
      gl.uniform1f(u.uBeaming, state.beaming);
      gl.uniform1f(u.uSkyGain, state.skyGain);
      gl.uniform1i(u.uCells9, 9);
      gl.uniform1f(u.uSkyContrast, state.skyContrast || 0);
      gl.uniform1f(u.uFarDens, state.farDens == null ? 0.6 : state.farDens);
      gl.uniform1f(u.uFarG, state.farG == null ? 1.7 : state.farG);
      gl.uniform1f(u.uStarGain, state.starGain);
      gl.uniform2f(u.uDiskR, state.rIn, state.rOut);
      gl.uniform1f(u.uFlowTexel, flowTexel);
      bindTex(gl, 0, tableTex);
      gl.uniform1i(u.uTable, 0);
      bindTex(gl, 1, disk.tex);
      gl.uniform1i(u.uDiskTex, 1);
      bindTex(gl, 2, flowCube, gl.TEXTURE_CUBE_MAP);
      gl.uniform1i(u.uFlow, 2);
      bindTex(gl, 3, noiseTex, gl.TEXTURE_3D);
      gl.uniform1i(u.uNoise, 3);
      gl.uniform1f(u.uSmoke, noiseTex ? (state.smoke == null ? 1 : state.smoke) : 0);
      gl.uniform1f(u.uSmokeLight, state.smokeLight == null ? 0.9 : state.smokeLight);
    },
    /* The smoke layers, at half resolution, into the bound two-target framebuffer. */
    renderSmoke(state, cam, pix, frame, tableTex, flowCube, flowTexel) {
      this._set(sprog, state, cam, pix, frame, tableTex, flowCube, flowTexel);
      draw(gl);
    },
    /* Draw into the currently bound target. smoke: {front, back} textures or null. */
    render(state, cam, pix, frame, tableTex, flowCube, flowTexel, smoke) {
      this._set(prog, state, cam, pix, frame, tableTex, flowCube, flowTexel);
      const u = prog.u;
      gl.uniform1f(u.uSmokeOn, smoke ? 1 : 0);
      if (smoke) {
        bindTex(gl, 4, smoke.front);
        gl.uniform1i(u.uSmokeFront, 4);
        bindTex(gl, 5, smoke.back);
        gl.uniform1i(u.uSmokeBack, 5);
      }
      draw(gl);
    },
  };
}

/* Camera looking at the hole from distance `dist`, `incl` degrees from
   the disk's axis (90 = edge on), `azim` degrees around it. */
export function orbitCamera(dist, inclDeg, azimDeg, fovDeg, shift, aspect, rollDeg = 0) {
  const d2r = Math.PI / 180;
  const th = inclDeg * d2r, ph = azimDeg * d2r;
  const pos = [dist * Math.sin(th) * Math.cos(ph), dist * Math.cos(th), dist * Math.sin(th) * Math.sin(ph)];
  const fwd = norm([-pos[0], -pos[1], -pos[2]]);
  let right = norm(cross(fwd, [0, 1, 0]));
  let up = cross(right, fwd);
  if (rollDeg) {
    const c = Math.cos(rollDeg * d2r), s = Math.sin(rollDeg * d2r);
    const r2 = right.map((v, i) => c * v + s * up[i]);
    const u2 = up.map((v, i) => c * v - s * right[i]);
    right = r2; up = u2;
  }
  return { pos, fwd, right, up, tanFov: Math.tan(fovDeg * d2r / 2), shift, aspect };
}

export function cross(a, b) {
  return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
}
export function norm(a) {
  const l = Math.hypot(a[0], a[1], a[2]) || 1;
  return [a[0] / l, a[1] / l, a[2] / l];
}
