(() => {
  const root = document.documentElement;
  const targets = document.querySelectorAll('[data-reveal]');
  if (!targets.length || !('IntersectionObserver' in window) || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;

  root.classList.add('js-reveal');
  const observer = new IntersectionObserver((entries, instance) => {
    entries.forEach(entry => {
      if (!entry.isIntersecting) return;
      entry.target.classList.add('is-visible');
      instance.unobserve(entry.target);
    });
  }, {threshold: 0.12, rootMargin: '0px 0px -8% 0px'});
  targets.forEach(target => observer.observe(target));
})();
