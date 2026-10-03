/* The entry: flying through the wormhole near Saturn into Gargantua's
 * galaxy.
 *
 * Geometry: a spherically symmetric wormhole, as in the film's own
 * visualisation paper. Space is described by a proper radial distance l
 * running from our universe (l < 0) through the throat (l = 0) into the
 * other one (l > 0); the size of the spheres at l is
 *
 *     r(l) = rho + W (sqrt(1 + (l/W)^2) - 1)
 *
 * a throat of radius rho whose surroundings flatten out over a distance
 * ~W (the "lensing width"). Every light ray stays in a plane through the
 * centre, so each pixel integrates just three numbers with RK4,
 *
 *     dl/dlambda = p,  dp/dlambda = b^2 r'(l) / r^3,  dphi/dlambda = b / r^2
 *
 * until it is far out on one side or the other. Rays aimed inside the
 * throat (b < rho) come out in the other universe; the rest swing round
 * and return to ours, which is what makes the mouth look like a crystal
 * ball with a ring of bent starlight around its edge.
 *
 * Our side: stars, the Sun, and Saturn with its rings, shaded with the
 * planet's shadow on the rings and the rings' shadow on the planet.
 * The far side: Gargantua exactly as the hero camera sees it (rendered
 * into a texture each frame), inside the cosmic flow at full strength.
 * Flying out of the throat therefore ends on the hero shot itself.
 */
import { FULLSCREEN_VS, startProgram, finishProgram, bindTex, draw } from './gl.js';
import { HASH, NOISE, STARS, FAR } from './glsl.js';

const FS = /* glsl */`#version 300 es
  precision highp float;
  precision highp int;
  in vec2 vUv;
  out vec4 outColor;

  uniform float uAspect;
  uniform float uTanFov;
  uniform vec2 uShift;
  uniform mat3 uView;           // camera -> travel frame (z = heading)
  uniform float uL;             // where we are along the passage
  uniform float uRho;
  uniform float uW;
  uniform vec2 uPix;
  uniform float uTime;

  uniform sampler2D uHero;      // Gargantua from the hero camera
  uniform float uHeroTan;
  uniform float uHeroExt;
  uniform vec2 uHeroShift;
  uniform vec3 uHeroRight;
  uniform vec3 uHeroUp;
  uniform vec3 uHeroFwd;
  uniform samplerCube uFlow;
  uniform float uFlowTexel;
  uniform float uFarGain;
  uniform float uNearGain;

  uniform vec3 uSatPos;
  uniform float uSatR;
  uniform vec3 uRingN;
  uniform vec3 uSunDir;

  const float PI = 3.14159265;
  ${HASH}
  ${NOISE}
  ${STARS}
  ${FAR}

  float rOf(float l) { float x = l / uW; return uRho + uW * (sqrt(1.0 + x * x) - 1.0); }
  float drOf(float l) { float x = l / uW; return x * inversesqrt(1.0 + x * x); }

  vec3 lin(vec3 c) { return pow(c, vec3(2.2)); }

  // ------------------------------------------------------------- Saturn
  float ringOpacity(float q) {
    float c = smoothstep(1.235, 1.25, q) * (1.0 - smoothstep(1.515, 1.527, q)) * 0.10;
    float b = smoothstep(1.525, 1.545, q) * (1.0 - smoothstep(1.935, 1.950, q))
            * (0.72 + 0.18 * sin(q * 61.0) * sin(q * 17.0));
    float a = smoothstep(2.025, 2.035, q) * (1.0 - smoothstep(2.262, 2.272, q)) * 0.52;
    a *= 1.0 - 0.9 * smoothstep(2.208, 2.212, q) * (1.0 - smoothstep(2.217, 2.221, q));   // Encke gap
    float fz = (q - 2.326) / 0.004;
    float f = exp(-fz * fz) * 0.35;                                                           // F ring
    float fine = 0.84 + 0.16 * sin(q * 410.0) * sin(q * 133.0 + 1.3);
    return clamp((c + b + a) * fine + f, 0.0, 0.94);
  }
  vec3 ringColor(float q) {
    vec3 cC = lin(vec3(0.52, 0.48, 0.44));
    vec3 cB = lin(vec3(0.93, 0.85, 0.70));
    vec3 cA = lin(vec3(0.82, 0.78, 0.71));
    vec3 c = q < 1.53 ? cC : (q < 2.0 ? cB : cA);
    return c * (0.9 + 0.1 * sin(q * 233.0));
  }

  // Saturn along the line o + s a (s > 0): colour and coverage.
  vec4 saturn(vec3 o, vec3 a) {
    vec3 N = uRingN;
    float R = uSatR;
    vec3 oc = o - uSatPos;
    const float k = 1.0 / 0.9;                 // the planet is 10% flattened
    vec3 ocS = oc + (k - 1.0) * dot(oc, N) * N;
    vec3 aS = a + (k - 1.0) * dot(a, N) * N;
    float A = dot(aS, aS), B = dot(ocS, aS), C = dot(ocS, ocS) - R * R;
    float disc = B * B - A * C;
    float sP = 1e20;
    if (disc > 0.0) {
      float s0 = (-B - sqrt(disc)) / A;
      if (s0 > 0.0) sP = s0;
    }
    float den = dot(a, N);
    float sR = 1e20, q = 0.0;
    if (abs(den) > 1e-6) {
      float s1 = -dot(oc, N) / den;
      if (s1 > 0.0) {
        q = length(oc + s1 * a) / R;
        if (q > 1.2 && q < 2.36) sR = s1;
      }
    }
    vec4 col = vec4(0.0);
    vec3 L = uSunDir;
    if (sP < 1e19) {
      vec3 x = oc + sP * a;
      vec3 n = normalize(x + (k * k - 1.0) * dot(x, N) * N);
      vec3 xn = normalize(x);
      float lat = dot(xn, N);
      // zonal bands, smeared by turbulence along the winds
      float turb = fbm3(vec3(xn.x * 3.0, lat * 22.0, xn.z * 3.0), 4);
      float bands = 0.5 + 0.5 * sin(lat * 23.0 + 1.6 * turb + 0.7 * sin(lat * 8.0 + 1.0));
      bands = mix(bands, 0.5 + 0.5 * sin(lat * 67.0 + 2.0 * turb), 0.22);
      vec3 base = mix(lin(vec3(0.70, 0.62, 0.48)), lin(vec3(0.88, 0.82, 0.68)), bands);
      base = mix(base, lin(vec3(0.58, 0.62, 0.64)), smoothstep(0.74, 0.92, abs(lat)));
      float lam = max(dot(n, L), 0.0);
      float term = smoothstep(-0.05, 0.25, dot(n, L));
      // the rings' shadow on the clouds
      float dn = dot(L, N);
      float sh = 0.0;
      if (abs(dn) > 1e-4) {
        float t = -dot(x, N) / dn;
        if (t > 0.0) sh = ringOpacity(length(x + t * L) / R);
      }
      // a little ringshine on the night side
      vec3 pc = base * (1.05 * lam * term * (1.0 - 0.85 * sh) + 0.006);
      col = vec4(pc, 1.0);
    }
    if (sR < sP) {
      vec3 x = oc + sR * a;
      float op = ringOpacity(q);
      // the planet's shadow across the rings
      float t = -dot(x, L);
      float d2 = dot(x, x) - t * t;
      float shadow = (t > 0.0 && d2 < R * R) ? 0.04 : 1.0;
      bool sameSide = (dot(L, N) > 0.0) == (dot(-a, N) > 0.0);
      float lit = sameSide ? (0.35 + 0.95 * abs(dot(L, N))) : 0.22 * (1.0 - op);
      vec3 rc = ringColor(q) * lit * shadow * 1.0;
      col.rgb = mix(col.rgb, rc, op);
      col.a = max(col.a, op);
    }
    return col;
  }

  // --------------------------------------------------------------- skies
  vec3 nearSky(vec3 a, vec3 o, float sig, float pix, float aniso) {
    float lod = log2(max(sig / uFlowTexel, 1.0));
    vec3 c = textureLod(uFlow, a.zxy, lod).rgb * uNearGain;
    c += starField(a.yzx, sig, pix) * (0.9 / (aniso * aniso));
    float sd = max(dot(a, uSunDir), 0.0);
    c += vec3(1.0, 0.93, 0.82) * (60.0 * pow(sd, 9000.0) + 0.6 * pow(sd, 400.0) + 0.04 * pow(sd, 30.0));
    vec4 s = saturn(o, a);
    return mix(c, s.rgb, s.a);
  }

  vec3 farSky(vec3 a, float sig, float pix, float aniso) {
    // as the camera will see it once through: heading -> +z, up kept
    vec3 c = vec3(-a.x, a.y, -a.z);
    vec3 w = c.x * uHeroRight + c.y * uHeroUp + c.z * uHeroFwd;
    float lod = log2(max(sig / uFlowTexel, 1.0));
    // the far galaxy: the same flow, but seen up close - more contrast,
    // more magenta and violet
    vec3 fl = textureLod(uFlow, w, lod).rgb * uFarGain;
    vec3 env = farGalaxy(fl);
    env += starField(w, sig, pix) * (1.0 / (aniso * aniso));
    if (c.z > 0.0) {
      vec2 X = c.xy / c.z;
      vec2 ndc = vec2((X.x - uHeroShift.x) / (uAspect * uHeroTan), (X.y - uHeroShift.y) / uHeroTan);
      vec2 uv = ndc / uHeroExt * 0.5 + 0.5;
      float e = min(min(uv.x, 1.0 - uv.x), min(uv.y, 1.0 - uv.y));
      if (e > 0.0) {
        vec3 hero = textureLod(uHero, uv, 0.0).rgb;
        return mix(env, hero, smoothstep(0.0, 0.07, e));
      }
    }
    return env;
  }

  void main() {
    vec2 ndc = vUv * 2.0 - 1.0;
    vec3 D = normalize(uView * vec3(ndc.x * uAspect * uTanFov + uShift.x, ndc.y * uTanFov + uShift.y, 1.0));
    float pix = 2.0 * uTanFov / uPix.y;

    // The ray's plane holds the axis of travel; e1 is our position seen
    // from the centre, e2 the sideways direction of this ray.
    vec3 e1 = vec3(0.0, 0.0, -1.0);
    float sa = length(D.xy);
    vec3 e2 = sa > 1e-7 ? vec3(D.xy / sa, 0.0) : vec3(1.0, 0.0, 0.0);
    float rc = rOf(uL);
    float b = rc * sa;
    float b2 = b * b;

    float l = uL, p = D.z, phi = 0.0;
    float phiTot;
    if (b > 2.6 * uRho) {
      // Never comes close to the mouth: the weak-field bend, fitted to the
      // full integration (10.3 deg / b^2.35, within 0.05 deg at b = 2.6),
      // applied as far as the ray's closest approach still lies ahead.
      float sAlong = -sign(uL) * D.z * rc;            // > 0: heading for closest approach
      float ahead = 0.5 + 0.5 * tanh(sAlong / b);
      float bend = radians(10.3) * pow(b / uRho, -2.35) * ahead;
      phiTot = atan(sa, -D.z) + bend;
    } else {
    float lfar = 2.0 * abs(uL) + 24.0;
    for (int i = 0; i < 170; i++) {
      if (abs(l) > lfar && l * p > 0.0) break;
      float h = 0.14 * (abs(l) + 0.45);
      // RK4 on (l, p, phi)
      float r1 = rOf(l);
      float k1l = p, k1p = b2 * drOf(l) / (r1 * r1 * r1), k1f = b / (r1 * r1);
      float l2 = l + 0.5 * h * k1l, p2 = p + 0.5 * h * k1p;
      float r2 = rOf(l2);
      float k2l = p2, k2p = b2 * drOf(l2) / (r2 * r2 * r2), k2f = b / (r2 * r2);
      float l3 = l + 0.5 * h * k2l, p3 = p + 0.5 * h * k2p;
      float r3 = rOf(l3);
      float k3l = p3, k3p = b2 * drOf(l3) / (r3 * r3 * r3), k3f = b / (r3 * r3);
      float l4 = l + h * k3l, p4 = p + h * k3p;
      float r4 = rOf(l4);
      float k4l = p4, k4p = b2 * drOf(l4) / (r4 * r4 * r4), k4f = b / (r4 * r4);
      l += h / 6.0 * (k1l + 2.0 * k2l + 2.0 * k3l + k4l);
      p += h / 6.0 * (k1p + 2.0 * k2p + 2.0 * k3p + k4p);
      phi += h / 6.0 * (k1f + 2.0 * k2f + 2.0 * k3f + k4f);
    }
    // the rest of the way out is a straight line
    float rEnd = rOf(l);
    phiTot = phi + asin(clamp(b / rEnd, 0.0, 1.0));
    }
    vec3 a = cos(phiTot) * e1 + sin(phiTot) * e2;
    bool far = l > 0.0;

    // lensing footprint, for the stars
    vec3 ddx = dFdx(a), ddy = dFdy(a);
    float area = max(length(cross(ddx, ddy)), 1e-14);
    float sig = clamp(sqrt(area), pix * 0.55, 0.5);
    float aniso = clamp((dot(ddx, ddx) + dot(ddy, ddy)) / (2.0 * area), 1.0, 60.0);

    vec3 col;
    if (far) {
      col = farSky(a, sig, pix, aniso);
    } else {
      // where the returning ray's straight path runs (for Saturn's parallax)
      vec3 o = b * (sin(phiTot) * e1 - cos(phiTot) * e2);
      col = nearSky(a, o, sig, pix, aniso);
    }
    outColor = vec4(col, 1.0);
  }
`;

export function createWormhole(gl) {
  const prog = startProgram(gl, FULLSCREEN_VS, FS, 'wormhole');
  return {
    programs: [prog],
    setup() { finishProgram(gl, prog); },
    /* f: { l, tanFov, shift, view (9 numbers, column-major), rho, w,
            farGain, nearGain, ext, saturn: {pos, r, ringN, sun} } */
    render(f, aspect, pix, heroTex, heroCam, flow, time) {
      const u = prog.u;
      gl.useProgram(prog.p);
      gl.uniform1f(u.uAspect, aspect);
      gl.uniform1f(u.uTanFov, f.tanFov);
      gl.uniform2fv(u.uShift, f.shift);
      gl.uniformMatrix3fv(u.uView, false, f.view);
      gl.uniform1f(u.uL, f.l);
      gl.uniform1f(u.uRho, f.rho);
      gl.uniform1f(u.uW, f.w);
      gl.uniform2f(u.uPix, pix[0], pix[1]);
      gl.uniform1f(u.uTime, time);
      gl.uniform1f(u.uHeroTan, heroCam.tanFov);
      gl.uniform1f(u.uHeroExt, f.ext);
      gl.uniform2fv(u.uHeroShift, heroCam.shift);
      gl.uniform3fv(u.uHeroRight, heroCam.right);
      gl.uniform3fv(u.uHeroUp, heroCam.up);
      gl.uniform3fv(u.uHeroFwd, heroCam.fwd);
      gl.uniform1f(u.uFlowTexel, flow.texel);
      gl.uniform1f(u.uFarGain, f.farGain);
      gl.uniform1f(u.uNearGain, f.nearGain);
      gl.uniform3fv(u.uSatPos, f.saturn.pos);
      gl.uniform1f(u.uSatR, f.saturn.r);
      gl.uniform3fv(u.uRingN, f.saturn.ringN);
      gl.uniform3fv(u.uSunDir, f.saturn.sun);
      bindTex(gl, 0, heroTex.tex);
      gl.uniform1i(u.uHero, 0);
      bindTex(gl, 1, flow.cube, gl.TEXTURE_CUBE_MAP);
      gl.uniform1i(u.uFlow, 1);
      draw(gl);
    },
  };
}
