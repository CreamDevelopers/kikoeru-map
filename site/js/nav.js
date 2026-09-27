function currentPage() {
  const file = location.pathname.split("/").pop();
  return !file || file === "index.html" ? "index.html" : file.endsWith(".html") ? file : `${file}.html`;
}

export function setupNav() {
  const nav = document.getElementById("site-nav");
  const button = document.getElementById("menu-button");
  if (!nav) return;

  const page = currentPage();
  for (const a of nav.querySelectorAll("a")) {
    const target = a.getAttribute("href").split("#")[0].replace(/^\.?\//, "") || "index.html";
    if (target === page) a.setAttribute("aria-current", "page");
  }

  if (!button) return;
  const setOpen = (open) => {
    nav.classList.toggle("is-open", open);
    button.setAttribute("aria-expanded", String(open));
  };
  button.addEventListener("click", () => setOpen(!nav.classList.contains("is-open")));
  nav.addEventListener("click", (e) => {
    if (e.target.closest("a")) setOpen(false);
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && nav.classList.contains("is-open")) {
      setOpen(false);
      button.focus();
    }
  });
  document.addEventListener("click", (e) => {
    if (nav.classList.contains("is-open") && !nav.contains(e.target) && !button.contains(e.target)) setOpen(false);
  });
}
