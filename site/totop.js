/* Back to top — only where there is nothing better to offer.
 *
 * WHY NOT EVERYWHERE
 * The report page has a sticky section bar. On a page with named sections,
 * "back to top" is a worse version of "jump to Patterns": nobody wants the
 * top, they want a different section. It is only useful on the two pages that
 * are one long list with no anchors — the tracker board and the employer
 * index.
 *
 * WHY IT HIDES
 * A control that is always on screen on a page that rarely needs it is
 * clutter competing with the content. This appears after two viewports AND
 * only while scrolling up — scrolling up is the gesture that means "I have
 * gone too far", which is the only moment the button answers a question the
 * reader is actually asking.
 */
(function () {
  "use strict";
  var SHOW_AFTER = window.innerHeight * 2;

  var btn = document.createElement("button");
  btn.type = "button";
  btn.className = "to-top";
  btn.setAttribute("aria-label", "Back to top");
  btn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"'
    + ' stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
    + '<path d="M12 19V5M5 12l7-7 7 7"/></svg>';
  document.body.appendChild(btn);

  var lastY = window.scrollY || 0, ticking = false;

  function update() {
    var y = window.scrollY || 0;
    var goingUp = y < lastY - 4;
    var deepEnough = y > SHOW_AFTER;
    btn.classList.toggle("on", goingUp && deepEnough);
    lastY = y;
    ticking = false;
  }

  window.addEventListener("scroll", function () {
    if (!ticking) { ticking = true; requestAnimationFrame(update); }
  }, { passive: true });

  window.addEventListener("resize", function () {
    SHOW_AFTER = window.innerHeight * 2;
  }, { passive: true });

  btn.addEventListener("click", function () {
    var reduce = window.matchMedia
      && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    window.scrollTo({ top: 0, behavior: reduce ? "auto" : "smooth" });
  });
})();
