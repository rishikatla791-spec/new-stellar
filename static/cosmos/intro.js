/* The entry sequence as a pure function of time, so any moment can be
 * rendered on its own.
 *
 *   0.0 - 1.6 s  light comes up: stars, Saturn half lit, the wormhole a
 *                small crystal ball ahead
 *   1.6 - 5.4 s  we speed up; the camera turns from Saturn to the mouth,
 *                which swells until its ring of bent light sweeps past
 *   5.4 - 6.0 s  through the throat: streaks, a flash
 *   6.0 - 8.6 s  out the far side, slowing, onto Gargantua
 */

export const INTRO = {
  THROAT: 5.7,      // seconds: crossing the throat
  END: 8.6,         // seconds: arrived, the hero shot
  L0: -12,          // start, in throat radii from the centre
  L1: 8,            // finish, on the far side
  RHO: 1.0,
  W: 0.32,
};

const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
const smooth = (a, b, x) => {
  const t = clamp((x - a) / (b - a), 0, 1);
  return t * t * (3 - 2 * t);
};
const d2r = Math.PI / 180;

function norm(v) {
  const l = Math.hypot(v[0], v[1], v[2]) || 1;
  return [v[0] / l, v[1] / l, v[2] / l];
}

/* Column-major rotation: yaw about y, then pitch about x, then roll. */
function viewMatrix(yaw, pitch, roll) {
  const cy = Math.cos(yaw), sy = Math.sin(yaw);
  const cp = Math.cos(pitch), sp = Math.sin(pitch);
  const cr = Math.cos(roll), sr = Math.sin(roll);
  // R = Ry * Rx * Rz
  const Ry = [cy, 0, -sy, 0, 1, 0, sy, 0, cy];
  const Rx = [1, 0, 0, 0, cp, sp, 0, -sp, cp];
  const Rz = [cr, sr, 0, -sr, cr, 0, 0, 0, 1];
  return mul(mul(Ry, Rx), Rz);
}
function mul(a, b) {
  const o = new Array(9);
  for (let c = 0; c < 3; c++) {
    for (let r = 0; r < 3; r++) {
      o[c * 3 + r] = a[r] * b[c * 3] + a[3 + r] * b[c * 3 + 1] + a[6 + r] * b[c * 3 + 2];
    }
  }
  return o;
}

export const SATURN = {
  pos: norm([-0.40, -0.22, 1.0]).map((v) => v * 270),
  r: 33,
  ringN: norm([0.15, 1.0, -0.35]),
  sun: norm([0.85, 0.35, 0.4]),
};

/* Where we are along the passage, and how fast we are going. */
export function passage(t) {
  const { THROAT, END, L0, L1 } = INTRO;
  if (t < THROAT) {
    const x = clamp(t / THROAT, 0, 1);
    const g = 0.45 * x + 0.55 * x ** 4;
    const dg = 0.45 + 2.2 * x ** 3;
    return { l: L0 * (1 - g), speed: (-L0 * dg) / THROAT };
  }
  const x = clamp((t - THROAT) / (END - THROAT), 0, 1);
  return { l: L1 * (1 - (1 - x) ** 3), speed: (3 * L1 * (1 - x) ** 2) / (END - THROAT) };
}

/* Everything the renderer needs for the intro at time t (seconds).
   hero: the hero camera settings it lands on. */
export function introFrame(t, hero, aspect) {
  const { THROAT, END } = INTRO;
  const { l, speed } = passage(t);
  const turn = 1 - smooth(0.8, 5.0, t);                 // from Saturn to the mouth
  const yaw = -15 * turn * d2r;
  const pitch = 8 * turn * d2r;                         // looking down at Saturn
  const roll = (4 * turn + 1.2 * Math.sin(t * 0.9) * (1 - smooth(5.5, 8.0, t))) * d2r;
  const fov = 52 + (hero.fov - 52) * smooth(4.4, 8.0, t);
  const tanFov = Math.tan((fov * d2r) / 2);
  const heroTan = Math.tan((hero.fov * d2r) / 2);
  const shift = [0, hero.shiftY * smooth(5.8, 8.2, t)];
  const view = viewMatrix(yaw, pitch, roll);

  // the point we fly toward, on screen (for the streaks)
  const ax = [view[2], view[5], view[8]];               // R^T * z
  const vx = ax[0] / ax[2], vy = ax[1] / ax[2];
  const center = [
    0.5 + 0.5 * (vx - shift[0]) / (aspect * tanFov),
    0.5 + 0.5 * (vy - shift[1]) / tanFov,
  ];
  // streaks belong to the passage itself: they build up into the throat
  // and fade out over the next second
  const dt = t - THROAT;
  const streak = 0.26 * (dt < 0 ? Math.exp(-((dt / 0.55) ** 2)) : Math.exp(-((dt / 0.9) ** 2)));
  const flash = Math.exp(-((dt / 0.09) ** 2));
  return {
    t,
    done: t >= END,
    l,
    speed,
    tanFov,
    shift,
    view,
    rho: INTRO.RHO,
    w: INTRO.W,
    farGain: 1 + 5.5 * (1 - smooth(6.2, 8.4, t)),
    farContrast: 1 - smooth(6.2, 8.4, t),
    // after the throat the camera keeps flying toward Gargantua
    heroDist: hero.dist + 46 * (1 - smooth(5.6, END, t)) ** 1.4,
    nearGain: 0.55,
    ext: Math.max(1.0, (tanFov / heroTan) * 1.08),
    // Gargantua is small through the mouth until the throat; then sharp
    heroScale: t < THROAT - 0.3 ? 0.55 : 0.85,
    wormScale: 0.6,
    saturn: SATURN,
    streak: { center, amount: streak },
    fade: smooth(0.1, 1.7, t),
    flash,
  };
}
