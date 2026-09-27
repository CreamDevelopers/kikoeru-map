import { api, errorMessage } from "./api.js";
import { h, icon, toast } from "./dom.js";
import { t } from "./i18n.js";
import { closeSheet, openSheet } from "./sheet.js";
import { Sequencer } from "./walk.js";

const L = window.L;

export async function listCourses(featured = false) {
  try {
    const data = await api(`/api/courses${featured ? "?featured=true" : ""}`);
    return data.items;
  } catch {
    return [];
  }
}

export function courseList(items, onPick) {
  return h(
    "ul",
    { class: "course-list" },
    items.map((c) =>
      h(
        "li",
        {},
        h(
          "button",
          { type: "button", onclick: () => onPick(c.slug) },
          icon("route"),
          h("span", {}, h("strong", {}, c.title), c.description ? h("br") : null, c.description ? h("span", { class: "small muted" }, c.description) : null),
        ),
      ),
    ),
  );
}

export async function showCourses(onPick) {
  const items = await listCourses(false);
  const body = items.length ? courseList(items, onPick) : h("p", { class: "muted" }, t("course.none"));
  openSheet(t("nav.courses"), body, { key: "courses" });
}

export async function playCourse(slug, { map, pins, onStop, stopOthers = null }) {
  let course;
  try {
    course = await api(`/api/courses/${encodeURIComponent(slug)}`);
  } catch (e) {
    toast(errorMessage(e.code));
    return null;
  }
  if (!course.items.length) {
    toast(t("course.empty"));
    return null;
  }
  stopOthers?.();
  closeSheet();
  const latlngs = course.items.map((it) => [it.sound.lat, it.sound.lng]);
  const accent = getComputedStyle(document.documentElement).getPropertyValue("--accent").trim();
  const route = L.polyline(latlngs, { color: accent, weight: 4, opacity: 0.85, dashArray: "8 6" }).addTo(map);
  const stops = L.layerGroup(
    course.items.map((it, i) =>
      L.marker([it.sound.lat, it.sound.lng], {
        icon: L.divIcon({ className: "cluster", html: `<span>${i + 1}</span>`, iconSize: [26, 26] }),
        title: `${i + 1}. ${it.sound.title}`,
        keyboard: false,
      }),
    ),
  ).addTo(map);
  map.fitBounds(route.getBounds().pad(0.2), { maxZoom: 14 });

  let index = 0;
  const noteEl = h("p", { class: "notice small" });
  const progressEl = h("p", { class: "small muted", "aria-live": "polite" });
  const extra = h("div", {}, h("p", {}, course.description), progressEl, noteEl);
  const seq = new Sequencer({
    map,
    pins,
    title: course.title,
    key: "course",
    extraControls: extra,
    provider: async () => {
      if (index >= course.items.length) return null;
      const it = course.items[index];
      index += 1;
      return it.sound;
    },
    onShow: (sound) => {
      const i = course.items.findIndex((it) => it.sound.id === sound.id);
      const it = course.items[i];
      progressEl.textContent = t("course.progress", { n: i + 1, total: course.items.length });
      noteEl.textContent = it?.note || "";
      noteEl.hidden = !it?.note;
    },
    onEnd: () => toast(t("course.finished")),
    onStop: () => {
      map.removeLayer(route);
      map.removeLayer(stops);
      onStop?.();
    },
  });
  await seq.start();
  return seq;
}
