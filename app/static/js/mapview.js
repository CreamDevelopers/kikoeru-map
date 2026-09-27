import { t } from "./i18n.js";
import { toast } from "./dom.js";

const L = window.L;

const GSI_ATTR = `<a href="https://maps.gsi.go.jp/development/ichiran.html" target="_blank" rel="noopener">${t("map.gsi")}</a>`;
const LAYERS = {
  pale: { url: "https://cyberjapandata.gsi.go.jp/xyz/pale/{z}/{x}/{y}.png", invert: true },
  std: { url: "https://cyberjapandata.gsi.go.jp/xyz/std/{z}/{x}/{y}.png", invert: true },
  photo: { url: "https://cyberjapandata.gsi.go.jp/xyz/seamlessphoto/{z}/{x}/{y}.jpg", invert: false },
};
export const JAPAN_BOUNDS = L.latLngBounds([17.5, 118], [48.5, 158]);
const DEFAULT_VIEW = { lat: 36.2, lng: 138.2, z: 6 };

function readUrlView() {
  const p = new URLSearchParams(location.search);
  const lat = parseFloat(p.get("lat"));
  const lng = parseFloat(p.get("lng"));
  const z = parseInt(p.get("z"), 10);
  if (Number.isFinite(lat) && Number.isFinite(lng) && JAPAN_BOUNDS.contains([lat, lng])) {
    return { lat, lng, z: Number.isFinite(z) ? Math.min(18, Math.max(5, z)) : 14 };
  }
  return null;
}

export function tileLayer(name) {
  const def = LAYERS[name] || LAYERS.pale;
  return L.tileLayer(def.url, {
    attribution: GSI_ATTR,
    maxZoom: 18,
    maxNativeZoom: 18,
    minZoom: 5,
    crossOrigin: true,
    bounds: JAPAN_BOUNDS,
  });
}

export function createMap(el, { interactiveUrl = true } = {}) {
  const view = readUrlView() || DEFAULT_VIEW;
  const map = L.map(el, {
    center: [view.lat, view.lng],
    zoom: view.z,
    minZoom: 5,
    maxZoom: 18,
    maxBounds: JAPAN_BOUNDS,
    maxBoundsViscosity: 0.9,
    zoomControl: true,
    attributionControl: true,
    worldCopyJump: false,
    keyboard: true,
  });
  map.attributionControl.setPrefix(false);
  map.zoomControl.setPosition("bottomright");

  let current = null;
  let currentName = null;
  function setLayer(name) {
    if (!LAYERS[name]) name = "pale";
    if (current) map.removeLayer(current);
    current = tileLayer(name).addTo(map);
    currentName = name;
    map.getPane("tilePane").classList.toggle("is-invertible", LAYERS[name].invert);
    try {
      localStorage.setItem("layer", name);
    } catch {}
    document.querySelectorAll("#layer-menu [data-layer]").forEach((b) => {
      b.setAttribute("aria-checked", String(b.dataset.layer === name));
    });
  }
  let saved = "pale";
  try {
    saved = localStorage.getItem("layer") || "pale";
  } catch {}
  setLayer(saved);

  if (interactiveUrl) {
    map.on("moveend", () => {
      const c = map.getCenter();
      const p = new URLSearchParams(location.search);
      p.set("lat", c.lat.toFixed(5));
      p.set("lng", c.lng.toFixed(5));
      p.set("z", String(map.getZoom()));
      history.replaceState(history.state, "", `${location.pathname}?${p.toString()}`);
    });
  }
  return { map, setLayer, get layerName() { return currentName; } };
}

// モーダルダイアログより前面に出すため、開いているダイアログの中に表示する
function showLocating() {
  const el = document.createElement("div");
  el.className = "locating";
  el.setAttribute("role", "status");
  el.setAttribute("aria-live", "polite");
  const ic = document.createElement("span");
  ic.className = "material-symbols-outlined";
  ic.setAttribute("aria-hidden", "true");
  ic.textContent = "my_location";
  el.append(ic, document.createTextNode(t("map.locating")));
  (document.querySelector("dialog[open]") || document.body).append(el);
  return () => el.remove();
}

const hereLayers = new WeakMap();

function showHere(map, latlng, accuracy) {
  let here = hereLayers.get(map);
  if (!here) {
    const accent = getComputedStyle(document.documentElement).getPropertyValue("--accent").trim();
    here = {
      circle: L.circle(latlng, {
        radius: accuracy,
        color: accent,
        weight: 1,
        fillColor: accent,
        fillOpacity: 0.12,
        interactive: false,
      }),
      marker: L.marker(latlng, {
        icon: L.divIcon({ className: "here-pin", html: "", iconSize: [18, 18] }),
        title: t("map.you_are_here"),
        alt: t("map.you_are_here"),
        keyboard: false,
        interactive: false,
        zIndexOffset: 1000,
      }),
    };
    here.circle.addTo(map);
    here.marker.addTo(map);
    hereLayers.set(map, here);
  }
  here.circle.setLatLng(latlng).setRadius(Math.min(accuracy, 2000));
  here.marker.setLatLng(latlng);
}

export function locate(map, zoom = 15) {
  if (!("geolocation" in navigator)) {
    toast(t("error.geolocation"));
    return Promise.resolve(null);
  }
  const hide = showLocating();
  return new Promise((resolve) => {
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        hide();
        const ll = [pos.coords.latitude, pos.coords.longitude];
        if (!JAPAN_BOUNDS.contains(ll)) {
          toast(t("error.outside_japan"));
          resolve(null);
          return;
        }
        showHere(map, ll, pos.coords.accuracy || 0);
        map.flyTo(ll, Math.max(map.getZoom(), zoom), { duration: 0.8 });
        resolve(ll);
      },
      () => {
        hide();
        toast(t("error.geolocation"));
        resolve(null);
      },
      { enableHighAccuracy: true, timeout: 10000, maximumAge: 60000 },
    );
  });
}

export function setupLayerMenu(ctl) {
  const btn = document.getElementById("layer-btn");
  const menu = document.getElementById("layer-menu");
  if (!btn || !menu) return;
  const close = () => {
    menu.hidden = true;
    btn.setAttribute("aria-expanded", "false");
  };
  btn.addEventListener("click", () => {
    menu.hidden = !menu.hidden;
    btn.setAttribute("aria-expanded", String(!menu.hidden));
    if (!menu.hidden) menu.querySelector('[aria-checked="true"]')?.focus();
  });
  menu.addEventListener("click", (e) => {
    const b = e.target.closest("[data-layer]");
    if (!b) return;
    ctl.setLayer(b.dataset.layer);
    close();
    btn.focus();
  });
  menu.addEventListener("keydown", (e) => {
    const items = [...menu.querySelectorAll("[data-layer]")];
    const i = items.indexOf(document.activeElement);
    if (e.key === "ArrowDown") items[(i + 1) % items.length].focus();
    else if (e.key === "ArrowUp") items[(i - 1 + items.length) % items.length].focus();
    else if (e.key === "Escape") {
      close();
      btn.focus();
    } else return;
    e.preventDefault();
  });
  document.addEventListener("click", (e) => {
    if (!menu.hidden && !menu.contains(e.target) && !btn.contains(e.target)) close();
  });
}

export function setupHeat(map) {
  const btn = document.getElementById("heat-btn");
  let layer = null;
  let on = false;
  btn?.addEventListener("click", async () => {
    on = !on;
    btn.setAttribute("aria-pressed", String(on));
    if (!on) {
      if (layer) map.removeLayer(layer);
      return;
    }
    try {
      const res = await fetch("/api/heat");
      const data = await res.json();
      const max = Math.max(1, ...data.p.map((p) => p[2]));
      if (layer) map.removeLayer(layer);
      layer = L.heatLayer(
        data.p.map((p) => [p[0], p[1], Math.min(1, 0.2 + p[2] / max)]),
        { radius: 22, blur: 18, maxZoom: 12, minOpacity: 0.25 },
      );
      if (on) layer.addTo(map);
    } catch {
      toast(t("error.generic"));
    }
  });
  return {
    hide() {
      if (layer && on) map.removeLayer(layer);
    },
    show() {
      if (layer && on) layer.addTo(map);
    },
  };
}
