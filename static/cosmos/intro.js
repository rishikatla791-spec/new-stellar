/* The entry sequence as a pure function of time, so any moment can be
 * rendered on its own. Modelled on the film's sequences:
 *
 *   0 - 2.5 s    black, then stars; Saturn backlit on the right (a thin
 *                crescent, rings almost edge on), the Sun throwing lens
 *                ghosts, the wormhole a small glass marble
 *   2.5 - 7 s    we move off and speed up: Saturn swells and slides past,
 *                its rings sweeping by underneath
 *   7 - 11.4 s   we slow toward the mouth and hold close to it: a glass
 *                sphere filling the frame, another universe of galaxies
 *                inside, arcs of bent light round its rim
 *   11.4 - 14    through the tunnel: galaxies streaming and swirling past
 *   14 - 14.3    out the far mouth in a flash of light
 *   14.3 - 18.3  darkness, then Gargantua, far off, blazing up as we close in
 */

export const INTRO = {
  ENTER: 11.4,       // seconds: into the cylinder
  THROAT: 12.7,      // halfway through
  EXIT: 14.0,        // out of the far mouth
  SWITCH: 14.06,     // from here on Gargantua is drawn directly (in the flash)
  END: 18.3,         // arrived: the hero shot
  RHO: 1.0,          // the film's wormhole: throat radius,
  A: 2.5,            // half-length of its cylinder: short like the film's as
                     // we approach (so the far universe fills the mouth),
                     // drawn out into a tunnel as we plunge in, and
  M: 0.22,           // the flare of each mouth (lensing width 1.43 M)
  WIND: 3.0,         // seen from inside, light winds round it three times as far (see 'wind')
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

// Where we are along the passage (throat radii; negative = our side; the
// cylinder spans -A..A).
const PATH = monotone([[0, -92], [2.5, -88], [4.8, -64], [7.0, -30], [8.6, -11],
                       [9.6, -4.6], [10.4, -3.25], [11.1, -2.8], [11.4, -2.55], [12.7, 0],
                       [14.0, 2.55], [14.5, 6], [15.2, 13]]);
// Where we look: yaw (right +), pitch (up +), roll, in degrees. Near the
// mouth the camera dips so the sphere fills the sky above, as in the film.
// In the tunnel the camera looks a little aside, so the way ahead sits
// off-centre and the galaxies swirl past the rest of the frame.
const YAW = monotone([[0, 24], [3.5, 21], [6, 11], [8, 2], [9.2, 0], [11.3, 0], [12.0, 14], [13.3, 16], [14.0, 0], [20, 0]]);
const PITCH = monotone([[0, -6], [3.5, -5], [6, -3], [8, -0.6], [9.2, 0], [11.3, 0], [12.2, 5], [13.5, 3], [14.0, 0], [20, 0]]);
const ROLL = monotone([[0, -3], [4.5, -2], [7.5, 0.6], [9.2, 0], [10.6, 3], [12.0, -6], [13.6, 8], [14.3, 0], [20, 0]]);
const FOV = monotone([[0, 50], [7.5, 47], [10.5, 50], [11.4, 56], [12.7, 64], [14.0, 56], [14.3, 50], [18.3, 40]]);

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
  const { ENTER, THROAT, EXIT, SWITCH, END } = INTRO;
  const [l, speed] = PATH(t);
  const dt = t - THROAT;
  const inTunnel = smooth(ENTER - 0.3, ENTER + 0.2, t) * (1 - smooth(EXIT - 0.1, EXIT + 0.25, t));

  // the ship shudders as it speeds past Saturn, hard in the tunnel
  const shake = 0.18 * smooth(4, 6, t) * (1 - smooth(8, 9.5, t))
              + 0.25 * inTunnel + 1.0 * Math.exp(-(((t - EXIT) / 0.25) ** 2))
              + 0.6 * Math.exp(-(((t - ENTER) / 0.2) ** 2));
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
  const fast = clamp((speed - 9) / 12, 0, 1) * 0.022;
  // the tunnel itself stays crisp; the streaks come at the two mouths
  const plunge = 0.012 * inTunnel + 0.16 * Math.exp(-(((t - EXIT) / 0.25) ** 2))
               + 0.06 * Math.exp(-(((t - ENTER) / 0.22) ** 2));
  // colour fringes only in the hard streaks at the mouths, not in the tunnel
  const streak = { center, amount: fast + plunge, chroma: 0.014 * clamp((plunge - 0.03) / 0.15, 0, 1) };
  const flash = 0.6 * Math.exp(-(((t - EXIT) / 0.08) ** 2));

  // The Sun's lens flare: on screen, not behind Saturn, not swallowed by
  // the mouth once that has grown past it.
  let flare = [0, 0, 0, 0];
  const sp = project(SATURN.sun, view, tanFov, shift, aspect);
  if (sp && t < ENTER) {
    const rc = Math.abs(l) + INTRO.RHO;
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
  // the light of the far universe bursting in as we leave the tunnel
  const burst = Math.exp(-(((t - EXIT) / 0.16) ** 2));
  if (burst > 0.02) flare = [0.62, 0.42, 0.5 * burst, 2];

  // Out the far side: Gargantua far away, lighting up as we close in.
  const x = clamp((t - SWITCH) / (END - SWITCH), 0, 1);
  const approach = 1 - (1 - x) ** 3;
  // The hand-off from the wormhole to Gargantua's own renderer happens in
  // the flash at the far mouth, and carries everything across: the same
  // sky (the far universe, thinning out over three seconds), the same
  // shake, the same field of view, the dust still streaming; the black
  // hole itself fades in from a speck.
  const heroFrame = {
    dist: hero.dist + 290 * (1 - approach),
    shiftX: Math.tan(yaw),
    roll: roll / d2r,
    hole: smooth(SWITCH, SWITCH + 1.2, t),
    farDens: 0.3,
    farG: 1.0,
    farGalaxies: 0.4,    // the same far universe as through the wormhole
    incl: 88.6 - (88.6 - hero.incl) * smooth(SWITCH, END, t),
    fov: t < SWITCH ? fov : FOV(t)[0],
    shiftY: hero.shiftY * smooth(END - 2.2, END, t) + Math.tan(pitch),
    diskGain: hero.diskGain * smooth(SWITCH + 0.3, SWITCH + 3.0, t),
    smoke: (hero.smoke || 0) * smooth(END - 2.8, END, t),
    skyContrast: 1 - smooth(SWITCH, SWITCH + 2.2, t),
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
    a: 0.05 + (INTRO.A - 0.05) * smooth(ENTER - 0.35, ENTER, t),
    m: INTRO.M,
    farGain: 1.0,
    nearGain: 0.0,
    wormScale: 0.6,
    // the film's tunnel light: dark teal-green, crushed blacks (measured
    // from the film: mid-tones ~RGB 7,24,18); a hint of it as the sphere
    // fills the frame, all of it inside, gone again in the exit flash
    grade: [0.72, 1.0, 0.9, 0.7 * inTunnel],
    // a gentle swirl inside the tunnel
    twist: 0.7,
    // Inside, the rays wind round the tunnel three times as far as its
    // length alone would make them - the film's tunnel is long - so the way
    // ahead shows the far universe again and again, nested, each image
    // smaller, the edges between them squeezed into arcs; the nesting
    // unwinds as the far mouth comes up and opens
    wind: 1 + (INTRO.WIND - 1) * smooth(ENTER - 0.35, ENTER, t),
    // the far universe: tiny galaxy specks (constant, so none pops)
    farGalaxies: 0.4,
    // how far we have travelled, for the dust streaming past: fast through
    // the wormhole, then easing off as Gargantua comes up
    travel: t < SWITCH ? (l + 92) * 1.15 : (PATH(SWITCH)[0] + 92) * 1.15 + 70 * approach,
    saturn: SATURN,
    streak,
    fade: smooth(0.5, 3.0, t),          // black first, then the stars and Saturn
    flash,
    flare,
    hero: heroFrame,
  };
}
