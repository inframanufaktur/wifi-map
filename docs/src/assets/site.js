const copyBlocks = document.querySelectorAll("[data-copy]");

for (const block of copyBlocks) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "copy-button";
  button.textContent = "Copy";
  button.setAttribute("aria-label", "Copy command to clipboard");

  button.addEventListener("click", async () => {
    const command = block.querySelector("code")?.textContent ?? block.textContent;
    try {
      await navigator.clipboard.writeText(command.trim());
      button.textContent = "Copied";
    } catch (_error) {
      button.textContent = "Copy failed";
    }
    window.setTimeout(() => {
      button.textContent = "Copy";
    }, 1800);
  });

  block.append(button);
}
