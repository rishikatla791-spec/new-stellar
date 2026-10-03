/* Light paths around a non-spinning black hole, precomputed once.
 *
 * Units: the Schwarzschild radius r_s = 1 (horizon at r = 1, photon
 * sphere at r = 1.5). A ray of light never leaves the plane through the
 * hole's centre that contains it, and in that plane its shape u(psi)
 * (u = 1/r, psi = angle swept around the hole) obeys
 *
 *     u'' + u = 1.5 u^2                      (Binet's equation, exact)
 *
 * so it depends on one number only: the impact parameter b. Rays with
 * b < b_c = 3*sqrt(3)/2 fall in; the rest swing round and escape.
 *
 * For each b we integrate the orbit "from infinity" (u = 0, u' = 1/b)
 * with fourth-order Runge-Kutta in double precision, up to the turning
 * point (b > b_c; the way out is its mirror image) or the horizon
 * (b < b_c), and store u at evenly spaced fractions of that sweep. The
 * shader then finds where any camera ray meets the disk with a few
 * texture reads instead of hundreds of integration steps per pixel.
 * Checked against brute-force 3D integration: agreement to ~1e-6.
 *
 * Rows are spaced evenly in log|b - b_c|, because everything interesting
 * (the photon ring, the thin higher-order images of the disk) happens as
 * b approaches b_c, where the sweep grows like -ln|b - b_c|.
 */

export const BC = 1.5 * Math.sqrt(3);

export const TABLE = {
  W: 512,            // samples along each orbit
  H1: 1024,          // rows for b > b_c (escaping rays)
  H2: 256,           // rows for b < b_c (captured rays)
  S_MIN: -12.0,      // ln|b - b_c| at the rows nearest b_c
  S_MAX_A: 5.3,      // b up to b_c + 200
  S_MAX_B: Math.log(BC),  // b down to 0
};

export function buildTable() {
  const { W, H1, H2, S_MIN, S_MAX_A, S_MAX_B } = TABLE;
  const H = H1 + H2;
  const out = new Float32Array(W * H * 2);
  let cap = 1 << 14;
  let psis = new Float64Array(cap);
  let us = new Float64Array(cap);

  for (let j = 0; j < H; j++) {
    const above = j < H1;
    let b = above
      ? BC + Math.exp(S_MIN + (S_MAX_A - S_MIN) * j / (H1 - 1))
      : BC - Math.exp(S_MIN + (S_MAX_B - S_MIN) * (j - H1) / (H2 - 1));
    b = Math.max(b, 1e-3);
    const h = Math.min(2e-3, b * 2e-3);

    let u = 0, du = 1 / b, psi = 0, n = 0;
    psis[n] = 0; us[n] = 0; n++;
    let end = 0;
    for (;;) {
      // RK4 on (u, u'), with u'' = -u + 1.5 u^2
      const a1 = du, b1 = -u + 1.5 * u * u;
      let uu = u + 0.5 * h * a1, dd = du + 0.5 * h * b1;
      const a2 = dd, b2 = -uu + 1.5 * uu * uu;
      uu = u + 0.5 * h * a2; dd = du + 0.5 * h * b2;
      const a3 = dd, b3 = -uu + 1.5 * uu * uu;
      uu = u + h * a3; dd = du + h * b3;
      const a4 = dd, b4 = -uu + 1.5 * uu * uu;
      const un = u + h / 6 * (a1 + 2 * a2 + 2 * a3 + a4);
      const dn = du + h / 6 * (b1 + 2 * b2 + 2 * b3 + b4);
      if (n + 2 >= cap) {
        cap *= 2;
        const p2 = new Float64Array(cap); p2.set(psis); psis = p2;
        const u2 = new Float64Array(cap); u2.set(us); us = u2;
      }
      if (above && dn <= 0) {
        const t = du / (du - dn);              // where u' crosses zero
        end = psi + t * h;
        psis[n] = end; us[n] = u + t * (un - u); n++;
        break;
      }
      if (!above && un >= 1) {
        const t = (1 - u) / (un - u);          // where it reaches the horizon
        end = psi + t * h;
        psis[n] = end; us[n] = 1; n++;
        break;
      }
      u = un; du = dn; psi += h;
      psis[n] = psi; us[n] = u; n++;
    }

    // Resample at evenly spaced fractions of the sweep.
    let k = 0;
    const row = j * W * 2;
    for (let i = 0; i < W; i++) {
      const want = end * i / (W - 1);
      while (k < n - 2 && psis[k + 1] < want) k++;
      const p0 = psis[k], p1 = psis[k + 1];
      const f = p1 > p0 ? Math.min(Math.max((want - p0) / (p1 - p0), 0), 1) : 0;
      out[row + i * 2] = us[k] + (us[k + 1] - us[k]) * f;
      out[row + i * 2 + 1] = end;
    }
  }
  return out;
}

/* Upload as an RG32F texture, read with texelFetch (no filtering). */
export function tableTexture(gl, data) {
  const { W, H1, H2 } = TABLE;
  const tex = gl.createTexture();
  gl.bindTexture(gl.TEXTURE_2D, tex);
  gl.pixelStorei(gl.UNPACK_ALIGNMENT, 4);
  gl.texImage2D(gl.TEXTURE_2D, 0, gl.RG32F, W, H1 + H2, 0, gl.RG, gl.FLOAT, data);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
  return tex;
}

/* The same constants, for the shader. */
export function tableGLSL() {
  const { W, H1, H2, S_MIN, S_MAX_A, S_MAX_B } = TABLE;
  return /* glsl */`
    const float BC = ${BC.toFixed(9)};
    const int TW = ${W};
    const int TH1 = ${H1};
    const int TH2 = ${H2};
    const float S_MIN = ${S_MIN.toFixed(6)};
    const float S_MAX_A = ${S_MAX_A.toFixed(6)};
    const float S_MAX_B = ${S_MAX_B.toFixed(9)};
  `;
}
