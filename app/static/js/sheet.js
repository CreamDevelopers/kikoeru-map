import { clear } from "./dom.js";
import { t } from "./i18n.js";

const sheet = document.getElementById("sheet");
const titleEl = document.getElementById("sheet-title");
const body = document.getElementById("sheet-body");
const closeBtn = document.getElementById("sheet-close");
const collapseBtn = document.getElementById("sheet-collapse");

let onCloseCb = null;
let owner = null;

export function openSheet(title, content, { onClose = null, key = null, focus = true } = {}) {
  // 同じモードが内容を差し替えるときは、そのモードの終了処理を呼ばない
  const sameOwner = key !== null && key === owner && !sheet.hidden;
  if (onCloseCb && !sameOwner) {
    const cb = onCloseCb;
    onCloseCb = null;
    cb();
  }
  owner = key;
  titleEl.textContent = title;
  clear(body).append(content);
  sheet.hidden = false;
  setCollapsed(false);
  onCloseCb = onClose;
  if (focus) body.focus({ preventScroll: true });
}

export function sheetOwner() {
  return sheet.hidden ? null : owner;
}

export function setSheetTitle(title) {
  titleEl.textContent = title;
}

export function closeSheet() {
  if (sheet.hidden) return;
  sheet.hidden = true;
  owner = null;
  clear(body);
  const cb = onCloseCb;
  onCloseCb = null;
  cb?.();
}

function setCollapsed(v) {
  sheet.classList.toggle("is-collapsed", v);
  collapseBtn.setAttribute("aria-expanded", String(!v));
  collapseBtn.setAttribute("aria-label", t(v ? "ui.expand" : "ui.collapse"));
  collapseBtn.firstElementChild.textContent = v ? "expand_less" : "expand_more";
}

closeBtn?.addEventListener("click", closeSheet);
collapseBtn?.addEventListener("click", () => setCollapsed(!sheet.classList.contains("is-collapsed")));

let startY = null;
const head = sheet?.querySelector(".sheet-head");
const grip = sheet?.querySelector(".sheet-grip");
for (const el of [head, grip]) {
  el?.addEventListener("touchstart", (e) => (startY = e.touches[0].clientY), { passive: true });
  el?.addEventListener(
    "touchend",
    (e) => {
      if (startY === null) return;
      const dy = e.changedTouches[0].clientY - startY;
      if (dy > 40) setCollapsed(true);
      else if (dy < -40) setCollapsed(false);
      startY = null;
    },
    { passive: true },
  );
}
