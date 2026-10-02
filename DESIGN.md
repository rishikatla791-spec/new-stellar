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

## Laptop hero: the light field
On screens 1024px and wider the hero gets its own scene in `silk.js`: a sea
of crimson light seen from low down, after the second reference image.
Phones and tablets keep the ribbon, and so does the closing section; the
renderer only swaps scenes while the light is faded out, so the switch is
never seen.
- 16 ground folds stack toward a horizon 55% up the frame (18% above the
  ribbon's base line). Each is a curtain hanging from its crest: a thin
  bright rim, a sheen just under it, a lit face fading down, and a body that
  hides most of what is behind it, so the troughs go dark. They are drawn
  far to near ("over" blending in the energy buffer); nearer folds are
  taller, swing wider, move faster and are dimmer, and the frame darkens
  toward the bottom.
- Above the horizon: a sky of domain-warped smoke lit from below, with fine
  bright veins, fading toward the top; four faint sky folds with soft edges.
  It and the luminous horizon band are drawn at half size (they are soft).
- 14 white-hot glints ride stretches of crests, mostly near the horizon,
  tapering at both ends and slowly coming and going; they are drawn on top
  so nothing dims them.
- The shared smoke pass is stretched along the flow for this scene, so the
  glow breaks into horizontal haze rather than blobs.
- Measured brightness by tenths of the frame tracks the reference: dark at
  the top, brightest 40-50% down, dark at the bottom.
- The text gets a soft dark backing and shadow so it stays crisp over the
  brightest band, and the field clears out a little sooner on scroll
  (gone by 0.9 screens) because it fills the whole frame.
- Measured: 165 fps (6.1 ms frames) at 1440x900 on an AMD integrated GPU.

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
