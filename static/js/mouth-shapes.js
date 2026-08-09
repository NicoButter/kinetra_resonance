/* Central visual targets for the Kinetra Review Editor mouth rig. */
(() => {
  const pose = (upperLip, lowerLip, interior, teethOpacity, tongueOpacity, jawY) => ({upperLip, lowerLip, interior, teethOpacity, tongueOpacity, jawY});
  const MOUTH_SHAPES = {
    A: pose('M48 84 Q110 77 172 84', 'M48 86 Q110 92 172 86', 'M50 84 Q110 80 170 84 Q110 90 50 84Z', 0, 0, 0),
    B: pose('M47 78 Q110 68 173 78', 'M47 93 Q110 103 173 93', 'M47 79 Q110 64 173 79 Q110 108 47 79Z', .94, 0, 2),
    C: pose('M45 72 Q110 54 175 72', 'M45 102 Q110 120 175 102', 'M45 73 Q110 47 175 73 Q110 127 45 73Z', .18, 0, 5),
    D: pose('M42 64 Q110 35 178 64', 'M42 111 Q110 140 178 111', 'M42 65 Q110 25 178 65 Q110 150 42 65Z', .08, .08, 10),
    E: pose('M63 68 Q110 54 157 68', 'M63 104 Q110 118 157 104', 'M64 69 Q110 47 156 69 Q110 125 64 69Z', 0, 0, 5),
    F: pose('M72 76 Q110 63 148 76', 'M72 96 Q110 109 148 96', 'M73 77 Q110 58 147 77 Q110 114 73 77Z', 0, 0, 3),
    G: pose('M46 74 Q110 55 174 74', 'M46 91 Q110 102 174 91', 'M46 75 Q110 52 174 75 Q110 111 46 75Z', 1, 0, 2),
    H: pose('M48 71 Q110 52 172 71', 'M48 101 Q110 120 172 101', 'M48 72 Q110 46 172 72 Q110 127 48 72Z', .1, 1, 6),
    X: pose('M50 81 Q110 76 170 81', 'M50 89 Q110 94 170 89', 'M51 82 Q110 78 169 82 Q110 93 51 82Z', 0, 0, 0),
  };
  globalThis.KinetraMouthShapes = MOUTH_SHAPES;
})();
