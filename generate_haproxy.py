#!/usr/bin/env python3
import argparse
import json
import re
from dataclasses import dataclass
from typing import Dict, List, Tuple
from urllib.parse import urlparse


IDENT_RE = re.compile(r"^[a-zA-Z0-9_-]+$")
PLACEHOLDER_RE = re.compile(r"\{\{([A-Za-z_][A-Za-z0-9_]*)\}\}")


@dataclass(frozen=True)
class Target:
    transport: str
    host: str
    port: int
    path: str
    headers: Dict[str, str]


@dataclass(frozen=True)
class Route:
    service: str
    provider: str
    chain: str
    rpc: Target
    ws: Target | None

    @property
    def route_path(self) -> str:
        return f"/{self.service}/{self.provider}/{self.chain}"

    @property
    def key(self) -> str:
        return f"{self.service}__{self.provider}__{self.chain}"


def validate_ident(value: str, kind: str) -> None:
    if not IDENT_RE.match(value):
        raise ValueError(
            f"Invalid {kind} '{value}'. Allowed characters: letters, digits, underscore, hyphen."
        )


def resolve_placeholders(value: str, tokens: Dict[str, str], context: str) -> str:
    def repl(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in tokens:
            raise ValueError(f"Missing token '{key}' while resolving {context}")
        return str(tokens[key])

    resolved = PLACEHOLDER_RE.sub(repl, value)
    unresolved = PLACEHOLDER_RE.findall(resolved)
    if unresolved:
        raise ValueError(
            f"Unresolved placeholders {sorted(set(unresolved))} while resolving {context}"
        )
    return resolved


def parse_target(
    url_value: str,
    headers: Dict[str, str] | None,
    allowed_schemes: set[str],
    context: str,
) -> Target:
    parsed = urlparse(url_value)
    if parsed.scheme not in allowed_schemes:
        allowed_display = "/".join(sorted(allowed_schemes))
        raise ValueError(
            f"Unsupported scheme in {context}: '{url_value}'. Allowed: {allowed_display}"
        )
    if not parsed.hostname:
        raise ValueError(f"Missing hostname in URL: '{url_value}'")

    if parsed.port:
        port = parsed.port
    elif parsed.scheme in {"https", "wss"}:
        port = 443
    else:
        port = 80

    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"

    return Target(
        transport=parsed.scheme,
        host=parsed.hostname,
        port=port,
        path=path,
        headers=headers or {},
    )


def load_routes(config_path: str) -> Tuple[str, str, str, str, List[Route]]:
    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    bind = config.get("bind", "*:8080")
    metrics_bind = config.get("metrics_bind", "*:8404")
    health_path = config.get("health_path", "/healthz")
    metrics_path = config.get("metrics_path", "/metrics")
    keys = config.get("keys")
    templates = config.get("provider_chain_templates")
    services = config.get("services")
    if not isinstance(keys, dict) or not keys:
        raise ValueError("'keys' must be a non-empty object")
    if not isinstance(templates, dict) or not templates:
        raise ValueError("'provider_chain_templates' must be a non-empty object")
    if not isinstance(services, dict) or not services:
        raise ValueError("'services' must be a non-empty object")

    seen = set()
    routes: List[Route] = []

    for service, providers in sorted(services.items()):
        validate_ident(service, "service")
        if not isinstance(providers, dict):
            raise ValueError(f"Service '{service}' must map to an object of providers")

        for provider, provider_cfg in sorted(providers.items()):
            validate_ident(provider, "provider")
            if not isinstance(provider_cfg, dict):
                raise ValueError(
                    f"Service '{service}', provider '{provider}' must map to an object"
                )
            key_name = provider_cfg.get("key")
            if not isinstance(key_name, str) or not key_name:
                raise ValueError(
                    f"Missing/invalid key at services.{service}.{provider}.key"
                )
            key_obj = keys.get(key_name)
            if not isinstance(key_obj, dict):
                raise ValueError(f"Unknown key '{key_name}' for service '{service}'")

            key_provider = key_obj.get("provider")
            if not isinstance(key_provider, str) or not key_provider:
                raise ValueError(f"Key '{key_name}' must contain non-empty 'provider'")
            if key_provider != provider:
                raise ValueError(
                    f"Provider mismatch for key '{key_name}': "
                    f"expected '{provider}', found '{key_provider}'"
                )

            token_map: Dict[str, str] = {}
            for token_key, token_value in key_obj.items():
                if token_key == "provider":
                    continue
                if isinstance(token_value, (dict, list)):
                    raise ValueError(
                        f"Key '{key_name}' field '{token_key}' must be scalar value"
                    )
                token_map[str(token_key)] = str(token_value)

            chains = provider_cfg.get("chains")
            if not isinstance(chains, list):
                raise ValueError(
                    f"Missing/invalid chains array at services.{service}.{provider}.chains"
                )

            unique_chains = sorted(set(chains))
            if len(unique_chains) != len(chains):
                raise ValueError(
                    f"Duplicate chains in services.{service}.{provider}.chains"
                )

            for chain in unique_chains:
                if not isinstance(chain, str):
                    raise ValueError(
                        f"Chain name must be string at "
                        f"services.{service}.{provider}.chains"
                    )
                validate_ident(chain, "chain")

                provider_templates = templates.get(provider)
                if not isinstance(provider_templates, dict):
                    raise ValueError(
                        f"Missing provider template for '{provider}' in provider_chain_templates"
                    )
                chain_template = provider_templates.get(chain)
                if not isinstance(chain_template, dict):
                    raise ValueError(
                        f"Missing chain template at provider_chain_templates.{provider}.{chain}"
                    )

                rpc_url_template = chain_template.get("rpc_url")
                if not isinstance(rpc_url_template, str) or not rpc_url_template:
                    raise ValueError(
                        f"Missing rpc_url at provider_chain_templates.{provider}.{chain}"
                    )

                headers_template = chain_template.get("headers", {})
                if not isinstance(headers_template, dict):
                    raise ValueError(
                        f"'headers' must be object at provider_chain_templates.{provider}.{chain}"
                    )

                rpc_url = resolve_placeholders(
                    rpc_url_template,
                    token_map,
                    f"provider_chain_templates.{provider}.{chain}.rpc_url",
                )

                headers: Dict[str, str] = {}
                for header_name, header_value in headers_template.items():
                    headers[str(header_name)] = resolve_placeholders(
                        str(header_value),
                        token_map,
                        (
                            f"provider_chain_templates.{provider}.{chain}"
                            f".headers.{header_name}"
                        ),
                    )

                ws_url_template = chain_template.get("ws_url")
                ws_url = None
                if ws_url_template is not None:
                    if not isinstance(ws_url_template, str) or not ws_url_template:
                        raise ValueError(
                            f"'ws_url' must be non-empty string at "
                            f"provider_chain_templates.{provider}.{chain}"
                        )
                    ws_url = resolve_placeholders(
                        ws_url_template,
                        token_map,
                        f"provider_chain_templates.{provider}.{chain}.ws_url",
                    )

                rpc_target = parse_target(
                    rpc_url,
                    headers,
                    allowed_schemes={"https"},
                    context=f"provider_chain_templates.{provider}.{chain}.rpc_url",
                )
                ws_target = (
                    parse_target(
                        ws_url,
                        headers,
                        allowed_schemes={"wss"},
                        context=f"provider_chain_templates.{provider}.{chain}.ws_url",
                    )
                    if ws_url
                    else None
                )

                route = Route(
                    service=service,
                    provider=provider,
                    chain=chain,
                    rpc=rpc_target,
                    ws=ws_target,
                )
                route_key = (service, provider, chain)
                if route_key in seen:
                    raise ValueError(f"Duplicate route definition: {route_key}")
                seen.add(route_key)
                routes.append(route)

    return bind, metrics_bind, health_path, metrics_path, routes


def server_line_for_target(target: Target) -> str:
    ssl = ""
    if target.transport in {"https", "wss"}:
        # Some upstreams (for example QuickNode and sequencers) require SNI
        # during TLS handshake, including active health checks.
        ssl = f" ssl verify none sni str({target.host}) check-sni {target.host}"
    return (
        f"    server upstream {target.host}:{target.port} check inter 10s fall 3 rise 2"
        f"{ssl}"
    )


def render_haproxy(
    bind: str,
    metrics_bind: str,
    health_path: str,
    metrics_path: str,
    routes: List[Route],
) -> str:
    not_found_payload = json.dumps({"error": "route_not_found"}, separators=(",", ":"))
    not_found_payload_escaped = not_found_payload.replace('"', '\\"')

    lines: List[str] = []
    lines.extend(
        [
            "global",
            "    log stdout format raw local0",
            "    maxconn 50000",
            "",
            "defaults",
            "    log global",
            "    mode http",
            "    option httplog",
            "    option dontlognull",
            "    option http-buffer-request",
            "    timeout connect 5s",
            "    timeout client 60s",
            "    timeout server 60s",
            "    timeout tunnel 1h",
            "",
            "frontend fe_rpc_gateway",
            f"    bind {bind}",
            "    option forwardfor",
            "    acl allow_rpc_src src 10.10.0.0/16",
            "    http-request deny deny_status 403 unless allow_rpc_src",
            f"    acl is_health path -i {health_path}",
            "    acl is_ws hdr(Upgrade) -i websocket",
            "    acl has_upgrade hdr(Connection) -i upgrade",
            '    http-request return status 200 content-type text/plain lf-string "ok" if is_health',
            "",
        ]
    )

    for r in routes:
        lines.append(f"    acl route_{r.key} path_beg -i {r.route_path}/ {r.route_path}")
    lines.append("")

    for r in routes:
        if r.ws is not None:
            lines.append(
                f"    use_backend be_ws_{r.key} if route_{r.key} is_ws has_upgrade"
            )
        lines.append(f"    use_backend be_rpc_{r.key} if route_{r.key}")

    lines.extend(
        [
            "    default_backend be_not_found",
            "",
            "frontend fe_metrics",
            f"    bind {metrics_bind}",
            "    acl allow_metrics_src src 10.10.0.0/16 138.199.220.100",
            "    http-request deny deny_status 403 unless allow_metrics_src",
            f"    http-request use-service prometheus-exporter if {{ path -i {metrics_path} }}",
            f"    http-request return status 200 content-type text/plain lf-string \"ok\" if {{ path -i {health_path} }}",
            '    http-request return status 404 content-type text/plain lf-string "not_found"',
            "",
            "backend be_not_found",
            "    http-request return status 404 content-type application/json "
            f'lf-string "{not_found_payload_escaped}"',
            "",
        ]
    )

    for r in routes:
        lines.extend(
            [
                f"backend be_rpc_{r.key}",
                "    mode http",
                f"    http-request set-path {r.rpc.path}",
                f"    http-request set-header Host {r.rpc.host}",
            ]
        )
        for header_name, header_value in sorted(r.rpc.headers.items()):
            lines.append(
                f"    http-request set-header {header_name} {json.dumps(header_value)}"
            )
        lines.append(server_line_for_target(r.rpc))
        lines.append("")

        if r.ws is not None:
            lines.extend(
                [
                    f"backend be_ws_{r.key}",
                    "    mode http",
                    "    option http-server-close",
                    f"    http-request set-path {r.ws.path}",
                    f"    http-request set-header Host {r.ws.host}",
                ]
            )
            for header_name, header_value in sorted(r.ws.headers.items()):
                lines.append(
                    f"    http-request set-header {header_name} {json.dumps(header_value)}"
                )
            lines.append(server_line_for_target(r.ws))
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate haproxy config from hierarchical JSON routing config."
    )
    parser.add_argument(
        "--config",
        default="rpc_routes.json",
        help="Path to routing JSON config",
    )
    parser.add_argument(
        "--out",
        default="haproxy.cfg",
        help="Output haproxy config path",
    )
    args = parser.parse_args()

    bind, metrics_bind, health_path, metrics_path, routes = load_routes(args.config)
    rendered = render_haproxy(bind, metrics_bind, health_path, metrics_path, routes)

    with open(args.out, "w", encoding="utf-8") as f:
        f.write(rendered)

    print(f"Wrote {args.out} with {len(routes)} routes.")


if __name__ == "__main__":
    main()
