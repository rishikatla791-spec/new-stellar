/* The entry: past Saturn, through the wormhole, into Gargantua's galaxy.
 *
 * Geometry: the film's own wormhole (James, von Tunzelmann, Franklin and
 * Thorne, "Visualizing Interstellar's Wormhole", 2015). Space is described
 * by a proper radial distance l running from our universe (l < 0) through
 * the wormhole (l = 0) into the other one (l > 0). Inside is a cylinder of
 * radius rho and length 2a; outside, each mouth flares like the space
 * around a mass M:
 *
 *     r(l) = rho                                    |l| <= a
 *     r(l) = rho + M (x atan x - ln(1 + x^2) / 2)   x = 2 (|l| - a) / (pi M)
 *
 * Every light ray stays in a plane through the centre, so each pixel
 * integrates just three numbers with RK4,
 *
 *     dl/dlambda = p,  dp/dlambda = b^2 r'(l) / r^3,  dphi/dlambda = b / r^2
 *
 * until it is far out on one side or the other. Rays aimed inside the
 * throat (b < rho) come out in the other universe; the rest swing round
 * and return to ours, which is what makes the mouth look like a crystal
 * ball with arcs of bent starlight around its edge. In the cylinder r is
 * constant, so a ray there runs straight along it while winding round it -
 * done in one step, exactly. Rays near the rim wind round many times: from
 * inside, the tunnel shows nested spheres, as in the film. Rays that never
 * come within 2.6 rho get a fitted bend (34.07 deg / b^1.13, exact at 2.6,
 * within 0.25 deg beyond) instead of the integration.
 *
 * Our side, as in the film's Saturn shots: near-black space with sparse
 * stars, the Sun, and Saturn backlit - a thin cream crescent, an
 * atmosphere glowing at the limb, pink-beige ringlets lit by sunlight
 * scattering forward through them, each body's shadow on the other.
 * The far side: billowing nebulae, dust lanes and small galaxies (glsl.js
 * NEBULA), in the frame the camera will have once through - Gargantua is not
 * in view yet; it is drawn once we are out (space.js switches to it).
 */
import { FULLSCREEN_VS, startProgram, finishProgram, bindTex, draw } from './gl.js';
import { HASH, STARS, NEBULA } from './glsl.js';

const FS = /* glsl */`#version 300 es
  precision highp float;
  precision highp int;
  precision highp sampler3D;
  in vec2 vUv;
  out vec4 outColor;

  uniform float uAspect;
  uniform float uTanFov;
  uniform vec2 uShift;
  uniform mat3 uView;           // camera -> travel frame (z = heading)
  uniform float uL;             // where we are along the passage
  uniform float uRho;
  uniform float uA;             // half-length of the cylinder
  uniform float uM;             // flare width of each mouth
  uniform vec2 uPix;
  uniform float uTime;

  uniform vec3 uFarRight;       // the destination's frame (the hero camera's)
  uniform vec3 uFarUp;
  uniform vec3 uFarFwd;
  uniform samplerCube uFlow;
  uniform float uFlowTexel;
  uniform sampler3D uNoise;
  uniform float uFarGain;
  uniform float uNearGain;

  uniform vec3 uSatPos;
  uniform float uSatR;
  uniform vec3 uRingN;
  uniform vec3 uSunDir;

  const float PI = 3.14159265;
  ${HASH}
  ${STARS}
  ${NEBULA}

  float rOf(float l) {
    float d = abs(l) - uA;
    if (d <= 0.0) return uRho;
    float x = 2.0 * d / (PI * uM);
    return uRho + uM * (x * atan(x) - 0.5 * log(1.0 + x * x));
  }
  float drOf(float l) {
    float d = abs(l) - uA;
    if (d <= 0.0) return 0.0;
    return sign(l) * (2.0 / PI) * atan(2.0 * d / (PI * uM));
  }
  vec3 lin(vec3 c) { return pow(c, vec3(2.2)); }

  // ------------------------------------------------------------- Saturn
  // Ring optical depth by radius (in planet radii): C ring, B ring, the
  // Cassini division, A ring with the Encke gap, the F ring. qfw is how much
  // q one pixel covers: ringlets finer than that are averaged away.
  float ringOpacity(float q, float qfw) {
    float c = smoothstep(1.235, 1.25, q) * (1.0 - smoothstep(1.515, 1.527, q)) * 0.12;
    float b = smoothstep(1.525, 1.545, q) * (1.0 - smoothstep(1.935, 1.950, q))
            * (0.70 + 0.2 * sin(q * 61.0) * sin(q * 17.0));
    float a = smoothstep(2.025, 2.035, q) * (1.0 - smoothstep(2.262, 2.272, q)) * 0.5;
    a *= 1.0 - 0.9 * smoothstep(2.208, 2.212, q) * (1.0 - smoothstep(2.217, 2.221, q));
    float fz = (q - 2.326) / 0.004;
    float f = exp(-fz * fz) * 0.35;
    float detail = 1.0 - smoothstep(0.0015, 0.008, qfw);
    float fine = 1.0 + 0.2 * sin(q * 410.0) * sin(q * 133.0 + 1.3) * detail
                     + 0.12 * sin(q * 97.0 + 0.7);
    return clamp((c + b + a) * fine + f * detail, 0.0, 0.95);
  }
  vec3 ringColor(float q) {
    vec3 cC = lin(vec3(0.58, 0.50, 0.50));
    vec3 cB = lin(vec3(0.88, 0.74, 0.70));
    vec3 cA = lin(vec3(0.82, 0.68, 0.68));
    vec3 c = q < 1.53 ? cC : (q < 2.0 ? cB : cA);
    return c * (0.9 + 0.1 * sin(q * 233.0)) * 0.95;
  }

  // Saturn along the line o + s a (s > 0): colour and coverage.
  vec4 saturn(vec3 o, vec3 a, float qfw) {
    vec3 N = uRingN;
    float R = uSatR;
    vec3 L = uSunDir;
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
    if (sP < 1e19) {
      vec3 x = oc + sP * a;
      vec3 n = normalize(x + (k * k - 1.0) * dot(x, N) * N);
      vec3 xn = normalize(x);
      float lat = dot(xn, N);
      // zonal bands, combed by the winds
      vec4 t = textureLod(uNoise, vec3(xn.x * 0.9, lat * 5.5, xn.z * 0.9) + 0.3, 0.0);
      float turb = t.r * 0.6 + t.g * 0.4 - 0.5;
      float bands = 0.5 + 0.5 * sin(lat * 23.0 + 2.2 * turb + 0.7 * sin(lat * 8.0 + 1.0));
      bands = mix(bands, 0.5 + 0.5 * sin(lat * 67.0 + 3.0 * turb), 0.2);
      vec3 base = mix(lin(vec3(0.78, 0.69, 0.55)), lin(vec3(0.94, 0.88, 0.75)), bands);
      base = mix(base, lin(vec3(0.62, 0.66, 0.68)), smoothstep(0.74, 0.92, abs(lat)));
      float mu = dot(n, L);
      float day = smoothstep(0.0, 0.3, mu) * (0.2 + 0.8 * max(mu, 0.0));
      // the rings' shadow on the clouds
      float dn = dot(L, N);
      float sh = 0.0;
      if (abs(dn) > 1e-4) {
        float tt = -dot(x, N) / dn;
        if (tt > 0.0) sh = ringOpacity(length(x + tt * L) / R, 0.01);
      }
      // backlit: sunlight scattering forward through the atmosphere makes a
      // bright rim along the limb on the Sun's side
      float limb = pow(1.0 - max(dot(n, -a), 0.0), 6.0);
      float fwd = pow(max(dot(a, L) * 0.5 + 0.5, 0.0), 3.0);
      float atm = limb * fwd * smoothstep(-0.12, 0.08, mu);
      vec3 pc = base * (1.7 * day * (1.0 - 0.85 * sh))
              + lin(vec3(1.0, 0.93, 0.82)) * atm * 1.3 * (1.0 - 0.7 * sh)
              + base * 0.0015;                 // ringshine on the night side
      col = vec4(pc, 1.0);
    }
    if (sR < sP) {
      vec3 x = oc + sR * a;
      float op = ringOpacity(q, qfw);
      float tt = -dot(x, L);
      float d2 = dot(x, x) - tt * tt;
      float shadow = (tt > 0.0 && d2 < R * R) ? 0.03 : 1.0;
      bool sameSide = (dot(L, N) > 0.0) == (dot(-a, N) > 0.0);
      // looking toward the Sun through the rings, the thin parts glow
      float fwd = op * (1.0 - op) * 4.0 * pow(max(dot(a, L), 0.0), 3.0);
      float lit = sameSide ? op * (0.2 + 0.8 * abs(dot(L, N))) + 0.6 * fwd : 1.2 * fwd + 0.03 * op;
      vec3 rc = ringColor(q) * lit * shadow * 1.35;
      col.rgb = mix(col.rgb, rc / max(op, 1e-3), op);
      col.a = max(col.a, op);
    }
    return col;
  }

  // --------------------------------------------------------------- skies
  vec3 nearSky(vec3 a, vec3 o, float sig, float pix, float aniso, float qfw) {
    vec3 c = starField(a.yzx, sig, pix) * (0.22 / (aniso * aniso));
    // the Sun: a hard white point (the lens adds its spikes and ghosts)
    float sd = dot(a, uSunDir);
    float ang2 = max(2.0 * (1.0 - sd), 0.0);
    c += vec3(1.0, 0.96, 0.90) * (260.0 * exp(-ang2 / 2.0e-6) + 0.25 * exp(-ang2 / 1.5e-4) + 0.004 * exp(-ang2 / 0.01));
    vec4 s = saturn(o, a, qfw);
    return mix(c, s.rgb, s.a);
  }

  vec3 farSky(vec3 a, float sig, float pix, float aniso) {
    // as the camera will see it once through: heading -> +z, up kept
    vec3 c = vec3(-a.x, a.y, -a.z);
    vec3 w = normalize(c.x * uFarRight + c.y * uFarUp + c.z * uFarFwd);
    vec3 cl = normalize(c);
    vec3 env = nebula(cl) * uFarGain + smallGalaxies(w, sig) * uFarGain;
    env += starField(w, sig, pix) * (1.1 / (aniso * aniso));
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

    // How much ring radius one pixel spans (for the ringlets), from the
    // straight camera ray - in uniform control flow, before any branching.
    vec3 cam = vec3(0.0, 0.0, -rc);
    float dnr = dot(D, uRingN);
    float sR0 = abs(dnr) > 1e-5 ? -dot(cam - uSatPos, uRingN) / dnr : -1.0;
    float q0 = sR0 > 0.0 ? length(cam + sR0 * D - uSatPos) / uSatR : 0.0;
    float qfw = sR0 > 0.0 ? fwidth(q0) : 0.0;

    float l = uL, p = D.z, phi = 0.0;
    float phiTot;
    bool straight = b > 2.6 * uRho;
    if (straight) {
      // Never comes close to the mouth: the fitted bend, applied as far as
      // the ray's closest approach still lies ahead.
      // (the outward direction is -z on our side, +z on the far side)
      float outward = uL > 0.0 ? D.z : -D.z;
      float ahead = 0.5 - 0.5 * tanh(outward * rc / b);
      float bend = radians(34.07) * pow(b / uRho, -1.13) * ahead;
      phiTot = atan(sa, outward) + bend;
    } else {
      float lfar = 2.0 * abs(uL) + 24.0;
      for (int i = 0; i < 200; i++) {
        if (abs(l) > lfar && l * p > 0.0) break;
        if (abs(l) < uA) {
          // through the cylinder in one step: r = rho, so p is constant
          float pc = sign(p == 0.0 ? 1.0 : p) * sqrt(max(1.0 - b2 / (uRho * uRho), 1e-8));
          float target = sign(pc) * (uA + 1e-4);
          phi += b / (uRho * uRho) * (target - l) / pc;
          l = target;
          p = pc;
          continue;
        }
        float h = 0.12 * (abs(l) - uA + 0.3);
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
      phiTot = phi + asin(clamp(b / rOf(l), 0.0, 1.0));
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
      // where the ray's straight path runs, for Saturn: from the camera if
      // it never neared the mouth, else from its closest approach to it
      vec3 o = straight ? cam : b * (sin(phiTot) * e1 - cos(phiTot) * e2);
      col = nearSky(a, o, sig, pix, aniso, qfw);
    }
    outColor = vec4(col, 1.0);
  }
`;

export function createWormhole(gl) {
  const prog = startProgram(gl, FULLSCREEN_VS, FS, 'wormhole');
  let noiseTex = null;
  return {
    programs: [prog],
    setup() { finishProgram(gl, prog); },
    setNoise(tex) { noiseTex = tex; },
    /* f: { l, tanFov, shift, view (9 numbers, column-major), rho, w,
            farGain, nearGain, saturn: {pos, r, ringN, sun} }
       farCam: the frame of the destination (the hero camera). */
    render(f, aspect, pix, farCam, flow, time) {
      const u = prog.u;
      gl.useProgram(prog.p);
      gl.uniform1f(u.uAspect, aspect);
      gl.uniform1f(u.uTanFov, f.tanFov);
      gl.uniform2fv(u.uShift, f.shift);
      gl.uniformMatrix3fv(u.uView, false, f.view);
      gl.uniform1f(u.uL, f.l);
      gl.uniform1f(u.uRho, f.rho);
      gl.uniform1f(u.uA, f.a);
      gl.uniform1f(u.uM, f.m);
      gl.uniform2f(u.uPix, pix[0], pix[1]);
      gl.uniform1f(u.uTime, time);
      gl.uniform3fv(u.uFarRight, farCam.right);
      gl.uniform3fv(u.uFarUp, farCam.up);
      gl.uniform3fv(u.uFarFwd, farCam.fwd);
      gl.uniform1f(u.uFlowTexel, flow.texel);
      gl.uniform1f(u.uFarGain, f.farGain);
      gl.uniform1f(u.uNearGain, f.nearGain);
      gl.uniform3fv(u.uSatPos, f.saturn.pos);
      gl.uniform1f(u.uSatR, f.saturn.r);
      gl.uniform3fv(u.uRingN, f.saturn.ringN);
      gl.uniform3fv(u.uSunDir, f.saturn.sun);
      bindTex(gl, 1, flow.cube, gl.TEXTURE_CUBE_MAP);
      gl.uniform1i(u.uFlow, 1);
      bindTex(gl, 2, noiseTex, gl.TEXTURE_3D);
      gl.uniform1i(u.uNoise, 2);
      draw(gl);
    },
  };
}
