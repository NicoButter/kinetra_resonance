/*
 * Canonical Rhubarb-code -> articulation -> base MouthPose mapping.
 * Rhubarb codes are animation positions, not literal phoneme names.
 */
(() => {
  const poseApi = globalThis.KinetraMouthPose || (typeof require !== 'undefined' ? require('./mouth-pose.js') : null);
  const define = (alias, label, values) => Object.freeze({alias, label, pose: poseApi.createMouthPose(values, {freeze: true})});
  const ARTICULATIONS = Object.freeze({
    A: define('MBP', 'Closed / pressure', {
      jawOpen: .02, lipOpen: 0, lipWidth: .62, lipClosure: 1, lipPressure: .78,
    }),
    B: define('CONS', 'Consonants / small', {
      jawOpen: .16, lipOpen: .12, lipWidth: .72, lipSpread: .32,
      lipClosure: .12, lipPressure: .15, upperTeethVisible: .78, lowerTeethVisible: .18,
    }),
    C: define('OPEN-MID', 'Open mid', {
      jawOpen: .42, lipOpen: .48, lipWidth: .70, lipSpread: .20,
      upperTeethVisible: .12, lowerTeethVisible: .05,
    }),
    D: define('OPEN-WIDE', 'Open wide', {
      jawOpen: .86, lipOpen: .90, lipWidth: .80, lipSpread: .12,
      upperTeethVisible: .04, lowerTeethVisible: .04, tongueVisible: .08,
    }),
    E: define('ROUND', 'Round', {
      jawOpen: .34, lipOpen: .34, lipWidth: .50, lipRound: .74, lipPucker: .18,
      upperTeethVisible: .04,
    }),
    F: define('UW', 'Puckered UW', {
      jawOpen: .18, lipOpen: .14, lipWidth: .31, lipRound: 1, lipPucker: .94,
      lipClosure: .04, lipPressure: .08, upperTeethVisible: .01,
    }),
    G: define('FV', 'Labiodental FV', {
      jawOpen: .18, lipOpen: .12, lipWidth: .68, lipRound: .05, lipSpread: .20,
      lipClosure: .03, lipPressure: .12, upperTeethVisible: 1,
      lowerTeethVisible: .08, lowerLipRaise: .78, labiodentalContact: 1,
    }),
    H: define('L', 'Tongue-raised L', {
      jawOpen: .50, lipOpen: .52, lipWidth: .68, lipRound: .08, lipSpread: .14,
      upperTeethVisible: .68, lowerTeethVisible: .05, lowerLipRaise: .10,
      tongueVisible: .90, tongueRaise: 1, tongueForward: .68,
    }),
    X: define('REST', 'Relaxed rest', {
      jawOpen: .06, lipOpen: .025, lipWidth: .64, lipRound: .03,
      lipClosure: .78, lipPressure: 0,
    }),
  });
  const validShape = shape => Object.hasOwn(ARTICULATIONS, shape) ? shape : 'X';
  const api = Object.freeze({ARTICULATIONS, validShape, entry: shape => ARTICULATIONS[validShape(shape)]});
  globalThis.KinetraMouthShapes = api;
  if (typeof module !== 'undefined') module.exports = api;
})();
