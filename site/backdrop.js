/* Scroll parallax for the shared backdrop. Clamped, rAF-throttled, and
 * skipped entirely under prefers-reduced-motion. */
(function(){
  var layers = [].slice.call(document.querySelectorAll('.par'));
  if (!layers.length) return;
  if (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  var LIMIT = 300, ticking = false;
  function apply(){
    var y = window.scrollY || 0;
    for (var i = 0; i < layers.length; i++){
      var rate = parseFloat(layers[i].dataset.rate) || 0;
      var off = Math.max(-LIMIT, Math.min(LIMIT, y * rate));
      layers[i].style.transform = 'translate3d(0,' + off.toFixed(1) + 'px,0)';
    }
    ticking = false;
  }
  window.addEventListener('scroll', function(){
    if (!ticking){ ticking = true; requestAnimationFrame(apply); }
  }, {passive:true});
  apply();
})();
