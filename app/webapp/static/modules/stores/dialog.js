// Stores: the native <dialog> shell the item-detail, apply and baseline editors share.
import { esc, icon } from "../dom.js";

// The editors are native <dialog>s on the vendored modal shell, built once
// and kept at <body> level so a pane re-render never tears them down.
export function dialogShell(id, title, saveLabel, init) {
  let dialog = document.getElementById(id);
  if (dialog) return dialog;
  dialog = document.createElement("dialog");
  dialog.id = id;
  // dynamic-id-exempt: `id` is the literal sheet id each caller passes (one constant per sheet)
  dialog.dataset.trackView = id;
  dialog.className = "detail-dialog stores-dialog";
  dialog.setAttribute("aria-labelledby", `${id}-title`);
  // dynamic-id-exempt: `id` is the literal sheet id each caller passes, so `<id>.save` is a fixed id per sheet
  const saveButton = `<button type="button" class="button-primary detail-save-btn" data-track="${id}.save" disabled>${esc(saveLabel)}</button>`;
  dialog.innerHTML = `<div class="detail-card">
      <div class="detail-header">
        <h2 id="${id}-title" tabindex="-1" autofocus>${esc(title)}</h2>
        <button type="button" class="icon-button detail-close" aria-label="Close" data-track="dialog.close" data-dialog-close>${icon("x")}</button>
      </div>
      <div class="stores-dialog-body"></div>
      <div class="panel-status error stores-dialog-status" role="status"></div>
      <div class="detail-actions">${saveButton}</div>
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
