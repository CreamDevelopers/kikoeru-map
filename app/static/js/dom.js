export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "dataset") Object.assign(el.dataset, v);
    else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
    else if (v === true) el.setAttribute(k, "");
    else el.setAttribute(k, String(v));
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

export function icon(name) {
  return h("span", { class: "material-symbols-outlined", "aria-hidden": "true" }, name);
}

export function clear(el) {
  while (el.firstChild) el.removeChild(el.firstChild);
  return el;
}

let toastTimer = 0;
export function toast(message, ms = 3200) {
  const el = document.getElementById("toast");
  if (!el) return;
  // モーダルの top layer より下に隠れないよう、開いているダイアログの中へ移す
  const modals = document.querySelectorAll("dialog[open]:modal");
  const host = modals[modals.length - 1] || document.body;
  if (el.parentElement !== host) host.append(el);
  el.textContent = message;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    el.hidden = true;
  }, ms);
}

export function debounce(fn, ms) {
  let timer = 0;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
}

export const reducedMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

export function wireDialog(dialog) {
  dialog.querySelectorAll("[data-close]").forEach((b) => b.addEventListener("click", () => dialog.close()));
  dialog.addEventListener("click", (e) => {
    if (e.target === dialog) dialog.close();
  });
}

// ブラウザ標準の confirm() の代わり。OK なら true、キャンセルや Esc・背景クリックなら false で解決する
export function confirmDialog(message, { ok = "OK", cancel = "キャンセル", danger = false } = {}) {
  return new Promise((resolve) => {
    const okBtn = h("button", { type: "button", class: danger ? "danger" : "btn-primary" }, ok);
    const cancelBtn = h("button", { type: "button" }, cancel);
    const dialog = h(
      "dialog",
      { class: "confirm-dialog", "aria-labelledby": "confirm-dialog-msg" },
      h("p", { id: "confirm-dialog-msg" }, message),
      h("div", { class: "confirm-actions" }, cancelBtn, okBtn),
    );
    let result = false;
    okBtn.addEventListener("click", () => {
      result = true;
      dialog.close();
    });
    cancelBtn.addEventListener("click", () => dialog.close());
    dialog.addEventListener("click", (e) => {
      if (e.target === dialog) dialog.close();
    });
    dialog.addEventListener("close", () => {
      dialog.remove();
      resolve(result);
    });
    document.body.append(dialog);
    dialog.showModal();
    (danger ? cancelBtn : okBtn).focus();
  });
}

export function boot() {
  try {
    return JSON.parse(document.getElementById("boot")?.textContent || "{}");
  } catch {
    return {};
  }
}
