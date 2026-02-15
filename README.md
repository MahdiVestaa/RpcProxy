# RPC Gateway (HAProxy)

This project generates an HAProxy configuration for routing JSON-RPC and WebSocket RPC traffic using this path format:

- `/{service}/{provider}/{chain}/`

Example:

- `/da/alchemy/arbitrum/`
- `/killswitch/quicknode/arbitrum/`

The generated proxy routes requests to upstream RPC providers like drpc, alchemy, dwellir, and quicknode.

## Project Files

- `rpc_routes.json`: source of truth for routing, templates, keys, and runtime settings.
- `generate_haproxy.py`: builds `haproxy.cfg` from `rpc_routes.json`.
- `haproxy.cfg`: generated HAProxy config (ignored by git).
- `Dockerfile`: container image for generator + HAProxy.
- `entrypoint.sh`: generates config, validates it, and starts HAProxy.
- `docker-compose.yml`: local run setup.

## Current Behavior

- Frontend listens on `:8080` for RPC traffic.
- Metrics frontend listens on `:8404`.
- Health endpoint is `/healthz`.
- Prometheus metrics endpoint is `/metrics`.
- Source IP restrictions:
  - RPC frontend allows only `10.10.0.0/16`.
  - Metrics frontend allows `10.10.0.0/16` and `138.199.220.100`.
- HAProxy is multi-threaded (defaults to available CPU cores).
- Transport policy: frontend accepts `http/ws`, and upstream provider traffic is `https/wss`.

## JSON Structure (`rpc_routes.json`)

Top-level keys:

- `bind`: RPC bind address, default example `*:8080`
- `metrics_bind`: metrics bind address, default example `*:8404`
- `health_path`: health endpoint path
- `metrics_path`: Prometheus endpoint path
- `keys`: named credentials/config sets (reusable)
- `provider_chain_templates`: provider/chain URL and optional header templates
- `services`: service routing using provider blocks

### 1) `keys`

`keys` stores user-defined named configurations.  
Each entry contains:

- `provider` (required): which provider this key belongs to
- any other token fields required by templates (`api_key`, `token`, `auth_token`, `endpoint`, etc.)

Example:

```json
"keys": {
  "da_quicknode": {
    "provider": "quicknode",
    "endpoint": "your-quicknode-endpoint",
    "token": "quicknode_token_da"
  }
}
```

### 2) `provider_chain_templates`

Defines how each provider+chain builds upstream URL(s) and headers.

- Required: `rpc_url`
- Optional: `ws_url`
- Optional: `headers`
- Placeholders use `{{name}}` and are resolved from the selected key.

Example:

```json
"quicknode": {
  "base": {
    "rpc_url": "https://{{endpoint}}.quiknode.pro/{{token}}/",
    "ws_url": "wss://{{endpoint}}.quiknode.pro/{{token}}/"
  }
}
```

### 3) `services`

Service routing configuration:

- `services.<service>.<provider>.key`: name of an entry in `keys`
- `services.<service>.<provider>.chains`: array of enabled chain names

Example:

```json
"services": {
  "da": {
    "quicknode": {
      "key": "da_quicknode",
      "chains": ["base"]
    }
  }
}
```

## Generate Config

From project root:

```bash
python3 generate_haproxy.py
```

Custom paths:

```bash
python3 generate_haproxy.py --config rpc_routes.json --out haproxy.cfg
```

## Run with Docker Compose

```bash
docker compose up --build
```

Useful endpoints:

- Health: `http://localhost:8404/healthz`
- Metrics: `http://localhost:8404/metrics`
- RPC route example: `http://localhost:8080/da/alchemy/arbitrum/`

## How to Add Another RPC Provider

Follow this order:

1. Add one or more templates under `provider_chain_templates.<new_provider>.<chain>`.
   - Include `rpc_url`.
   - Add `ws_url` if provider supports websocket.
   - Add `headers` if auth must be sent in HTTP headers.
2. Decide required tokens (`api_key`, `endpoint`, etc.) and place placeholders in template values as `{{token_name}}`.
3. Add one or more named entries under `keys` with:
   - `provider: "<new_provider>"`
   - token values needed by placeholders.
4. Wire services by editing `services.<service>.<new_provider>`:
   - set `key`
   - set `chains` array.
5. Regenerate and validate:
   - `python3 generate_haproxy.py`
   - (optional) `docker compose up --build` and test routes.

## How to Add Another Service

1. Add a new service object in `services`, e.g. `services.newservice`.
2. For each provider this service should use:
   - reference an existing key (or create a new key in `keys`),
   - choose enabled chains via `chains` array.
3. Regenerate:
   - `python3 generate_haproxy.py`
4. Test route format:
   - `/{newservice}/{provider}/{chain}/`

## Validation Rules (Generator)

The generator enforces:

- identifiers (`service/provider/chain`) must be alphanumeric + `_` or `-`.
- every service provider block must define `key` and `chains` (array).
- `key` must exist in `keys`.
- key `provider` must match the service provider name.
- placeholders in templates must be fully resolvable from selected key fields.
- enabled chains must exist in the provider templates.

## Error Monitoring in Grafana

You can detect `5xx` errors in Grafana when Prometheus scrapes HAProxy metrics from `/metrics`.

- Use backend `5xx` metrics to catch upstream/provider failures.
- Use frontend `5xx` metrics to see what clients experience.
- Build panels/alerts with PromQL rates over a window (for example 5 minutes).

Example PromQL patterns (metric names can vary by HAProxy build/exporter labels):

```promql
sum(rate(<frontend_or_backend_response_metric>{code="5xx"}[5m]))
```

```promql
sum by (backend) (rate(<frontend_or_backend_response_metric>{code="5xx"}[5m]))
```

Tip: open Prometheus expression browser first and search for `haproxy` + `5xx` to confirm the exact metric names and labels in your environment.

## Notes

- `haproxy.cfg` is generated; do not edit it manually for long-term changes.
- Keep secrets in `rpc_routes.json` only if this repository is private and access-controlled.
- If you later want external secret management, we can add an optional encrypted/templated workflow.
