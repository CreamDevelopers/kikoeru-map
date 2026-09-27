import { boot } from "./dom.js";

let loading = null;

function load() {
  if (window.turnstile) return Promise.resolve(window.turnstile);
  if (loading) return loading;
  loading = new Promise((resolve, reject) => {
    window.__kkTurnstileReady = () => resolve(window.turnstile);
    const s = document.createElement("script");
    s.src = "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit&onload=__kkTurnstileReady";
    s.async = true;
    const nonce = document.querySelector("script[nonce]")?.nonce;
    if (nonce) s.nonce = nonce;
    s.onerror = () => reject(new Error("turnstile load failed"));
    document.head.append(s);
  });
  return loading;
}

export class TurnstileWidget {
  constructor(container, action) {
    this.container = container;
    this.action = action;
    this.id = null;
    this.token = "";
  }

  async render() {
    const ts = await load();
    if (this.id !== null) {
      this.reset();
      return;
    }
    this.id = ts.render(this.container, {
      sitekey: boot().turnstile_site_key,
      action: this.action,
      language: document.documentElement.lang || "auto",
      callback: (token) => {
        this.token = token;
      },
      "expired-callback": () => {
        this.token = "";
      },
      "error-callback": () => {
        this.token = "";
      },
    });
  }

  getToken() {
    if (this.id !== null && window.turnstile) {
      this.token = window.turnstile.getResponse(this.id) || this.token;
    }
    return this.token;
  }

  reset() {
    this.token = "";
    if (this.id !== null && window.turnstile) window.turnstile.reset(this.id);
  }
}
