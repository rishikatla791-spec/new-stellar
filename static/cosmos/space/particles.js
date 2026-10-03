/* Depth: dust and faint stars in the space right around the camera.
 *
 * A few thousand points fill a box that repeats in every direction around
 * the camera, so there are always some near, some far. Each is drawn where
 * it is now and where it was a frame ago, as a short streak: still while
 * the camera is still, long streaks while it races through the wormhole.
 * Near ones are out of focus (wide, soft and dim, like the film's bokeh),
 * far ones are fine points. They are added to the scene before the glow,
 * so the brightest catch a little bloom.
 *
 * The page moves the camera forward a little as it scrolls, so the dust
 * drifts past at different speeds: parallax, which is what makes space
 * read as deep rather than painted.
 */
import { startProgram, finishProgram } from './gl.js';

const VS = /* glsl */`#version 300 es
  precision highp float;
  layout(location = 0) in vec2 aCorner;     // x: 0 tail .. 1 head, y: -1..1 across
  layout(location = 1) in vec4 aP;          // position in the unit box, w: brightness
  layout(location = 2) in vec4 aC;          // colour, a: size seed
  uniform vec3 uRight, uUp, uFwd;
  uniform vec3 uOff, uOffPrev;              // camera travel now and a frame ago
  uniform float uBox;
  uniform float uTanFov;
  uniform float uAspect;
  uniform vec2 uRes;
  uniform float uGain;
  uniform float uFocus;
  uniform float uAperture;
  out vec2 vQ;          // along, across (px)
  flat out float vLen;  // streak length (px)
  flat out float vSig;  // blur radius (px)
  flat out vec3 vCol;

  vec2 toPx(vec3 r) {
    float z = dot(r, uFwd);
    vec2 s = vec2(dot(r, uRight), dot(r, uUp)) / z;
    return vec2(s.x / (uAspect * uTanFov), s.y / uTanFov) * 0.5 * uRes;
  }

  void main() {
    vec3 p = aP.xyz * uBox - uOff;
    vec3 r0 = mod(p + 0.5 * uBox, uBox) - 0.5 * uBox;    // where it is now
    vec3 r1 = r0 + (uOff - uOffPrev);                   // and a frame ago
    float z0 = dot(r0, uFwd);
    float fade = smoothstep(0.35, 1.2, z0) * (1.0 - smoothstep(0.32 * uBox, 0.5 * uBox, length(r0)));
    if (z0 < 0.3 || fade <= 0.001) {
      gl_Position = vec4(2.0, 2.0, 2.0, 1.0);           // behind us or too far: skip
      return;
    }
    vec2 a0 = toPx(r0);
    vec2 a1 = dot(r1, uFwd) > 0.25 ? toPx(r1) : a0;
    vec2 d = a0 - a1;
    float len = length(d);
    if (len > uRes.y * 0.6) { a1 = a0 - d / len * uRes.y * 0.6; d = a0 - a1; len = length(d); }
    vec2 dir = len > 1e-3 ? d / len : vec2(1.0, 0.0);
    vec2 nrm = vec2(-dir.y, dir.x);
    // out of focus when near: the blur circle grows like 1/z
    float coc = uAperture * abs(1.0 / z0 - 1.0 / uFocus) * uRes.y;
    float sig = max(0.55 + aC.a * 0.5, coc * 0.5);
    float pad = sig * 2.6;
    vec2 q = mix(a1, a0, aCorner.x) + dir * (aCorner.x * 2.0 - 1.0) * pad + nrm * aCorner.y * pad;
    gl_Position = vec4(q / (0.5 * uRes), 0.0, 1.0);
    vQ = vec2(aCorner.x * len + (aCorner.x * 2.0 - 1.0) * pad, aCorner.y * pad);
    vLen = len;
    vSig = sig;
    // the same light spread over a longer streak or a wider blur is dimmer
    float flux = uGain * (0.25 + aP.w * aP.w * 2.0) * fade / (1.0 + 0.012 * z0 * z0);
    vCol = aC.rgb * flux / (6.2832 * sig * sig + 2.5066 * sig * len);
  }
`;

const FS = /* glsl */`#version 300 es
  precision highp float;
  in vec2 vQ;
  flat in float vLen;
  flat in float vSig;
  flat in vec3 vCol;
  out vec4 o;
  void main() {
    float t = clamp(vQ.x, 0.0, vLen);
    float dd = length(vec2(vQ.x - t, vQ.y));
    float k = exp(-0.5 * dd * dd / (vSig * vSig));
    o = vec4(vCol * k, 0.0);
  }
`;

export function createParticles(gl, count = 2600) {
  const prog = startProgram(gl, VS, FS, 'particles');
  let vao = null;
  return {
    programs: [prog],
    setup() {
      finishProgram(gl, prog);
      vao = gl.createVertexArray();
      gl.bindVertexArray(vao);
      const quad = new Float32Array([0, -1, 1, -1, 0, 1, 1, 1]);
      const qb = gl.createBuffer();
      gl.bindBuffer(gl.ARRAY_BUFFER, qb);
      gl.bufferData(gl.ARRAY_BUFFER, quad, gl.STATIC_DRAW);
      gl.enableVertexAttribArray(0);
      gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0);
      // deterministic, so every visit looks the same
      let seed = 1234567;
      const rnd = () => ((seed = (seed * 1664525 + 1013904223) >>> 0) / 4294967296);
      const pa = new Float32Array(count * 4), ca = new Float32Array(count * 4);
      for (let i = 0; i < count; i++) {
        pa[i * 4] = rnd(); pa[i * 4 + 1] = rnd(); pa[i * 4 + 2] = rnd();
        pa[i * 4 + 3] = Math.pow(rnd(), 3);
        const t = rnd();
        const col = t < 0.6 ? [0.86, 0.9, 1.0] : t < 0.85 ? [1.0, 0.86, 0.72] : [1.0, 0.95, 0.9];
        ca.set([...col, rnd()], i * 4);
      }
      const pb = gl.createBuffer();
      gl.bindBuffer(gl.ARRAY_BUFFER, pb);
      gl.bufferData(gl.ARRAY_BUFFER, pa, gl.STATIC_DRAW);
      gl.enableVertexAttribArray(1);
      gl.vertexAttribPointer(1, 4, gl.FLOAT, false, 0, 0);
      gl.vertexAttribDivisor(1, 1);
      const cb = gl.createBuffer();
      gl.bindBuffer(gl.ARRAY_BUFFER, cb);
      gl.bufferData(gl.ARRAY_BUFFER, ca, gl.STATIC_DRAW);
      gl.enableVertexAttribArray(2);
      gl.vertexAttribPointer(2, 4, gl.FLOAT, false, 0, 0);
      gl.vertexAttribDivisor(2, 1);
      gl.bindVertexArray(null);
    },
    /* f: { right, up, fwd, off, offPrev, gain, focus, aperture, box }
       Drawn additively into the bound target. */
    render(f, aspect, tanFov, res) {
      if (!vao || !(f.gain > 0)) return;
      const u = prog.u;
      gl.useProgram(prog.p);
      gl.uniform3fv(u.uRight, f.right);
      gl.uniform3fv(u.uUp, f.up);
      gl.uniform3fv(u.uFwd, f.fwd);
      gl.uniform3fv(u.uOff, f.off);
      gl.uniform3fv(u.uOffPrev, f.offPrev);
      gl.uniform1f(u.uBox, f.box || 48);
      gl.uniform1f(u.uTanFov, tanFov);
      gl.uniform1f(u.uAspect, aspect);
      gl.uniform2f(u.uRes, res[0], res[1]);
      gl.uniform1f(u.uGain, f.gain);
      gl.uniform1f(u.uFocus, f.focus || 14);
      gl.uniform1f(u.uAperture, f.aperture == null ? 0.012 : f.aperture);
      gl.enable(gl.BLEND);
      gl.blendFunc(gl.ONE, gl.ONE);
      gl.bindVertexArray(vao);
      gl.drawArraysInstanced(gl.TRIANGLE_STRIP, 0, 4, count);
      gl.bindVertexArray(null);
      gl.disable(gl.BLEND);
    },
  };
}
