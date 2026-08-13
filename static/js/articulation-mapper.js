/* Maps Rhubarb positions and bounded vocal expression to an anatomical pose. */
(() => {
  const poseApi = globalThis.KinetraMouthPose || (typeof require !== 'undefined' ? require('./mouth-pose.js') : null);
  const shapesApi = globalThis.KinetraMouthShapes || (typeof require !== 'undefined' ? require('./mouth-shapes.js') : null);

  class ArticulationMapper {
    constructor({articulationWeight = .86, expressionWeight = .14} = {}) {
      this.articulationWeight = poseApi.clamp01(articulationWeight);
      this.expressionWeight = Math.min(.2, poseApi.clamp01(expressionWeight));
    }
    entry(viseme) { return shapesApi.entry(viseme); }
    aliasFor(viseme) { return this.entry(viseme).alias; }
    basePose(viseme) { return this.entry(viseme).pose; }
    applyExpression(basePose, {intensity = .5, pitchNormalized = null, presence = 1} = {}) {
      const pose = poseApi.createMouthPose(basePose);
      const vocalPresence = poseApi.clamp01(presence);
      const intensityDelta = (poseApi.clamp01(intensity) - .5) * this.expressionWeight * (.35 + .65 * vocalPresence);
      pose.jawOpen = poseApi.clamp01(pose.jawOpen + intensityDelta * .72);
      pose.lipOpen = poseApi.clamp01(pose.lipOpen + intensityDelta * .58);
      pose.lipPressure = poseApi.clamp01(pose.lipPressure + intensityDelta * .12);
      const pitch = Number(pitchNormalized);
      if (pitchNormalized != null && Number.isFinite(pitch)) {
        const pitchDelta = (poseApi.clamp01(pitch) - .5) * .04;
        pose.lipSpread = poseApi.clamp01(pose.lipSpread + pitchDelta);
        pose.jawOpen = poseApi.clamp01(pose.jawOpen + pitchDelta * .25);
      }
      return pose;
    }
    map({viseme, previousViseme = null, nextViseme = null, intensity = .5, pitchNormalized = null, presence = 1, previousInfluence = 0, nextInfluence = 0} = {}) {
      let articulation = poseApi.createMouthPose(this.basePose(viseme));
      if (previousViseme) articulation = poseApi.blendMouthPose(articulation, this.basePose(previousViseme), Math.min(.18, poseApi.clamp01(previousInfluence)));
      if (nextViseme) articulation = poseApi.blendMouthPose(articulation, this.basePose(nextViseme), Math.min(.22, poseApi.clamp01(nextInfluence)));
      return this.applyExpression(articulation, {intensity, pitchNormalized, presence});
    }
  }
  const api = {ArticulationMapper};
  globalThis.KinetraArticulation = api;
  if (typeof module !== 'undefined') module.exports = api;
})();
