/* Runs before first paint: lets the stylesheet hide what landing.js will
   animate in, so nothing flashes. Without JavaScript everything stays
   visible. */
(function () {
  var c = document.documentElement.classList;
  c.remove('no-js');
  c.add('js');
  try {
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) c.add('reduced');
  } catch (e) { /* old browser: animate */ }
})();
