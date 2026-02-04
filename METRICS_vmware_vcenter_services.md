# Metrics mapping – pandorafms.vmware_vcenter_services

This document maps **Pandora modules** to their **source metrics / API endpoints**.

## Data sources
- `POST /api/session` → VMware vAPI session id (header `vmware-api-session-id`)
- `GET /api/vcenter/services` → service list with fields such as `state` and `health`

## Module mapping

### API availability modules
| Pandora module | Type | Source | Notes |
|---|---|---|---|
| `API:Session:OK` | `async_data` | `POST https://<host>/api/session` | `1` if HTTP < 400, else `0` |
| `API:Services:OK` | `async_data` | `GET https://<host>/api/vcenter/services` | `1` if HTTP < 400, else `0` |

### Per-service modules (dynamic)
For each service returned by `/api/vcenter/services`:

| Pandora module pattern | Type | Source field | Encoding |
|---|---|---|---|
| `Services:<svc_friendly>:State` | `async_data` | `services[*].state` | `STARTED=1`, `STOPPED=0`, other/empty=`2` |
| `Services:<svc_friendly>:Health` | `async_data` | `services[*].health` | `GREEN=1`, `YELLOW=2`, `ORANGE=3`, `RED=4`, other/empty=`0` |

### Summary modules
| Pandora module | Type | Source | Notes |
|---|---|---|---|
| `Services:Total` | `async_data` | `/api/vcenter/services` | total services seen |
| `Services:Started` | `async_data` | `/api/vcenter/services` | count state == STARTED |
| `Services:Stopped` | `async_data` | `/api/vcenter/services` | count state == STOPPED |
| `Services:Unknown` | `async_data` | `/api/vcenter/services` | count other/empty |

### Table module
| Pandora module | Type | Source | Notes |
|---|---|---|---|
| `Services:SummaryTable` | `async_data_string` | `/api/vcenter/services` | CSV: `Service,State,Health` |

### Error module
| Pandora module | Type | Source | Notes |
|---|---|---|---|
| `Services:Error` | `async_data_string` | local exception | created only on failure |
