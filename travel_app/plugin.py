"""Entrypoint referenced by aw-app.json's ``runtime.entrypoint``
("travel_app.plugin:TravelAppPlugin").

Nothing for this Tier-1 plugin to register through the framework's ``ctx``
facades: no routes, no CLIs, no config, no secret. All of this app's value is
its root ``mcp.json`` — a static file aw-mcp-gateway's app-scan reads directly
(see ``travel_app/mcp_server.py``'s module docstring) — and the ``aw-travel``
skill. Same shape as aw-app-weather's ``weather_app/plugin.py``.
"""
from __future__ import annotations

import logging

log = logging.getLogger("aw_apps.travel")


class TravelAppPlugin:
    async def activate(self, ctx) -> None:
        self.ctx = ctx
        log.info("aw-app-travel activated: mcp server=travel (stdio, no config needed)")

    async def deactivate(self) -> None:
        log.info("aw-app-travel deactivated")
