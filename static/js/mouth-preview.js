/* Lightweight SVG mouth renderer; it intentionally contains no Teleo artwork. */
window.MouthPreview = class MouthPreview {
  constructor(element) { this.element = element; this.shape = 'X'; this.intensity = .5; this.useIntensity = true; this.draw(); }
  setShape(shape) { this.shape = /^[A-HX]$/.test(shape) ? shape : 'X'; this.draw(); }
  setIntensity(value) { this.intensity = Math.max(0, Math.min(1, Number(value) || 0)); this.draw(); }
  setUseIntensity(value) { this.useIntensity = value; this.draw(); }
  setTransitionProgress(value) { this.element.style.setProperty('--mouth-transition', Math.max(0, Math.min(1, value))); }
  draw() {
    const open = {A: 3, B: 12, C: 24, D: 42, E: 28, F: 11, G: 15, H: 20, X: 5}[this.shape];
    const scaled = Math.max(3, open * (this.useIntensity ? .55 + this.intensity * .45 : 1));
    const rounded = this.shape === 'E' || this.shape === 'F';
    const teeth = ['B', 'G'].includes(this.shape); const tongue = this.shape === 'H';
    this.element.innerHTML = `<svg viewBox="0 0 220 130" role="img" aria-label="Mouth shape ${this.shape}"><path d="M35 65 Q110 ${65 - scaled} 185 65 Q110 ${65 + scaled} 35 65Z" fill="#241c2a" stroke="#f5b879" stroke-width="6" stroke-linejoin="round" ${rounded ? '' : 'stroke-linecap="round"'}/>${teeth ? '<path d="M63 57 Q110 48 157 57 L151 65 Q110 61 69 65Z" fill="#fff5dc"/>' : ''}${tongue ? '<path d="M75 73 Q110 57 150 73 Q110 87 75 73Z" fill="#ff8fc8"/>' : ''}<text x="110" y="116" text-anchor="middle" fill="#eff2f8" font-family="system-ui" font-size="16">${this.shape} · ${this.useIntensity ? Math.round(this.intensity * 100) + '%' : 'shape only'}</text></svg>`;
  }
};
