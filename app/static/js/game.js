import { api, errorMessage } from "./api.js";
import { Track, gameSourceUrl, nowPlaying } from "./audio.js";
import { h, icon, toast, wireDialog } from "./dom.js";
import { formatDistance, t } from "./i18n.js";
import { PlayerView } from "./player.js";
import { closeSheet, openSheet } from "./sheet.js";
import { TurnstileWidget } from "./turnstile.js";

const L = window.L;

export class Game {
  constructor({ map, pins, heat, onStop }) {
    this.map = map;
    this.pins = pins;
    this.heat = heat;
    this.onStop = onStop;
    this.layer = L.layerGroup();
    this.guess = null;
    this.track = null;
    this.stopped = false;
    this.onClick = (e) => this.placeGuess(e.latlng);
  }

  start() {
    this.pins.hide();
    this.heat?.hide();
    this.layer.addTo(this.map);
    this.map.on("click", this.onClick);
    this.menu();
  }

  accent() {
    return getComputedStyle(document.documentElement).getPropertyValue("--accent").trim();
  }

  async menu() {
    this.clearRound();
    this.layer.clearLayers();
    const rankList = h("ol", { class: "rank-list" });
    const root = h(
      "div",
      { class: "stack" },
      h("p", {}, t("game.intro")),
      h(
        "div",
        { class: "actions" },
        h("button", { type: "button", class: "primary", onclick: () => this.begin("random") }, icon("shuffle"), t("game.random")),
        h("button", { type: "button", onclick: () => this.begin("daily") }, icon("today"), t("game.daily")),
      ),
      h("h3", {}, t("game.ranking_today")),
      rankList,
    );
    openSheet(t("mode.game"), root, { key: "game", onClose: () => this.stop(true) });
    try {
      const data = await api("/api/game/daily/ranking");
      if (!data.items.length) rankList.replaceWith(h("p", { class: "muted small" }, t("game.ranking_empty")));
      else rankList.replaceChildren(...data.items.map((r) => h("li", {}, `${r.nickname} — ${r.score}`)));
    } catch {
      rankList.replaceWith(h("p", { class: "muted small" }, t("error.generic")));
    }
  }

  async begin(mode) {
    try {
      this.state = await api("/api/game/start", { method: "POST", body: { mode } });
    } catch (e) {
      toast(errorMessage(e.code));
      return;
    }
    this.results = [];
    this.round();
  }

  clearRound() {
    if (this.track) {
      const tr = this.track;
      nowPlaying.clear(tr);
      tr.pause(0.4).then(() => tr.destroy());
      this.track = null;
    }
    this.view?.destroy();
    this.view = null;
    if (this.guess) this.layer.removeLayer(this.guess);
    this.guess = null;
  }

  round() {
    this.clearRound();
    this.layer.clearLayers();
    const st = this.state;
    this.map.flyTo([36.2, 138.2], 5, { duration: 0.8 });
    const playerEl = h("div", { class: "player" });
    this.confirmBtn = h("button", { type: "button", class: "primary", disabled: true, onclick: () => this.submit() }, icon("check"), t("game.confirm"));
    this.hint = h("p", { class: "small muted", "aria-live": "polite" }, t("game.click_map"));
    const root = h(
      "div",
      { class: "stack" },
      h("p", {}, h("strong", {}, t("game.round", { n: st.round, total: st.rounds })), "  ", h("span", { class: "muted" }, t("game.total", { n: st.total }))),
      playerEl,
      this.hint,
      h("div", { class: "actions" }, this.confirmBtn, h("button", { type: "button", onclick: () => this.menu() }, t("game.quit"))),
    );
    openSheet(t("mode.game"), root, { key: "game", onClose: () => this.stop(true) });
    this.track = new Track(gameSourceUrl(st.audio), { loop: true });
    this.view = new PlayerView(playerEl);
    this.view.attach(this.track, { peaks_url: null });
    nowPlaying.set(this.track, { title: t("game.round", { n: st.round, total: st.rounds }) });
    this.track.play().catch(() => {});
  }

  placeGuess(latlng) {
    if (!this.confirmBtn || this.confirmBtn.dataset.done) return;
    if (this.guess) this.guess.setLatLng(latlng);
    else {
      this.guess = L.marker(latlng, {
        icon: L.divIcon({ className: "guess-pin", html: "", iconSize: [16, 16] }),
        draggable: true,
        keyboard: false,
        title: t("game.your_guess"),
      }).addTo(this.layer);
    }
    this.confirmBtn.disabled = false;
    this.hint.textContent = t("game.guess_placed");
  }

  async submit() {
    if (!this.guess) return;
    const ll = this.guess.getLatLng();
    this.confirmBtn.disabled = true;
    this.confirmBtn.dataset.done = "1";
    let res;
    try {
      res = await api(`/api/game/${encodeURIComponent(this.state.game_id)}/guess`, { method: "POST", body: { lat: ll.lat, lng: ll.lng } });
    } catch (e) {
      toast(errorMessage(e.code));
      delete this.confirmBtn.dataset.done;
      this.confirmBtn.disabled = false;
      return;
    }
    this.state = res;
    const r = res.result;
    this.results.push(r);
    const a = r.answer;
    L.marker([a.lat, a.lng], { icon: L.divIcon({ className: "answer-pin", html: "", iconSize: [18, 18] }), title: a.title }).addTo(this.layer);
    L.polyline([[ll.lat, ll.lng], [a.lat, a.lng]], { color: this.accent(), weight: 3, dashArray: "6 6" }).addTo(this.layer);
    this.map.flyToBounds(L.latLngBounds([[ll.lat, ll.lng], [a.lat, a.lng]]).pad(0.4), { maxZoom: 13, duration: 1 });
    const place = [a.pref_name, a.city_name].filter(Boolean).join(" ");
    const next = res.finished
      ? h("button", { type: "button", class: "primary", onclick: () => this.finish() }, icon("emoji_events"), t("game.see_result"))
      : h("button", { type: "button", class: "primary", onclick: () => this.round() }, icon("arrow_forward"), t("game.next"));
    this.hint.replaceChildren(
      h("span", { class: "game-score" }, `${r.score}`),
      " ",
      t("game.points"),
      h("br"),
      t("game.distance", { d: formatDistance(r.distance_m) }),
      h("br"),
      h("a", { href: a.page_url }, a.title),
      place ? ` (${place})` : "",
    );
    this.confirmBtn.replaceWith(next);
    next.focus();
  }

  async finish() {
    this.clearRound();
    let result;
    try {
      result = await api(`/api/game/${encodeURIComponent(this.state.game_id)}/result`);
    } catch (e) {
      toast(errorMessage(e.code));
      return;
    }
    this.layer.clearLayers();
    const bounds = L.latLngBounds([]);
    result.guesses.forEach((g, i) => {
      const a = g.answer;
      L.marker([g.lat, g.lng], { icon: L.divIcon({ className: "guess-pin", html: "", iconSize: [16, 16] }), title: `${i + 1}` }).addTo(this.layer);
      L.marker([a.lat, a.lng], { icon: L.divIcon({ className: "answer-pin", html: "", iconSize: [18, 18] }), title: a.title }).addTo(this.layer);
      L.polyline([[g.lat, g.lng], [a.lat, a.lng]], { color: this.accent(), weight: 3, dashArray: "6 6" }).addTo(this.layer);
      bounds.extend([g.lat, g.lng]).extend([a.lat, a.lng]);
    });
    if (bounds.isValid()) this.map.flyToBounds(bounds.pad(0.2), { duration: 1 });

    const shareText = result.share_text;
    const copyBtn = h("button", { type: "button", onclick: () => this.shareResult(shareText) }, icon("share"), t("game.share"));
    const rows = result.guesses.map((g, i) =>
      h("li", {}, h("strong", {}, `${g.score}`), ` — ${formatDistance(g.distance_m)} — `, h("a", { href: g.answer.page_url }, g.answer.title), ` (${i + 1})`),
    );
    const actions = h("div", { class: "actions" }, copyBtn);
    if (result.can_submit) {
      actions.append(h("button", { type: "button", class: "primary", onclick: () => this.openRank() }, icon("leaderboard"), t("game.submit_ranking")));
    }
    actions.append(h("button", { type: "button", onclick: () => this.menu() }, t("game.again")));
    openSheet(
      t("game.result"),
      h(
        "div",
        { class: "stack" },
        h("p", {}, h("span", { class: "game-score" }, `${result.total}`), ` / ${result.max}`),
        h("ol", { class: "rank-list" }, rows),
        h("pre", { class: "small", "aria-label": t("game.share_text") }, shareText),
        actions,
      ),
      { key: "game", onClose: () => this.stop(true) },
    );
  }

  async shareResult(text) {
    if (navigator.share) {
      try {
        await navigator.share({ text });
        return;
      } catch {}
    }
    try {
      await navigator.clipboard.writeText(text);
      toast(t("ui.copied"));
    } catch {
      toast(text);
    }
  }

  openRank() {
    const dialog = document.getElementById("rank-dialog");
    const form = document.getElementById("rank-form");
    const err = document.getElementById("rank-error");
    if (!this.rankWidget) {
      wireDialog(dialog);
      this.rankWidget = new TurnstileWidget(document.getElementById("rank-turnstile"), "ranking");
      form.addEventListener("submit", async (e) => {
        e.preventDefault();
        err.textContent = "";
        const token = this.rankWidget.getToken();
        if (!token) {
          err.textContent = t("error.turnstile_pending");
          return;
        }
        try {
          await api(`/api/game/${encodeURIComponent(this.state.game_id)}/ranking`, {
            method: "POST",
            body: { nickname: String(new FormData(form).get("nickname") || ""), "cf-turnstile-response": token },
          });
          dialog.close();
          toast(t("game.ranked"));
          this.menu();
        } catch (ex) {
          err.textContent = errorMessage(ex.code);
        } finally {
          this.rankWidget.reset();
        }
      });
    }
    err.textContent = "";
    dialog.showModal();
    this.rankWidget.render().catch(() => (err.textContent = t("error.turnstile_load")));
  }

  stop(fromClose = false) {
    if (this.stopped) return;
    this.stopped = true;
    this.clearRound();
    this.map.off("click", this.onClick);
    this.map.removeLayer(this.layer);
    this.pins.show();
    this.heat?.show();
    if (!fromClose) closeSheet();
    this.onStop?.();
  }
}
