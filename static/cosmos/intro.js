/* The entry sequence as a pure function of time, so any moment can be
 * rendered on its own. Modelled on the film's two sequences:
 *
 *   0 - 2 s     black, then stars; Saturn backlit on the right (a thin
 *               crescent, rings almost edge on), the Sun low on the left
 *               throwing lens ghosts, the wormhole a small glass marble
 *   2 - 6.5 s   we move off and speed up: Saturn swells and slides past,
 *               its rings sweeping by underneath
 *   6.5 - 9.6 s the camera settles on the mouth, which grows into a crystal
 *               ball holding a dusty galaxy - no Gargantua yet
 *   9.6 - 10.7  the plunge: shake, streaks, colour fringing, a surge of light
 *   10.7 - 15   out in the other galaxy: darkness, then Gargantua, far off,
 *               lighting up slowly as we close in, wrapped in its smoke
 */

export const INTRO = {
  THROAT: 10.25,     // seconds: crossing the throat
  SWITCH: 10.7,      // from here on Gargantua is drawn directly
  END: 15.0,         // arrived: the hero shot
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

/* Monotone cubic through keyframes (Fritsch-Carlson): no overshoot, so the
   ship never backs up. Returns value and slope. */
function monotone(keys) {
  const n = keys.length;
  const xs = keys.map((k) => k[0]), ys = keys.map((k) => k[1]);
  const d = [], m = new Array(n);
  for (let i = 0; i < n - 1; i++) d.push((ys[i + 1] - ys[i]) / (xs[i + 1] - xs[i]));
  m[0] = d[0]; m[n - 1] = d[n - 2];
  for (let i = 1; i < n - 1; i++) m[i] = d[i - 1] * d[i] <= 0 ? 0 : (d[i - 1] + d[i]) / 2;
  for (let i = 0; i < n - 1; i++) {
    if (d[i] === 0) { m[i] = m[i + 1] = 0; continue; }
    const a = m[i] / d[i], b = m[i + 1] / d[i], s = a * a + b * b;
    if (s > 9) { const t = 3 / Math.sqrt(s); m[i] = t * a * d[i]; m[i + 1] = t * b * d[i]; }
  }
  return (x) => {
    if (x <= xs[0]) return [ys[0], m[0]];
    if (x >= xs[n - 1]) return [ys[n - 1] + m[n - 1] * (x - xs[n - 1]), m[n - 1]];
    let i = 0;
    while (x > xs[i + 1]) i++;
    const h = xs[i + 1] - xs[i], t = (x - xs[i]) / h;
    const t2 = t * t, t3 = t2 * t;
    const v = (2 * t3 - 3 * t2 + 1) * ys[i] + (t3 - 2 * t2 + t) * h * m[i]
            + (-2 * t3 + 3 * t2) * ys[i + 1] + (t3 - t2) * h * m[i + 1];
    const dv = ((6 * t2 - 6 * t) * ys[i] + (3 * t2 - 4 * t + 1) * h * m[i]
             + (-6 * t2 + 6 * t) * ys[i + 1] + (3 * t2 - 2 * t) * h * m[i + 1]) / h;
    return [v, dv];
  };
}

// Where we are along the passage (throat radii; negative = our side).
const PATH = monotone([[0, -90], [2.0, -86], [4.5, -60], [6.5, -25], [8.3, -8],
                       [9.6, -2.6], [10.25, 0], [10.9, 4.0], [12, 9]]);
// Where we look: yaw (right +), pitch (up +), roll, in degrees.
const YAW = monotone([[0, 24], [3, 21], [5.5, 11], [7.5, 2], [8.6, 0], [20, 0]]);
const PITCH = monotone([[0, -6], [3, -5], [5.5, -3], [7.5, -0.6], [8.6, 0], [20, 0]]);
const ROLL = monotone([[0, -3], [4, -2], [7, 0.6], [8.6, 0], [20, 0]]);
const FOV = monotone([[0, 50], [7, 47], [9.6, 48], [10.25, 54], [10.7, 48], [15, 43]]);

/* Column-major rotation: yaw about y, then pitch about x, then roll. */
function viewMatrix(yaw, pitch, roll) {
  const cy = Math.cos(yaw), sy = Math.sin(yaw);
  const cp = Math.cos(pitch), sp = Math.sin(pitch);
  const cr = Math.cos(roll), sr = Math.sin(roll);
  const Ry = [cy, 0, -sy, 0, 1, 0, sy, 0, cy];
  const Rx = [1, 0, 0, 0, cp, -sp, 0, sp, cp];   // positive pitch looks up
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

// Saturn as in the film's wide shot: 26 units across the equator, 120
// units ahead-right of where we start, seen ten degrees above its rings,
// with the Sun nearly behind it - a thin lit crescent on the night side.
const START = 90.68;
const SAT_DIR = norm([0.62, -0.18, 0.76]);
export const SATURN = {
  pos: [SAT_DIR[0] * 120, SAT_DIR[1] * 120, -START + SAT_DIR[2] * 120],
  r: 26,
  ringN: norm([0.05, 1.0, 0.04]),
  sun: norm([0.035, 0.14, 1.0]),
};

/* Where a direction (travel frame) lands on screen, as uv; null if behind. */
function project(dir, view, tanFov, shift, aspect) {
  // camera = R^T * dir
  const x = view[0] * dir[0] + view[1] * dir[1] + view[2] * dir[2];
  const y = view[3] * dir[0] + view[4] * dir[1] + view[5] * dir[2];
  const z = view[6] * dir[0] + view[7] * dir[1] + view[8] * dir[2];
  if (z <= 0.01) return null;
  return [0.5 + 0.5 * (x / z - shift[0]) / (aspect * tanFov), 0.5 + 0.5 * (y / z - shift[1]) / tanFov];
}

/* Everything the renderer needs for the entry at time t (seconds).
   hero: the hero camera settings it lands on. */
export function introFrame(t, hero, aspect) {
  const { THROAT, SWITCH, END } = INTRO;
  const [l, speed] = PATH(t);
  const dt = t - THROAT;

  // the ship shudders as it speeds past Saturn, hard in the throat
  const shake = 0.18 * smooth(3.5, 5.5, t) * (1 - smooth(7.5, 9.0, t))
              + 1.1 * Math.exp(-((dt / 0.55) ** 2));
  const sx = (Math.sin(t * 37.0) + 0.6 * Math.sin(t * 53.0 + 1.1) + 0.4 * Math.sin(t * 91.0 + 2.3)) * 0.5;
  const sy = (Math.sin(t * 41.0 + 0.7) + 0.6 * Math.sin(t * 59.0 + 2.9) + 0.4 * Math.sin(t * 83.0 + 0.4)) * 0.5;
  const yaw = (YAW(t)[0] + shake * sx) * d2r;
  const pitch = (PITCH(t)[0] + shake * sy) * d2r;
  const roll = (ROLL(t)[0] + shake * sx * 0.7) * d2r;
  const fov = FOV(t)[0];
  const tanFov = Math.tan((fov * d2r) / 2);
  const shift = [0, 0];
  const view = viewMatrix(yaw, pitch, roll);

  // the point we fly toward, on screen (for the streaks)
  const center = project([0, 0, 1], view, tanFov, shift, aspect) || [0.5, 0.5];
  const fast = clamp((speed - 9) / 12, 0, 1) * 0.045;
  const plunge = 0.28 * (dt < 0 ? Math.exp(-((dt / 0.5) ** 2)) : Math.exp(-((dt / 0.85) ** 2)));
  const streak = { center, amount: fast + plunge, chroma: 0.014 * plunge / 0.28 };
  const flash = Math.exp(-((dt / 0.09) ** 2));

  // The Sun's lens flare: on screen, not behind Saturn, not swallowed by
  // the mouth once that has grown past it.
  let flare = [0, 0, 0, 0];
  const sp = project(SATURN.sun, view, tanFov, shift, aspect);
  if (sp && t < SWITCH) {
    const rc = Math.abs(l) + INTRO.RHO - INTRO.W;
    const cam = [0, 0, -rc];
    const oc = [cam[0] - SATURN.pos[0], cam[1] - SATURN.pos[1], cam[2] - SATURN.pos[2]];
    const L = SATURN.sun;
    const tt = -(oc[0] * L[0] + oc[1] * L[1] + oc[2] * L[2]);
    const cx = oc[0] + tt * L[0], cy = oc[1] + tt * L[1], cz = oc[2] + tt * L[2];
    const miss = tt > 0 ? Math.hypot(cx, cy, cz) : 1e9;
    const open = smooth(SATURN.r * 0.92, SATURN.r * 1.04, miss);
    const sunAngle = Math.acos(clamp(L[2], -1, 1));
    const mouth = Math.asin(clamp(INTRO.RHO * 1.4 / rc, 0, 1));
    const clear = 1 - smooth(sunAngle * 0.6, sunAngle * 1.1, mouth);
    const onScreen = smooth(-0.35, 0.0, Math.min(sp[0], sp[1])) * smooth(-0.35, 0.0, Math.min(1 - sp[0], 1 - sp[1]));
    flare = [sp[0], sp[1], open * clear * onScreen, 1];
  }

  // Out the far side: Gargantua far away, lighting up as we close in.
  const x = clamp((t - SWITCH) / (END - SWITCH), 0, 1);
  const approach = 1 - (1 - x) ** 3;
  const heroFrame = {
    dist: hero.dist + 290 * (1 - approach),
    incl: 88.6 - (88.6 - hero.incl) * smooth(SWITCH, END, t),
    fov: t < SWITCH ? fov : FOV(t)[0],
    shiftY: hero.shiftY * smooth(END - 2.2, END, t),
    diskGain: hero.diskGain * smooth(SWITCH + 0.3, SWITCH + 3.0, t),
    smoke: (hero.smoke || 0) * smooth(END - 2.8, END, t),
    skyContrast: 1 - smooth(END - 2.4, END, t),
    skyGain: hero.skyGain,
    starGain: hero.starGain,
  };

  return {
    t,
    done: t >= END,
    useHero: t >= SWITCH,
    l,
    speed,
    tanFov,
    shift,
    view,
    rho: INTRO.RHO,
    w: INTRO.W,
    farGain: 0.6,
    nearGain: 0.18,
    wormScale: 0.6,
    saturn: SATURN,
    streak,
    fade: smooth(0.4, 2.8, t),          // black first, then the stars and Saturn
    flash,
    flare,
    hero: heroFrame,
  };
}
