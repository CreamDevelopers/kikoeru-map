let dict = {};
try {
  dict = JSON.parse(document.getElementById("i18n")?.textContent || "{}");
} catch {
  dict = {};
}

export const lang = document.documentElement.lang || "ja";

export function t(key, vars) {
  let s = dict[key] ?? key;
  if (vars) {
    for (const [k, v] of Object.entries(vars)) s = s.replaceAll(`{${k}}`, String(v));
  }
  return s;
}

export function formatDuration(sec) {
  if (!Number.isFinite(sec) || sec < 0) sec = 0;
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}

export function formatDate(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  return d.toLocaleString(lang === "ja" ? "ja-JP" : "en-US", {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function formatDistance(m) {
  if (m < 1000) return t("unit.m", { n: Math.round(m) });
  return t("unit.km", { n: (m / 1000).toFixed(m < 10000 ? 1 : 0) });
}
