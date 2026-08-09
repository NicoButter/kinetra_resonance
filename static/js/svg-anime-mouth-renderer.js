/* SVG rig for Kinetra review. Anime.js is optional: the rig snaps if unavailable. */
(() => {
  const shapes = globalThis.KinetraMouthShapes;
  const helpers = globalThis.MouthPreviewHelpers;
  class SvgAnimeMouthRenderer extends globalThis.MouthRenderer {
    constructor(element, {transitionMs = 70} = {}) {
      super(); this.element = element; this.transitionMs = transitionMs; this.shape = 'X'; this.activeAnimations = [];
      this.intensity = .5; this.pitch = null; this.presence = 1; this.reducedMotion = matchMedia('(prefers-reduced-motion: reduce)').matches;
      this.element.innerHTML = `<svg class="mouth-rig" viewBox="0 0 220 170" role="img" aria-live="polite" aria-label="Mouth viseme X"><path class="mouth-nose-guide" d="M101 24 Q110 16 119 24 L125 45 Q110 51 95 45Z"/><path class="mouth-jaw-guide" d="M65 132 Q110 154 155 132"/><g class="mouth-expression"><path class="mouth-interior"/><path class="mouth-tongue" d="M78 103 Q110 84 142 103 Q110 116 78 103Z"/><path class="mouth-upper-teeth" d="M59 77 Q110 62 161 77 L154 88 Q110 82 66 88Z"/><path class="mouth-upper-lip"/><path class="mouth-lower-lip"/></g><g class="mouth-targets" aria-hidden="true"></g></svg>`;
      this.svg = this.element.querySelector('svg'); this.expression = this.element.querySelector('.mouth-expression'); this.targets = this.element.querySelector('.mouth-targets');
      this.layers = Object.fromEntries(['upperLip', 'lowerLip', 'interior'].map(name => [name, this.element.querySelector(`.mouth-${name.replace(/[A-Z]/g, letter => `-${letter.toLowerCase()}`)}`)]));
      Object.entries(shapes).forEach(([name, shape]) => { ['upperLip', 'lowerLip', 'interior'].forEach(layer => { const path = document.createElementNS('http://www.w3.org/2000/svg', 'path'); path.id = `mouth-target-${name}-${layer}`; path.setAttribute('d', shape[layer]); this.targets.append(path); }); });
      this.setViseme('X', {immediate: true}); this.applyExpression();
    }
    cancelAnimations() { this.activeAnimations.forEach(animation => animation?.cancel?.()); this.activeAnimations = []; }
    setViseme(shape, {immediate = false} = {}) {
      const next = helpers.validShape(shape); if (next === this.shape && !immediate) return; this.cancelAnimations(); const target = shapes[next];
      const duration = immediate || this.reducedMotion ? 0 : this.transitionMs;
      Object.entries(this.layers).forEach(([layer, path]) => { const targetPath = this.targets.querySelector(`#mouth-target-${next}-${layer}`); if (!duration || !globalThis.anime?.animate || !globalThis.anime?.svg?.morphTo) path.setAttribute('d', target[layer]); else this.activeAnimations.push(globalThis.anime.animate(path, {d: globalThis.anime.svg.morphTo(targetPath, .28), duration, ease: 'outCubic'})); });
      this.shape = next; this.svg.setAttribute('aria-label', `Mouth viseme ${next}`); this.element.dataset.rendererMode = globalThis.anime ? 'anime' : 'snap';
      const teeth = this.element.querySelector('.mouth-upper-teeth'); const tongue = this.element.querySelector('.mouth-tongue'); const jaw = this.element.querySelector('.mouth-jaw-guide');
      teeth.style.opacity = target.teethOpacity; tongue.style.opacity = target.tongueOpacity; jaw.style.transform = `translateY(${target.jawY}px)`;
    }
    setIntensity(value) { this.intensity = Math.max(0, Math.min(1, Number(value) || 0)); this.applyExpression(); }
    setPitch(value) { this.pitch = value == null || !Number.isFinite(Number(value)) ? null : Math.max(0, Math.min(1, Number(value))); this.applyExpression(); }
    setPresence(value) { this.presence = Math.max(0, Math.min(1, Number(value) || 0)); this.applyExpression(); }
    applyExpression() { const opening = Math.max(.85, Math.min(1.1, .85 + this.intensity * .25)); const pitchLift = this.pitch == null ? 0 : (this.pitch - .5) * 3; const presence = .92 + this.presence * .08; this.expression.style.transform = `translateY(${pitchLift.toFixed(2)}px) scaleY(${(opening * presence).toFixed(3)})`; }
    destroy() { this.cancelAnimations(); this.element.replaceChildren(); }
  }
  globalThis.SvgAnimeMouthRenderer = SvgAnimeMouthRenderer;
})();
