from contextlib import asynccontextmanager

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware


# Hard-coded Bing tile URL template
TILE_URL_TEMPLATE = (
    "https://ecn.t{server}.tiles.virtualearth.net/"
    "tiles/a{quadkey}.jpeg?g=13816"
)

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:159.0) Gecko/20100101 Firefox/159.0"


def bing_quadkey(x: int, y: int, z: int) -> str:
    """Convert XYZ coordinates to a Bing Maps quadkey."""
    quadkey = []

    for i in range(z, 0, -1):
        mask = 1 << (i - 1)
        digit = 0

        if x & mask:
            digit += 1
        if y & mask:
            digit += 2

        quadkey.append(str(digit))

    return "".join(quadkey)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.client = httpx.AsyncClient(
        timeout=20.0,
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
    )
    yield
    await app.state.client.aclose()


app = FastAPI(
    title="Local Bing XYZ Tile Proxy",
    lifespan=lifespan,
)

# CORS: allow cross-origin tile requests during local development.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "HEAD", "OPTIONS"],
    allow_headers=["*"],
)


@app.get("/")
async def index():
    return {
        "status": "running",
        "tile_url": "http://127.0.0.1:8000/tiles/{z}/{x}/{y}.jpg",
        "example": "/tiles/10/511/340.jpg",
    }


@app.get("/tiles/{z}/{x}/{y}.jpg")
async def get_tile(z: int, x: int, y: int):
    # Validate zoom level
    if not 0 <= z <= 20:
        raise HTTPException(
            status_code=400,
            detail="Zoom must be between 0 and 20",
        )

    # Validate XYZ coordinates
    limit = 1 << z
    if not (0 <= x < limit and 0 <= y < limit):
        raise HTTPException(
            status_code=400,
            detail="Invalid XYZ tile coordinates",
        )

    # Calculate Bing quadkey and select tile server
    quadkey = bing_quadkey(x, y, z)
    server = (x + y) % 4

    url = TILE_URL_TEMPLATE.format(
        server=server,
        quadkey=quadkey,
    )

    try:
        client = app.state.client
        upstream = await client.get(url)
        upstream.raise_for_status()

        content_type = upstream.headers.get(
            "content-type", ""
        ).lower()

        if not content_type.startswith("image/"):
            raise HTTPException(
                status_code=502,
                detail="Upstream did not return an image",
            )

        return Response(
            content=upstream.content,
            media_type=content_type.split(";")[0],
            headers={
                "Access-Control-Allow-Origin": "*",
                "Cache-Control": "public, max-age=300",
            },
        )

    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=502,
            detail=(
                "Bing tile server returned HTTP "
                f"{exc.response.status_code}"
            ),
        ) from exc

    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Could not reach tile server: {exc}",
        ) from exc


if __name__ == "__main__":
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
    )
