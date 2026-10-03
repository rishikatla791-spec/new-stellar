# Stellar landing page design

The public page at `/` (for visitors who are not signed in) and `/welcome`
(for everyone). Files: `templates/landing.html`, `static/landing/`.

## Identity
Deep black space, one crimson ribbon of light flowing left to right, thin
wide-tracked type. Taken from the reference artwork: nav (star mark,
HOME / FEATURES / ABOUT, GET STARTED pill), "MORE THAN JUST AN AI" eyebrow
with hairlines, the STELLΛR wordmark, the tagline, EXPLORE STELLAR, and the
IDEAS / ASSISTANT / BUILDER and BUILT FOR THE CURIOUS footers.

## Tokens
- Background `#030304`; card surface `rgba(16,12,18,.74)` to `rgba(9,7,11,.86)`
- Brand crimson `#FF2E63`, rose `#FF5E88`, pink `#FF9FBC`, text `#FFF5F8`
- Secondary text 68% and 44% of the text colour; hairlines 7.5% and 13% white
- Typeface: Montserrat (variable 100-900, self-hosted woff2, Latin + Λ).
  Wordmark 300 with 0.3em tracking; labels 500 uppercase at 0.22-0.42em;
  headings 200-300 sentence case; body 400
- The wordmark's A is the Greek capital lambda (Λ), as in the artwork; the
  heading carries `aria-label="Stellar"` so screen readers say the word
- Radius 22px for cards, pills for actions; content max 1280px

## Card colours
Every card sets `--c: R G B` in the markup; its spotlight, border glow,
icon, title tint, meta label and shadow all come from it.

| Card | Colour |
|---|---|
| Agent | crimson 255 46 99 |
| Sandbox | emerald 45 226 160 |
| Web research | cyan 34 211 238 |
| Images | amber 255 184 48 |
| Slide decks | violet 167 139 250 |
| Video insight | orange 255 112 67 |
| Memory | magenta 232 121 249 |
| Scheduled tasks | blue 96 165 250 |
| Apps | lime 163 230 53 |
| Chess | silver 226 232 240 |

Flow steps run crimson to pale pink; About pillars reuse crimson, emerald,
blue, amber.

## Motion
- Easing: `cubic-bezier(.16,1,.3,1)` for arrivals, `(.65,0,.35,1)` for loops.
- Intro: eyebrow lines stretch out from the words; the letters of STELLΛR
  gather from a wider spacing and unblur, 70ms apart; the rest rises 18px.
- Ribbon: light flows in from the left over 2.8s, then everything in it
  travels right. Body waves 0.03-0.09 q/s (a screen is ~1.9 q, so ~20-60s
  to cross), folds 0.115 q/s, light pulses 0.30 q/s (~6s to cross).
- Scroll: wheel input eases toward its target (time constant 0.1s);
  keyboard, scrollbar and touch stay native. Anchor links glide with
  ease-in-out over 0.8-1.7s depending on distance.
- Hero exit: content lifts 16vh, shrinks 5% and fades; the ribbon rises at
  half the scroll speed and fades out by 1.2 screens. It returns under the
  closing section, attached to it.
- Reveals: 38px lift, 1.5% scale and 8px blur over 1.1-1.3s; elements that
  arrive together are staggered by position, 85ms apart.
- Cards: pointer spotlight and border glow in the card colour; tilt toward
  the pointer, at most 1.4-5 degrees (less on wide cards), with a 6px lift,
  eased every frame. On touch screens a card lights while it crosses the
  middle of the screen.
- Ticker: capabilities drift left to right (60s loop) and briefly speed up
  with scroll velocity.
- How it works: the line fills left to right with scroll (top to bottom on
  phones); a spark keeps running along the lit part; each dot lights as the
  fill reaches it.
- Workspace: the window swings up from 16 degrees as it arrives; the demo
  types the question, runs four tools, streams the answer and delivers a
  .pptx, once, with Replay.

## Ribbon renderer (`silk.js`)
WebGL2, nothing sampled from an image, and it does not react to the
pointer. Six silk ribbons wind around one travelling path on two depth
layers:
- near (3 ribbons): full resolution, sharp, bright, full speed;
- far (3 ribbons): half resolution and blurred (out of focus), flatter,
  dimmer and 30% slower, so the scene has parallax.

Each ribbon is drawn four ways, all additively into half-float "energy"
buffers:
- veil: a smooth translucent curtain from edge to edge, lit from the edge
  that faces the light and fading across (the lit edge swaps as the ribbon
  twists), with a soft rim and a gentle texture streaming right; brighter
  where the sheet folds edge-on (energy x compression^0.95); it thins out
  and thickens along the ribbon;
- strands: a handful per ribbon (a few strong, more faint), each a soft
  bright core in a glowing halo; they drift across the sheet, weave
  together and apart, and thicken, thin and fade along their length;
- vanes: the feathered fringe of every strand, painted per pixel on a wide
  strip around it: fine curved barbs that leave the strand at a shallow
  angle and lean forward, each with its own length and brightness, in
  tufts with gaps; the vane sits on one side, then the other, and slides
  right with the flow;
- wisps: a few longer barbs that peel right away, curving as they fade.
About 1,600 glitter points ride the stream and twinkle; bokeh and stars sit
behind. Hot zones shared by every ribbon drift right.

Glow is four blurred copies (1/4, 1/8, 1/16, 1/32). The widest two feed a
smoke pass (1/6 size): domain-warped fbm drifting right and churning, so
the haze is textured rather than a smooth halo. The final pass maps energy
to colour (crimson first, warm white only in the hottest light) and adds
film grain that is stronger in the light and fresh every frame.

Budget: at most 2.4M pixels and 1.75 device pixel ratio; resolution drops
in steps when frames run long and recovers when they are fast; phones and
4-core machines draw about two thirds of the strands, wisps and glitter.
Rendering stops when the ribbon is faded out, the tab is hidden, or motion
is paused. Context loss is handled. Measured: about 158 fps (6.3 ms
frames) on an AMD integrated GPU at 964x932.

## Chess card
King g2, knight c3, pawn e7, drawn with the Cburnett pieces (CC BY-SA 3.0,
the set `chess_ui.py` uses). On hover: the pawn steps to e8 (0.5s, e7 and
e8 highlighted gold), turns into a queen with a gold burst (0.52s), then
the knight hops c3 to d5 (from 1s, 0.62s, lifting off the board mid-jump;
both squares light teal like a last move). The label turns to "e8=Q ·
Brilliant !!" and the evaluation bar rises. Leaving plays it backwards. On
touch screens it plays while the card crosses the middle of the screen.

## Resilience
- Reduced motion: no intro, reveals, tilt, smooth scroll or loops; the
  ribbon is one still frame; the demo shows its finished state.
- Pause button (bottom right) stops the ribbon and every CSS loop and
  finishes the demo; remembered per browser.
- Without WebGL: `ribbon-fallback.jpg` (`-sm` on phones) fades with the
  same scroll curve. Without JavaScript: everything is visible and the
  image sits behind the hero.
- Phones: menu overlay with Get started; one-column cards; vertical flow.
- CSP unchanged (`script-src 'self'`): no inline scripts; `boot.js` runs
  before first paint to mark the page as scripted.

# Cosmos edition (`/cosmos`)

A second landing page: `templates/cosmos.html`, `static/cosmos/`. Laptop
first (1024 px and up); phones get a working but unpolished layout.

## Identity
Near-black space (`#020206`) with the cosmic flow moving through it;
Gargantua as the hero; the wormhole near Saturn as the way in; the tesseract
for "how it works"; the footer on the bright flow. Montserrat, as on `/`.
Palette, named in the footer: midnight indigo `#1a1446`, deep plum
`#4a1942`, cosmic blue `#2a5bff`, cosmic violet `#7c4dff`, magenta
`#ff1f9b`, ice white `#e3f3ff`, pearl white `#f8f3ea` (used in the flow, not listed on the page). UI accents use
lighter tints (violet `#8f6bff`, magenta `#ff3fae`, blue `#4d7dff`).

## Renderer (`static/cosmos/space/`)
One fixed WebGL2 canvas, scenes in linear light into a half-float target,
then bloom (13-tap down, tent up, 7 levels), ACES, grain and dither.
- `geodesics.js`: light paths around a non-spinning hole (r_s = 1). Binet's
  equation integrated with RK4 in double precision for 1,280 impact
  parameters (rows spaced in log|b - b_c|), 512 samples each, as an RG32F
  table. Checked against brute-force 3D integration: 1e-6 relative.
- `gargantua.js`: per pixel, b from the camera (a static observer), the
  camera's place on its orbit (8-point Gauss-Legendre), the disk-plane
  crossings (exact, every half turn) looked up in the table: the disk in
  front, the far side over the top and the underside below, and the thin
  ring at the shadow. The disk is a thin slab with flared, turbulent
  thickness (the band frays like the film's), optically thin face-on and
  thick edge-on, Keplerian rotation (the inner edge turns in ~12 s), sooty
  lanes in the outer disk, Doppler beaming at 25% (the film turned it off).
  High energy (2026-10-03): fibrous strands along the flow (a ridged noise
  channel, plus a finer copy), fire colours (ember, orange, gold, white
  heat at the inner edge), the front band sampled twice through its depth,
  and a tight glow (bloom spread 0.6) so the shadow stays black. Hero
  camera: 21 r_s, 4.5 degrees above the disk, FOV 40, disk out to 16 r_s.
  Stars keep their flux under lensing and fade where lensing would smear them.
- Smoke (off since 2026-10-03 - the film's disk is clean; `smoke` > 0
  turns it back on): a flared torus of dusty gas out to 21 r_s, from a 64^3 tiling noise
  volume (`noise3d.js`), swirling and boiling, lit by the disk (fire-orange
  near it, rose, then dusk violet). It is ray marched along the *bent* light
  path (14 samples, in sweep angle) at half resolution into two targets -
  smoke in front of the disk's first crossing and behind it - which the main
  pass composites in order, so the disk, smoke and lensed sky layer correctly.
- `wormhole.js`: the film's own wormhole metric (James, von Tunzelmann,
  Franklin & Thorne 2015): a cylinder of radius rho = 1 and half-length a,
  joined to each universe by a flare of width M = 0.22 (r = rho +
  M(x atan x - ln(1+x^2)/2)). a is 0.05 on the approach (so the far
  universe fills the mouth, as in the film) and is drawn out to 2.5 as we
  plunge, for the tunnel. Per-pixel RK4 for rays within 2.6 rho, the
  cylinder crossed exactly in one step, a fitted bend (34.07 deg / b^1.13)
  beyond. Nothing is drawn on the sphere: it is only the lens. Every pixel
  takes the screen-space derivatives of its sky direction - how the lens
  stretches the sky across it - and draws each source as a gaussian
  convolved with that footprint (glsl.js LENSED). Stars are points: their
  flux scales with the magnification, so squeezed stars fade. Galaxies and
  star-forming knots are extended: they keep their surface brightness, so
  squeezed into the sphere they stay bright, only smaller, and near the
  edge they stretch into arcs. Where one pixel holds a large patch of sky
  (the repeated images just inside the edge) it shows that patch's average
  light, following the sky's large-scale structure - irregular glassy arcs,
  not a ring - and goes dark past a radian per pixel. Mapping (measured):
  the sphere's centre shows straight ahead, 0.5 R ~86 deg, 0.7 R ~131,
  0.85 R straight behind, then the whole sky again, squeezed. Saturn's
  strongly bent images are left out (they hugged the edge as a hard
  crescent the film never shows). Inside, rays wind round the tunnel three
  times as far as its length says (`wind`), so the way ahead shows the far
  universe nested again and again, each image smaller, the edges between
  them squeezed into arcs; the nesting unwinds as the far mouth opens, and
  `twist` turns each nested image a little more. The loops over cells and
  layers have uniform bounds, so one copy of each compiles (~2 s cold).
- `tesseract.js`: ray-marched lattice of beams on three axes plus a finer
  grid; each face is "worldlines" (stripes along the beam) in the film's
  umber, parchment, pewter, oxblood, old gold, teal and white.
- `particles.js`: depth - 2,600 dust specks in a box that repeats round
  the camera, drawn as motion streaks between this frame and the last,
  out of focus when near (bokeh). They race past during the entry and
  drift past as the page scrolls (parallax).
- Sky: black, as in the film (hero ~RGB 3-5; sections carry only a faint
  flow, ~RGB 6). No indigo lift or rose glow tint in the finish pass.
- `flow.js`: the cosmic flow as a 256 px cube map (domain-warped noise),
  one face redrawn per frame; the footer ribbon in screen space, added on
  top so it rises out of the sky without a seam.

## Motion
- Entry (`intro.js`, 18.3 s, skippable with the button, Escape, Enter,
  Space, the wheel or a touch), after the film: black, then sparse stars;
  Saturn backlit with the Sun's six-point star and lens ghosts; we speed
  past it; we slow and hold close to the wormhole, a lensing sphere with
  another universe squeezed inside; through the tunnel (11.4-14 s), that
  universe nested and bent into arcs around us; out in a
  burst of light; then Gargantua far off, blazing up as we close in. One
  continuous shot: the hand-off from the wormhole pass to Gargantua's pass
  happens inside the exit flash and carries the same sky across (the far
  universe's galaxies, thinning out over 3.2 s - the black sky's few
  galaxies are a subset, so none jumps), the same shake, field of view and
  dust; the hole fades in from a speck. No galaxies on our side of the
  wormhole (only stars, the Sun, Saturn); the Sun's lens ghosts are faint. The entry
  starts as soon as its own shaders are ready; Gargantua's finish
  compiling in the background. "Replay entry" runs it again. Not played
  for reduced motion, `#anchor` addresses or `?noentry`.
- Hero: scrolling lifts the camera 13 degrees over the disk and draws it
  back; the hole travels up with the page at 85% speed and fades by one
  screen; the lens shift then relaxes so the sky is undistorted.
- Tesseract: 420vh section, sticky stage; the camera flies the corridor
  with scroll; four steps (x, y, z, t) light in turn; a real 4D hypercube
  (16 corners, 32 edges, x-w and y-z rotation, two perspective steps).
- Smooth wheel scrolling, reveals, card spotlights as on `/`.

## Cards
Near-black at rest (#09090d to #030305, 5.5% white hairline). On hover each
card glows in its own colour pair (--c, --c2) from the palette brought up
to light - indigo 84 70 230, plum 190 64 160, cosmic blue 56 110 255,
violet 132 86 255, magenta 255 40 160, ice 214 236 255, pearl 250 240 222:
pointer light, a conic rim turning round the edge (@property --ang, 5 s),
outer halo, lit icon, glowing title, a 4 px lift.

## Budget and resilience
Shaders compile in parallel (KHR_parallel_shader_compile) while the CPU
builds the table: cold start ~1.1-1.9 s on the AMD iGPU without blocking
the page; the entry starts at ~2.3 s on a cold cache (fast after). The
wormhole pass: ~3 ms on the RTX 3050, ~21 ms on the iGPU at 0.88 scale.
Measured at 1920x1080: RTX 3050 ~6 ms a frame; AMD Radeon 680M hero
~15 ms, tesseract ~14 ms, entry median 15 ms / p90 22 ms (the wormhole
renders at 60% inside the streaks). The hero drifts slowly once live, so the
lensing visibly shifts - it is computed every frame. Resolution adapts to frame
time. `?gputime` reports per-pass GPU timings in `__cosmos.space.stats`.
No WebGL (or `?nogl`): `gargantua.jpg`, rendered by this renderer. Reduced
motion: no entry, a still frame redrawn only on scroll. Pause is remembered.

## The journey (2026-10-03, after the film's tunnel clip)
One continuous shot, start to footer, nothing swapped between sections:
- Entry: Saturn, the wormhole, the tunnel, out to Gargantua (above).
- Wormhole (2026-10-03, matched frame by frame to the film's approach
  still and tunnel clip): no outlined sphere, no drawn shells, no particle
  tunnel - the lensing alone (above). Approach measured against the film
  by radius: black centre between warm specks, the great galaxy at ~0.73 R
  on the left, the cluster low at ~0.5 R, a glassy band brightening to the
  edge (RGB ~20-27 at 0.96-1.0 R vs the film's 17-35). Tunnel: nested,
  off-centre images and irregular ivory arcs in a dark teal grade (mid-tones
  ~RGB 6,18,16; the film's 7,26,20); the entry streak kept light.
- Far universe (glsl.js FARSKY, shared by the wormhole and Gargantua's sky
  so the hand-off cannot jump): mostly black; faint stars; ~80 large and
  ~300 small warm galaxies; one great galaxy (gold core, peach arms
  strung with pink knots, dust lanes), long axis toward straight behind and
  ending short of it, since anything there is smeared round the whole
  sphere; a ragged cluster of blue-white and pink knots in glowing gas,
  torn by dust; a faint teal veil of dust.
- Scroll: the hero is pinned for 1.9 screens while the camera falls into
  Gargantua (21 -> 4 r_s, accelerating; the shadow centres and swells,
  exposure eases down, the dust streams past); the last light at the edges
  goes out as the shadow fills the frame. Features read in that darkness;
  the tesseract emerges from it and is gone before About; stars and a faint
  flow return as About arrives; the footer flow rises last.
- Resolution: steered by the GPU's own time per frame (timer queries),
  floor 0.72, so Gargantua stays sharp however long the page is open.
