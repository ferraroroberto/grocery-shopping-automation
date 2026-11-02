// Copy-to-clipboard with a textarea + execCommand fallback: the
// Clipboard API needs a secure context, which the documented plain-HTTP LAN
// access (`http://<local-ip>:8502`, docs/running-and-access.md) never has, so
// `navigator.clipboard` is undefined there and a bare `writeText` throws with
// nothing on screen. Shared by every caller instead of re-typing the fallback.
export async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch (_) {
    const area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.className = "visually-hidden";
    document.body.appendChild(area);
    area.select();
    let copied = false;
    try {
      copied = document.execCommand("copy");
    } catch (_) {
      copied = false;
    }
    area.remove();
    return copied;
  }
}
