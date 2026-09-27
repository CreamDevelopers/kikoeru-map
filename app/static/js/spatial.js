import { api } from "./api.js";
import { Track, audioContext, countPlay, sourceUrl } from "./audio.js";
import { debounce, h, icon, toast } from "./dom.js";
import { formatDistance, t } from "./i18n.js";
import { closeSheet, openSheet } from "./sheet.js";

const L = window.L;
const MAX_SOURCES = 8;
const METERS_PER_DEG_LAT = 111320;
// 0=北から時計回りの 8 方位（x=東, y=北）
const DIRS = Array.from({ length: 8 }, (_, i) => {
  const a = (i * Math.PI) / 4;
  return [Math.sin(a), Math.cos(a)];
});

export class Spatial {
  constructor({ map, pins, onStop }) {
    this.map = map;
    this.pins = pins;
    this.onStop = onStop;
    this.sources = new Map();
    this.radius = 1500;
    this.stopped = false;
    this.refresh = debounce(() => this.load(), 350);
    this.onMove = () => this.updatePositions();
  }

  start() {
    const ac = audioContext();
    this.master = ac.createGain();
    this.master.gain.value = 0.9;
    this.master.connect(ac.destination);
    this.listEl = h("ul", { class: "compare-list", "aria-live": "polite" });
    this.radiusSel = h(
      "select",
      { "aria-label": t("spatial.radius") },
      [500, 1500, 5000].map((r) => h("option", { value: String(r), selected: r === this.radius }, formatDistance(r))),
    );
    this.radiusSel.addEventListener("change", () => {
      this.radius = Number(this.radiusSel.value);
      this.load();
    });
    const root = h(
      "div",
      {},
      h("p", { class: "notice" }, icon("headphones"), " ", t("spatial.headphones")),
      h("p", { class: "small muted" }, t("spatial.hint")),
      h("label", { class: "field" }, h("span", {}, t("spatial.radius")), this.radiusSel),
      this.listEl,
      h("div", { class: "actions" }, h("button", { type: "button", class: "danger", onclick: () => this.stop() }, icon("stop"), t("spatial.stop"))),
    );
    openSheet(t("mode.spatial"), root, { key: "spatial", onClose: () => this.stop(true) });
    document.getElementById("listener-mark").hidden = false;
    this.pins.hide();
    this.layer = L.layerGroup().addTo(this.map);
    this.map.on("move", this.onMove);
    this.map.on("moveend", this.refresh);
    if (this.map.getZoom() < 14) this.map.setZoom(15);
    this.setupListener();
    this.load();
  }

  setupListener() {
    const ac = audioContext();
    const l = ac.listener;
    // 聞き手は原点で北（-Z）を向く。x=東, z=南
    if (l.forwardX) {
      l.positionX.value = 0;
      l.positionY.value = 0;
      l.positionZ.value = 0;
      l.forwardX.value = 0;
      l.forwardY.value = 0;
      l.forwardZ.value = -1;
      l.upX.value = 0;
      l.upY.value = 1;
      l.upZ.value = 0;
    } else {
      l.setPosition(0, 0, 0);
      l.setOrientation(0, 0, -1, 0, 1, 0);
    }
  }

  async load() {
    if (this.stopped) return;
    const c = this.map.getCenter();
    let items = [];
    try {
      const data = await api(`/api/nearby?lat=${c.lat.toFixed(6)}&lng=${c.lng.toFixed(6)}&radius=${this.radius}&limit=${MAX_SOURCES}`);
      items = data.items;
    } catch {
      toast(t("error.generic"));
      return;
    }
    if (this.stopped) return;
    const keep = new Set(items.map((s) => s.id));
    for (const [id, src] of this.sources) {
      if (!keep.has(id)) this.removeSource(id, src);
    }
    for (const s of items) {
      if (!this.sources.has(s.id)) this.addSource(s);
    }
    this.updatePositions();
    this.renderList();
    if (!items.length) this.listEl.replaceChildren(h("li", { class: "muted" }, t("spatial.none")));
  }

  addSource(sound) {
    const ac = audioContext();
    const panner = ac.createPanner();
    panner.panningModel = "HRTF";
    panner.distanceModel = "inverse";
    panner.refDistance = 40;
    panner.maxDistance = 20000;
    panner.rolloffFactor = 1.1;
    if (sound.direction !== null && sound.direction !== undefined) {
      // 録音した向きの少し先から、録音地点へ向かって鳴る指向性の音源にする
      panner.coneInnerAngle = 200;
      panner.coneOuterAngle = 320;
      panner.coneOuterGain = 0.45;
    }
    panner.connect(this.master);
    const track = new Track(sourceUrl(sound), { loop: true, output: panner, analyser: true });
    const marker = L.marker([sound.lat, sound.lng], {
      icon: L.divIcon({ className: "spatial-source", html: "", iconSize: [12, 12] }),
      title: sound.title,
      keyboard: false,
    }).addTo(this.layer);
    const src = { sound, track, panner, marker };
    this.sources.set(sound.id, src);
    this.updateSource(src);
    track.el.addEventListener(
      "loadedmetadata",
      () => {
        if (track.duration) track.seek(Math.random() * track.duration * 0.8);
      },
      { once: true },
    );
    setTimeout(() => {
      if (this.stopped || !this.sources.has(sound.id)) return;
      track.play(2).then(() => countPlay(sound.id)).catch(() => {});
    }, Math.random() * 600);
  }

  removeSource(id, src) {
    this.sources.delete(id);
    this.layer.removeLayer(src.marker);
    src.track.pause(1.2).then(() => {
      src.track.destroy();
      src.panner.disconnect();
    });
  }

  updateSource(src) {
    const c = this.map.getCenter();
    const s = src.sound;
    let x = (s.lng - c.lng) * METERS_PER_DEG_LAT * Math.cos((c.lat * Math.PI) / 180);
    let north = (s.lat - c.lat) * METERS_PER_DEG_LAT;
    const p = src.panner;
    const ac = audioContext();
    const now = ac.currentTime;
    let orient = null;
    if (s.direction !== null && s.direction !== undefined) {
      const [dx, dy] = DIRS[s.direction];
      x += dx * 25;
      north += dy * 25;
      orient = [-dx, 0, dy];
    }
    if (p.positionX) {
      p.positionX.setTargetAtTime(x, now, 0.08);
      p.positionY.setTargetAtTime(0, now, 0.08);
      p.positionZ.setTargetAtTime(-north, now, 0.08);
      if (orient) {
        p.orientationX.value = orient[0];
        p.orientationY.value = orient[1];
        p.orientationZ.value = orient[2];
      }
    } else {
      p.setPosition(x, 0, -north);
      if (orient) p.setOrientation(...orient);
    }
    src.distance = Math.hypot(x, north);
    const el = src.marker.getElement();
    if (el) el.style.setProperty("--gain", String(Math.max(0, 1 - src.distance / this.radius)));
  }

  updatePositions() {
    for (const src of this.sources.values()) this.updateSource(src);
  }

  renderList() {
    const c = this.map.getCenter();
    const items = [...this.sources.values()].sort((a, b) => a.distance - b.distance);
    this.listEl.replaceChildren(
      ...items.map((src) => {
        const s = src.sound;
        const bearing = (Math.atan2(s.lng - c.lng, s.lat - c.lat) * 180) / Math.PI;
        const dir = Math.round(((bearing + 360) % 360) / 45) % 8;
        return h(
          "li",
          {},
          h("span", { class: "chip" }, t(`dir.${dir}`)),
          h("span", {}, s.title, h("br"), h("span", { class: "small muted" }, formatDistance(src.distance))),
          h("a", { href: s.page_url, class: "small" }, t("sound.page")),
          h("span"),
        );
      }),
    );
  }

  stop(fromClose = false) {
    if (this.stopped) return;
    this.stopped = true;
    this.map.off("move", this.onMove);
    this.map.off("moveend", this.refresh);
    for (const [id, src] of this.sources) this.removeSource(id, src);
    if (this.layer) this.map.removeLayer(this.layer);
    setTimeout(() => this.master?.disconnect(), 1500);
    document.getElementById("listener-mark").hidden = true;
    this.pins.show();
    if (!fromClose) closeSheet();
    this.onStop?.();
  }
}
