import { api, errorMessage } from "./api.js";
import { t } from "./i18n.js";
import { toast, wireDialog } from "./dom.js";
import { TurnstileWidget } from "./turnstile.js";

let widget = null;
let currentId = null;

export function setupReport() {
  const dialog = document.getElementById("report-dialog");
  if (!dialog) return;
  wireDialog(dialog);
  widget = new TurnstileWidget(document.getElementById("report-turnstile"), "report");
  const form = document.getElementById("report-form");
  const err = document.getElementById("report-error");
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    err.textContent = "";
    const fd = new FormData(form);
    const token = widget.getToken();
    if (!token) {
      err.textContent = t("error.turnstile_pending");
      return;
    }
    const submit = document.getElementById("report-submit");
    submit.disabled = true;
    try {
      await api(`/api/sounds/${encodeURIComponent(currentId)}/reports`, {
        method: "POST",
        body: { reason: fd.get("reason"), detail: String(fd.get("detail") || ""), "cf-turnstile-response": token },
      });
      dialog.close();
      form.reset();
      toast(t("report.thanks"));
    } catch (ex) {
      err.textContent = errorMessage(ex.code);
    } finally {
      submit.disabled = false;
      widget.reset();
    }
  });
}

export function openReport(soundId) {
  const dialog = document.getElementById("report-dialog");
  if (!dialog) return;
  currentId = soundId;
  document.getElementById("report-error").textContent = "";
  dialog.showModal();
  widget.render().catch(() => {
    document.getElementById("report-error").textContent = t("error.turnstile_load");
  });
}
