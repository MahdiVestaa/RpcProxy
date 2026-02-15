#!/bin/sh
set -eu

python3 /app/generate_haproxy.py \
  --config /app/rpc_routes.json \
  --out /usr/local/etc/haproxy/haproxy.cfg

haproxy -c -f /usr/local/etc/haproxy/haproxy.cfg
exec haproxy -W -db -f /usr/local/etc/haproxy/haproxy.cfg
