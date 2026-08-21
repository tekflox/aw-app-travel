---
repo: architecture
path: docs/architecture/aw-app-travel.md
source: generated
edited: false
checksum: sha256:1e1cd8d2e8d6027d60d9918616489fdf453f4f7c7b947e2998cf368b848d5dc6
---
# Travel

- **repo**: aw-app-travel
- **layer**: app
- **technologies**: python
- **health** (derived): planned

Cheapest-window flight search: give a rough departure/return date and it fans a background search across every nearby date combination to find the cheapest fare, instead of you checking one date pair at a time. Direct Google Flights API access via the fli library — no scraping, no browser.

## Connections
- `stdio-mcp` → **mcp-gateway** — MCP surface aggregated by the gateway

## MCP tools
- `search_flight_window_start`
- `search_flight_window_status`
- `search_flight_window_wait`

## Requirements
_none documented_
