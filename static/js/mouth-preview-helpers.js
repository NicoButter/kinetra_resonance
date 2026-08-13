/* Pure timing helpers shared by the Review Editor and lightweight Node checks. */
(() => {
  const SHAPES = new Set(['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H', 'X']);
  const validShape = shape => SHAPES.has(shape) ? shape : 'X';
  const effectiveShape = cue => validShape(cue?.reviewedShape ?? cue?.automaticShape ?? cue?.effectiveShape ?? cue?.shape);
  const activeViseme = (cues, timeMs) => (cues || []).find(cue => cue.startMs <= timeMs && timeMs < cue.endMs) || null;
  const visemeContext = (cues, timeMs) => {
    const list = cues || [];
    const index = list.findIndex(cue => cue.startMs <= timeMs && timeMs < cue.endMs);
    return {index, previous: index > 0 ? list[index - 1] : null, current: index >= 0 ? list[index] : null, next: index >= 0 && index < list.length - 1 ? list[index + 1] : null};
  };
  const isSeekJump = (previousMs, currentMs, thresholdMs = 250) => previousMs != null && Math.abs(currentMs - previousMs) > thresholdMs;
  const api = {validShape, effectiveShape, activeViseme, visemeContext, isSeekJump};
  globalThis.MouthPreviewHelpers = api;
  if (typeof module !== 'undefined') module.exports = api;
})();
