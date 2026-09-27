const KEY = "site-theme";
const ORDER = ["auto", "light", "dark"];
const LABELS = { auto: "テーマ: 自動", light: "テーマ: ライト", dark: "テーマ: ダーク" };
const ICONS = {
  auto: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" stroke-width="2"/><path d="M12 3a9 9 0 0 1 0 18z" fill="currentColor"/></svg>',
  light:
    '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="4.5" fill="none" stroke="currentColor" stroke-width="2"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M4.9 19.1 7 17M17 7l2.1-2.1" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>',
  dark: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 14.5A8.5 8.5 0 0 1 9.5 4a8.5 8.5 0 1 0 10.5 10.5z" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/></svg>',
};

function current() {
  return document.documentElement.getAttribute("data-theme") || "auto";
}

function apply(theme) {
  if (theme === "auto") document.documentElement.removeAttribute("data-theme");
  else document.documentElement.setAttribute("data-theme", theme);
  try {
    localStorage.setItem(KEY, theme);
  } catch {}
}

export function setupThemeToggle() {
  const button = document.getElementById("theme-button");
  if (!button) return;
  const render = () => {
    const theme = current();
    button.innerHTML = ICONS[theme];
    button.setAttribute("aria-label", LABELS[theme]);
    button.title = LABELS[theme];
  };
  button.addEventListener("click", () => {
    apply(ORDER[(ORDER.indexOf(current()) + 1) % ORDER.length]);
    render();
  });
  render();
}
