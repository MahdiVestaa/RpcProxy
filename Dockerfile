FROM haproxy:2.9-alpine

RUN apk add --no-cache python3

WORKDIR /app

COPY generate_haproxy.py /app/generate_haproxy.py
COPY rpc_routes.json /app/rpc_routes.json
COPY entrypoint.sh /entrypoint.sh

RUN chmod +x /entrypoint.sh

EXPOSE 8080
EXPOSE 8404

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD wget -q -O- "http://127.0.0.1:8404/healthz" >/dev/null || exit 1

ENTRYPOINT ["/entrypoint.sh"]
