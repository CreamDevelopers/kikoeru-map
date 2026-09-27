FROM cloudflare/cloudflared:2025.9.1 AS cloudflared

FROM debian:bookworm-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --system --uid 65532 --home-dir /nonexistent --shell /usr/sbin/nologin cloudflared
COPY --from=cloudflared /usr/local/bin/cloudflared /usr/local/bin/cloudflared
COPY entrypoint.sh healthcheck.sh /usr/local/bin/
USER cloudflared
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
