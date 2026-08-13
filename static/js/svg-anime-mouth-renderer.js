/* Anatomical SVG rig. It receives normalized MouthPose values, never phonetic labels. */
(() => {
  const poseApi = globalThis.KinetraMouthPose;
  const n = value => Number(value).toFixed(2);
  const mix = (from, to, amount) => from + (to - from) * amount;

  function geometryForPose(input) {
    const pose = poseApi.createMouthPose(input);
    const cx = 130;
    const puckerNarrowing = pose.lipPucker * 28;
    const width = Math.max(54, 62 + pose.lipWidth * 112 - puckerNarrowing);
    const half = width / 2;
    const left = cx - half;
    const right = cx + half;
    const closureFactor = 1 - pose.lipClosure * .90;
    const rawOpening = 1.5 + pose.lipOpen * 49 + pose.jawOpen * 10;
    const opening = Math.max(.8, rawOpening * closureFactor);
    const jawDrop = pose.jawOpen * 34;
    const roundLift = pose.lipRound * 7;
    const spreadFlatten = pose.lipSpread * 4;
    const top = 101 - opening * .45 - roundLift * .28;
    const bottom = 101 + opening * .55 + jawDrop * .22 + roundLift * .28;
    const upperArch = Math.max(2, 7 + pose.lipRound * 8 + pose.lipPucker * 4 - spreadFlatten);
    const lowerArch = Math.max(2, 6 + pose.lipRound * 7 + pose.lipPucker * 4 - spreadFlatten);
    const pressure = pose.lipPressure;
    const upperThickness = 4.5 + pressure * 5 + pose.lipPucker * 2.5;
    const lowerThickness = 5 + pressure * 4 + pose.lipPucker * 3;
    const teethBottom = top + Math.max(7, opening * .34);
    const contactY = teethBottom + 2.2;
    const raisedBottom = mix(bottom, contactY, Math.max(pose.lowerLipRaise * .72, pose.labiodentalContact * .92));
    const tongueY = mix(bottom - 3, top + opening * .46, pose.tongueRaise);
    const tongueX = cx + (pose.tongueForward - .5) * 18;
    const tongueWidth = width * mix(.38, .58, pose.tongueForward);
    const innerLeft = left + Math.max(5, width * (.06 + pose.lipPucker * .08));
    const innerRight = right - Math.max(5, width * (.06 + pose.lipPucker * .08));

    return {
      upperLip: `M${n(left)} ${n(top + 1)} Q${n(cx)} ${n(top - upperArch)} ${n(right)} ${n(top + 1)} Q${n(cx)} ${n(top + upperThickness)} ${n(left)} ${n(top + 1)}Z`,
      lowerLip: `M${n(left)} ${n(raisedBottom)} Q${n(cx)} ${n(raisedBottom + lowerArch)} ${n(right)} ${n(raisedBottom)} Q${n(cx)} ${n(raisedBottom - lowerThickness)} ${n(left)} ${n(raisedBottom)}Z`,
      interior: `M${n(innerLeft)} ${n(top + 2)} Q${n(cx)} ${n(top - 1)} ${n(innerRight)} ${n(top + 2)} Q${n(cx)} ${n(bottom)} ${n(innerLeft)} ${n(top + 2)}Z`,
      upperTeeth: `M${n(innerLeft + 4)} ${n(top + 3)} Q${n(cx)} ${n(top - 1)} ${n(innerRight - 4)} ${n(top + 3)} L${n(innerRight - 8)} ${n(teethBottom)} Q${n(cx)} ${n(teethBottom + 2)} ${n(innerLeft + 8)} ${n(teethBottom)}Z`,
      lowerTeeth: `M${n(innerLeft + 10)} ${n(bottom - 5)} Q${n(cx)} ${n(bottom - 8)} ${n(innerRight - 10)} ${n(bottom - 5)} L${n(innerRight - 7)} ${n(bottom - 1)} Q${n(cx)} ${n(bottom + 1)} ${n(innerLeft + 7)} ${n(bottom - 1)}Z`,
      tongue: `M${n(tongueX - tongueWidth / 2)} ${n(tongueY + 4)} Q${n(tongueX)} ${n(tongueY - 7 - pose.tongueRaise * 4)} ${n(tongueX + tongueWidth / 2)} ${n(tongueY + 4)} Q${n(tongueX)} ${n(tongueY + 12)} ${n(tongueX - tongueWidth / 2)} ${n(tongueY + 4)}Z`,
      jaw: `M74 ${n(145 + jawDrop * .25)} Q130 ${n(169 + jawDrop)} 186 ${n(145 + jawDrop * .25)}`,
      upperTeethOpacity: pose.upperTeethVisible,
      lowerTeethOpacity: pose.lowerTeethVisible,
      tongueOpacity: pose.tongueVisible,
      pressure,
    };
  }

  class SvgAnimeMouthRenderer extends globalThis.MouthRenderer {
    constructor(element, {transitionMs = 70} = {}) {
      super();
      this.element = element;
      this.transitionMs = transitionMs;
      this.activeAnimations = [];
      this.currentPose = null;
      this.reducedMotion = globalThis.matchMedia?.('(prefers-reduced-motion: reduce)').matches || false;
      this.element.innerHTML = `<svg class="mouth-rig" viewBox="0 0 260 210" role="img" aria-label="Articulatory mouth visualization">
        <path class="mouth-nose-guide" d="M119 29 Q130 20 141 29 L147 53 Q130 60 113 53Z"/>
        <g class="mouth-root">
          <path class="mouth-jaw"/>
          <path class="mouth-interior"/>
          <path class="mouth-tongue"/>
          <path class="mouth-lower-teeth"/>
          <path class="mouth-upper-teeth"/>
          <path class="mouth-upper-lip"/>
          <path class="mouth-lower-lip"/>
        </g>
        <g class="mouth-targets" aria-hidden="true">
          <path data-target="jaw"/><path data-target="interior"/><path data-target="tongue"/>
          <path data-target="lowerTeeth"/><path data-target="upperTeeth"/>
          <path data-target="upperLip"/><path data-target="lowerLip"/>
        </g>
      </svg>`;
      this.svg = this.element.querySelector('svg');
      this.layers = Object.fromEntries(['jaw', 'interior', 'tongue', 'lowerTeeth', 'upperTeeth', 'upperLip', 'lowerLip'].map(name => [name, this.element.querySelector(`.mouth-${name.replace(/[A-Z]/g, letter => `-${letter.toLowerCase()}`)}`)]));
      this.targets = Object.fromEntries(Object.keys(this.layers).map(name => [name, this.element.querySelector(`[data-target="${name}"]`)]));
      this.element.dataset.rendererMode = globalThis.anime ? 'anime' : 'snap';
    }
    cancelAnimations() { this.activeAnimations.forEach(animation => animation?.cancel?.()); this.activeAnimations = []; }
    setLayerPath(name, value, duration) {
      const layer = this.layers[name];
      const target = this.targets[name];
      target.setAttribute('d', value);
      if (!duration || !layer.getAttribute('d') || !globalThis.anime?.animate || !globalThis.anime?.svg?.morphTo) {
        layer.setAttribute('d', value);
        return;
      }
      this.activeAnimations.push(globalThis.anime.animate(layer, {d: globalThis.anime.svg.morphTo(target, .32), duration, ease: 'outCubic'}));
    }
    setPose(input, {immediate = false, continuous = false} = {}) {
      const pose = poseApi.createMouthPose(input);
      if (poseApi.mouthPoseEquals(this.currentPose, pose, continuous ? .002 : .0001)) return;
      const geometry = geometryForPose(pose);
      this.cancelAnimations();
      const duration = immediate || continuous || this.reducedMotion ? 0 : this.transitionMs;
      Object.keys(this.layers).forEach(name => this.setLayerPath(name, geometry[name], duration));
      this.layers.upperTeeth.style.opacity = geometry.upperTeethOpacity.toFixed(3);
      this.layers.lowerTeeth.style.opacity = geometry.lowerTeethOpacity.toFixed(3);
      this.layers.tongue.style.opacity = geometry.tongueOpacity.toFixed(3);
      this.layers.upperLip.style.filter = `saturate(${(1 + geometry.pressure * .25).toFixed(2)})`;
      this.layers.lowerLip.style.filter = `saturate(${(1 + geometry.pressure * .25).toFixed(2)})`;
      this.currentPose = pose;
    }
    reset(pose) { super.reset(pose); }
    destroy() { this.cancelAnimations(); this.element.replaceChildren(); }
  }
  SvgAnimeMouthRenderer.geometryForPose = geometryForPose;
  globalThis.SvgAnimeMouthRenderer = SvgAnimeMouthRenderer;
})();
