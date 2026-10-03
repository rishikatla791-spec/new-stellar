/* Small WebGL2 helpers shared by every pass. */

export const FULLSCREEN_VS = /* glsl */`#version 300 es
  // One triangle that covers the screen; no vertex buffers needed.
  out vec2 vUv;
  void main() {
    vec2 p = vec2(float((gl_VertexID << 1) & 2), float(gl_VertexID & 2));
    vUv = p;
    gl_Position = vec4(p * 2.0 - 1.0, 0.0, 1.0);
  }
`;

function excerpt(src, log) {
  const lines = src.split('\n').map((l, i) => `${String(i + 1).padStart(4)} ${l}`);
  const m = /ERROR: \d+:(\d+)/.exec(log || '');
  const at = m ? Number(m[1]) : 0;
  return at ? lines.slice(Math.max(0, at - 4), at + 3).join('\n') : '';
}

/* Start compiling a program without waiting for it. With
   KHR_parallel_shader_compile the driver builds it in the background; ask
   programDone() before finishProgram(), or finishProgram() will wait. */
export function startProgram(gl, vsSrc, fsSrc, name) {
  const vs = gl.createShader(gl.VERTEX_SHADER);
  gl.shaderSource(vs, vsSrc);
  gl.compileShader(vs);
  const fs = gl.createShader(gl.FRAGMENT_SHADER);
  gl.shaderSource(fs, fsSrc);
  gl.compileShader(fs);
  const p = gl.createProgram();
  gl.attachShader(p, vs);
  gl.attachShader(p, fs);
  gl.linkProgram(p);
  return { p, vs, fs, vsSrc, fsSrc, name, u: {} };
}

export function programDone(gl, prog, ext) {
  return !ext || gl.getProgramParameter(prog.p, ext.COMPLETION_STATUS_KHR);
}

/* Check the result and look up the uniforms (prog.u.uName). */
export function finishProgram(gl, prog) {
  const { p, vs, fs, name } = prog;
  if (!gl.getProgramParameter(p, gl.LINK_STATUS) && !gl.isContextLost()) {
    for (const [sh, src, kind] of [[vs, prog.vsSrc, 'vs'], [fs, prog.fsSrc, 'fs']]) {
      if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS)) {
        const log = gl.getShaderInfoLog(sh);
        throw new Error(`${name}.${kind}: ${log}\n${excerpt(src, log)}`);
      }
    }
    throw new Error(`${name}: ${gl.getProgramInfoLog(p)}`);
  }
  const n = gl.getProgramParameter(p, gl.ACTIVE_UNIFORMS);
  for (let i = 0; i < n; i++) {
    const info = gl.getActiveUniform(p, i);
    prog.u[info.name.replace(/\[0\]$/, '')] = gl.getUniformLocation(p, info.name);
  }
  gl.deleteShader(vs);
  gl.deleteShader(fs);
  return prog;
}

/* Compile and link now (blocking). */
export function program(gl, vsSrc, fsSrc, name) {
  return finishProgram(gl, startProgram(gl, vsSrc, fsSrc, name));
}

/* A colour target: texture + framebuffer. */
export function target(gl, w, h, opt = {}) {
  const internal = opt.internal || gl.RGBA16F;
  const format = opt.format || gl.RGBA;
  const type = opt.type || gl.HALF_FLOAT;
  const filter = opt.filter || gl.LINEAR;
  const tex = gl.createTexture();
  gl.bindTexture(gl.TEXTURE_2D, tex);
  gl.texImage2D(gl.TEXTURE_2D, 0, internal, w, h, 0, format, type, null);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, filter);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, filter === gl.LINEAR_MIPMAP_LINEAR ? gl.LINEAR : filter);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, opt.wrap || gl.CLAMP_TO_EDGE);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, opt.wrap || gl.CLAMP_TO_EDGE);
  const fb = gl.createFramebuffer();
  gl.bindFramebuffer(gl.FRAMEBUFFER, fb);
  gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, tex, 0);
  const ok = gl.checkFramebufferStatus(gl.FRAMEBUFFER) === gl.FRAMEBUFFER_COMPLETE;
  gl.bindFramebuffer(gl.FRAMEBUFFER, null);
  return { tex, fb, w, h, ok };
}

export function dropTarget(gl, t) {
  if (!t) return;
  gl.deleteFramebuffer(t.fb);
  gl.deleteTexture(t.tex);
}

/* Bind a target (or the canvas when t is null) and set the viewport. */
export function bindTarget(gl, t, w, h) {
  gl.bindFramebuffer(gl.FRAMEBUFFER, t ? t.fb : null);
  gl.viewport(0, 0, t ? t.w : w, t ? t.h : h);
}

export function bindTex(gl, unit, tex, kind) {
  gl.activeTexture(gl.TEXTURE0 + unit);
  gl.bindTexture(kind || gl.TEXTURE_2D, tex);
}

export function draw(gl) {
  gl.drawArrays(gl.TRIANGLES, 0, 3);
}
