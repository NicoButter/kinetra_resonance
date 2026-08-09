(() => {
  class MouthRenderer {
    setViseme() { throw new Error('MouthRenderer.setViseme must be implemented.'); }
    setIntensity() {}
    setPitch() {}
    setPresence() {}
    reset() { this.setViseme('X', {immediate: true}); }
    destroy() {}
  }
  globalThis.MouthRenderer = MouthRenderer;
})();
