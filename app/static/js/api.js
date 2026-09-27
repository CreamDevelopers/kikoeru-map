import { t } from "./i18n.js";

export class ApiError extends Error {
  constructor(status, code, detail) {
    super(code);
    this.status = status;
    this.code = code;
    this.detail = detail;
  }
}

export function errorMessage(code) {
  const key = `error.${code}`;
  const msg = t(key);
  return msg === key ? t("error.generic") : msg;
}

export async function api(path, { method = "GET", body, headers = {}, signal, json = true } = {}) {
  const opts = { method, headers: { Accept: "application/json", ...headers }, signal, credentials: "same-origin" };
  if (body !== undefined) {
    if (body instanceof FormData) opts.body = body;
    else {
      opts.body = JSON.stringify(body);
      opts.headers["Content-Type"] = "application/json";
    }
  }
  const res = await fetch(path, opts);
  if (res.status === 204) return null;
  let data = null;
  if (json) {
    try {
      data = await res.json();
    } catch {
      data = null;
    }
  }
  if (!res.ok) {
    const err = data?.error || {};
    throw new ApiError(res.status, err.code || (res.status === 429 ? "rate_limited" : "generic"), err);
  }
  return data;
}

export function uploadForm(path, form, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", path);
    xhr.setRequestHeader("Accept", "application/json");
    xhr.responseType = "json";
    xhr.upload.addEventListener("progress", (e) => {
      if (e.lengthComputable) onProgress?.(e.loaded / e.total);
    });
    xhr.addEventListener("load", () => {
      const data = xhr.response;
      if (xhr.status >= 200 && xhr.status < 300) resolve(data);
      else reject(new ApiError(xhr.status, data?.error?.code || "generic", data?.error));
    });
    xhr.addEventListener("error", () => reject(new ApiError(0, "network")));
    xhr.send(form);
  });
}
