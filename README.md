# PandoraFMS – VMware vCenter Services (vAPI)

Package: **pandorafms.vmware_vcenter_services**  
Purpose: Monitor *vCenter services* and their *state/health* using VMware vAPI (HTTPS) without pyVmomi.

## What it collects
The script authenticates to vCenter vAPI and queries the services endpoint:

- Session token:
  - `POST https://<vcenter>/api/session` (Basic Auth) → header `vmware-api-session-id`
- Services:
  - `GET https://<vcenter>/api/vcenter/services` (uses `vmware-api-session-id`)

> Note: Some vCenter versions also expose similar data under `/api/appliance/services`, but this DISCO uses `/api/vcenter/services` as implemented in the script.

## Requirements
- PandoraFMS server / discovery node with:
  - `python3`
  - Network access to vCenter `https://<host>:443/`

## Configuration (macros)
From `discovery_definition.ini`, this plugin expects:

- `_host_` – vCenter Host/IP
- `_user_` – SSO username (e.g. `administrator@vsphere.local`)
- `_password_` – SSO password
- `_verify_ssl_` – `0` to skip SSL verify (default), `1` to verify certificate

## Output in Pandora
This DISCO produces **one agent** (by default `vcenter-services`) containing:

- Per-service modules:
  - `Services:<ServiceFriendlyName>:State` (numeric)
  - `Services:<ServiceFriendlyName>:Health` (numeric)
- Summary/count modules:
  - `Services:Total`, `Services:Started`, `Services:Stopped`, `Services:Unknown`
  - `API:Session:OK`, `API:Services:OK`
- A table module:
  - `Services:SummaryTable` (CSV-like text)
- Error/diagnostic module:
  - `Services:Error` (only when failing)

## Interpreting values
- **State**
  - `1` = STARTED
  - `0` = STOPPED
  - `2` = UNKNOWN/OTHER
- **Health**
  - `1` = GREEN
  - `2` = YELLOW
  - `3` = ORANGE
  - `4` = RED
  - `0` = UNKNOWN/EMPTY

## Troubleshooting quick checklist
- Ensure `_host_` resolves/reachable from Pandora node.
- Ensure account has permission to login to vCenter vAPI (SSO).
- If SSL verify fails, set `_verify_ssl_ = 0`.
- If modules are missing, check discovery task logs and the generated agent output file in Pandora discovery incoming directory.
