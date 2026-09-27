import { errorMessage, uploadForm } from "./api.js";
import { audioContext } from "./audio.js";
import { toast, wireDialog } from "./dom.js";
import { formatDuration, t } from "./i18n.js";
import { JAPAN_BOUNDS, locate, tileLayer } from "./mapview.js";
import { drawWave } from "./player.js";
import { saveToken } from "./mine.js";
import { TurnstileWidget } from "./turnstile.js";
import { currentTimeOfDay } from "./walk.js";

const L = window.L;
const MAX_SEC = 60;
const MIN_SEC = 5;
const MAX_BYTES = 20 * 1024 * 1024;
const STEPS = ["source", "trim", "place", "details", "license", "progress"];

const $ = (id) => document.getElementById(id);

function seasonOf(d) {
  const m = d.getMonth() + 1;
  if (m >= 3 && m <= 5) return "spring";
  if (m >= 6 && m <= 8) return "summer";
  if (m >= 9 && m <= 11) return "autumn";
  return "winter";
}

function toLocalInput(d) {
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function computePeaks(buffer, points = 400) {
  const data = buffer.getChannelData(0);
  const step = Math.max(1, Math.floor(data.length / points));
  const peaks = [];
  let top = 0;
  for (let i = 0; i < points; i++) {
    let m = 0;
    const end = Math.min(data.length, (i + 1) * step);
    for (let j = i * step; j < end; j++) {
      const v = Math.abs(data[j]);
      if (v > m) m = v;
    }
    peaks.push(m);
    if (m > top) top = m;
  }
  return peaks.map((p) => p / (top || 1));
}

async function encodeWav(buffer, start, end) {
  const rate = 48000;
  const channels = Math.min(2, buffer.numberOfChannels);
  const length = Math.max(1, Math.round((end - start) * rate));
  const off = new OfflineAudioContext(channels, length, rate);
  const src = off.createBufferSource();
  src.buffer = buffer;
  src.connect(off.destination);
  src.start(0, start, end - start);
  const out = await off.startRendering();
  const bytes = 44 + length * channels * 2;
  const view = new DataView(new ArrayBuffer(bytes));
  const str = (o, s) => [...s].forEach((c, i) => view.setUint8(o + i, c.charCodeAt(0)));
  str(0, "RIFF");
  view.setUint32(4, bytes - 8, true);
  str(8, "WAVE");
  str(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, channels, true);
  view.setUint32(24, rate, true);
  view.setUint32(28, rate * channels * 2, true);
  view.setUint16(32, channels * 2, true);
  view.setUint16(34, 16, true);
  str(36, "data");
  view.setUint32(40, length * channels * 2, true);
  const chData = Array.from({ length: channels }, (_, c) => out.getChannelData(c));
  let o = 44;
  for (let i = 0; i < length; i++) {
    for (let c = 0; c < channels; c++) {
      const v = Math.max(-1, Math.min(1, chData[c][i]));
      view.setInt16(o, v < 0 ? v * 0x8000 : v * 0x7fff, true);
      o += 2;
    }
  }
  return new Blob([view], { type: "audio/wav" });
}

export class PostFlow {
  constructor({ getMap }) {
    this.getMap = getMap;
    this.dialog = $("post-dialog");
    wireDialog(this.dialog);
    this.step = 0;
    this.buffer = null;
    this.rawFile = null;
    this.trim = { start: 0, end: 0 };
    this.recordedAt = new Date();
    this.dirty = { tod: false, season: false };
    this.turnstile = new TurnstileWidget($("post-turnstile"), "post");
    this.bind();
  }

  bind() {
    $("post-next").addEventListener("click", () => this.next());
    $("post-back").addEventListener("click", () => this.back());
    $("rec-btn").addEventListener("click", () => (this.recorder ? this.stopRecording() : this.startRecording()));
    $("file-input").addEventListener("change", (e) => this.onFile(e.target.files[0]));
    $("trim-play").addEventListener("click", () => this.previewTrim());
    $("trim-reset").addEventListener("click", () => {
      this.trim = { start: 0, end: Math.min(this.buffer.duration, MAX_SEC) };
      this.renderTrim();
    });
    $("post-locate").addEventListener("click", () => this.useCurrentLocation());
    $("f-recorded").addEventListener("change", () => {
      const d = new Date($("f-recorded").value);
      if (!Number.isNaN(d.getTime())) {
        this.recordedAt = d;
        this.autoFill();
      }
    });
    $("f-tod").addEventListener("change", () => (this.dirty.tod = true));
    $("f-season").addEventListener("change", () => (this.dirty.season = true));
    this.dialog.addEventListener("close", () => this.cleanup());
    this.bindTrimHandles();
  }

  open() {
    this.reset();
    this.dialog.showModal();
  }

  reset() {
    this.step = 0;
    this.buffer = null;
    this.rawFile = null;
    this.blob = null;
    this.place = null;
    this.submitting = false;
    this.recordedAt = new Date();
    this.dirty = { tod: false, season: false };
    for (const id of ["f-title", "f-comment", "f-weather", "f-direction"]) $(id).value = "";
    this.dialog.querySelectorAll('input[name="tags"]').forEach((c) => (c.checked = false));
    $("f-agree").checked = false;
    $("file-input").value = "";
    $("post-error").textContent = "";
    $("post-result").replaceChildren();
    $("upload-bar").style.width = "0";
    $("rec-time").textContent = `0:00 / ${formatDuration(MAX_SEC)}`;
    this.showStep();
  }

  cleanup() {
    if (this.recorder) this.stopRecording(true);
    this.previewSrc?.stop();
    this.events?.close();
  }

  showStep() {
    const name = STEPS[this.step];
    this.dialog.querySelectorAll("section[data-step]").forEach((s) => (s.hidden = s.dataset.step !== name));
    this.dialog.querySelectorAll(".steps li").forEach((li, i) => {
      li.className = i < this.step ? "is-done" : i === this.step ? "is-current" : "";
    });
    $("post-step-label").textContent = t("post.step_of", { n: this.step + 1, total: STEPS.length, name: t(`post.step.${name}`) });
    const back = $("post-back");
    const next = $("post-next");
    back.hidden = this.step === 0 || name === "progress";
    next.hidden = (name === "source" && !this.rawFile) || name === "progress";
    next.textContent = name === "license" ? t("post.submit") : t("ui.next");
    if (name === "trim") requestAnimationFrame(() => this.renderTrim());
    if (name === "place") this.initMiniMap();
    if (name === "details") {
      $("f-recorded").value = toLocalInput(this.recordedAt);
      this.autoFill();
      $("f-title").focus();
    }
    if (name === "license") this.turnstile.render().catch(() => ($("post-error").textContent = t("error.turnstile_load")));
  }

  autoFill() {
    if (!this.dirty.tod) $("f-tod").value = currentTimeOfDay(this.recordedAt);
    if (!this.dirty.season) $("f-season").value = seasonOf(this.recordedAt);
  }

  back() {
    if (this.step === 0) return;
    this.step -= 1;
    if (STEPS[this.step] === "trim" && !this.buffer) this.step -= 1;
    this.showStep();
  }

  async next() {
    const name = STEPS[this.step];
    if (name === "trim") {
      const len = this.trim.end - this.trim.start;
      if (len < MIN_SEC) return toast(t("error.too_short"));
      if (len > MAX_SEC + 0.01) return toast(t("post.trim_too_long"));
    }
    if (name === "place" && !this.place) return toast(t("post.place_required"));
    if (name === "details") {
      const title = $("f-title").value.trim();
      if (!title) {
        $("f-title").focus();
        return toast(t("post.title_required"));
      }
    }
    if (name === "license") return this.submit();
    this.step += 1;
    if (STEPS[this.step] === "trim" && !this.buffer) this.step += 1;
    this.showStep();
  }

  async startRecording() {
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false, channelCount: 2 },
      });
    } catch {
      toast(t("error.microphone"));
      return;
    }
    const mime = ["audio/webm;codecs=opus", "audio/mp4", "audio/ogg;codecs=opus", "audio/webm"].find(
      (m) => window.MediaRecorder && MediaRecorder.isTypeSupported(m),
    );
    const rec = new MediaRecorder(stream, mime ? { mimeType: mime, audioBitsPerSecond: 192000 } : undefined);
    const chunks = [];
    rec.ondataavailable = (e) => e.data.size && chunks.push(e.data);
    rec.onstop = async () => {
      stream.getTracks().forEach((tr) => tr.stop());
      if (this.discardRecording) return;
      // デコードできない形式でもサーバー送信前に止められるよう、録音時間で判定する
      if ((Date.now() - this.recStart.getTime()) / 1000 < MIN_SEC) {
        $("rec-time").textContent = `0:00 / ${formatDuration(MAX_SEC)}`;
        this.rejectBlob("error.too_short");
        return;
      }
      const blob = new Blob(chunks, { type: rec.mimeType || "audio/webm" });
      this.recordedAt = this.recStart;
      await this.loadBlob(blob);
    };
    const ac = audioContext();
    const srcNode = ac.createMediaStreamSource(stream);
    const analyser = ac.createAnalyser();
    analyser.fftSize = 2048;
    srcNode.connect(analyser);
    this.recorder = rec;
    this.discardRecording = false;
    this.recStart = new Date();
    rec.start(1000);
    const btn = $("rec-btn");
    btn.classList.add("is-recording");
    btn.setAttribute("aria-label", t("post.stop_record"));
    btn.firstElementChild.textContent = "stop";
    $("rec-hint").textContent = t("post.recording");

    const canvas = $("rec-canvas");
    const g = canvas.getContext("2d");
    const buf = new Float32Array(analyser.fftSize);
    const meter = $("rec-meter");
    const accent = getComputedStyle(document.documentElement).getPropertyValue("--accent").trim();
    const draw = () => {
      if (!this.recorder) {
        srcNode.disconnect();
        return;
      }
      analyser.getFloatTimeDomainData(buf);
      let peak = 0;
      for (const v of buf) peak = Math.max(peak, Math.abs(v));
      const pct = Math.min(100, Math.round(peak * 100));
      meter.firstElementChild.style.width = `${pct}%`;
      meter.setAttribute("aria-valuenow", String(pct));
      const dpr = window.devicePixelRatio || 1;
      canvas.width = canvas.clientWidth * dpr;
      canvas.height = canvas.clientHeight * dpr;
      g.setTransform(dpr, 0, 0, dpr, 0, 0);
      g.clearRect(0, 0, canvas.clientWidth, canvas.clientHeight);
      g.strokeStyle = accent;
      g.lineWidth = 2;
      g.beginPath();
      const w = canvas.clientWidth;
      const hh = canvas.clientHeight;
      for (let i = 0; i < buf.length; i += 4) {
        const x = (i / buf.length) * w;
        const y = hh / 2 + buf[i] * (hh / 2);
        if (i === 0) g.moveTo(x, y);
        else g.lineTo(x, y);
      }
      g.stroke();
      const elapsed = (Date.now() - this.recStart.getTime()) / 1000;
      $("rec-time").textContent = `${formatDuration(elapsed)} / ${formatDuration(MAX_SEC)}  (${t("post.remaining", { s: Math.max(0, Math.ceil(MAX_SEC - elapsed)) })})`;
      if (elapsed >= MAX_SEC) {
        this.stopRecording();
        return;
      }
      requestAnimationFrame(draw);
    };
    requestAnimationFrame(draw);
  }

  stopRecording(discard = false) {
    const rec = this.recorder;
    if (!rec) return;
    this.recorder = null;
    this.discardRecording = discard;
    if (rec.state !== "inactive") rec.stop();
    const btn = $("rec-btn");
    btn.classList.remove("is-recording");
    btn.setAttribute("aria-label", t("post.record"));
    btn.firstElementChild.textContent = "mic";
    $("rec-hint").textContent = t("post.record_hint");
  }

  async onFile(file) {
    if (!file) return;
    if (file.size > MAX_BYTES && file.size > 200 * 1024 * 1024) {
      toast(t("error.too_large"));
      return;
    }
    this.recordedAt = file.lastModified ? new Date(file.lastModified) : new Date();
    await this.loadBlob(file);
  }

  async loadBlob(blob) {
    this.rawFile = blob;
    this.buffer = null;
    try {
      const ab = await blob.arrayBuffer();
      this.buffer = await audioContext().decodeAudioData(ab);
    } catch {
      this.buffer = null;
    }
    if (this.buffer) {
      if (this.buffer.duration < MIN_SEC) {
        this.rejectBlob("error.too_short");
        return;
      }
      this.peaks = computePeaks(this.buffer);
      this.trim = { start: 0, end: Math.min(this.buffer.duration, MAX_SEC) };
      this.step = 1;
    } else {
      // ブラウザで読めない形式はそのまま送り、サーバー側で判定・カットする
      if (blob.size > MAX_BYTES) {
        this.rejectBlob("error.too_large");
        return;
      }
      toast(t("post.no_preview"));
      this.step = 2;
    }
    this.showStep();
  }

  // 使えない音声は破棄し、前に読み込んだ音声で先へ進めないようにする
  rejectBlob(key) {
    this.rawFile = null;
    this.buffer = null;
    $("file-input").value = "";
    toast(t(key));
    this.showStep();
  }

  renderTrim() {
    if (!this.buffer) return;
    const d = this.buffer.duration;
    const s = this.trim.start / d;
    const e = this.trim.end / d;
    drawWave($("trim-canvas"), this.peaks, 1, { start: s, end: e });
    $("trim-shade-l").style.cssText = `left:0;width:${s * 100}%`;
    $("trim-shade-r").style.cssText = `left:${e * 100}%;right:0`;
    for (const [id, v] of [["trim-start", this.trim.start], ["trim-end", this.trim.end]]) {
      const el = $(id);
      el.style.left = `${(v / d) * 100}%`;
      el.setAttribute("aria-valuemax", String(Math.round(d)));
      el.setAttribute("aria-valuenow", v.toFixed(1));
      el.setAttribute("aria-valuetext", formatDuration(v));
    }
    const len = this.trim.end - this.trim.start;
    $("trim-info").textContent = t("post.trim_info", {
      start: formatDuration(this.trim.start),
      end: formatDuration(this.trim.end),
      len: len.toFixed(1),
    });
  }

  setTrim(which, value) {
    const d = this.buffer.duration;
    value = Math.max(0, Math.min(d, value));
    if (which === "start") {
      this.trim.start = Math.min(value, this.trim.end - MIN_SEC);
      if (this.trim.end - this.trim.start > MAX_SEC) this.trim.end = this.trim.start + MAX_SEC;
    } else {
      this.trim.end = Math.max(value, this.trim.start + MIN_SEC);
      if (this.trim.end - this.trim.start > MAX_SEC) this.trim.start = this.trim.end - MAX_SEC;
    }
    this.trim.start = Math.max(0, this.trim.start);
    this.trim.end = Math.min(d, this.trim.end);
    this.renderTrim();
  }

  bindTrimHandles() {
    const box = $("trim");
    for (const which of ["start", "end"]) {
      const el = $(`trim-${which}`);
      let drag = false;
      el.addEventListener("pointerdown", (e) => {
        drag = true;
        el.setPointerCapture(e.pointerId);
      });
      el.addEventListener("pointermove", (e) => {
        if (!drag || !this.buffer) return;
        const r = box.getBoundingClientRect();
        this.setTrim(which, ((e.clientX - r.left) / r.width) * this.buffer.duration);
      });
      el.addEventListener("pointerup", () => (drag = false));
      el.addEventListener("keydown", (e) => {
        if (!this.buffer) return;
        const delta = { ArrowLeft: -0.5, ArrowRight: 0.5, ArrowDown: -0.5, ArrowUp: 0.5, PageDown: -5, PageUp: 5 }[e.key];
        if (delta === undefined) return;
        e.preventDefault();
        this.setTrim(which, this.trim[which] + delta);
      });
    }
  }

  previewTrim() {
    if (!this.buffer) return;
    if (this.previewSrc) {
      this.previewSrc.stop();
      this.previewSrc = null;
      return;
    }
    const ac = audioContext();
    const src = ac.createBufferSource();
    src.buffer = this.buffer;
    const g = ac.createGain();
    const len = this.trim.end - this.trim.start;
    g.gain.setValueAtTime(0, ac.currentTime);
    g.gain.linearRampToValueAtTime(1, ac.currentTime + 0.3);
    g.gain.setValueAtTime(1, ac.currentTime + Math.max(0.3, len - 0.3));
    g.gain.linearRampToValueAtTime(0, ac.currentTime + len);
    src.connect(g).connect(ac.destination);
    src.start(0, this.trim.start, len);
    src.onended = () => {
      if (this.previewSrc === src) this.previewSrc = null;
    };
    this.previewSrc = src;
  }

  initMiniMap() {
    if (!this.miniMap) {
      const main = this.getMap();
      this.miniMap = L.map("post-map", {
        center: main.getCenter(),
        zoom: Math.max(main.getZoom(), 12),
        minZoom: 5,
        maxZoom: 18,
        maxBounds: JAPAN_BOUNDS,
      });
      this.miniMap.attributionControl.setPrefix(false);
      tileLayer("pale").addTo(this.miniMap);
      this.miniMap.on("click", (e) => this.setPlace(e.latlng));
      this.addLocateControl();
    }
    setTimeout(() => this.miniMap.invalidateSize(), 50);
  }

  addLocateControl() {
    const LocateControl = L.Control.extend({
      options: { position: "topright" },
      onAdd: () => {
        const btn = L.DomUtil.create("button", "map-locate-btn");
        btn.type = "button";
        btn.title = t("post.use_location");
        btn.setAttribute("aria-label", t("post.use_location"));
        const ic = L.DomUtil.create("span", "material-symbols-outlined", btn);
        ic.setAttribute("aria-hidden", "true");
        ic.textContent = "my_location";
        L.DomEvent.disableClickPropagation(btn);
        L.DomEvent.on(btn, "click", () => this.useCurrentLocation());
        return btn;
      },
    });
    new LocateControl().addTo(this.miniMap);
  }

  async useCurrentLocation() {
    const ll = await locate(this.miniMap, 16);
    if (ll) this.setPlace(L.latLng(ll));
  }

  setPlace(latlng) {
    if (!JAPAN_BOUNDS.contains(latlng)) {
      toast(t("error.outside_japan"));
      return;
    }
    this.place = latlng;
    if (this.placeMarker) this.placeMarker.setLatLng(latlng);
    else {
      this.placeMarker = L.marker(latlng, {
        icon: L.divIcon({ className: "pin", html: "", iconSize: [18, 18] }),
        draggable: true,
        title: t("post.place_marker"),
      }).addTo(this.miniMap);
      this.placeMarker.on("dragend", () => this.setPlace(this.placeMarker.getLatLng()));
    }
    $("post-coords").textContent = `${latlng.lat.toFixed(5)}, ${latlng.lng.toFixed(5)}`;
  }

  async submit() {
    if (this.submitting) return;
    const err = $("post-error");
    err.textContent = "";
    if (!$("f-agree").checked) {
      err.textContent = t("post.agree_required");
      return;
    }
    const token = this.turnstile.getToken();
    if (!token) {
      err.textContent = t("error.turnstile_pending");
      return;
    }
    this.submitting = true;
    let file;
    try {
      file = this.buffer ? await encodeWav(this.buffer, this.trim.start, this.trim.end) : this.rawFile;
    } catch {
      file = this.rawFile;
    }
    if (file.size > MAX_BYTES) {
      err.textContent = t("error.too_large");
      this.submitting = false;
      return;
    }
    const fd = new FormData();
    fd.append("file", file, this.buffer ? "audio.wav" : "audio");
    fd.append("title", $("f-title").value.trim());
    fd.append("comment", $("f-comment").value.trim());
    fd.append("recorded_at", this.recordedAt.toISOString());
    fd.append("time_of_day", $("f-tod").value);
    fd.append("season", $("f-season").value);
    fd.append("weather", $("f-weather").value);
    fd.append("direction", $("f-direction").value);
    this.dialog.querySelectorAll('input[name="tags"]:checked').forEach((c) => fd.append("tags", c.value));
    fd.append("license", this.dialog.querySelector('input[name="license"]:checked').value);
    fd.append("precision", this.dialog.querySelector('input[name="precision"]:checked').value);
    fd.append("lat", String(this.place.lat));
    fd.append("lng", String(this.place.lng));
    fd.append("agree", "true");
    fd.append("cf-turnstile-response", token);

    this.step = STEPS.indexOf("progress");
    this.showStep();
    this.setStatus("upload");
    let res;
    try {
      res = await uploadForm("/api/sounds", fd, (p) => ($("upload-bar").style.width = `${Math.round(p * 100)}%`));
    } catch (e) {
      this.turnstile.reset();
      this.submitting = false;
      this.step = STEPS.indexOf("license");
      this.showStep();
      err.textContent = errorMessage(e.code);
      return;
    }
    this.turnstile.reset();
    saveToken(res.id, res.delete_token);
    this.setStatus("processing");
    this.watch(res);
  }

  setStatus(active) {
    const order = ["upload", "processing", "done"];
    const idx = order.indexOf(active);
    $("post-status").querySelectorAll("li").forEach((li) => {
      const i = order.indexOf(li.dataset.s);
      li.className = i < idx ? "is-done" : i === idx ? "is-active" : "";
    });
  }

  watch(res) {
    const result = $("post-result");
    const done = (state, reason, warnings = []) => {
      this.events?.close();
      this.events = null;
      this.submitting = false;
      const nodes = [];
      if (state === "published") {
        this.setStatus("done");
        nodes.push(Object.assign(document.createElement("p"), { textContent: t("post.done.published") }));
        const a = Object.assign(document.createElement("a"), { className: "btn btn-primary", href: res.page_url, textContent: t("sound.page") });
        nodes.push(a);
      } else if (state === "pending_review") {
        this.setStatus("done");
        nodes.push(Object.assign(document.createElement("p"), { textContent: t("post.done.pending") }));
      } else {
        const p = Object.assign(document.createElement("p"), { className: "notice warn", textContent: `${t("post.done.failed")} ${errorMessage(reason)}` });
        nodes.push(p);
      }
      if (warnings.includes("clipping")) {
        nodes.push(Object.assign(document.createElement("p"), { className: "notice warn", textContent: t("post.warn.clipping") }));
      }
      result.replaceChildren(...nodes);
      $("post-back").hidden = true;
    };
    const es = new EventSource(res.events_url);
    this.events = es;
    es.addEventListener("status", (e) => {
      const d = JSON.parse(e.data);
      if (["published", "pending_review", "rejected", "failed"].includes(d.state)) done(d.state, d.reason, d.warnings || []);
      else if (d.retrying) result.textContent = t("post.retrying", { n: d.attempt });
    });
  }
}
