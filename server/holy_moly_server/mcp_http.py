"""Mount the FastMCP server into the FastAPI app as a Streamable HTTP endpoint.

Public endpoint: ``<HOLY_MOLY_PUBLIC_URL>/mcp-server/mcp`` (see ``MCP_MOUNT_PATH``/``MCP_PATH``
in ``auth.py``). Design notes in ``docs/http-transport.md``.

Two things are easy to get wrong here and are handled explicitly:

- ``http_app()`` carries its own lifespan (the MCP session manager). It must run, or the
  endpoint accepts connections and fails on the first request. ``web_server.lifespan`` enters it.
- The auth provider's RFC 9728 routes (``/.well-known/oauth-protected-resource/...``) are part of
  the sub-app and would end up under ``/mcp-server/.well-known/...``. Clients look for them at the
  site root, so they are added to the FastAPI router too, ahead of the SPA catch-all route.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastmcp import FastMCP
from starlette.routing import Mount

from holy_moly_server.auth import MCP_MOUNT_PATH, MCP_PATH


def mount_mcp(app: FastAPI, server: FastMCP):
    """Mount ``server`` at ``MCP_MOUNT_PATH`` and return the ASGI sub-app (for its lifespan)."""
    mcp_app = server.http_app(path=MCP_PATH)
    # Insert at the front: the SPA catch-all ``/{full_path:path}`` must never shadow these.
    app.router.routes.insert(0, Mount(MCP_MOUNT_PATH, app=mcp_app, name="mcp"))
    if server.auth is not None:
        for route in server.auth.get_well_known_routes(mcp_path=MCP_PATH):
            app.router.routes.insert(0, route)
    return mcp_app


def well_known_paths(server: FastMCP) -> list[str]:
    """Paths of the RFC 9728 routes this server advertises (for tests and diagnostics)."""
    if server.auth is None:
        return []
    return [route.path for route in server.auth.get_well_known_routes(mcp_path=MCP_PATH)]
