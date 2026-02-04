#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Pandora FMS Discovery plugin script (vCenter Services):
- POST /api/session (Basic Auth) => session token
- GET /api/vcenter/services => services dict
- Always emits JSON with 'monitoring_data' (even on errors) so you still get modules updated:
  - API:Session:* modules (HTTPStatus/OK/LatencyMs/Error)
  - API:Services:* modules (HTTPStatus/OK/LatencyMs/Error)
  - If services fetch works: creates per-service Health/State (+ OK flags) and summary counts

Enhancement:
- Module names use human-friendly vCenter service names (similar to VCSA Services UI).
  Custom IDs remain stable and based on the service id.
"""

from __future__ import annotations

import argparse
import base64
import json
import ssl
import sys
import time
from typing import Any, Dict, Tuple, Optional
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

# Map service-id (API key) -> friendly display name
SERVICE_NAMES: Dict[str, str] = {
    "applmgmt": "Appliance Management Service",
    "rbd": "Auto Deploy",
    "content-library": "Content Library Service",
    "envoy-hgw": "Envoy Host Gateway",
    "envoy-sidecar": "Envoy Sidecar",
    "envoy": "Envoy",
    "imagebuilder": "ImageBuilder Service",
    "cis-license": "License Service",
    "sca": "Service Control Agent",
    "vapi-endpoint": "vAPI Endpoint",
    "infraprofile": "vCenter Server Profiles",
    "vpxd": "VMware vCenter Server",
    "vpxd-svcs": "VMware vCenter-Services",
    "lookupsvc": "VMware Lookup Service",
    "topologysvc": "VMware Topology Service",
    "trustmanagement": "VMware Trust Management Service",
    "sts": "VMware Secure Token Service",
    "pschealth": "VMware PSC Health",
    "perfcharts": "VMware Performance Charts Service",
    "observability-vapi": "VMware Observability Vapi Service",
    "vmware-vpostgres": "VMware Postgres",
    "vmware-postgres-archiver": "VMware Postgres Archiver",
    "rhttpproxy": "VMware HTTP Reverse Proxy",
    "vsphere-ui": "VMware vSphere Client",
    "vsan-health": "vSAN health Service",
    "vstats": "VMware vStats Service",
    "vtsdb": "VMware vTSDB Service",
    "wcp": "Workload Control Plane",
    "eam": "VMware ESX Agent Manager",
    "sps": "VMware vSphere Profile-Driven Storage Service",
    "updatemgr": "VMware vSphere Update Manager",
    "analytics": "VMware Analytics Service",
    "certificateauthority": "VMware Certificate Authority Service",
    "certificatemanagement": "VMware Certificate Management Service",
    "hvc": "Hybrid vCenter Service",
    "vcha": "VMware vCenter High Availability",
    "netdumper": "VMware ESXi Dump Collector",
}

def parse_bool(v: Optional[str]) -> bool:
    if v is None:
        return False
    s = str(v).strip().lower()
    return s in ("1", "true", "yes", "y", "on", "checked")

def https_ctx(verify_ssl: bool) -> ssl.SSLContext:
    if verify_ssl:
        return ssl.create_default_context()
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx

def http(method: str, url: str, headers: Optional[Dict[str, str]] = None,
         data: Optional[bytes] = None, timeout: int = 25, verify_ssl: bool = False) -> Tuple[int, Dict[str, str], str, float, str]:
    """Returns: (status_code, headers, body_text, elapsed_ms, error_str)"""
    req = Request(url, data=data, headers=headers or {}, method=method)
    ctx = https_ctx(verify_ssl)
    t0 = time.time()
    try:
        with urlopen(req, context=ctx, timeout=timeout) as r:
            body = r.read().decode("utf-8", "replace")
            elapsed_ms = (time.time() - t0) * 1000.0
            return r.status, dict(r.headers), body, elapsed_ms, ""
    except HTTPError as e:
        try:
            b = e.read().decode("utf-8", "replace")
        except Exception:
            b = str(e)
        elapsed_ms = (time.time() - t0) * 1000.0
        return e.code, dict(getattr(e, "headers", {})) if hasattr(e, "headers") else {}, b, elapsed_ms, ""
    except URLError as e:
        elapsed_ms = (time.time() - t0) * 1000.0
        return 0, {}, "", elapsed_ms, f"URLError: {e}"
    except Exception as e:
        elapsed_ms = (time.time() - t0) * 1000.0
        return 0, {}, "", elapsed_ms, f"Exception: {e}"

def vc_base(host: str) -> str:
    host = host.strip()
    if host.startswith("http://") or host.startswith("https://"):
        return host.rstrip("/")
    return ("https://" + host).rstrip("/")

def shorten(s: str, max_len: int = 200) -> str:
    s = (s or "").replace("\n", " ").replace("\r", " ").strip()
    return s if len(s) <= max_len else (s[:max_len] + "...")

def sanitize_custom_id(service_id: str, suffix: str) -> str:
    import re
    sid = re.sub(r"[^A-Za-z0-9_]+", "_", service_id)
    return f"vcenter_service_{sid}_{suffix}"

def pretty_service_name(service_id: str) -> str:
    if service_id in SERVICE_NAMES:
        return SERVICE_NAMES[service_id]
    return service_id.replace("_", " ").replace("-", " ").strip().title()

def add_mod(mods: list, **kwargs) -> None:
    for k in ("data", "name", "type", "module_group", "custom_id", "module_interval", "description"):
        if k in kwargs and kwargs[k] is not None:
            kwargs[k] = str(kwargs[k])
    mods.append(kwargs)

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-H", "--host", required=True, help="vCenter host or https://host")
    ap.add_argument("-u", "--user", required=True, help="SSO username")
    ap.add_argument("-p", "--password", required=True, help="SSO password")
    ap.add_argument("-A", "--agent", default="", help="Pandora agent name (default: VC-<host>)")
    ap.add_argument("--group_id", default="", help="Pandora group ID (string number)")
    ap.add_argument("--interval", default="300", help="Agent/module interval in seconds")
    ap.add_argument("--timeout", type=int, default=25, help="HTTP timeout seconds")
    ap.add_argument("--verify_ssl", default="0", help="1/true to verify TLS cert; default is insecure")
    args = ap.parse_args()

    verify_ssl = parse_bool(args.verify_ssl)
    base = vc_base(args.host)

    agent_name = args.agent.strip() or ("VC-" + base.replace("https://", "").replace("http://", "").split("/")[0])
    group_id = args.group_id.strip()
    interval = str(args.interval).strip() or "300"

    out: Dict[str, Any] = {"summary": {}, "info": "", "monitoring_data": []}
    module_data: list = []

    agent_data: Dict[str, str] = {
        "agent_name": agent_name,
        "agent_alias": agent_name,
        "os_version": "vCenter",
        "interval": interval,
        "address": base.replace("https://", "").replace("http://", "").split("/")[0],
        "description": "vCenter services monitoring via REST /api/vcenter/services (friendly module names + API HTTP status modules)",
        "agent_version": "1.5",
    }
    if group_id:
        agent_data["id_group"] = group_id

    # Session call
    session_url = base + "/api/session"
    raw = f"{args.user}:{args.password}".encode("utf-8")
    auth = base64.b64encode(raw).decode("ascii")
    st_sess, _h_sess, body_sess, ms_sess, err_sess = http(
        "POST", session_url,
        headers={"Authorization": f"Basic {auth}"},
        timeout=args.timeout,
        verify_ssl=verify_ssl
    )

    sess_ok = 1 if st_sess in (200, 201) else 0
    sess_err_str = err_sess or ("" if sess_ok else shorten(body_sess, 200))

    add_mod(module_data, name="API:Session:HTTPStatus", data=st_sess, type="async_data",
            module_group="vCenter API", custom_id="vcenter_api_session_http_status", module_interval=interval)
    add_mod(module_data, name="API:Session:OK", data=sess_ok, type="async_data",
            module_group="vCenter API", custom_id="vcenter_api_session_ok", module_interval=interval,
            description="1 if login HTTP 200/201, else 0")
    add_mod(module_data, name="API:Session:LatencyMs", data=int(ms_sess), type="async_data",
            module_group="vCenter API", custom_id="vcenter_api_session_latency_ms", module_interval=interval)
    add_mod(module_data, name="API:Session:Error", data=sess_err_str, type="async_string",
            module_group="vCenter API", custom_id="vcenter_api_session_error", module_interval=interval)

    session_id = ""
    if sess_ok:
        try:
            v = json.loads(body_sess)
            if isinstance(v, str):
                session_id = v
        except Exception:
            session_id = body_sess.strip().strip('"')

    # Services call
    services_url = base + "/api/vcenter/services"
    st_srv, _h_srv, body_srv, ms_srv, err_srv = (0, {}, "", 0.0, "No session")
    if session_id:
        st_srv, _h_srv, body_srv, ms_srv, err_srv = http(
            "GET", services_url,
            headers={"vmware-api-session-id": session_id},
            timeout=args.timeout,
            verify_ssl=verify_ssl
        )

    srv_ok = 1 if st_srv == 200 else 0
    srv_err_str = err_srv or ("" if srv_ok else shorten(body_srv, 200))

    add_mod(module_data, name="API:Services:HTTPStatus", data=st_srv, type="async_data",
            module_group="vCenter API", custom_id="vcenter_api_services_http_status", module_interval=interval)
    add_mod(module_data, name="API:Services:OK", data=srv_ok, type="async_data",
            module_group="vCenter API", custom_id="vcenter_api_services_ok", module_interval=interval,
            description="1 if /api/vcenter/services returns HTTP 200, else 0")
    add_mod(module_data, name="API:Services:LatencyMs", data=int(ms_srv), type="async_data",
            module_group="vCenter API", custom_id="vcenter_api_services_latency_ms", module_interval=interval)
    add_mod(module_data, name="API:Services:Error", data=srv_err_str, type="async_string",
            module_group="vCenter API", custom_id="vcenter_api_services_error", module_interval=interval)

    services: Dict[str, Any] = {}
    if srv_ok:
        try:
            services = json.loads(body_srv)
            if not isinstance(services, dict):
                services = {}
                add_mod(module_data, name="API:Services:ParseError", data="Non-dict JSON response",
                        type="async_string", module_group="vCenter API",
                        custom_id="vcenter_api_services_parse_error", module_interval=interval)
        except Exception as e:
            services = {}
            add_mod(module_data, name="API:Services:ParseError", data=str(e),
                    type="async_string", module_group="vCenter API",
                    custom_id="vcenter_api_services_parse_error", module_interval=interval)

    if services:
        total = len(services)
        started = sum(1 for _k, v in services.items() if str((v or {}).get("state", "")).upper() == "STARTED")
        stopped = sum(1 for _k, v in services.items() if str((v or {}).get("state", "")).upper() == "STOPPED")
        healthy = sum(1 for _k, v in services.items() if str((v or {}).get("health", "")).upper() == "HEALTHY")
        unhealthy = sum(1 for _k, v in services.items() if "health" in (v or {}) and str((v or {}).get("health", "")).upper() != "HEALTHY")
        unknown_health = total - (healthy + unhealthy)

        add_mod(module_data, name="Services:Total", data=total, type="async_data",
                module_group="vCenter Services", custom_id="vcenter_services_total", module_interval=interval)
        add_mod(module_data, name="Services:Started", data=started, type="async_data",
                module_group="vCenter Services", custom_id="vcenter_services_started", module_interval=interval)
        add_mod(module_data, name="Services:Stopped", data=stopped, type="async_data",
                module_group="vCenter Services", custom_id="vcenter_services_stopped", module_interval=interval)
        add_mod(module_data, name="Services:Healthy", data=healthy, type="async_data",
                module_group="vCenter Services", custom_id="vcenter_services_healthy", module_interval=interval)
        add_mod(module_data, name="Services:Unhealthy", data=unhealthy, type="async_data",
                module_group="vCenter Services", custom_id="vcenter_services_unhealthy", module_interval=interval)
        add_mod(module_data, name="Services:UnknownHealth", data=unknown_health, type="async_data",
                module_group="vCenter Services", custom_id="vcenter_services_unknown_health", module_interval=interval)

        for service_id in sorted(services.keys()):
            v = services[service_id] or {}
            health = str(v.get("health", "UNKNOWN"))
            state = str(v.get("state", "UNKNOWN"))
            disp = pretty_service_name(service_id)

            add_mod(module_data, name=f"Service:{disp}:Health", data=health,
                    type="async_string", module_group="vCenter Services",
                    custom_id=sanitize_custom_id(service_id, "health"), module_interval=interval)
            add_mod(module_data, name=f"Service:{disp}:State", data=state,
                    type="async_string", module_group="vCenter Services",
                    custom_id=sanitize_custom_id(service_id, "state"), module_interval=interval)

            health_ok = 1 if health.upper() == "HEALTHY" else 0
            state_ok = 1 if state.upper() == "STARTED" else 0

            add_mod(module_data, name=f"Service:{disp}:HealthOK", data=health_ok,
                    type="async_data", module_group="vCenter Services",
                    custom_id=sanitize_custom_id(service_id, "health_ok"), module_interval=interval,
                    description="1=HEALTHY, 0=otherwise/unknown")
            add_mod(module_data, name=f"Service:{disp}:StateOK", data=state_ok,
                    type="async_data", module_group="vCenter Services",
                    custom_id=sanitize_custom_id(service_id, "state_ok"), module_interval=interval,
                    description="1=STARTED, 0=otherwise/unknown")

        out["info"] = "Created/updated modules for each vCenter service (friendly names) + API HTTP status/latency/error modules."
        out["summary"] = {"vCenter": base, "Services total": str(total), "Session HTTP": str(st_sess), "Services HTTP": str(st_srv), "TLS verify": "1" if verify_ssl else "0"}
    else:
        out["info"] = "Services data not available; published API HTTP status/latency/error modules so the monitoring team can see the reason."
        out["summary"] = {"vCenter": base, "Services total": "0", "Session HTTP": str(st_sess), "Services HTTP": str(st_srv), "TLS verify": "1" if verify_ssl else "0"}

    out["monitoring_data"] = [{"agent_data": agent_data, "module_data": module_data}]
    print(json.dumps(out, ensure_ascii=False))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
