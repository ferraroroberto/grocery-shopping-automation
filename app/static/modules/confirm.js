// The confirm sheet (#254): the vendored modal shell asking one yes/no
// question, in place of the browser's confirm(), which ignores the theme, the
// type scale and the button tiers (design.md "editor modal"). One <dialog> at
// <body> level, built on first use; the × close and Esc answer no.
import { icon } from "./dom.js";

const ID = "confirm-dialog";
let answer = null; // resolves the open question's promise

function sheet() {
  let dialog = document.getElementById(ID);
  if (dialog) return dialog;
  dialog = document.createElement("dialog");
  dialog.id = ID;
  dialog.className = "detail-dialog confirm-dialog";
  dialog.setAttribute("aria-labelledby", `${ID}-title`);
  dialog.innerHTML = `<div class="detail-card">
      <div class="detail-header">
        <h2 id="${ID}-title"></h2>
        <button type="button" class="icon-button detail-close" aria-label="Close" data-confirm="no">${icon("x")}</button>
      </div>
      <p class="confirm-message"></p>
      <div class="detail-actions"><button type="button" class="detail-save-btn" data-confirm="yes"></button></div>
    </div>`;
  dialog.addEventListener("click", (event) => {
    const button = event.target.closest("[data-confirm]");
    if (button) settle(button.dataset.confirm === "yes");
  });
  // Esc, or the dialog closing any other way, is a no.
  dialog.addEventListener("close", () => settle(false));
  document.body.appendChild(dialog);
  return dialog;
}

function settle(yes) {
  const resolve = answer;
  answer = null;
  const dialog = document.getElementById(ID);
  if (dialog?.open) dialog.close();
  resolve?.(yes);
}

// Ask, and resolve true only on the confirm button. `danger` draws that button
// in the destructive tint (design.md: a destructive action restates the tint
// recipe on danger) instead of the primary fill.
export function confirmSheet({ title, message = "", confirmLabel, danger = false }) {
  settle(false); // a question still open is answered no, never left hanging
  const dialog = sheet();
  dialog.querySelector(`#${ID}-title`).textContent = title;
  const text = dialog.querySelector(".confirm-message");
  text.textContent = message;
  text.hidden = !message;
  const yes = dialog.querySelector('[data-confirm="yes"]');
  yes.className = `${danger ? "button-tint danger" : "button-primary"} detail-save-btn`;
  yes.textContent = confirmLabel;
  return new Promise((resolve) => {
    answer = resolve;
    dialog.showModal();
  });
}
