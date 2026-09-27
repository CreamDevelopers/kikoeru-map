import { api } from "./api.js";
import { Track, countPlay, nowPlaying, sourceUrl } from "./audio.js";
import { h, icon, reducedMotion, toast } from "./dom.js";
import { t } from "./i18n.js";
import { soundDetails } from "./panel.js";
import { PlayerView } from "./player.js";
import { closeSheet, openSheet, setSheetTitle } from "./sheet.js";

const CROSSFADE = 3;

export function currentTimeOfDay(d = new Date()) {
  const hr = d.getHours();
  if (hr >= 4 && hr < 6) return "early_morning";
  if (hr >= 6 && hr < 10) return "morning";
  if (hr >= 10 && hr < 16) return "noon";
  if (hr >= 16 && hr < 19) return "evening";
  if (hr >= 19 && hr < 23) return "night";
  return "late_night";
}

export class Sequencer {
  constructor({ map, pins, provider, title, key, extraControls = null, onEnd = null, onStop = null, onShow = null }) {
    this.onShow = onShow;
    this.map = map;
    this.pins = pins;
    this.provider = provider;
    this.title = title;
    this.key = key;
    this.extraControls = extraControls;
    this.onEnd = onEnd;
    this.onStop = onStop;
    this.history = [];
    this.current = null;
    this.next = null;
    this.fading = false;
    this.stopped = false;
    this.sleepTimer = 0;
    this.sleepEnd = 0;
    this.prefetching = null;
  }

  buildUi() {
    this.titleEl = h("h3", { class: "walk-title" });
    this.placeEl = h("p", { class: "small muted" });
    const playerEl = h("div", { class: "player" });
    this.view = new PlayerView(playerEl);
    this.detailsEl = h("div", { class: "sound-meta walk-controls-extra" });

    const nextBtn = h("button", { type: "button", onclick: () => this.skip() }, icon("skip_next"), t("walk.next"));
    const immersiveBtn = h("button", { type: "button", onclick: () => this.setImmersive(true) }, icon("fullscreen"), t("walk.immersive"));
    const stopBtn = h("button", { type: "button", class: "danger", onclick: () => this.stop() }, icon("stop"), t("walk.stop"));
    this.sleepSelect = h(
      "select",
      { "aria-label": t("walk.sleep") },
      h("option", { value: "0" }, t("walk.sleep_off")),
      [15, 30, 60].map((m) => h("option", { value: String(m) }, t("walk.sleep_min", { n: m }))),
    );
    this.sleepSelect.addEventListener("change", () => this.setSleep(Number(this.sleepSelect.value)));
    this.sleepInfo = h("span", { class: "small muted", "aria-live": "polite" });

    const exitImmersive = h(
      "button",
      { type: "button", class: "btn-ghost", onclick: () => this.setImmersive(false) },
      icon("fullscreen_exit"),
      t("walk.exit_immersive"),
    );
    this.immersiveExit = exitImmersive;
    exitImmersive.hidden = true;

    const root = h(
      "div",
      { class: "walk immersive-card" },
      this.titleEl,
      this.placeEl,
      playerEl,
      h("div", { class: "actions" }, nextBtn, exitImmersive),
      h(
        "div",
        { class: "walk-controls-extra" },
        h("div", { class: "actions" }, immersiveBtn, stopBtn),
        h("div", { class: "row", style: null }, h("label", { class: "field grow" }, h("span", {}, t("walk.sleep")), this.sleepSelect), this.sleepInfo),
        this.extraControls,
        this.detailsEl,
      ),
    );
    return root;
  }

  async start() {
    this.stopped = false;
    // 音が見つからないときにパネルが一瞬表示されないよう、先に最初の音を取得する
    const first = await this.fetchNext();
    if (this.stopped) {
      first?.track.destroy();
      return;
    }
    if (!first) {
      toast(t("walk.no_sound"));
      this.stopped = true;
      this.onStop?.();
      return;
    }
    this.next = first;
    openSheet(this.title, this.buildUi(), { key: this.key, onClose: () => this.stop(true) });
    this.keyHandler = (e) => {
      if (e.key === "Escape" && document.body.classList.contains("immersive")) this.setImmersive(false);
    };
    document.addEventListener("keydown", this.keyHandler);
    await this.advance();
  }

  async fetchNext() {
    if (this.prefetching) return this.prefetching;
    this.prefetching = (async () => {
      const sound = await this.provider(this.history.slice(-30));
      if (!sound || this.stopped) return null;
      const track = new Track(sourceUrl(sound), { preload: "auto" });
      return { sound, track };
    })()
      .catch(() => null)
      .finally(() => {
        this.prefetching = null;
      });
    return this.prefetching;
  }

  async advance() {
    if (this.stopped) return;
    let item = this.next || (await this.fetchNext());
    this.next = null;
    if (!item) {
      if (!this.current) toast(t("walk.no_sound"));
      if (this.onEnd) this.onEnd();
      else if (!this.current) this.stop();
      return;
    }
    const prev = this.current;
    this.current = item;
    this.history.push(item.sound.id);
    this.fading = false;
    this.show(item.sound);
    this.view.attach(item.track, item.sound);
    nowPlaying.set(item.track, item.sound, { onNext: () => this.skip() });
    this.pins?.setPlaying(item.sound.id);
    item.track.addEventListener("timeupdate", () => this.onTime(item));
    item.track.addEventListener("ended", () => {
      if (this.current === item && !this.fading) this.advance();
    });
    try {
      await item.track.play(prev ? CROSSFADE : 1.2);
      countPlay(item.sound.id);
    } catch {
      toast(t("player.tap_to_play"));
    }
    if (prev) prev.track.pause(CROSSFADE).then(() => prev.track.destroy());
    this.fetchNext().then((n) => {
      if (this.stopped) n?.track.destroy();
      else this.next = n;
    });
  }

  onTime(item) {
    if (this.current !== item || this.fading || this.stopped) return;
    const d = item.track.duration;
    if (d && d - item.track.currentTime <= CROSSFADE && this.next) {
      this.fading = true;
      this.advance();
    }
  }

  show(s) {
    this.titleEl.textContent = s.title;
    this.placeEl.textContent = [s.pref_name, s.city_name].filter(Boolean).join(" ");
    setSheetTitle(this.title);
    this.onShow?.(s);
    this.detailsEl.replaceChildren(soundDetails(s), h("a", { href: s.page_url, class: "btn" }, icon("open_in_new"), t("sound.page")));
    const target = [s.lat, s.lng];
    const zoom = Math.max(this.map.getZoom(), 12);
    if (reducedMotion()) this.map.setView(target, zoom);
    else this.map.flyTo(target, Math.min(zoom, 14), { duration: 4, easeLinearity: 0.2 });
  }

  async skip() {
    if (!this.current || this.stopped) return;
    this.fading = true;
    await this.advance();
  }

  setImmersive(on) {
    document.body.classList.toggle("immersive", on);
    this.immersiveExit.hidden = !on;
    if (on) this.immersiveExit.focus();
  }

  setSleep(minutes) {
    clearTimeout(this.sleepTimer);
    clearInterval(this.sleepTick);
    this.sleepInfo.textContent = "";
    if (!minutes) return;
    this.sleepEnd = Date.now() + minutes * 60000;
    this.sleepTimer = setTimeout(() => {
      toast(t("walk.sleep_done"));
      this.stop();
    }, minutes * 60000);
    const tick = () => {
      const left = Math.max(0, Math.round((this.sleepEnd - Date.now()) / 60000));
      this.sleepInfo.textContent = t("walk.sleep_left", { n: left });
    };
    tick();
    this.sleepTick = setInterval(tick, 30000);
  }

  stop(fromClose = false) {
    if (this.stopped) return;
    this.stopped = true;
    clearTimeout(this.sleepTimer);
    clearInterval(this.sleepTick);
    document.removeEventListener("keydown", this.keyHandler);
    this.setImmersive(false);
    this.view?.destroy();
    for (const item of [this.current, this.next]) {
      if (!item) continue;
      item.track.pause(1.5).then(() => item.track.destroy());
      nowPlaying.clear(item.track);
    }
    this.current = null;
    this.next = null;
    this.pins?.setPlaying(null);
    if (!fromClose) closeSheet();
    this.onStop?.();
  }
}

export function startWalk({ map, pins, regions, onStop }) {
  const filters = { region: "", weather: "", time_of_day: "", season: "", tag: "" };
  const sel = (name, options) => {
    const s = h("select", { "aria-label": t(`walk.filter.${name}`) }, h("option", { value: "" }, t("walk.any")), options);
    s.addEventListener("change", () => {
      filters[name] = s.value;
      seq.next?.track.destroy();
      seq.next = null;
    });
    return h("label", { class: "field grow" }, h("span", {}, t(`walk.filter.${name}`)), s);
  };
  const extra = h(
    "details",
    { class: "walk-filters" },
    h("summary", {}, t("walk.filters")),
    h(
      "div",
      { class: "row" },
      sel("region", Object.entries(regions).map(([id, names]) => h("option", { value: id }, document.documentElement.lang === "ja" ? names[0] : names[1]))),
      sel("weather", ["sunny", "cloudy", "rain", "snow", "wind"].map((w) => h("option", { value: w }, t(`weather.${w}`)))),
    ),
    h(
      "div",
      { class: "row" },
      sel("time_of_day", [
        h("option", { value: "now" }, t("walk.now_tod")),
        ...["early_morning", "morning", "noon", "evening", "night", "late_night"].map((k) => h("option", { value: k }, t(`tod.${k}`))),
      ]),
      sel("season", ["spring", "summer", "autumn", "winter"].map((k) => h("option", { value: k }, t(`season.${k}`)))),
      sel("tag", ["nature", "city", "transit", "shop", "water", "festival", "other"].map((k) => h("option", { value: k }, t(`tag.${k}`)))),
    ),
  );
  const seq = new Sequencer({
    map,
    pins,
    title: t("mode.walk"),
    key: "walk",
    extraControls: extra,
    onStop,
    provider: async (exclude) => {
      const p = new URLSearchParams();
      for (const [k, v] of Object.entries(filters)) {
        if (!v) continue;
        p.set(k, k === "time_of_day" && v === "now" ? currentTimeOfDay() : v);
      }
      if (exclude.length) p.set("exclude", exclude.join(","));
      try {
        return await api(`/api/walk/next?${p.toString()}`);
      } catch {
        return null;
      }
    },
  });
  seq.start();
  return seq;
}
