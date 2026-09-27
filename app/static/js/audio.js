let ctx = null;

export function audioContext() {
  if (!ctx) {
    const AC = window.AudioContext || window.webkitAudioContext;
    ctx = new AC({ latencyHint: "playback" });
  }
  if (ctx.state === "suspended") ctx.resume().catch(() => {});
  return ctx;
}

let opusSupport = null;
export function sourceUrl(sound) {
  if (opusSupport === null) {
    const a = document.createElement("audio");
    opusSupport = a.canPlayType('audio/webm; codecs="opus"') !== "";
  }
  return (opusSupport && sound.webm_url) || sound.m4a_url || sound.webm_url;
}

export function gameSourceUrl(audio) {
  if (opusSupport === null) sourceUrl({});
  return opusSupport ? audio.webm : audio.m4a;
}

export const FADE = 0.8;

export class Track extends EventTarget {
  constructor(url, { loop = false, analyser = true, output = null, preload = "auto" } = {}) {
    super();
    const ac = audioContext();
    this.el = new Audio();
    this.el.preload = preload;
    this.el.loop = loop;
    this.el.src = url;
    this.volume = 1;
    this.source = ac.createMediaElementSource(this.el);
    this.gain = ac.createGain();
    this.gain.gain.value = 0;
    this.source.connect(this.gain);
    if (analyser) {
      this.analyser = ac.createAnalyser();
      this.analyser.fftSize = 1024;
      this.buf = new Float32Array(this.analyser.fftSize);
      this.gain.connect(this.analyser);
    }
    this.output = output || ac.destination;
    this.gain.connect(this.output);
    this.stopTimer = 0;
    for (const ev of ["play", "pause", "ended", "timeupdate", "loadedmetadata", "waiting", "playing", "error"]) {
      this.el.addEventListener(ev, () => this.dispatchEvent(new Event(ev)));
    }
  }

  get duration() {
    return Number.isFinite(this.el.duration) ? this.el.duration : 0;
  }
  get currentTime() {
    return this.el.currentTime;
  }
  get paused() {
    return this.el.paused;
  }

  seek(t) {
    const d = this.duration;
    this.el.currentTime = Math.max(0, d ? Math.min(t, d - 0.05) : t);
  }

  rampTo(value, seconds) {
    const ac = audioContext();
    const g = this.gain.gain;
    const now = ac.currentTime;
    g.cancelScheduledValues(now);
    g.setValueAtTime(g.value, now);
    if (seconds <= 0) g.setValueAtTime(value, now);
    else g.linearRampToValueAtTime(value, now + seconds);
  }

  async play(fade = FADE) {
    clearTimeout(this.stopTimer);
    audioContext();
    await this.el.play();
    this.rampTo(this.volume, fade);
  }

  setVolume(v, seconds = 0.08) {
    this.volume = v;
    if (!this.el.paused) this.rampTo(v, seconds);
  }

  pause(fade = FADE) {
    return new Promise((resolve) => {
      this.rampTo(0, fade);
      clearTimeout(this.stopTimer);
      this.stopTimer = setTimeout(() => {
        this.el.pause();
        resolve();
      }, fade * 1000);
    });
  }

  toggle() {
    if (this.el.paused) return this.play();
    return this.pause(0.3);
  }

  level() {
    if (!this.analyser || this.el.paused) return 0;
    this.analyser.getFloatTimeDomainData(this.buf);
    let sum = 0;
    for (let i = 0; i < this.buf.length; i++) sum += this.buf[i] * this.buf[i];
    return Math.min(1, Math.sqrt(sum / this.buf.length) * 4);
  }

  destroy() {
    clearTimeout(this.stopTimer);
    try {
      this.el.pause();
      this.el.removeAttribute("src");
      this.el.load();
      this.source.disconnect();
      this.gain.disconnect();
      this.analyser?.disconnect();
    } catch {}
  }
}

export const nowPlaying = {
  track: null,
  sound: null,
  onNext: null,
  listeners: new Set(),
  set(track, sound, { onNext = null } = {}) {
    this.track = track;
    this.sound = sound;
    this.onNext = onNext;
    for (const fn of this.listeners) fn(this);
    updateMediaSession(this);
  },
  clear(track) {
    if (track && this.track !== track) return;
    this.track = null;
    this.sound = null;
    this.onNext = null;
    for (const fn of this.listeners) fn(this);
    updateMediaSession(this);
  },
  subscribe(fn) {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  },
};

function updateMediaSession(np) {
  if (!("mediaSession" in navigator)) return;
  const ms = navigator.mediaSession;
  if (!np.track || !np.sound) {
    ms.metadata = null;
    ms.playbackState = "none";
    return;
  }
  const s = np.sound;
  const place = [s.pref_name, s.city_name].filter(Boolean).join(" ");
  ms.metadata = new MediaMetadata({
    title: s.title || "",
    artist: place,
    album: document.title,
    artwork: [{ src: "/static/icons/icon-512.png", sizes: "512x512", type: "image/png" }],
  });
  const tr = np.track;
  const handlers = {
    play: () => tr.play(),
    pause: () => tr.pause(0.3),
    seekbackward: () => tr.seek(tr.currentTime - 10),
    seekforward: () => tr.seek(tr.currentTime + 10),
    seekto: (d) => tr.seek(d.seekTime),
    nexttrack: np.onNext ? () => np.onNext() : null,
  };
  for (const [action, fn] of Object.entries(handlers)) {
    try {
      ms.setActionHandler(action, fn);
    } catch {}
  }
  const sync = () => {
    ms.playbackState = tr.paused ? "paused" : "playing";
    if (tr.duration && ms.setPositionState) {
      try {
        ms.setPositionState({ duration: tr.duration, position: Math.min(tr.currentTime, tr.duration), playbackRate: 1 });
      } catch {}
    }
  };
  tr.addEventListener("play", sync);
  tr.addEventListener("pause", sync);
  tr.addEventListener("loadedmetadata", sync);
  sync();
}

export function countPlay(soundId) {
  fetch(`/api/sounds/${encodeURIComponent(soundId)}/play`, { method: "POST", keepalive: true }).catch(() => {});
}
