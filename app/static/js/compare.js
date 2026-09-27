import { api } from "./api.js";
import { Track, sourceUrl } from "./audio.js";
import { h, icon } from "./dom.js";
import { formatDate, t } from "./i18n.js";

const GROUPS = ["time_of_day", "weather", "season"];
const LABEL_PREFIX = { time_of_day: "tod", weather: "weather", season: "season" };

export function buildCompare(sound, { onBeforePlay }) {
  const root = h("section", { class: "compare", "aria-label": t("compare.title") });
  const status = h("p", { class: "small muted" }, t("ui.loading"));
  root.append(h("h3", {}, icon("compare_arrows"), " ", t("compare.title")), status);

  let items = [];
  let group = "time_of_day";
  let pickA = sound.id;
  let pickB = null;
  let tracks = [];
  const list = h("ul", { class: "compare-list" });
  const groupRow = h("div", { class: "chips", role: "radiogroup", "aria-label": t("compare.group") });
  const slider = h("input", {
    type: "range",
    min: "0",
    max: "100",
    value: "50",
    "aria-label": t("compare.mix"),
  });
  const labelA = h("span", { class: "small" }, "A");
  const labelB = h("span", { class: "small" }, "B");
  const playBtn = h("button", { type: "button", class: "primary" }, icon("play_arrow"), t("compare.play_both"));

  function stop() {
    for (const tr of tracks) {
      tr.pause(0.4).then(() => tr.destroy());
    }
    tracks = [];
    playBtn.replaceChildren(icon("play_arrow"), t("compare.play_both"));
  }

  function applyMix() {
    const x = Number(slider.value) / 100;
    if (tracks[0]) tracks[0].setVolume(Math.cos((x * Math.PI) / 2));
    if (tracks[1]) tracks[1].setVolume(Math.sin((x * Math.PI) / 2));
  }
  slider.addEventListener("input", applyMix);

  playBtn.addEventListener("click", async () => {
    if (tracks.length) {
      stop();
      return;
    }
    const a = items.find((s) => s.id === pickA);
    const b = items.find((s) => s.id === pickB);
    if (!a || !b) return;
    onBeforePlay?.();
    tracks = [new Track(sourceUrl(a), { loop: true }), new Track(sourceUrl(b), { loop: true })];
    applyMix();
    try {
      await Promise.all(tracks.map((tr) => tr.play(0.6)));
      applyMix();
      playBtn.replaceChildren(icon("stop"), t("compare.stop"));
    } catch {
      stop();
    }
  });

  function describe(s) {
    const parts = [];
    for (const g of GROUPS) if (s[g]) parts.push(t(`${LABEL_PREFIX[g]}.${s[g]}`));
    return parts.join(" / ");
  }

  function renderList() {
    list.replaceChildren();
    const sorted = [...items].sort((x, y) => String(x[group] || "~").localeCompare(String(y[group] || "~")));
    for (const s of sorted) {
      const ra = h("input", { type: "radio", name: `cmp-a-${sound.id}`, "aria-label": `A: ${s.title}` });
      ra.checked = s.id === pickA;
      ra.addEventListener("change", () => {
        pickA = s.id;
        if (pickB === pickA) pickB = null;
        stop();
        renderList();
      });
      const rb = h("input", { type: "radio", name: `cmp-b-${sound.id}`, "aria-label": `B: ${s.title}` });
      rb.checked = s.id === pickB;
      rb.disabled = s.id === pickA;
      rb.addEventListener("change", () => {
        pickB = s.id;
        stop();
        renderList();
      });
      const tag = s[group] ? t(`${LABEL_PREFIX[group]}.${s[group]}`) : t("ui.unspecified");
      list.append(
        h(
          "li",
          {},
          h("span", { class: "chip" }, tag),
          h("span", {}, s.title, h("br"), h("span", { class: "small muted" }, describe(s), " ", formatDate(s.recorded_at))),
          h("label", { class: "check small" }, ra, "A"),
          h("label", { class: "check small" }, rb, "B"),
        ),
      );
    }
    const a = items.find((s) => s.id === pickA);
    const b = items.find((s) => s.id === pickB);
    labelA.textContent = a ? `A: ${describe(a) || a.title}` : "A";
    labelB.textContent = b ? `B: ${describe(b) || b.title}` : "B";
    playBtn.disabled = !(a && b);
  }

  for (const g of GROUPS) {
    const r = h("input", { type: "radio", name: `cmp-g-${sound.id}`, value: g });
    r.checked = g === group;
    r.addEventListener("change", () => {
      group = g;
      renderList();
    });
    groupRow.append(h("label", { class: "chip" }, r, h("span", {}, t(`field.${g}`))));
  }

  api(`/api/sounds/${encodeURIComponent(sound.id)}/same-place`)
    .then((data) => {
      items = data.items;
      if (items.length < 2) {
        status.textContent = t("compare.none");
        return;
      }
      pickB = items.find((s) => s.id !== pickA)?.id || null;
      status.textContent = t("compare.count", { n: items.length });
      root.append(
        groupRow,
        list,
        h("div", { class: "mix" }, labelA, slider, labelB),
        h("div", { class: "actions" }, playBtn),
      );
      renderList();
    })
    .catch(() => {
      status.textContent = t("error.generic");
    });

  return { root, stop };
}
