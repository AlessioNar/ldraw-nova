// The editor's Player mode: the same WebAssembly player as player.html (ldraw-player), on a canvas of its own inside
// editor.html. It plays the model that is on screen (the draft, or the version being viewed) and steps through it.
// Plain script; the player's ES module is imported on first use, so the editor does not pay for it until needed.
/* global ViewerCommon */
var EditorPlayer = (function () {
  async function create(canvas, background, backend) {
    const module = await import('./player-vendor/ldraw_player.js');
    await module.default();
    const player = await module.Player.create(canvas, {
      libraryUrl: '/ldraw/', resolverUrl: '/ldraw-id/', background, backend: backend || undefined,
    });
    return new Instance(player);
  }

  class Instance {
    constructor(player) {
      this.player = player;
      this.steps = new Float32Array();
      this.duration = 0;
      this.listeners = [];
      this.source = null;                       // what is loaded: a key the caller compares to know when to reload
      player.onChange(() => this.listeners.forEach(f => f()));
    }

    onChange(f) { this.listeners.push(f); }

    // Load model text (an MPD). Resolves to { parts, steps, missing }, or null when another load superseded this one.
    async load(text, source, progress) {
      const url = URL.createObjectURL(new Blob([text], { type: 'text/plain' }));
      try {
        const result = await this.player.load(url, progress);
        if (!result) return null;
        this.steps = this.player.stepTimes();
        this.duration = this.player.duration;
        this.source = source;
        this.listeners.forEach(f => f());
        return result;
      } finally { URL.revokeObjectURL(url); }
    }

    get stepCount() { return this.steps.length; }
    get time() { return this.player.time; }
    get playing() { return this.player.playing; }
    get step() { return this.steps.length ? this.player.stepAt(this.player.time) + 1 : 0; }

    // Show the model as it is when step n (1-based) is complete: just before step n + 1 starts.
    showStep(n) {
      if (!this.steps.length) return;
      const k = Math.min(Math.max(1, n), this.steps.length);
      const end = k < this.steps.length ? this.steps[k] - 0.001 : this.duration;
      this.player.pause();
      this.player.seek(Math.max(this.steps[k - 1], end));
    }
    next() { const t = this.player.time, s = this.steps.find(x => x > t + 1e-4); this.player.pause(); this.player.seek(s === undefined ? this.duration : s); }
    previous() {
      const t = this.player.time, k = this.steps.length ? this.player.stepAt(t) : 0;
      this.player.pause();
      this.player.seek(this.steps.length ? this.steps[(t - this.steps[k] > 1e-3 || k === 0) ? k : k - 1] : 0);
    }
    seekFraction(f) { this.player.pause(); this.player.seek(Math.min(1, Math.max(0, f)) * this.duration); }
    toggle() {
      if (!this.duration) return;
      if (this.player.playing) this.player.pause();
      else { if (this.player.time >= this.duration) this.player.seek(0); this.player.play(); }
    }
    pause() { this.player.pause(); }
    resetView() { this.player.resetView(); }
  }

  return { create };
})();
