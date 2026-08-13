/* Normalized anatomical contract shared by articulation and render layers. */
(() => {
  const FIELDS = Object.freeze([
    'jawOpen', 'lipOpen', 'lipWidth', 'lipRound', 'lipPucker', 'lipSpread',
    'lipClosure', 'lipPressure', 'upperTeethVisible', 'lowerTeethVisible',
    'lowerLipRaise', 'labiodentalContact', 'tongueVisible', 'tongueRaise',
    'tongueForward',
  ]);
  const DEFAULTS = Object.freeze(Object.fromEntries(FIELDS.map(field => [field, field === 'lipWidth' ? .5 : 0])));
  const clamp01 = value => {
    const number = Number(value);
    return Number.isFinite(number) ? Math.max(0, Math.min(1, number)) : 0;
  };
  const createMouthPose = (values = {}, {freeze = false} = {}) => {
    const result = {};
    FIELDS.forEach(field => { result[field] = clamp01(values[field] ?? DEFAULTS[field]); });
    return freeze ? Object.freeze(result) : result;
  };
  const blendMouthPose = (poseA, poseB, amount) => {
    const mix = clamp01(amount);
    const result = {};
    FIELDS.forEach(field => { result[field] = clamp01((poseA?.[field] ?? DEFAULTS[field]) * (1 - mix) + (poseB?.[field] ?? DEFAULTS[field]) * mix); });
    return result;
  };
  const mouthPoseEquals = (poseA, poseB, epsilon = .001) => FIELDS.every(field => Math.abs((poseA?.[field] ?? 0) - (poseB?.[field] ?? 0)) <= epsilon);
  const api = {FIELDS, DEFAULTS, clamp01, createMouthPose, blendMouthPose, mouthPoseEquals};
  globalThis.KinetraMouthPose = api;
  if (typeof module !== 'undefined') module.exports = api;
})();
