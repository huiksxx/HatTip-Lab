(() => {
  "use strict";

  const bubble = document.getElementById("pet-bubble");
  const text = document.getElementById("bubble-text");
  const close = document.getElementById("bubble-close");
  let typingToken = 0;

  function setSide(side) {
    const normalized = side === "left" ? "left" : "right";
    bubble.classList.toggle("side-left", normalized === "left");
    bubble.classList.toggle("side-right", normalized === "right");
  }

  function render(payload = {}) {
    const message = String(payload.text || "");
    const typed = Boolean(payload.typed);
    typingToken += 1;
    const token = typingToken;
    setSide(payload.side);
    bubble.classList.toggle("thinking", Boolean(payload.thinking));
    bubble.style.animation = "none";
    void bubble.offsetWidth;
    bubble.style.animation = "";
    if (!typed) {
      text.textContent = message;
      return;
    }
    text.textContent = "";
    let index = 0;
    const step = () => {
      if (token !== typingToken) return;
      text.textContent += message.slice(index, index + 2);
      index += 2;
      if (index < message.length) window.setTimeout(step, 22);
    };
    step();
  }

  window.setPetBubble = render;
  window.setPetBubbleSide = setSide;

  close.addEventListener("click", () => {
    typingToken += 1;
    if (window.pywebview?.api?.hide_bubble) {
      window.pywebview.api.hide_bubble();
    }
  });

  const preview = new URLSearchParams(window.location.search).get("preview");
  if (preview) render({ text: preview, typed: false, side: "right" });
})();
