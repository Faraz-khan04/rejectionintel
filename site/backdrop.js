/* Scroll parallax for the shared backdrop. Clamped, rAF-throttled, and
 * skipped entirely under prefers-reduced-motion. */
/* Texture layers are injected rather than pasted into nine files, so there is
 * one place to change them and no chance of eight pages agreeing and one
 * drifting. Both are inert: fixed, pointer-events none, no animation. */
(function(){
  if (!document.querySelector('.stage')) return;   // page has no backdrop
  ['grain','contour'].forEach(function(cls){
    if (document.querySelector('.' + cls)) return;
    var el = document.createElement('div');
    el.className = cls;
    el.setAttribute('aria-hidden', 'true');
    document.body.appendChild(el);
  });
})();

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
