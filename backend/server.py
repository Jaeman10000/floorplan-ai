# server.py
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
import uvicorn, uuid, os, shutil

from extractor import extract_outline
from validator import FloorPlan, Room, validate
from dxf_builder import build_dxf_from_result, build_dxf, DXFBuildConfig

app = FastAPI(title="AI 도면 분석 3D 공간 재설계 플랫폼")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.get("/")
def health():
    return {"status": "ok", "message": "AI 도면 분석 서버 실행 중"}


@app.post("/api/upload")
async def upload_floorplan(file: UploadFile = File(...), known_area_m2: float = None):
    if not file.content_type.startswith("image/"):
        raise HTTPException(400, "이미지 파일만 업로드 가능합니다.")
    job_id = str(uuid.uuid4())[:8]
    tmp_path = f"/tmp/{job_id}{os.path.splitext(file.filename)[1]}"
    with open(tmp_path, "wb") as f:
        shutil.copyfileobj(file.file, f)
    try:
        result = extract_outline(tmp_path, known_area_m2=known_area_m2)
    except Exception as e:
        raise HTTPException(500, f"외곽 추출 실패: {str(e)}")
    return JSONResponse({
        "job_id": job_id,
        "pts_px": result.pts_px,
        "pts_mm": result.pts_mm,
        "scale_mm_per_px": result.scale_mm_per_px,
        "area_m2": result.area_m2,
        "confidence": result.confidence,
        "ocr_dimensions": result.ocr_dimensions,
        "warnings": result.warnings,
        "image_path": tmp_path,
    })


@app.post("/api/convert-dxf")
async def convert_to_dxf(payload: dict):
    """
    /api/upload 결과를 받아 DXF 파일로 변환한다.

    Request body:
        job_id      (str)              — upload에서 받은 job_id
        pts_mm      (list[[x,y]])      — 외곽 좌표 (mm)
        area_m2     (float, optional)  — 면적
        add_dims    (bool, optional)   — 치수선 추가 여부 (기본 true)
        source_name (str, optional)    — 원본 파일명 (메타 기록용)

    Response:
        200 + application/dxf 파일 스트림  (download)
        또는 JSON { "dxf_path": "/tmp/..." }  (경로만 반환 모드)
    """
    job_id = payload.get("job_id", str(uuid.uuid4())[:8])
    pts_mm_raw = payload.get("pts_mm")

    if not pts_mm_raw or len(pts_mm_raw) < 3:
        raise HTTPException(400, "pts_mm 좌표가 없거나 3개 미만입니다.")

    pts_mm = [tuple(p) for p in pts_mm_raw]
    area_m2 = payload.get("area_m2")
    source_name = payload.get("source_name", "")
    add_dims = payload.get("add_dims", True)

    cfg = DXFBuildConfig(add_dimensions=add_dims, add_area_text=True)
    meta = {"job_id": job_id}
    if source_name:
        meta["source"] = source_name

    dxf_path = f"/tmp/{job_id}_outline.dxf"

    try:
        build_dxf(pts_mm=pts_mm, output_path=dxf_path, area_m2=area_m2, config=cfg, metadata=meta)
    except Exception as e:
        raise HTTPException(500, f"DXF 변환 실패: {str(e)}")

    # 파일 다운로드 응답
    return FileResponse(
        path=dxf_path,
        media_type="application/dxf",
        filename=f"floorplan_{job_id}.dxf",
        headers={"X-Job-Id": job_id},
    )


@app.post("/api/upload-and-convert")
async def upload_and_convert_dxf(
    file: UploadFile = File(...),
    known_area_m2: float = None,
    add_dims: bool = True,
):
    """
    이미지 업로드 → 외곽 추출 → DXF 변환을 한 번에 처리한다.
    DXF 파일을 직접 다운로드로 반환한다.
    """
    if not file.content_type.startswith("image/"):
        raise HTTPException(400, "이미지 파일만 업로드 가능합니다.")

    job_id = str(uuid.uuid4())[:8]
    tmp_img = f"/tmp/{job_id}{os.path.splitext(file.filename)[1]}"
    with open(tmp_img, "wb") as f:
        shutil.copyfileobj(file.file, f)

    try:
        result = extract_outline(tmp_img, known_area_m2=known_area_m2)
    except Exception as e:
        raise HTTPException(500, f"외곽 추출 실패: {str(e)}")

    cfg = DXFBuildConfig(add_dimensions=add_dims, add_area_text=True)
    dxf_path = f"/tmp/{job_id}_outline.dxf"

    try:
        build_dxf_from_result(result, dxf_path, config=cfg)
    except Exception as e:
        raise HTTPException(500, f"DXF 변환 실패: {str(e)}")

    return FileResponse(
        path=dxf_path,
        media_type="application/dxf",
        filename=f"floorplan_{job_id}.dxf",
        headers={
            "X-Job-Id": job_id,
            "X-Area-M2": str(result.area_m2),
            "X-Confidence": str(result.confidence),
            "X-Pts-Count": str(len(result.pts_mm)),
        },
    )


@app.post("/api/build3d/asis")
async def build_asis(payload: dict):
    job_id = payload.get("job_id", str(uuid.uuid4())[:8])
    return {"job_id": job_id, "status": "queued", "message": "3D 변환 중...", "glb_path": f"/tmp/{job_id}_asis.glb"}


@app.post("/api/build3d/redesign")
async def build_redesign(payload: dict):
    job_id = payload.get("job_id", str(uuid.uuid4())[:8])
    rooms_data = payload.get("rooms", [])
    if not rooms_data:
        return {"job_id": job_id, "status": "error", "message": "방 배치 데이터가 없습니다."}
    rooms = [Room(
        name=r["name"],
        polygon_mm=[tuple(p) for p in r["polygon_mm"]],
        has_window=r.get("has_window", False),
        connects_to=r.get("connects_to", []),
    ) for r in rooms_data]
    return {"job_id": job_id, "status": "queued", "message": "3D 변환 중...", "rooms_count": len(rooms)}


@app.post("/api/interior")
async def apply_interior(payload: dict):
    prompt = payload.get("prompt", "")
    job_id = payload.get("job_id", "")
    msg = prompt + " 스타일 적용 중..."
    return {"job_id": job_id, "prompt": prompt, "status": "queued", "message": msg}


if __name__ == "__main__":
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)
