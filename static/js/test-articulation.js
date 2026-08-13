const assert = require('node:assert/strict');

const poseApi = require('./mouth-pose.js');
const shapesApi = require('./mouth-shapes.js');
const {coarticulationWeights} = require('./mouth-coarticulation.js');
const {ArticulationMapper} = require('./articulation-mapper.js');
require('./mouth-renderer.js');
require('./svg-anime-mouth-renderer.js');

const mapper = new ArticulationMapper();
const base = shape => mapper.basePose(shape);

assert.deepEqual(Object.keys(shapesApi.ARTICULATIONS), ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H', 'X']);
poseApi.FIELDS.forEach(field => Object.values(shapesApi.ARTICULATIONS).forEach(entry => {
  assert.equal(Number.isFinite(entry.pose[field]), true);
  assert.equal(entry.pose[field] >= 0 && entry.pose[field] <= 1, true);
}));
assert.equal(Object.isFrozen(base('A')), true);
assert.notDeepEqual(base('A'), base('X'));
assert.ok(base('A').lipPressure > base('X').lipPressure + .5);
assert.equal(base('A').lipClosure, 1);
assert.equal(shapesApi.entry('F').alias, 'UW');
assert.ok(base('F').lipRound >= .9);
assert.ok(base('F').lipPucker >= .9);
assert.equal(shapesApi.entry('G').alias, 'FV');
assert.equal(base('G').labiodentalContact, 1);
assert.equal(base('G').upperTeethVisible, 1);
assert.ok(base('G').lowerLipRaise >= .75);
assert.equal(shapesApi.entry('H').alias, 'L');
assert.ok(base('H').tongueVisible >= .85);
assert.equal(base('H').tongueRaise, 1);
assert.ok(base('D').jawOpen > base('C').jawOpen);
assert.ok(base('D').lipOpen > base('C').lipOpen);
assert.ok(base('F').lipPucker > base('E').lipPucker + .5);

const blended = poseApi.blendMouthPose(base('A'), base('D'), .5);
assert.equal(blended.jawOpen, (base('A').jawOpen + base('D').jawOpen) / 2);
assert.equal(poseApi.clamp01(Number.NaN), 0);
assert.equal(poseApi.clamp01(Infinity), 0);
assert.equal(poseApi.clamp01(2), 1);

const cue = {startMs: 100, endMs: 200};
assert.deepEqual(coarticulationWeights({cue, timeMs: 150, windowMs: 40, hasPrevious: true, hasNext: true}), {previousInfluence: 0, nextInfluence: 0});
assert.ok(coarticulationWeights({cue, timeMs: 100, windowMs: 40, hasPrevious: true}).previousInfluence > 0);
assert.ok(coarticulationWeights({cue, timeMs: 195, windowMs: 40, hasNext: true}).nextInfluence > 0);
assert.deepEqual(coarticulationWeights({cue, timeMs: 195, windowMs: 0, hasPrevious: true, hasNext: true}), {previousInfluence: 0, nextInfluence: 0});

const contextual = mapper.map({viseme: 'G', previousViseme: 'A', nextViseme: 'C', previousInfluence: .1, nextInfluence: .2});
assert.ok(contextual.jawOpen > base('G').jawOpen);
assert.ok(contextual.labiodentalContact > .7);
const quiet = mapper.map({viseme: 'D', intensity: 0, presence: 0});
const loud = mapper.map({viseme: 'D', intensity: 1, presence: 1});
assert.ok(loud.jawOpen > quiet.jawOpen);
assert.ok(loud.jawOpen - quiet.jawOpen < .2);
const nullPitch = mapper.map({viseme: 'C', pitchNormalized: null});
const midPitch = mapper.map({viseme: 'C', pitchNormalized: .5});
assert.ok(poseApi.mouthPoseEquals(nullPitch, midPitch));
const lowPitch = mapper.map({viseme: 'C', pitchNormalized: 0});
const highPitch = mapper.map({viseme: 'C', pitchNormalized: 1});
assert.ok(Math.abs(highPitch.lipSpread - lowPitch.lipSpread) <= .041);

const geometry = globalThis.SvgAnimeMouthRenderer.geometryForPose;
const distinctive = ['A', 'F', 'G', 'H'].map(shape => geometry(base(shape)));
assert.equal(new Set(distinctive.map(item => `${item.upperLip}|${item.lowerLip}|${item.upperTeethOpacity}|${item.tongueOpacity}`)).size, 4);
assert.equal(distinctive[0].upperTeethOpacity, 0); // MBP
assert.equal(distinctive[2].upperTeethOpacity, 1); // FV
assert.ok(distinctive[3].tongueOpacity >= .85); // L

const artificialSequence = ['A', 'G', 'C', 'F', 'H', 'X'];
for (let index = 0; index < artificialSequence.length; index += 1) {
  const dynamic = mapper.map({viseme: artificialSequence[index], previousViseme: artificialSequence[index - 1], nextViseme: artificialSequence[index + 1], previousInfluence: .12, nextInfluence: .18});
  poseApi.FIELDS.forEach(field => assert.ok(dynamic[field] >= 0 && dynamic[field] <= 1));
  Object.values(geometry(dynamic)).filter(value => typeof value === 'string').forEach(path => assert.equal(path.includes('NaN'), false));
}

console.log('articulation mapper and anatomical geometry: ok');
