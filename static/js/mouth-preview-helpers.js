/* Pure timing helpers shared by the Review Editor and lightweight Node checks. */
(() => {
  const SHAPES = new Set(['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H', 'X']);
  const validShape = shape => SHAPES.has(shape) ? shape : 'X';
  const effectiveShape = cue => validShape(cue?.reviewedShape ?? cue?.automaticShape ?? cue?.effectiveShape ?? cue?.shape);
  const activeViseme = (cues, timeMs) => (cues || []).find(cue => cue.startMs <= timeMs && timeMs < cue.endMs) || null;
  const isSeekJump = (previousMs, currentMs, thresholdMs = 250) => previousMs != null && Math.abs(currentMs - previousMs) > thresholdMs;
  const api = {validShape, effectiveShape, activeViseme, isSeekJump};
  globalThis.MouthPreviewHelpers = api;
  if (typeof module !== 'undefined') module.exports = api;
})();
