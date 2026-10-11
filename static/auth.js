/* The sign-in stage: two spotlights that follow the focused field.
 *
 * When a field, the submit button or the Google button takes focus, the
 * card is marked .is-lit (the lamps catch and the rest of the card dims)
 * and each light turns to the target: its head rotates about the pivot
 * under its mount, and its beam is stretched to reach just past it. The
 * two beams land a little left and right of the target's centre, so they
 * cross on it. Focus leaving the form turns the lights off. .is-ready
 * marks a complete form; the submit button then stays lit.
 */
(() => {
  "use strict";

  const card = document.querySelector("[data-stage]");
  const form = card && card.querySelector("[data-spot-form]");
  if (!card || !form) return;
  card.classList.add("is-staged");

  const rigs = [...card.querySelectorAll(".rig")].map((el) => ({
    el,
    head: el.querySelector(".rig__head"),
    cone: el.querySelector(".rig__cone"),
    side: el.classList.contains("rig--left") ? -1 : 1,
  }));
  const targets = [...card.querySelectorAll(".spot-field input, .spot-submit, .spot-google")];
  const PIVOT_Y = 16;      // from the top of the rig to the pivot
  const LENS = 40;         // from the pivot to the lens, along the head

  // Dust drifting in each beam.
  for (const rig of rigs) {
    for (let i = 0; i < 16; i++) {
      const d = document.createElement("i");
      const r = () => Math.random();
      d.style.cssText = `left:${30 + r() * 40}%;top:${8 + r() * 80}%;` +
        `--d:${5 + r() * 6}s;--delay:${-r() * 8}s;--o:${0.35 + r() * 0.6};` +
        `--dx:${(r() - 0.5) * 18}px;--dy:${14 + r() * 26}px;` +
        `width:${1 + r() * 1.8}px;height:${1 + r() * 1.8}px`;
      rig.cone.appendChild(d);
    }
  }

  let current = null;
  let offTimer = 0;

  const holderOf = (el) => el.closest(".spot-field") || el;

  function aim(el) {
    const c = card.getBoundingClientRect();
    const t = el.getBoundingClientRect();
    const tx = t.left - c.left, ty = t.top - c.top;
    for (const rig of rigs) {
      const px = rig.el.offsetLeft + rig.el.offsetWidth / 2;
      const py = rig.el.offsetTop + PIVOT_Y;
      const x = tx + t.width * (rig.side < 0 ? 0.27 : 0.73) - px;
      const y = ty + t.height * 0.55 - py;
      const angle = -Math.atan2(x, y) * 180 / Math.PI;
      const reach = Math.max(60, Math.hypot(x, y) - LENS + t.height * 0.15);
      rig.head.style.transform = `rotate(${angle.toFixed(2)}deg)`;
      rig.el.style.setProperty("--reach", `${reach.toFixed(1)}px`);
      rig.el.style.setProperty("--spread", `${Math.min(170, Math.max(90, t.width * 0.4)).toFixed(1)}px`);
    }
  }

  function light(el) {
    clearTimeout(offTimer);
    if (current) holderOf(current).classList.remove("is-focus");
    current = el;
    holderOf(el).classList.add("is-focus");
    aim(el);
    card.classList.add("is-lit");
  }

  function dark() {
    if (current) holderOf(current).classList.remove("is-focus");
    current = null;
    card.classList.remove("is-lit");
    for (const rig of rigs) rig.head.style.transform = "";
  }

  card.addEventListener("focusin", (e) => {
    if (targets.includes(e.target)) light(e.target);
  });
  card.addEventListener("focusout", () => {
    // Moving between fields passes through "nothing focused" for a moment.
    clearTimeout(offTimer);
    offTimer = setTimeout(() => {
      if (!targets.includes(document.activeElement)) dark();
    }, 60);
  });

  function checkReady() {
    card.classList.toggle("is-ready", form.checkValidity());
  }
  form.addEventListener("input", checkReady);
  form.addEventListener("change", checkReady);
  // Browsers fill saved passwords in after the page has loaded.
  setTimeout(checkReady, 400);
  setTimeout(checkReady, 1500);
  checkReady();

  window.addEventListener("resize", () => { if (current) aim(current); });

  // A field focused by autofocus before this script ran: bring the lights
  // up a moment after the page appears, so the entrance is seen.
  if (targets.includes(document.activeElement)) {
    const el = document.activeElement;
    setTimeout(() => { if (document.activeElement === el) light(el); }, 550);
  }
})();
