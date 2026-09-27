import { h } from "./dom.js";

let cfg = {};
try {
  cfg = JSON.parse(document.getElementById("admin-boot")?.textContent || "{}");
} catch {
  cfg = {};
}

async function call(path, { method = "GET", body } = {}) {
  const res = await fetch(path, {
    method,
    credentials: "same-origin",
    headers: { Accept: "application/json", "X-CSRF-Token": cfg.csrf || "", ...(body ? { "Content-Type": "application/json" } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (res.status === 401) {
    location.href = "/admin/login";
    throw new Error("login required");
  }
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

document.addEventListener("click", (e) => {
  const btn = e.target.closest("[data-confirm]");
  if (btn && !confirm(btn.dataset.confirm)) e.preventDefault();
});

document.getElementById("select-all")?.addEventListener("change", (e) => {
  document.querySelectorAll('#bulk-form input[name="ids"]').forEach((c) => (c.checked = e.target.checked));
});

function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function barChart(el, labels, values) {
  const W = 600;
  const H = 180;
  const pad = { l: 36, r: 8, t: 10, b: 22 };
  const max = Math.max(1, ...values);
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.setAttribute("preserveAspectRatio", "none");
  const bw = (W - pad.l - pad.r) / values.length;
  for (let i = 0; i <= 4; i++) {
    const y = pad.t + ((H - pad.t - pad.b) * i) / 4;
    const line = document.createElementNS(ns, "line");
    line.setAttribute("x1", pad.l);
    line.setAttribute("x2", W - pad.r);
    line.setAttribute("y1", y);
    line.setAttribute("y2", y);
    line.setAttribute("class", "grid");
    svg.append(line);
    const txt = document.createElementNS(ns, "text");
    txt.setAttribute("x", 2);
    txt.setAttribute("y", y + 3);
    txt.setAttribute("class", "axis");
    txt.textContent = String(Math.round(max * (1 - i / 4)));
    svg.append(txt);
  }
  values.forEach((v, i) => {
    const hgt = ((H - pad.t - pad.b) * v) / max;
    const r = document.createElementNS(ns, "rect");
    r.setAttribute("x", pad.l + i * bw + 1);
    r.setAttribute("y", H - pad.b - hgt);
    r.setAttribute("width", Math.max(1, bw - 2));
    r.setAttribute("height", hgt);
    r.setAttribute("class", "bar");
    const title = document.createElementNS(ns, "title");
    title.textContent = `${labels[i]}: ${v}`;
    r.append(title);
    svg.append(r);
    if (i % 5 === 0) {
      const txt = document.createElementNS(ns, "text");
      txt.setAttribute("x", pad.l + i * bw);
      txt.setAttribute("y", H - 6);
      txt.setAttribute("class", "axis");
      txt.textContent = labels[i].slice(5);
      svg.append(txt);
    }
  });
  el.replaceChildren(svg);
}

async function dashboard() {
  const data = await call("/admin/api/stats");
  barChart(document.getElementById("chart-posts"), data.days, data.posts);
  barChart(document.getElementById("chart-plays"), data.days, data.plays);
  const tbody = document.querySelector("#pref-table tbody");
  tbody.replaceChildren(...data.prefs.map((p) => h("tr", {}, h("td", {}, p.name), h("td", {}, String(p.count)))));
  const L = window.L;
  if (!L) return;
  const map = L.map("pref-map", { center: [36.5, 137.5], zoom: 5, minZoom: 4, maxZoom: 9 });
  map.attributionControl.setPrefix(false);
  L.tileLayer("https://cyberjapandata.gsi.go.jp/xyz/pale/{z}/{x}/{y}.png", {
    attribution: '<a href="https://maps.gsi.go.jp/development/ichiran.html" target="_blank" rel="noopener">国土地理院</a>',
    maxZoom: 18,
  }).addTo(map);
  const max = Math.max(1, ...data.prefs.map((p) => p.count));
  const accent = cssVar("--accent");
  for (const p of data.prefs) {
    if (!p.count) continue;
    L.circleMarker([p.lat, p.lng], {
      radius: 4 + 22 * Math.sqrt(p.count / max),
      color: accent,
      weight: 1,
      fillColor: accent,
      fillOpacity: 0.45,
    })
      .bindTooltip(`${p.name}: ${p.count}`)
      .addTo(map);
  }
}
if (document.getElementById("chart-posts")) dashboard().catch(console.error);

const reviewEl = document.getElementById("review");
if (reviewEl) {
  let item = null;
  let lastId = null;
  let audio = null;
  const remainingEl = document.getElementById("review-remaining");

  const render = () => {
    audio?.pause();
    audio = null;
    if (!item) {
      reviewEl.replaceChildren(h("p", { class: "muted" }, "確認待ちの投稿はありません"));
      return;
    }
    audio = h("audio", { controls: true, preload: "auto", src: item.m4a_url || item.webm_url || "" });
    const reasons = { voice: "人の声の可能性", reports: "通報", preapproval: "事前承認モード", admin: "管理者" };
    reviewEl.replaceChildren(
      h("h2", {}, item.title),
      h("p", { class: "small muted" }, `${item.id} ・ ${item.pref_name || ""}${item.city_name || ""} ・ 保留理由: ${reasons[item.hidden_reason] || item.hidden_reason || "-"}`),
      audio,
      item.spectrogram_url ? h("img", { class: "spectrogram", src: item.spectrogram_url, alt: "スペクトログラム" }) : null,
      item.comment ? h("p", {}, item.comment) : null,
      h(
        "dl",
        {},
        h("dt", {}, "人の声の含有率"),
        h("dd", {}, item.voice_ratio == null ? "-" : `${Math.round(item.voice_ratio * 100)}%${item.voice_flag ? "（要確認）" : ""}`),
        h("dt", {}, "音割れ"),
        h("dd", {}, item.clipping_warning ? `あり（${(item.clipping_ratio * 100).toFixed(2)}%）` : "なし"),
        h("dt", {}, "無音率"),
        h("dd", {}, item.silence_ratio == null ? "-" : `${Math.round(item.silence_ratio * 100)}%`),
        h("dt", {}, "タグ"),
        h("dd", {}, (item.tags || []).join(", ")),
        h("dt", {}, "位置"),
        h("dd", {}, `${item.lat.toFixed(4)}, ${item.lng.toFixed(4)}（${item.precision}）`),
        h("dt", {}, "通報"),
        h("dd", {}, item.reports.length ? item.reports.map((r) => `${r.reason}${r.detail ? `: ${r.detail}` : ""}`).join(" / ") : "なし"),
      ),
    );
    audio.play().catch(() => {});
  };

  const load = async (after) => {
    const data = await call(`/admin/api/review/next${after ? `?after=${encodeURIComponent(after)}` : ""}`);
    item = data.item;
    remainingEl.textContent = `残り ${data.remaining} 件`;
    render();
  };

  const decide = async (decision) => {
    if (!item) return;
    const id = item.id;
    await call(`/admin/api/review/${encodeURIComponent(id)}`, { method: "POST", body: { decision } });
    lastId = id;
    await load(lastId);
  };
  const skip = () => item && load(item.id);

  document.getElementById("review-approve").addEventListener("click", () => decide("approve"));
  document.getElementById("review-reject").addEventListener("click", () => decide("reject"));
  document.getElementById("review-skip").addEventListener("click", skip);
  document.addEventListener("keydown", (e) => {
    if (["INPUT", "TEXTAREA", "SELECT"].includes(e.target.tagName) || e.ctrlKey || e.metaKey) return;
    const k = e.key.toLowerCase();
    if (k === "a") decide("approve");
    else if (k === "r") decide("reject");
    else if (k === "j" || e.key === "ArrowRight") skip();
    else if (e.key === " " && audio && e.target.tagName !== "BUTTON") {
      e.preventDefault();
      if (audio.paused) audio.play();
      else audio.pause();
    } else return;
  });
  load(null).catch(() => reviewEl.replaceChildren(h("p", { class: "error-text" }, "読み込みに失敗しました")));
}
