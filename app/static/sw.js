const VERSION = "__VERSION__";
const SHELL = __SHELL__;
const STATIC_CACHE = `static-${VERSION}`;
const API_CACHE = "api-v1";
const AUDIO_CACHE = "audio-v1";
const TILE_CACHE = "tiles-v1";
const PAGE_CACHE = "pages-v1";

const LIMITS = {
  [API_CACHE]: 80,
  [AUDIO_CACHE]: 60,
  // 国土地理院の利用規約に配慮して、タイルは少量・短期間だけ保持する
  [TILE_CACHE]: 500,
  [PAGE_CACHE]: 20,
};
const TILE_MAX_AGE_MS = 7 * 24 * 60 * 60 * 1000;

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(STATIC_CACHE)
      .then((c) => c.addAll(SHELL))
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k.startsWith("static-") && k !== STATIC_CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

async function trim(cacheName) {
  const limit = LIMITS[cacheName];
  if (!limit) return;
  const cache = await caches.open(cacheName);
  const keys = await cache.keys();
  for (let i = 0; i < keys.length - limit; i++) await cache.delete(keys[i]);
}

async function putLimited(cacheName, request, response) {
  const cache = await caches.open(cacheName);
  await cache.delete(request);
  await cache.put(request, response);
  await trim(cacheName);
}

async function cacheFirst(request, cacheName) {
  const cached = await caches.match(request);
  if (cached) return cached;
  const res = await fetch(request);
  if (res.ok) (await caches.open(cacheName)).put(request, res.clone());
  return res;
}

async function networkFirst(request, cacheName) {
  try {
    const res = await fetch(request);
    if (res.ok) putLimited(cacheName, request, res.clone());
    return res;
  } catch (e) {
    const cached = await caches.match(request);
    if (cached) return cached;
    throw e;
  }
}

// audio 要素は Range で要求してくるので、全体をキャッシュして要求範囲を切り出して返す
async function audioResponse(request) {
  const url = new URL(request.url);
  const key = url.origin + url.pathname;
  const cache = await caches.open(AUDIO_CACHE);
  let full = await cache.match(key);
  if (!full) {
    const res = await fetch(key, { credentials: "same-origin" });
    if (!res.ok || res.status !== 200) return res;
    full = res.clone();
    putLimited(AUDIO_CACHE, key, res);
  } else {
    putLimited(AUDIO_CACHE, key, full.clone());
  }
  const range = request.headers.get("range");
  const blob = await full.blob();
  const type = full.headers.get("content-type") || "application/octet-stream";
  if (!range) {
    return new Response(blob, { status: 200, headers: { "Content-Type": type, "Content-Length": String(blob.size), "Accept-Ranges": "bytes" } });
  }
  const m = /bytes=(\d*)-(\d*)/.exec(range);
  let start = m && m[1] ? Number(m[1]) : 0;
  let end = m && m[2] ? Number(m[2]) : blob.size - 1;
  if (m && !m[1] && m[2]) {
    start = Math.max(0, blob.size - Number(m[2]));
    end = blob.size - 1;
  }
  end = Math.min(end, blob.size - 1);
  if (start > end) return new Response(null, { status: 416, headers: { "Content-Range": `bytes */${blob.size}` } });
  return new Response(blob.slice(start, end + 1), {
    status: 206,
    headers: {
      "Content-Type": type,
      "Content-Length": String(end - start + 1),
      "Content-Range": `bytes ${start}-${end}/${blob.size}`,
      "Accept-Ranges": "bytes",
    },
  });
}

async function tileResponse(request) {
  const cache = await caches.open(TILE_CACHE);
  const cached = await cache.match(request);
  if (cached) {
    const at = Number(cached.headers.get("x-sw-cached-at") || 0);
    if (Date.now() - at < TILE_MAX_AGE_MS) return cached;
  }
  try {
    const res = await fetch(request);
    if (res.ok && res.type !== "opaque") {
      const headers = new Headers(res.headers);
      headers.set("x-sw-cached-at", String(Date.now()));
      const body = await res.clone().blob();
      putLimited(TILE_CACHE, request, new Response(body, { status: 200, headers }));
    }
    return res;
  } catch (e) {
    if (cached) return cached;
    throw e;
  }
}

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);

  if (url.hostname === "cyberjapandata.gsi.go.jp") {
    event.respondWith(tileResponse(req));
    return;
  }
  if (url.origin !== self.location.origin) return;

  if (url.pathname.startsWith("/admin") || url.pathname === "/metrics" || url.pathname.endsWith("/events") || url.pathname === "/api/stream") return;
  if (url.pathname.startsWith("/api/game/")) return;

  if (url.pathname.startsWith("/media/")) {
    if (/\.(webm|m4a)$/.test(url.pathname)) event.respondWith(audioResponse(req));
    else event.respondWith(cacheFirst(req, AUDIO_CACHE));
    return;
  }
  if (url.pathname.startsWith("/static/")) {
    event.respondWith(url.searchParams.has("v") ? cacheFirst(req, STATIC_CACHE) : networkFirst(req, STATIC_CACHE));
    return;
  }
  if (url.pathname.startsWith("/api/")) {
    event.respondWith(networkFirst(req, API_CACHE));
    return;
  }
  if (req.mode === "navigate") {
    event.respondWith(
      networkFirst(req, PAGE_CACHE).catch(async () => (await caches.match("/", { ignoreSearch: true })) || Response.error()),
    );
  }
});
