# server.py
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse, PlainTextResponse
import uvicorn, uuid, os, shutil

from extractor import extract_outline
from validator import FloorPlan, Room, validate
from dxf_builder import build_dxf_from_result, build_dxf, DXFBuildConfig
from blender_builder import (
    generate_script, generate_script_from_result,
    RoomData, BlenderBuildConfig, save_script,
)

app = FastAPI(title="AI 도면 분석 3D 공간 재설계 플랫폼")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)


# ─── 헬스체크 ────────────────────────────────────────────────────────────────

@app.get("/")
def health():
    return {"status": "ok", "message": "AI 도면 분석 서버 실행 중"}


# ─── 1단계: 이미지 업로드 → 외곽 추출 ──────────────────────────────────────

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


# ─── 2단계: pts_mm → DXF 변환 ───────────────────────────────────────────────

@app.post("/api/convert-dxf")
async def convert_to_dxf(payload: dict):
    """
    Request: { job_id, pts_mm, area_m2?, add_dims?, source_name? }
    Response: DXF 파일 다운로드
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
    """이미지 업로드 → 외곽 추출 → DXF 파일 원스텝 반환"""
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


# ─── 3단계: pts_mm + rooms → Blender 스크립트 ──────────────────────────────

@app.post("/api/build3d/asis")
async def build_asis(payload: dict):
    """
    현황(AS-IS) 3D 씬 생성.

    Request:
        job_id      (str)            — upload에서 받은 job_id
        pts_mm      (list[[x,y]])    — 외곽 좌표 (mm)
        area_m2     (float, optional)
        floor_height_mm (float, optional) — 층고 (기본 2800)
        return_file (bool, optional) — true면 .py 파일 다운로드, false면 JSON 본문

    Response:
        return_file=true  → Blender Python 스크립트 파일 다운로드
        return_file=false → { "job_id", "script": "..." }
    """
    job_id = payload.get("job_id", str(uuid.uuid4())[:8])
    pts_mm_raw = payload.get("pts_mm")
    if not pts_mm_raw or len(pts_mm_raw) < 3:
        raise HTTPException(400, "pts_mm 좌표가 없거나 3개 미만입니다.")

    pts_mm = [tuple(p) for p in pts_mm_raw]
    return_file = payload.get("return_file", False)

    cfg = BlenderBuildConfig(
        floor_height_mm=payload.get("floor_height_mm", 2800.0),
    )

    try:
        script = generate_script(
            pts_mm=pts_mm,
            rooms=None,  # AS-IS: 방 구획 없음
            config=cfg,
            scene_name=f"ASIS_{job_id}",
        )
    except Exception as e:
        raise HTTPException(500, f"Blender 스크립트 생성 실패: {str(e)}")

    if return_file:
        script_path = f"/tmp/{job_id}_asis.py"
        save_script(script, script_path)
        return FileResponse(
            path=script_path,
            media_type="text/x-python",
            filename=f"blender_asis_{job_id}.py",
            headers={"X-Job-Id": job_id},
        )

    return JSONResponse({
        "job_id": job_id,
        "status": "ok",
        "message": "Blender 스크립트 생성 완료 (AS-IS)",
        "script": script,
        "pts_count": len(pts_mm),
    })


@app.post("/api/build3d/redesign")
async def build_redesign(payload: dict):
    """
    재설계(TO-BE) 3D 씬 생성 — 방 배치 포함.

    Request:
        job_id      (str)
        pts_mm      (list[[x,y]])
        rooms       (list[{name, polygon_mm, has_window?, connects_to?}])
        floor_height_mm (float, optional)
        return_file (bool, optional)

    Response:
        return_file=true  → Blender Python 스크립트 파일 다운로드
        return_file=false → { "job_id", "script": "...", "rooms_count" }
    """
    job_id = payload.get("job_id", str(uuid.uuid4())[:8])
    pts_mm_raw = payload.get("pts_mm")
    rooms_data = payload.get("rooms", [])

    if not pts_mm_raw or len(pts_mm_raw) < 3:
        raise HTTPException(400, "pts_mm 좌표가 없거나 3개 미만입니다.")
    if not rooms_data:
        raise HTTPException(400, "방 배치 데이터(rooms)가 없습니다.")

    pts_mm = [tuple(p) for p in pts_mm_raw]
    return_file = payload.get("return_file", False)

    rooms = [
        RoomData(
            name=r["name"],
            polygon_mm=[tuple(p) for p in r["polygon_mm"]],
            has_window=r.get("has_window", False),
            connects_to=r.get("connects_to", []),
        )
        for r in rooms_data
    ]

    cfg = BlenderBuildConfig(
        floor_height_mm=payload.get("floor_height_mm", 2800.0),
    )

    try:
        script = generate_script(
            pts_mm=pts_mm,
            rooms=rooms,
            config=cfg,
            scene_name=f"Redesign_{job_id}",
        )
    except Exception as e:
        raise HTTPException(500, f"Blender 스크립트 생성 실패: {str(e)}")

    if return_file:
        script_path = f"/tmp/{job_id}_redesign.py"
        save_script(script, script_path)
        return FileResponse(
            path=script_path,
            media_type="text/x-python",
            filename=f"blender_redesign_{job_id}.py",
            headers={"X-Job-Id": job_id},
        )

    return JSONResponse({
        "job_id": job_id,
        "status": "ok",
        "message": "Blender 스크립트 생성 완료 (Redesign)",
        "script": script,
        "pts_count": len(pts_mm),
        "rooms_count": len(rooms),
    })


# ─── 4단계: 인테리어 스타일 (미구현 → placeholder) ──────────────────────────

@app.post("/api/interior")
async def apply_interior(payload: dict):
    prompt = payload.get("prompt", "")
    job_id = payload.get("job_id", "")
    return {
        "job_id": job_id,
        "prompt": prompt,
        "status": "queued",
        "message": f"{prompt} 스타일 적용 중...",
    }


if __name__ == "__main__":
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)
