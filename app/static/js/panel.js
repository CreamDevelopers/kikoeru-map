import { api, errorMessage } from "./api.js";
import { Track, countPlay, nowPlaying, sourceUrl } from "./audio.js";
import { buildCompare } from "./compare.js";
import { h, icon, toast } from "./dom.js";
import { formatDate, t } from "./i18n.js";
import { PlayerView } from "./player.js";
import { openReport } from "./report.js";
import { closeSheet, openSheet } from "./sheet.js";

export function soundDetails(s) {
  const dl = h("dl");
  const add = (label, value) => {
    if (value === null || value === undefined || value === "") return;
    dl.append(h("dt", {}, label), h("dd", {}, value));
  };
  add(t("field.place"), [s.pref_name, s.city_name].filter(Boolean).join(" ") || t("ui.unknown_place"));
  add(t("field.recorded_at"), formatDate(s.recorded_at));
  add(t("field.time_of_day"), s.time_of_day ? t(`tod.${s.time_of_day}`) : "");
  add(t("field.weather"), s.weather ? t(`weather.${s.weather}`) : "");
  add(t("field.season"), s.season ? t(`season.${s.season}`) : "");
  add(t("field.tags"), h("span", { class: "chips" }, (s.tags || []).map((tg) => h("span", { class: "chip" }, t(`tag.${tg}`)))));
  add(t("field.license"), t(`license.${s.license}`));
  if (s.precision && s.precision !== "exact") add(t("field.precision"), t(`post.precision.${s.precision}`));
  return dl;
}

export async function share(s) {
  const url = new URL(s.page_url, location.origin).toString();
  if (navigator.share) {
    try {
      await navigator.share({ title: s.title, url });
      return;
    } catch {}
  }
  try {
    await navigator.clipboard.writeText(url);
    toast(t("ui.copied"));
  } catch {
    toast(url);
  }
}

let current = null;

export function stopPanel() {
  if (!current) return;
  const c = current;
  current = null;
  c.compare?.stop();
  c.view.destroy();
  c.track.pause(0.5).then(() => c.track.destroy());
  nowPlaying.clear(c.track);
}

export async function showSound(idOrSound, { map, pins, autoplay = true, stopOthers } = {}) {
  let s = idOrSound;
  if (typeof idOrSound === "string") {
    try {
      s = await api(`/api/sounds/${encodeURIComponent(idOrSound)}`);
    } catch (e) {
      toast(errorMessage(e.code));
      return;
    }
  }
  stopOthers?.();
  stopPanel();

  const playerEl = h("div", { class: "player" });
  const root = h(
    "div",
    { class: "sound-meta" },
    playerEl,
    s.comment ? h("p", { class: "sound-comment" }, s.comment) : null,
    soundDetails(s),
  );
  const actions = h(
    "div",
    { class: "actions" },
    h("a", { class: "btn", href: s.page_url }, icon("open_in_new"), t("sound.page")),
    h("button", { type: "button", onclick: () => share(s) }, icon("share"), t("sound.share")),
    h("button", { type: "button", class: "danger", onclick: () => openReport(s.id) }, icon("flag"), t("report.button")),
  );
  root.append(actions);

  const track = new Track(sourceUrl(s));
  const view = new PlayerView(playerEl);
  const state = { track, view, sound: s, compare: null };
  current = state;
  if (s.nearby_count > 0) {
    state.compare = buildCompare(s, {
      onBeforePlay: () => {
        if (!track.paused) track.pause(0.4);
      },
    });
    root.append(state.compare.root);
  }

  openSheet(s.title, root, {
    key: "sound",
    onClose: () => {
      if (current === state) stopPanel();
      pins?.setPlaying(null);
    },
  });
  view.attach(track, s);
  nowPlaying.set(track, s);
  pins?.setPlaying(s.id);
  if (map && s.lat) {
    const target = [s.lat, s.lng];
    if (!map.getBounds().pad(-0.2).contains(target)) map.panTo(target, { animate: true });
  }
  let counted = false;
  track.addEventListener("play", () => {
    if (!counted) {
      counted = true;
      countPlay(s.id);
    }
  });
  track.addEventListener("ended", () => pins?.setPlaying(null));
  if (autoplay) {
    try {
      await track.play();
    } catch {}
  }
  return state;
}

export { closeSheet };
