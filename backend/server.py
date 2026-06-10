# server.py
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import uvicorn, uuid, os, shutil

from extractor import extract_outline
from validator import FloorPlan, Room, validate

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
    return JSONResponse({"job_id": job_id, "pts_px": result.pts_px, "pts_mm": result.pts_mm,
        "scale_mm_per_px": result.scale_mm_per_px, "area_m2": result.area_m2,
        "confidence": result.confidence, "ocr_dimensions": result.ocr_dimensions,
        "warnings": result.warnings, "image_path": tmp_path})

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
    rooms = [Room(name=r["name"], polygon_mm=[tuple(p) for p in r["polygon_mm"]],
        has_window=r.get("has_window", False), connects_to=r.get("connects_to", [])) for r in rooms_data]
    return {"job_id": job_id, "status": "queued", "message": "3D 변환 중...", "rooms_count": len(rooms)}

@app.post("/api/interior")
async def apply_interior(payload: dict):
    prompt = payload.get("prompt", "")
    job_id = payload.get("job_id", "")
    msg = prompt + " 스타일 적용 중..."
    return {"job_id": job_id, "prompt": prompt, "status": "queued", "message": msg}

if __name__ == "__main__":
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)
