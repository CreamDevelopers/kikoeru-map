export function setupCopyButtons() {
  for (const block of document.querySelectorAll(".code")) {
    const pre = block.querySelector("pre");
    if (!pre) continue;
    const button = document.createElement("button");
    button.type = "button";
    button.className = "copy-button";
    button.textContent = "コピー";
    button.setAttribute("aria-label", "コードをコピー");
    button.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(pre.innerText.trim());
        button.textContent = "コピーしました";
      } catch {
        button.textContent = "コピーできませんでした";
      }
      setTimeout(() => (button.textContent = "コピー"), 2000);
    });
    block.append(button);
  }
}
