"""Upload/manage custom GIS overlay layers (REQUIREMENTS.md 3.2 -- "Import GIS
overlays: Shapefile, KML/KMZ ... for operation-specific reference layers"). Example
real use case (Gerry, 2026-09-12): marking USAF/CAP intercept-training "play areas"
for a specific exercise (e.g. FERTILE KEYNOTE), or practice areas for a joint
training exercise -- ad hoc, operation-specific, not permanent reference data like
airfields or Special Use Airspace (app/api/airspace.py).

Parsing uses the system GDAL/OGR `ogr2ogr` CLI (not a hand-rolled per-format parser,
not a Python GDAL binding) to convert whatever format is uploaded to GeoJSON in one
shot -- it natively handles Shapefile (including reprojecting whatever CRS the
shapefile is in), KML, and KMZ. IMPORTANT: must be the real system GDAL
(`/usr/bin/ogr2ogr`, GDAL 3.8, confirmed has full KML/LIBKML driver support) --
Gerry's miniconda install shadows `ogr2ogr` earlier on PATH with an older GDAL 3.2
build, so this is invoked by absolute path deliberately, matching the same
psql/curl/python3 PATH-shadowing lesson from elsewhere in this project. The same
shadowing also shows up as an inherited `PROJ_LIB` env var pointing at miniconda's
PROJ database (confirmed 2026-09-12: real ogr2ogr + wrong PROJ_LIB = "lacks
DATABASE.LAYOUT.VERSION" reprojection failure even though the binary itself was
correct) -- explicitly overridden below, not just the executable path.
"""

import json
import os
import subprocess
import tempfile
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from geoalchemy2.shape import from_shape, to_shape
from shapely.geometry import shape
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import GisFeature, GisLayer

router = APIRouter(prefix="/api/gis", tags=["gis"])

OGR2OGR = "/usr/bin/ogr2ogr"
SYSTEM_PROJ_LIB = "/usr/share/proj"
SUPPORTED_EXTENSIONS = {".zip", ".kml", ".kmz", ".geojson", ".json"}


def _convert_to_geojson(upload_path: Path, extension: str, output_path: Path) -> None:
    # A zipped shapefile is read directly out of the zip via GDAL's /vsizip/ virtual
    # filesystem -- no manual extraction needed, GDAL finds the .shp inside.
    input_arg = f"/vsizip/{upload_path}" if extension == ".zip" else str(upload_path)
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
    file: UploadFile = File(...),
    name: str = Form(None),
    session: AsyncSession = Depends(get_session),
):
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

    layer = GisLayer(name=name or Path(file.filename).stem, source_format=extension.lstrip("."))
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
async def list_layers(session: AsyncSession = Depends(get_session)):
    # Count via SQL rather than accessing layer.features -- that relationship is
    # lazy="select" (default), which triggers a sync-style lazy load and blows up
    # with MissingGreenlet under an async session outside of an awaited context.
    result = await session.execute(
        select(GisLayer, func.count(GisFeature.id))
        .outerjoin(GisFeature, GisFeature.layer_id == GisLayer.id)
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
async def delete_layer(layer_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    await session.execute(delete(GisFeature).where(GisFeature.layer_id == layer_id))
    result = await session.execute(delete(GisLayer).where(GisLayer.id == layer_id))
    await session.commit()
    if result.rowcount == 0:
        raise HTTPException(404, "Layer not found")
    return {"deleted": str(layer_id)}
