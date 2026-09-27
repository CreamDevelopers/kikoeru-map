import { api, errorMessage } from "./api.js";
import { h, icon, toast, wireDialog } from "./dom.js";
import { formatDate, t } from "./i18n.js";

const KEY = "kk_tokens";

export function loadTokens() {
  try {
    return JSON.parse(localStorage.getItem(KEY) || "{}");
  } catch {
    return {};
  }
}

export function saveToken(id, token) {
  const all = loadTokens();
  all[id] = token;
  try {
    localStorage.setItem(KEY, JSON.stringify(all));
  } catch {
    toast(t("mine.storage_failed"));
  }
}

function removeToken(id) {
  const all = loadTokens();
  delete all[id];
  localStorage.setItem(KEY, JSON.stringify(all));
}

const TAGS = ["nature", "city", "transit", "shop", "water", "festival", "other"];

function editForm(s, token, onDone) {
  const title = h("input", { maxlength: "40", value: s.title, required: true });
  const comment = h("textarea", { maxlength: "200" });
  comment.value = s.comment || "";
  const tags = TAGS.map((tg) => {
    const c = h("input", { type: "checkbox", value: tg });
    c.checked = (s.tags || []).includes(tg);
    return h("label", { class: "chip" }, c, h("span", {}, t(`tag.${tg}`)));
  });
  const form = h(
    "form",
    { class: "stack" },
    h("label", { class: "field" }, h("span", {}, t("field.title")), title),
    h("label", { class: "field" }, h("span", {}, t("field.comment")), comment),
    h("fieldset", { class: "field" }, h("legend", {}, t("field.tags")), h("div", { class: "chips" }, tags)),
    h("div", { class: "actions" }, h("button", { type: "submit", class: "primary" }, t("ui.save")), h("button", { type: "button", onclick: onDone }, t("ui.cancel"))),
  );
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      await api(`/api/sounds/${encodeURIComponent(s.id)}`, {
        method: "PATCH",
        headers: { "X-Delete-Token": token },
        body: {
          title: title.value.trim(),
          comment: comment.value.trim(),
          tags: tags.map((l) => l.querySelector("input")).filter((c) => c.checked).map((c) => c.value),
        },
      });
      toast(t("ui.saved"));
      onDone(true);
    } catch (ex) {
      toast(errorMessage(ex.code));
    }
  });
  return form;
}

export async function openMine() {
  const dialog = document.getElementById("mine-dialog");
  const body = document.getElementById("mine-body");
  if (!dialog.dataset.wired) {
    wireDialog(dialog);
    dialog.dataset.wired = "1";
  }
  dialog.showModal();
  await render(body);
}

async function render(body) {
  const tokens = loadTokens();
  const items = Object.entries(tokens).map(([id, token]) => ({ id, token }));
  if (!items.length) {
    body.replaceChildren(h("p", { class: "muted" }, t("mine.empty")));
    return;
  }
  body.replaceChildren(h("p", { class: "muted" }, t("ui.loading")));
  let data;
  try {
    data = await api("/api/mine", { method: "POST", body: { items } });
  } catch (e) {
    body.replaceChildren(h("p", { class: "error-text" }, errorMessage(e.code)));
    return;
  }
  if (!data.items.length) {
    body.replaceChildren(h("p", { class: "muted" }, t("mine.empty")));
    return;
  }
  const list = h("ul", { class: "card-list" });
  for (const s of data.items) {
    const token = tokens[s.id];
    const li = h("li");
    const show = () => {
      li.replaceChildren(
        h("h3", {}, s.status === "published" ? h("a", { href: s.page_url }, s.title) : s.title),
        h("p", { class: "small muted" }, `${t(`status.${s.status}`)} ・ ${formatDate(s.published_at || s.recorded_at)} ・ ${t("field.plays")} ${s.play_count}`),
        s.reason && s.status !== "published" ? h("p", { class: "small" }, errorMessage(s.reason)) : null,
        s.clipping_warning ? h("p", { class: "small notice warn" }, t("post.warn.clipping")) : null,
        h(
          "div",
          { class: "actions" },
          h(
            "button",
            {
              type: "button",
              onclick: () => li.replaceChildren(editForm(s, token, (changed) => (changed ? render(document.getElementById("mine-body")) : show()))),
            },
            icon("edit"),
            t("ui.edit"),
          ),
          h(
            "button",
            {
              type: "button",
              class: "danger",
              onclick: async () => {
                if (!confirm(t("mine.confirm_delete"))) return;
                try {
                  await api(`/api/sounds/${encodeURIComponent(s.id)}`, { method: "DELETE", headers: { "X-Delete-Token": token } });
                  removeToken(s.id);
                  li.remove();
                  toast(t("mine.deleted"));
                } catch (e) {
                  toast(errorMessage(e.code));
                }
              },
            },
            icon("delete"),
            t("ui.delete"),
          ),
        ),
      );
    };
    show();
    list.append(li);
  }
  body.replaceChildren(list);
}
