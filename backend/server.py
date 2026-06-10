# server.py
import tempfile, uuid, os, shutil
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse

from extractor import extract_outline, result_to_dict
from dxf_builder import build_dxf_from_result, build_dxf, DXFBuildConfig
from blender_builder import generate_script, RoomData, BlenderBuildConfig, save_script

app = FastAPI(title="AI 도면 분석 3D 공간 재설계 플랫폼")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)

TMPDIR = tempfile.gettempdir()


@app.get("/")
def health():
    return {"status": "ok", "message": "AI 도면 분석 서버 실행 중"}


# ─── 1단계: 이미지 업로드 → 전체 분석 ──────────────────────────────────────

@app.post("/api/upload")
async def upload_floorplan(file: UploadFile = File(...), known_area_m2: float = None):
    if not file.content_type.startswith("image/"):
        raise HTTPException(400, "이미지 파일만 업로드 가능합니다.")

    job_id = str(uuid.uuid4())[:8]
    ext = os.path.splitext(file.filename)[1] if file.filename else ".png"
    tmp_path = os.path.join(TMPDIR, f"{job_id}{ext}")

    with open(tmp_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    try:
        result = extract_outline(tmp_path, known_area_m2=known_area_m2)
    except Exception as e:
        raise HTTPException(500, f"외곽 추출 실패: {str(e)}")

    data = result_to_dict(result)
    data["job_id"] = job_id
    data["image_path"] = tmp_path
    return JSONResponse(data)


# ─── 2단계: DXF 변환 ────────────────────────────────────────────────────────

@app.post("/api/convert-dxf")
async def convert_to_dxf(payload: dict):
    job_id = payload.get("job_id", str(uuid.uuid4())[:8])
    pts_mm_raw = payload.get("pts_mm")
    if not pts_mm_raw or len(pts_mm_raw) < 3:
        raise HTTPException(400, "pts_mm 좌표가 없거나 3개 미만입니다.")

    pts_mm = [tuple(p) for p in pts_mm_raw]
    cfg = DXFBuildConfig(add_dimensions=payload.get("add_dims", True))
    meta = {"job_id": job_id}
    if payload.get("source_name"):
        meta["source"] = payload["source_name"]

    dxf_path = os.path.join(TMPDIR, f"{job_id}_outline.dxf")
    try:
        build_dxf(pts_mm=pts_mm, output_path=dxf_path,
                  area_m2=payload.get("area_m2"), config=cfg, metadata=meta)
    except Exception as e:
        raise HTTPException(500, f"DXF 변환 실패: {str(e)}")

    return FileResponse(dxf_path, media_type="application/dxf",
                        filename=f"floorplan_{job_id}.dxf",
                        headers={"X-Job-Id": job_id})


@app.post("/api/upload-and-convert")
async def upload_and_convert_dxf(
    file: UploadFile = File(...),
    known_area_m2: float = None,
    add_dims: bool = True,
):
    if not file.content_type.startswith("image/"):
        raise HTTPException(400, "이미지 파일만 업로드 가능합니다.")

    job_id = str(uuid.uuid4())[:8]
    ext = os.path.splitext(file.filename)[1] if file.filename else ".png"
    tmp_img = os.path.join(TMPDIR, f"{job_id}{ext}")
    with open(tmp_img, "wb") as f:
        shutil.copyfileobj(file.file, f)

    try:
        result = extract_outline(tmp_img, known_area_m2=known_area_m2)
    except Exception as e:
        raise HTTPException(500, f"외곽 추출 실패: {str(e)}")

    dxf_path = os.path.join(TMPDIR, f"{job_id}_outline.dxf")
    cfg = DXFBuildConfig(add_dimensions=add_dims)
    try:
        build_dxf_from_result(result, dxf_path, config=cfg)
    except Exception as e:
        raise HTTPException(500, f"DXF 변환 실패: {str(e)}")

    return FileResponse(dxf_path, media_type="application/dxf",
                        filename=f"floorplan_{job_id}.dxf",
                        headers={"X-Job-Id": job_id,
                                 "X-Area-M2": str(result.area_m2),
                                 "X-Pts-Count": str(len(result.pts_mm))})


# ─── 3단계: Blender 스크립트 생성 ───────────────────────────────────────────

@app.post("/api/build3d/asis")
async def build_asis(payload: dict):
    job_id = payload.get("job_id", str(uuid.uuid4())[:8])
    pts_mm_raw = payload.get("pts_mm")
    if not pts_mm_raw or len(pts_mm_raw) < 3:
        raise HTTPException(400, "pts_mm 좌표가 없거나 3개 미만입니다.")

    pts_mm = [tuple(p) for p in pts_mm_raw]
    cfg = BlenderBuildConfig(floor_height_mm=payload.get("floor_height_mm", 2800.0))

    try:
        script = generate_script(pts_mm=pts_mm, rooms=None, config=cfg,
                                  scene_name=f"ASIS_{job_id}")
    except Exception as e:
        raise HTTPException(500, f"Blender 스크립트 생성 실패: {str(e)}")

    if payload.get("return_file"):
        path = os.path.join(TMPDIR, f"{job_id}_asis.py")
        save_script(script, path)
        return FileResponse(path, media_type="text/x-python",
                            filename=f"blender_asis_{job_id}.py")

    return JSONResponse({"job_id": job_id, "status": "ok",
                         "script": script, "pts_count": len(pts_mm)})


@app.post("/api/build3d/redesign")
async def build_redesign(payload: dict):
    job_id = payload.get("job_id", str(uuid.uuid4())[:8])
    pts_mm_raw = payload.get("pts_mm")
    rooms_data = payload.get("rooms", [])

    if not pts_mm_raw or len(pts_mm_raw) < 3:
        raise HTTPException(400, "pts_mm 좌표가 없거나 3개 미만입니다.")
    if not rooms_data:
        raise HTTPException(400, "방 배치 데이터(rooms)가 없습니다.")

    pts_mm = [tuple(p) for p in pts_mm_raw]
    rooms = [RoomData(
        name=r["name"],
        polygon_mm=[tuple(p) for p in r["polygon_mm"]],
        has_window=r.get("has_window", False),
        connects_to=r.get("connects_to", []),
    ) for r in rooms_data]

    cfg = BlenderBuildConfig(floor_height_mm=payload.get("floor_height_mm", 2800.0))

    try:
        script = generate_script(pts_mm=pts_mm, rooms=rooms, config=cfg,
                                  scene_name=f"Redesign_{job_id}")
    except Exception as e:
        raise HTTPException(500, f"Blender 스크립트 생성 실패: {str(e)}")

    if payload.get("return_file"):
        path = os.path.join(TMPDIR, f"{job_id}_redesign.py")
        save_script(script, path)
        return FileResponse(path, media_type="text/x-python",
                            filename=f"blender_redesign_{job_id}.py")

    return JSONResponse({"job_id": job_id, "status": "ok",
                         "script": script, "rooms_count": len(rooms)})


# ─── 4단계: 인테리어 스타일 ─────────────────────────────────────────────────

@app.post("/api/interior")
async def apply_interior(payload: dict):
    return {"job_id": payload.get("job_id", ""),
            "prompt": payload.get("prompt", ""),
            "status": "queued",
            "message": f"{payload.get('prompt','')} 스타일 적용 중..."}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)
