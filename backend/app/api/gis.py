"""Upload/manage custom GIS overlay layers (REQUIREMENTS.md 3.2 -- "Import GIS
overlays: Shapefile, KML/KMZ, Geodatabase ... for operation-specific reference
layers", point/line/polygon alike -- e.g. named reporting points or a search-area
boundary, per Gerry 2026-10-26). Example real use case (Gerry, 2026-09-12): marking
USAF/CAP intercept-training "play areas" for a specific exercise (e.g. FERTILE
KEYNOTE), or practice areas for a joint training exercise -- ad hoc,
operation-specific, not permanent reference data like airfields or Special Use
Airspace (app/api/airspace.py).

Deliberately scoped to the three formats Gerry actually asked for (2026-10-26):
Shapefile, KML/KMZ, and File Geodatabase -- NOT bare GeoJSON/JSON, which this
endpoint accepted in an earlier version but was narrowed back out of scope
(nothing stops someone converting a GeoJSON to one of the three first, if needed).

Parsing uses the system GDAL/OGR `ogr2ogr` CLI (not a hand-rolled per-format parser,
not a Python GDAL binding) to convert whatever format is uploaded to GeoJSON in one
shot -- it natively handles Shapefile (including reprojecting whatever CRS the
shapefile is in), KML, KMZ, and File Geodatabase (via GDAL's own OpenFileGDB driver,
not Esri's proprietary FileGDB SDK -- no licensing concern, read support only, which
is all this needs). IMPORTANT: must be the real system GDAL (`/usr/bin/ogr2ogr`,
confirmed has full KML/LIBKML + OpenFileGDB driver support) -- Gerry's miniconda
install shadows `ogr2ogr` earlier on PATH with an older GDAL build, so this is
invoked by absolute path deliberately, matching the same psql/curl/python3
PATH-shadowing lesson from elsewhere in this project. The same shadowing also shows
up as an inherited `PROJ_LIB` env var pointing at miniconda's PROJ database
(confirmed 2026-09-12: real ogr2ogr + wrong PROJ_LIB = "lacks DATABASE.LAYOUT.VERSION"
reprojection failure even though the binary itself was correct) -- explicitly
overridden below, not just the executable path.
"""

import json
import os
import subprocess
import tempfile
import uuid
import zipfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from geoalchemy2.shape import from_shape, to_shape
from shapely.geometry import shape
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import optional_mission_member
from app.db import get_session
from app.models import GisFeature, GisLayer

router = APIRouter(prefix="/api/gis", tags=["gis"])

OGR2OGR = "/usr/bin/ogr2ogr"
SYSTEM_PROJ_LIB = "/usr/share/proj"
# A zipped Shapefile and a zipped File Geodatabase both arrive with this same
# ".zip" extension -- which one it actually is only gets resolved by peeking
# inside, see _find_gdb_dir below.
SUPPORTED_EXTENSIONS = {".zip", ".kml", ".kmz"}


def _find_gdb_dir(zip_path: Path) -> str | None:
    """A File Geodatabase is a directory (of .gdbtable/.gdbtablx/etc files), not a
    single file, so when one is zipped up, GDAL's /vsizip/ virtual filesystem needs
    the inner .gdb path spelled out explicitly to find it -- confirmed empirically
    2026-10-26: unlike a zipped Shapefile (which GDAL auto-detects at the zip root),
    pointing ogr2ogr at a bare .gdb-containing zip's root fails outright. Returns the
    .gdb directory's name (e.g. "MyExercise.gdb") if one exists anywhere in the zip,
    else None -- meaning this is an ordinary zipped Shapefile instead."""
    with zipfile.ZipFile(zip_path) as zf:
        for name in zf.namelist():
            for part in name.split("/"):
                if part.lower().endswith(".gdb"):
                    return part
    return None


def _convert_to_geojson(upload_path: Path, extension: str, output_path: Path) -> None:
    if extension == ".zip":
        gdb_dir = _find_gdb_dir(upload_path)
        # A zipped Shapefile is read directly out of the zip via GDAL's /vsizip/
        # virtual filesystem -- no manual extraction needed, GDAL finds the .shp
        # inside on its own. A zipped Geodatabase needs that extra /{gdb_dir} --
        # see _find_gdb_dir's docstring.
        input_arg = f"/vsizip/{upload_path}/{gdb_dir}" if gdb_dir else f"/vsizip/{upload_path}"
    else:
        input_arg = str(upload_path)
    env = {**os.environ, "PROJ_LIB": SYSTEM_PROJ_LIB, "PROJ_DATA": SYSTEM_PROJ_LIB}
    result = subprocess.run(
        # -dim XY: KML in particular often carries a Z/altitude coordinate (even a
        # meaningless 0, as GDAL's KML driver commonly emits) -- our geometry column
        # is 2D, so force it here rather than fail per-feature at insert time.
        [OGR2OGR, "-f", "GeoJSON", "-t_srs", "EPSG:4326", "-dim", "XY", str(output_path), input_arg],
        capture_output=True,
        env=env,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        raise HTTPException(400, f"Could not parse uploaded file: {result.stderr.strip()}")


@router.post("/layers")
async def upload_layer(
    request: Request,
    file: UploadFile = File(...),
    name: str = Form(None),
    mission_id: uuid.UUID | None = Query(None),
    session: AsyncSession = Depends(get_session),
):
    await optional_mission_member(mission_id, request, session)
    extension = Path(file.filename or "").suffix.lower()
    if extension not in SUPPORTED_EXTENSIONS:
        raise HTTPException(400, f"Unsupported file type {extension!r}. Use one of: {sorted(SUPPORTED_EXTENSIONS)}")

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_dir_path = Path(tmp_dir)
        upload_path = tmp_dir_path / f"upload{extension}"
        upload_path.write_bytes(await file.read())
        output_path = tmp_dir_path / "output.geojson"

        _convert_to_geojson(upload_path, extension, output_path)
        data = json.loads(output_path.read_text())

    features = data.get("features", [])
    if not features:
        raise HTTPException(400, "Uploaded file parsed successfully but contained no features")

    layer = GisLayer(name=name or Path(file.filename).stem, source_format=extension.lstrip("."), mission_id=mission_id)
    session.add(layer)
    await session.flush()  # need layer.id before creating features

    for feature in features:
        session.add(
            GisFeature(
                layer_id=layer.id,
                geometry=from_shape(shape(feature["geometry"]), srid=4326),
                properties=feature.get("properties") or {},
            )
        )

    await session.commit()
    return {"id": str(layer.id), "name": layer.name, "source_format": layer.source_format, "feature_count": len(features)}


@router.get("/layers")
async def list_layers(
    request: Request, mission_id: uuid.UUID | None = Query(None), session: AsyncSession = Depends(get_session)
):
    await optional_mission_member(mission_id, request, session)
    # Mission-scoped (added 2026-10-26, found live: every mission was seeing every
    # other mission's uploaded layers forever -- "Mission Layers" in name only).
    # mission_id given -> only that mission's layers; omitted -> only the
    # pre-Mission-entity legacy rows (mission_id IS NULL), matching this app's usual
    # optional_mission_member transitional-fallback convention (app/auth.py) rather
    # than dumping every mission's layers into an unscoped view.
    #
    # Count via SQL rather than accessing layer.features -- that relationship is
    # lazy="select" (default), which triggers a sync-style lazy load and blows up
    # with MissingGreenlet under an async session outside of an awaited context.
    result = await session.execute(
        select(GisLayer, func.count(GisFeature.id))
        .outerjoin(GisFeature, GisFeature.layer_id == GisLayer.id)
        .where(GisLayer.mission_id == mission_id if mission_id is not None else GisLayer.mission_id.is_(None))
        .group_by(GisLayer.id)
    )
    return [
        {
            "id": str(layer.id),
            "name": layer.name,
            "source_format": layer.source_format,
            "uploaded_at": layer.uploaded_at.isoformat(),
            "feature_count": feature_count,
        }
        for layer, feature_count in result.all()
    ]


@router.get("/layers/{layer_id}/geojson")
async def get_layer_geojson(layer_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(GisFeature).where(GisFeature.layer_id == layer_id))
    features = []
    for f in result.scalars():
        features.append(
            {"type": "Feature", "geometry": to_shape(f.geometry).__geo_interface__, "properties": f.properties or {}}
        )
    return {"type": "FeatureCollection", "features": features}


@router.delete("/layers/{layer_id}")
async def delete_layer(
    layer_id: uuid.UUID,
    request: Request,
    mission_id: uuid.UUID | None = Query(None),
    session: AsyncSession = Depends(get_session),
):
    await optional_mission_member(mission_id, request, session)
    # Scoped the same way list_layers is -- a layer belonging to another mission
    # shouldn't be deletable (or even visible) just by knowing its id.
    layer_filter = GisLayer.mission_id == mission_id if mission_id is not None else GisLayer.mission_id.is_(None)
    layer = (await session.execute(select(GisLayer).where(GisLayer.id == layer_id, layer_filter))).scalar_one_or_none()
    if layer is None:
        raise HTTPException(404, "Layer not found")
    await session.execute(delete(GisFeature).where(GisFeature.layer_id == layer_id))
    await session.delete(layer)
    await session.commit()
    return {"deleted": str(layer_id)}
