/* Visual-only timing weights. Rhubarb cue timestamps remain unchanged. */
(() => {
  const poseApi = globalThis.KinetraMouthPose || (typeof require !== 'undefined' ? require('./mouth-pose.js') : null);
  const coarticulationWeights = ({cue, timeMs, windowMs = 60, hasPrevious = false, hasNext = false} = {}) => {
    if (!cue || !Number.isFinite(Number(timeMs))) return {previousInfluence: 0, nextInfluence: 0};
    const window = Math.max(0, Number(windowMs) || 0);
    if (!window) return {previousInfluence: 0, nextInfluence: 0};
    const fromStart = Math.max(0, Number(timeMs) - Number(cue.startMs));
    const toEnd = Math.max(0, Number(cue.endMs) - Number(timeMs));
    const previousInfluence = hasPrevious && fromStart < window ? .18 * (1 - poseApi.clamp01(fromStart / window)) : 0;
    const nextInfluence = hasNext && toEnd <= window ? .22 * (1 - poseApi.clamp01(toEnd / window)) : 0;
    return {previousInfluence, nextInfluence};
  };
  const api = {coarticulationWeights};
  globalThis.KinetraMouthCoarticulation = api;
  if (typeof module !== 'undefined') module.exports = api;
})();
