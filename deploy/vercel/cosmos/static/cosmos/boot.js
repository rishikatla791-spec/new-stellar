/* Runs before first paint. Marks the page as scripted and decides whether
 * the wormhole entry plays: not for reduced motion, not when the address
 * points into the page (#features...), not with ?noentry. Without
 * JavaScript none of this runs and everything stays visible. */
(function () {
  var c = document.documentElement.classList;
  c.remove('no-js');
  c.add('js');
  var reduced = false;
  try { reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches; } catch (e) { /* old browser */ }
  if (reduced) c.add('reduced');
  var deep = location.hash && location.hash.length > 1;
  var off = /[?&](noentry|nogl)\b/.test(location.search);
  if (!reduced && !deep && !off) c.add('entering');
})();
