(() => {
  class MouthRenderer {
    setPose() { throw new Error('MouthRenderer.setPose must be implemented.'); }
    reset(pose, options = {}) { if (pose) this.setPose(pose, {...options, immediate: true}); }
    destroy() {}
  }
  globalThis.MouthRenderer = MouthRenderer;
  if (typeof module !== 'undefined') module.exports = {MouthRenderer};
})();
