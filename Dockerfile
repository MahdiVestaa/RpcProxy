FROM python:3.12-alpine AS config-builder

WORKDIR /app
COPY generate_haproxy.py /app/generate_haproxy.py
COPY rpc_routes.json /app/rpc_routes.json
RUN python3 /app/generate_haproxy.py --config /app/rpc_routes.json --out /app/haproxy.cfg

FROM haproxy:2.9-alpine

COPY --from=config-builder /app/haproxy.cfg /usr/local/etc/haproxy/haproxy.cfg

EXPOSE 8080
EXPOSE 8404

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD wget -q -O- "http://127.0.0.1:8404/healthz" >/dev/null || exit 1
