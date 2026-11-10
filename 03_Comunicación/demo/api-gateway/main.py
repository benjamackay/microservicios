"""Extensiones REST del gateway del equipo para la UI de Media."""
from typing import Annotated

from fastapi import Header, Query
from fastapi.routing import APIRoute
from starlette.responses import RedirectResponse
from starlette.routing import Mount

from gateway.main import app, upstream


async def list_media_link_jobs(
    channel_id: str,
    x_user_id: Annotated[str, Header(min_length=1)],
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    response = await upstream(
        app.state,
        "media",
        "/links/jobs",
        params={"channel_id": channel_id, "limit": limit, "offset": offset},
        headers={"X-User-Id": x_user_id},
    )
    return response.json()


route = APIRoute(
    "/api/v1/media/links/jobs",
    endpoint=list_media_link_jobs,
    methods=["GET"],
    name="list_media_link_jobs",
)
# El Gateway expone su documentación en la raíz; la interfaz de Media vive en 8004.
app.router.routes = [
    existing
    for existing in app.router.routes
    if not (isinstance(existing, Mount) and existing.path in ("", "/"))
]
app.router.routes.append(route)


async def gateway_home():
    return RedirectResponse(url="/docs", status_code=307)


app.add_api_route("/", gateway_home, methods=["GET"], include_in_schema=False)
