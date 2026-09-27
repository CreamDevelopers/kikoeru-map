export function setupToc() {
  const content = document.querySelector("[data-toc]");
  const toc = document.getElementById("toc");
  if (!content || !toc) return;
  const headings = [...content.querySelectorAll("h2[id]")];
  if (!headings.length) return;

  const list = document.createElement("ol");
  const links = new Map();
  for (const h of headings) {
    const li = document.createElement("li");
    const a = document.createElement("a");
    a.href = `#${h.id}`;
    a.textContent = h.textContent;
    li.append(a);
    list.append(li);
    links.set(h, a);
  }
  const title = document.createElement("p");
  title.textContent = "このページの内容";
  toc.replaceChildren(title, list);

  const setActive = (h) => {
    for (const a of links.values()) a.classList.remove("is-active");
    links.get(h)?.classList.add("is-active");
  };
  const observer = new IntersectionObserver(
    (entries) => {
      const visible = entries.filter((e) => e.isIntersecting).sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top);
      if (visible.length) setActive(visible[0].target);
    },
    { rootMargin: "-90px 0px -60% 0px" },
  );
  headings.forEach((h) => observer.observe(h));
  setActive(headings[0]);
}
