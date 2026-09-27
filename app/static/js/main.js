import { nowPlaying } from "./audio.js";
import { courseList, listCourses, playCourse, showCourses } from "./course.js";
import { boot, h, icon, toast } from "./dom.js";
import { Game } from "./game.js";
import { t } from "./i18n.js";
import { createMap, locate, setupHeat, setupLayerMenu } from "./mapview.js";
import { openMine } from "./mine.js";
import { showSound, stopPanel } from "./panel.js";
import { PinLayer } from "./pins.js";
import { PostFlow } from "./post.js";
import { setupReport } from "./report.js";
import { setupSearch } from "./search.js";
import { closeSheet, openSheet, sheetOwner } from "./sheet.js";
import { Spatial } from "./spatial.js";
import { startWalk } from "./walk.js";

const cfg = boot();
const ctl = createMap(document.getElementById("map"));
const map = ctl.map;
setupLayerMenu(ctl);
const heat = setupHeat(map);
setupSearch(map);
setupReport();

let mode = null;

const pins = new PinLayer(map, {
  onSelect: (id) => {
    if (mode && mode.name !== "walk" && mode.name !== "course") return;
    stopMode();
    showSound(id, { map, pins });
  },
});

function setModeButtons(name) {
  document.querySelectorAll(".dock [data-mode]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.mode === name)));
}

function stopMode() {
  const m = mode;
  mode = null;
  setModeButtons(null);
  m?.obj?.stop();
}

function onModeStopped(name) {
  return () => {
    if (mode?.name === name) {
      mode = null;
      setModeButtons(null);
    }
  };
}

function enterMode(name) {
  if (mode?.name === name) {
    stopMode();
    return;
  }
  stopMode();
  stopPanel();
  closeSheet();
  let obj = null;
  if (name === "walk") obj = startWalk({ map, pins, regions: cfg.regions || {}, onStop: onModeStopped("walk") });
  else if (name === "spatial") {
    obj = new Spatial({ map, pins, onStop: onModeStopped("spatial") });
    obj.start();
  } else if (name === "game") {
    obj = new Game({ map, pins, heat, onStop: onModeStopped("game") });
    obj.start();
  }
  mode = { name, obj };
  setModeButtons(name);
}

async function startCourse(slug) {
  stopMode();
  stopPanel();
  const seq = await playCourse(slug, { map, pins, onStop: onModeStopped("course") });
  if (seq) mode = { name: "course", obj: seq };
}

document.querySelectorAll(".dock [data-mode]").forEach((b) => b.addEventListener("click", () => enterMode(b.dataset.mode)));

let postFlow = null;
document.getElementById("post-btn").addEventListener("click", () => {
  if (!postFlow) postFlow = new PostFlow({ getMap: () => map });
  postFlow.open();
});

document.getElementById("locate-btn").addEventListener("click", () => locate(map, 14));

const drawer = document.getElementById("drawer");
const backdrop = document.getElementById("backdrop");
const menuBtn = document.getElementById("menu-btn");
function setDrawer(open) {
  drawer.hidden = !open;
  backdrop.hidden = !open;
  menuBtn.setAttribute("aria-expanded", String(open));
  if (open) drawer.querySelector("a, button")?.focus();
  else menuBtn.focus();
}
menuBtn.addEventListener("click", () => setDrawer(drawer.hidden));
backdrop.addEventListener("click", () => setDrawer(false));
drawer.addEventListener("keydown", (e) => e.key === "Escape" && setDrawer(false));
document.getElementById("mine-btn").addEventListener("click", () => {
  setDrawer(false);
  openMine();
});
document.getElementById("courses-btn").addEventListener("click", () => {
  setDrawer(false);
  showCourses((slug) => startCourse(slug));
});

const themeLabel = document.getElementById("theme-label");
function currentTheme() {
  return document.documentElement.getAttribute("data-theme") || "auto";
}
function renderTheme() {
  themeLabel.textContent = `${t("nav.theme")}: ${t(`theme.${currentTheme()}`)}`;
}
document.getElementById("theme-btn").addEventListener("click", () => {
  const order = ["auto", "light", "dark"];
  const next = order[(order.indexOf(currentTheme()) + 1) % order.length];
  if (next === "auto") document.documentElement.removeAttribute("data-theme");
  else document.documentElement.setAttribute("data-theme", next);
  try {
    localStorage.setItem("theme", next);
  } catch {}
  renderTheme();
});
renderTheme();

document.addEventListener("keydown", (e) => {
  const tag = e.target.tagName;
  if (["INPUT", "TEXTAREA", "SELECT"].includes(tag) || e.target.isContentEditable) return;
  if (document.querySelector("dialog[open]")) return;
  if (e.ctrlKey || e.metaKey || e.altKey) return;
  const tr = nowPlaying.track;
  if (e.key === " " && tr && tag !== "BUTTON") {
    e.preventDefault();
    tr.toggle();
  } else if (e.key === "ArrowLeft" && tr && !e.target.closest("#map")) {
    tr.seek(tr.currentTime - 5);
  } else if (e.key === "ArrowRight" && tr && !e.target.closest("#map")) {
    tr.seek(tr.currentTime + 5);
  } else if ((e.key === "n" || e.key === "N") && nowPlaying.onNext) {
    nowPlaying.onNext();
  } else if (e.key === "Escape" && !document.body.classList.contains("immersive")) {
    if (!drawer.hidden) setDrawer(false);
    else if (sheetOwner()) {
      if (mode) stopMode();
      closeSheet();
    }
  }
});

function animate() {
  const tr = nowPlaying.track;
  if (tr && !tr.paused) pins.setLevel(tr.level());
  requestAnimationFrame(animate);
}
requestAnimationFrame(animate);

function connectStream() {
  const es = new EventSource("/api/stream");
  es.addEventListener("sound", (e) => {
    try {
      const d = JSON.parse(e.data);
      pins.addPoint(d);
      if (map.getBounds().contains([d.lat, d.lng])) toast(t("map.new_sound", { title: d.title }));
    } catch {}
  });
}
connectStream();

async function welcome() {
  const featured = await listCourses(true);
  if (sheetOwner()) return;
  const root = h(
    "div",
    { class: "welcome" },
    h("p", {}, t("welcome.lead")),
    h(
      "div",
      { class: "actions" },
      h("button", { type: "button", class: "primary", onclick: () => enterMode("walk") }, icon("directions_walk"), t("welcome.start_walk")),
      h("a", { class: "btn", href: "/about/help" }, icon("help"), t("nav.help")),
    ),
  );
  if (featured.length) root.append(h("h3", {}, t("welcome.featured")), courseList(featured, (slug) => startCourse(slug)));
  openSheet(t("app.name"), root, { key: "welcome", focus: false });
}

const params = new URLSearchParams(location.search);
if (params.get("s")) showSound(params.get("s"), { map, pins, autoplay: false });
else if (cfg.mode === "walk") enterMode("walk");
else if (cfg.mode === "game") enterMode("game");
else if (cfg.mode === "course" && cfg.course) startCourse(cfg.course);
else welcome();

if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(() => {}));
}
