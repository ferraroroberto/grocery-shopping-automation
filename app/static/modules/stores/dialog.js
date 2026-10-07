// Stores: the native <dialog> shell the item-detail, apply and baseline editors share.
import { esc, icon } from "../dom.js";

// The editors are native <dialog>s on the vendored modal shell, built once
// and kept at <body> level so a pane re-render never tears them down.
export function dialogShell(id, title, saveLabel, init) {
  let dialog = document.getElementById(id);
  if (dialog) return dialog;
  dialog = document.createElement("dialog");
  dialog.id = id;
  dialog.className = "detail-dialog stores-dialog";
  dialog.setAttribute("aria-labelledby", `${id}-title`);
  dialog.innerHTML = `<div class="detail-card">
      <div class="detail-header">
        <h2 id="${id}-title">${esc(title)}</h2>
        <button type="button" class="detail-close" aria-label="Close" data-dialog-close>${icon("x")}</button>
      </div>
      <div class="stores-dialog-body"></div>
      <div class="panel-status error stores-dialog-status" role="status"></div>
      <div class="detail-actions"><button type="button" class="button-primary detail-save-btn" disabled>${esc(saveLabel)}</button></div>
    </div>`;
  dialog.querySelector("[data-dialog-close]").addEventListener("click", () => dialog.close());
  init?.(dialog);
  document.body.appendChild(dialog);
  return dialog;
}

export function setDialogStatus(dialog, text, kind = "error") {
  const status = dialog.querySelector(".stores-dialog-status");
  status.className = `panel-status ${kind} stores-dialog-status`;
  status.textContent = text;
}
