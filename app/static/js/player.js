import { formatDuration, t } from "./i18n.js";
import { h, icon } from "./dom.js";

const peaksCache = new Map();

export async function loadPeaks(url) {
  if (!url) return null;
  if (peaksCache.has(url)) return peaksCache.get(url);
  const p = fetch(url)
    .then((r) => (r.ok ? r.json() : null))
    .then((d) => d?.peaks || null)
    .catch(() => null);
  peaksCache.set(url, p);
  return p;
}

function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

export function drawWave(canvas, peaks, progress = 0, { start = 0, end = 1 } = {}) {
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth;
  const hgt = canvas.clientHeight;
  if (!w || !hgt) return;
  if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(hgt * dpr)) {
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(hgt * dpr);
  }
  const g = canvas.getContext("2d");
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  g.clearRect(0, 0, w, hgt);
  const accent = cssVar("--accent");
  const muted = cssVar("--border");
  const data = peaks && peaks.length ? peaks : new Array(100).fill(0.15);
  const n = data.length;
  const barW = w / n;
  const mid = hgt / 2;
  for (let i = 0; i < n; i++) {
    const x = i * barW;
    const frac = i / n;
    const amp = Math.max(0.03, data[i]) * (hgt / 2 - 2);
    const inRange = frac >= start && frac <= end;
    g.fillStyle = frac <= progress && inRange ? accent : muted;
    if (!inRange) g.globalAlpha = 0.35;
    g.fillRect(x, mid - amp, Math.max(1, barW - 0.5), amp * 2);
    g.globalAlpha = 1;
  }
}

export class PlayerView {
  constructor(container) {
    this.container = container;
    this.playBtn = h("button", { type: "button", class: "play", "aria-label": t("player.play") }, icon("play_arrow"));
    this.canvas = h("canvas", { "aria-hidden": "true" });
    this.wave = h("div", {
      class: "wave",
      role: "slider",
      tabindex: "0",
      "aria-label": t("player.seek"),
      "aria-valuemin": "0",
      "aria-valuemax": "0",
      "aria-valuenow": "0",
    });
    this.wave.append(this.canvas);
    this.cur = h("span", {}, "0:00");
    this.dur = h("span", {}, "0:00");
    container.append(this.playBtn, this.wave, h("div", { class: "times" }, this.cur, this.dur));
    this.track = null;
    this.peaks = null;
    this.raf = 0;

    this.playBtn.addEventListener("click", () => this.track?.toggle());
    const seekAt = (clientX) => {
      if (!this.track?.duration) return;
      const r = this.wave.getBoundingClientRect();
      const frac = Math.min(1, Math.max(0, (clientX - r.left) / r.width));
      this.track.seek(frac * this.track.duration);
      this.render();
    };
    let dragging = false;
    this.wave.addEventListener("pointerdown", (e) => {
      dragging = true;
      this.wave.setPointerCapture(e.pointerId);
      seekAt(e.clientX);
    });
    this.wave.addEventListener("pointermove", (e) => dragging && seekAt(e.clientX));
    this.wave.addEventListener("pointerup", () => (dragging = false));
    this.wave.addEventListener("pointercancel", () => (dragging = false));
    this.wave.addEventListener("keydown", (e) => {
      if (!this.track) return;
      if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
        e.preventDefault();
        e.stopPropagation();
        this.track.seek(this.track.currentTime + (e.key === "ArrowLeft" ? -5 : 5));
      } else if (e.key === "Home") {
        e.preventDefault();
        this.track.seek(0);
      }
    });
    this.ro = new ResizeObserver(() => this.render());
    this.ro.observe(this.wave);
  }

  async attach(track, sound) {
    this.detach();
    this.track = track;
    const onState = () => this.updateButton();
    this.handlers = ["play", "pause", "ended", "loadedmetadata"].map((ev) => {
      track.addEventListener(ev, onState);
      return [ev, onState];
    });
    this.updateButton();
    this.loop();
    this.peaks = await loadPeaks(sound.peaks_url);
    this.render();
  }

  detach() {
    cancelAnimationFrame(this.raf);
    if (this.track && this.handlers) {
      for (const [ev, fn] of this.handlers) this.track.removeEventListener(ev, fn);
    }
    this.track = null;
  }

  destroy() {
    this.detach();
    this.ro.disconnect();
  }

  updateButton() {
    const playing = this.track && !this.track.paused;
    this.playBtn.replaceChildren(icon(playing ? "pause" : "play_arrow"));
    this.playBtn.setAttribute("aria-label", t(playing ? "player.pause" : "player.play"));
  }

  loop() {
    this.render();
    this.raf = requestAnimationFrame(() => this.loop());
  }

  render() {
    const tr = this.track;
    const d = tr?.duration || 0;
    const c = tr?.currentTime || 0;
    drawWave(this.canvas, this.peaks, d ? c / d : 0);
    this.cur.textContent = formatDuration(c);
    this.dur.textContent = formatDuration(d);
    this.wave.setAttribute("aria-valuemax", String(Math.round(d)));
    this.wave.setAttribute("aria-valuenow", String(Math.round(c)));
    this.wave.setAttribute("aria-valuetext", `${formatDuration(c)} / ${formatDuration(d)}`);
  }
}
