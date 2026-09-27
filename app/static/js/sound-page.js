import { Track, countPlay, nowPlaying, sourceUrl } from "./audio.js";
import { formatDate } from "./i18n.js";
import { share } from "./panel.js";
import { PlayerView } from "./player.js";
import { openReport, setupReport } from "./report.js";

const el = document.getElementById("player");
const sound = JSON.parse(el.dataset.sound);
const track = new Track(sourceUrl(sound));
const view = new PlayerView(el);
view.attach(track, sound);
nowPlaying.set(track, sound);
let counted = false;
track.addEventListener("play", () => {
  if (!counted) {
    counted = true;
    countPlay(sound.id);
  }
});

document.querySelectorAll("time[data-local-time]").forEach((tm) => {
  tm.textContent = formatDate(tm.getAttribute("datetime"));
});

setupReport();
document.getElementById("report-btn").addEventListener("click", () => openReport(sound.id));
document.getElementById("share-btn").addEventListener("click", () => share(sound));

document.addEventListener("keydown", (e) => {
  if (["INPUT", "TEXTAREA", "SELECT", "BUTTON"].includes(e.target.tagName) || document.querySelector("dialog[open]")) return;
  if (e.key === " ") {
    e.preventDefault();
    track.toggle();
  } else if (e.key === "ArrowLeft") track.seek(track.currentTime - 5);
  else if (e.key === "ArrowRight") track.seek(track.currentTime + 5);
});

if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(() => {});
