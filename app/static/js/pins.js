import { t } from "./i18n.js";
import { debounce } from "./dom.js";

const L = window.L;
export const TAGS = ["nature", "city", "transit", "shop", "water", "festival", "other"];

export class PinLayer {
  constructor(map, { onSelect }) {
    this.map = map;
    this.onSelect = onSelect;
    this.group = L.layerGroup().addTo(map);
    this.points = new Map();
    this.index = null;
    this.controller = null;
    this.visible = true;
    this.playingId = null;
    this.newIds = new Set();
    this.markerById = new Map();
    this.debouncedLoad = debounce(() => this.load(), 300);
    map.on("moveend", () => {
      this.render();
      this.debouncedLoad();
    });
    this.load();
  }

  async load() {
    if (!this.visible) return;
    this.controller?.abort();
    const controller = new AbortController();
    this.controller = controller;
    const b = this.map.getBounds();
    const bbox = [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()].map((v) => v.toFixed(4)).join(",");
    const z = this.map.getZoom();
    try {
      const res = await fetch(`/api/pins?z=${z}&bbox=${bbox}`, { signal: controller.signal, cache: "no-cache" });
      if (!res.ok) return;
      const data = await res.json();
      if (controller.signal.aborted) return;
      this.setPoints(data.p);
    } catch (e) {
      if (e.name !== "AbortError") console.warn("pins load failed", e);
    }
  }

  setPoints(rows) {
    this.points.clear();
    for (const [id, lat, lng, tag, flags] of rows) {
      this.points.set(id, {
        type: "Feature",
        properties: { id, tag: TAGS[tag] || "other", overlap: (flags & 1) === 1 },
        geometry: { type: "Point", coordinates: [lng, lat] },
      });
    }
    this.rebuild();
  }

  addPoint({ id, lat, lng, tag }) {
    this.points.set(id, {
      type: "Feature",
      properties: { id, tag: TAGS[tag] || "other", overlap: false },
      geometry: { type: "Point", coordinates: [lng, lat] },
    });
    this.newIds.add(id);
    setTimeout(() => this.newIds.delete(id), 5000);
    this.rebuild();
  }

  rebuild() {
    this.index = new window.Supercluster({ radius: 56, maxZoom: 17, minPoints: 3 });
    this.index.load([...this.points.values()]);
    this.render();
  }

  render() {
    if (!this.index || !this.visible) return;
    const b = this.map.getBounds().pad(0.2);
    const z = Math.round(this.map.getZoom());
    const clusters = this.index.getClusters([b.getWest(), b.getSouth(), b.getEast(), b.getNorth()], z);
    this.group.clearLayers();
    this.markerById.clear();
    for (const c of clusters) {
      const [lng, lat] = c.geometry.coordinates;
      if (c.properties.cluster) {
        const n = c.properties.point_count;
        const size = n < 10 ? 34 : n < 100 ? 42 : n < 1000 ? 50 : 58;
        const label = c.properties.point_count_abbreviated;
        const m = L.marker([lat, lng], {
          icon: L.divIcon({ html: `<span>${label}</span>`, className: "cluster", iconSize: [size, size] }),
          keyboard: true,
          title: t("map.cluster", { n }),
        });
        m.on("click", () => {
          const zoom = Math.min(this.index.getClusterExpansionZoom(c.properties.cluster_id), 18);
          this.map.flyTo([lat, lng], zoom, { duration: 0.5 });
        });
        this.group.addLayer(m);
      } else {
        const p = c.properties;
        const cls = ["pin", `tag-${p.tag}`];
        if (p.overlap) cls.push("is-overlap");
        if (p.id === this.playingId) cls.push("is-playing");
        if (this.newIds.has(p.id)) cls.push("is-new");
        const m = L.marker([lat, lng], {
          icon: L.divIcon({ html: "", className: cls.join(" "), iconSize: [18, 18] }),
          keyboard: true,
          title: `${t("tag." + p.tag)}${p.overlap ? " / " + t("map.overlap") : ""}`,
          riseOnHover: true,
        });
        m.on("click", () => this.onSelect(p.id, [lat, lng]));
        this.group.addLayer(m);
        this.markerById.set(p.id, m);
      }
    }
  }

  setPlaying(id) {
    this.playingId = id;
    this.render();
  }

  setLevel(level) {
    if (!this.playingId) return;
    const el = this.markerById.get(this.playingId)?.getElement();
    if (el) el.style.setProperty("--level", level.toFixed(3));
  }

  hide() {
    this.visible = false;
    this.controller?.abort();
    this.group.clearLayers();
  }

  show() {
    this.visible = true;
    this.render();
    this.load();
  }
}
