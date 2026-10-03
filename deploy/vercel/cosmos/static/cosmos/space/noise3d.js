/* A 64x64x64 block of smooth noise that tiles in every direction, made on
 * the GPU once (one draw per slice). Four octaves in the four channels
 * (4, 8, 16 and 32 cells across), so one texture read gives a whole
 * fractal sum. The smoke around Gargantua and the dust of the far galaxy
 * read it many times per pixel, which a procedural noise could not afford.
 */
import { FULLSCREEN_VS, startProgram, finishProgram, draw } from './gl.js';
import { HASH } from './glsl.js';

const FS = /* glsl */`#version 300 es
  precision highp float;
  precision highp int;
  in vec2 vUv;
  out vec4 o;
  uniform float uZ;
  uniform int uPer;      // cells across this octave
  uniform uint uSalt;
  ${HASH}
  // gradient noise on a lattice that wraps every 'per' cells
  float pnoise3(vec3 p, int per, uint salt) {
    ivec3 i = ivec3(floor(p));
    vec3 f = fract(p);
    vec3 w = f * f * f * (f * (f * 6.0 - 15.0) + 10.0);
    float v[8];
    for (int k = 0; k < 8; k++) {
      ivec3 o3 = ivec3(k & 1, (k >> 1) & 1, k >> 2);
      ivec3 c = ((i + o3) % per + per) % per;
      uint h = pcg(uint(c.x) * 73856093u ^ pcg(uint(c.y) * 19349663u ^ pcg(uint(c.z) * 83492791u + salt)));
      float z = float(h & 1023u) * (2.0 / 1023.0) - 1.0;
      float a = float(h >> 10u) * (6.2831853 / 4194304.0);
      float s = sqrt(max(1.0 - z * z, 0.0));
      v[k] = dot(vec3(s * cos(a), s * sin(a), z), f - vec3(o3));
    }
    return 1.5 * mix(mix(mix(v[0], v[1], w.x), mix(v[2], v[3], w.x), w.y),
                     mix(mix(v[4], v[5], w.x), mix(v[6], v[7], w.x), w.y), w.z);
  }
  // one octave per draw (written to one channel through the colour mask):
  // the noise function appears once, so the shader compiles quickly
  void main() {
    vec3 p = vec3(vUv, uZ);
    o = vec4(0.5 + 0.5 * pnoise3(p * float(uPer), uPer, uSalt));
  }
`;

export function createNoise3D(gl, size = 64) {
  const prog = startProgram(gl, FULLSCREEN_VS, FS, 'noise3d');
  let tex = null;
  return {
    programs: [prog],
    setup() {
      finishProgram(gl, prog);
      tex = gl.createTexture();
      gl.bindTexture(gl.TEXTURE_3D, tex);
      gl.texStorage3D(gl.TEXTURE_3D, 1, gl.RGBA8, size, size, size);
      gl.texParameteri(gl.TEXTURE_3D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_3D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_3D, gl.TEXTURE_WRAP_S, gl.REPEAT);
      gl.texParameteri(gl.TEXTURE_3D, gl.TEXTURE_WRAP_T, gl.REPEAT);
      gl.texParameteri(gl.TEXTURE_3D, gl.TEXTURE_WRAP_R, gl.REPEAT);
      const fb = gl.createFramebuffer();
      gl.bindFramebuffer(gl.FRAMEBUFFER, fb);
      gl.viewport(0, 0, size, size);
      gl.useProgram(prog.p);
      const octaves = [[4, 1], [8, 2], [16, 3], [32, 4]];
      for (let z = 0; z < size; z++) {
        gl.framebufferTextureLayer(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, tex, 0, z);
        gl.uniform1f(prog.u.uZ, (z + 0.5) / size);
        octaves.forEach(([per, salt], ch) => {
          gl.colorMask(ch === 0, ch === 1, ch === 2, ch === 3);
          gl.uniform1i(prog.u.uPer, per);
          gl.uniform1ui(prog.u.uSalt, salt);
          draw(gl);
        });
      }
      gl.colorMask(true, true, true, true);
      gl.bindFramebuffer(gl.FRAMEBUFFER, null);
      gl.deleteFramebuffer(fb);
      gl.deleteProgram(prog.p);
    },
    get tex() { return tex; },
  };
}
