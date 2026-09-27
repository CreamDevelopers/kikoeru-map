import { debounce, h } from "./dom.js";
import { t } from "./i18n.js";

const ENDPOINT = "https://msearch.gsi.go.jp/address-search/AddressSearch";

export function setupSearch(map) {
  const input = document.getElementById("search-input");
  const list = document.getElementById("search-results");
  if (!input || !list) return;
  let results = [];
  let active = -1;
  let controller = null;

  const close = () => {
    list.hidden = true;
    input.setAttribute("aria-expanded", "false");
    input.removeAttribute("aria-activedescendant");
    active = -1;
  };

  const choose = (i) => {
    const r = results[i];
    if (!r) return;
    const [lng, lat] = r.geometry.coordinates;
    input.value = r.properties.title;
    close();
    map.flyTo([lat, lng], 15, { duration: 1 });
  };

  const render = () => {
    list.replaceChildren(
      ...results.map((r, i) =>
        h(
          "li",
          {
            id: `search-opt-${i}`,
            role: "option",
            "aria-selected": String(i === active),
            onmousedown: (e) => {
              e.preventDefault();
              choose(i);
            },
          },
          r.properties.title,
        ),
      ),
    );
    if (!results.length) list.append(h("li", { role: "option", "aria-disabled": "true" }, t("search.none")));
    list.hidden = false;
    input.setAttribute("aria-expanded", "true");
    if (active >= 0) input.setAttribute("aria-activedescendant", `search-opt-${active}`);
  };

  const query = debounce(async () => {
    const q = input.value.trim();
    if (q.length < 2) {
      close();
      return;
    }
    controller?.abort();
    controller = new AbortController();
    try {
      const res = await fetch(`${ENDPOINT}?q=${encodeURIComponent(q)}`, { signal: controller.signal });
      const data = await res.json();
      results = (Array.isArray(data) ? data : []).slice(0, 10);
      active = -1;
      render();
    } catch (e) {
      if (e.name !== "AbortError") close();
    }
  }, 250);

  input.addEventListener("input", query);
  input.addEventListener("keydown", (e) => {
    if (list.hidden) return;
    if (e.key === "ArrowDown") active = Math.min(results.length - 1, active + 1);
    else if (e.key === "ArrowUp") active = Math.max(0, active - 1);
    else if (e.key === "Enter") {
      e.preventDefault();
      choose(active >= 0 ? active : 0);
      return;
    } else if (e.key === "Escape") {
      close();
      return;
    } else return;
    e.preventDefault();
    render();
  });
  input.addEventListener("blur", () => setTimeout(close, 150));
}
