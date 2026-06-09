# server.py — FastAPI 백엔드
# 웹에서 도면 이미지 업로드 → 분석 → 3D 변환 파이프라인

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
import uvicorn
import uuid
import json
import os
import tempfile
import shutil

from outline import A_UNIT
from validator import FloorPlan, Room, validate

app = FastAPI(title="AI 도면 분석 3D 공간 재설계 플랫폼")

# CORS 설정 (프론트엔드에서 API 호출 허용)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─────────────────────────────────────────────────────
# 헬스 체크
# ─────────────────────────────────────────────────────
@app.get("/")
def health():
    return {"status": "ok", "message": "AI 도면 분석 서버 실행 중"}

# ─────────────────────────────────────────────────────
# 버튼 1: 도면 이미지 업로드 → 외곽 추출
# ─────────────────────────────────────────────────────
@app.post("/api/upload")
async def upload_floorplan(file: UploadFile = File(...)):
    """
    도면 이미지 업로드.
    외곽 좌표 추출 후 반환.
    """
    if not file.content_type.startswith("image/"):
        raise HTTPException(400, "이미지 파일만 업로드 가능합니다.")

    job_id = str(uuid.uuid4())[:8]

    # 임시 저장
    suffix = os.path.splitext(file.filename)[1]
    tmp_path = f"/tmp/{job_id}{suffix}"
    with open(tmp_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    # 외곽 추출 (현재는 A세대 고정 — 추후 OpenCV 자동 추출로 교체)
    outline_info = {
        "job_id": job_id,
        "unit_name": A_UNIT.name,
        "area_m2": A_UNIT.area_m2,
        "usable_area_m2": round(A_UNIT.usable_area_m2(), 1),
        "outline_px": list(A_UNIT.pts_px),
        "outline_mm": [(round(x, 1), round(y, 1)) for x, y in A_UNIT.pts_mm],
        "scale_mm_per_px": A_UNIT.scale,
        "image_path": tmp_path,
    }

    return JSONResponse(outline_info)

# ─────────────────────────────────────────────────────
# 버튼 1: 도면 그대로 3D화 (외곽 + 내부 그대로)
# ─────────────────────────────────────────────────────
@app.post("/api/build3d/asis")
async def build_asis(payload: dict):
    """
    도면 그대로 3D화.
    외곽 좌표 → Blender bpy → GLB 반환.
    """
    job_id = payload.get("job_id", str(uuid.uuid4())[:8])
    outline_mm = payload.get("outline_mm", list(A_UNIT.pts_mm))
    height_mm  = payload.get("height_mm", 2400)

    # Blender 헤드리스 호출 (wall_builder.py)
    wall_data = {
        "job_id": job_id,
        "outline_mm": outline_mm,
        "height_mm": height_mm,
        "output_path": f"/tmp/{job_id}_asis.glb"
    }

    # TODO: subprocess로 Blender 헤드리스 실행
    # result = subprocess.run(["blender", "--background",
    #     "--python", "wall_builder.py", "--", json.dumps(wall_data)])

    return {
        "job_id": job_id,
        "status": "queued",
        "message": "3D 변환 중... (Blender 연결 후 자동 실행)",
        "glb_path": wall_data["output_path"]
    }

# ─────────────────────────────────────────────────────
# 버튼 2: 내부 재배치 후 3D화
# ─────────────────────────────────────────────────────
@app.post("/api/build3d/redesign")
async def build_redesign(payload: dict):
    """
    외곽만 유지, 내부 AI 재설계 후 3D화.
    1. 외곽 고정
    2. MD 규칙 기반 재설계
    3. 검증 통과 후 3D 변환
    """
    job_id      = payload.get("job_id", str(uuid.uuid4())[:8])
    rooms_data  = payload.get("rooms", [])

    if not rooms_data:
        return {
            "job_id": job_id,
            "status": "error",
            "message": "방 배치 데이터가 없습니다."
        }

    # Room 객체 변환
    rooms = []
    for r in rooms_data:
        rooms.append(Room(
            name=r["name"],
            polygon_mm=[tuple(p) for p in r["polygon_mm"]],
            has_window=r.get("has_window", False),
            connects_to=r.get("connects_to", [])
        ))

    entrance = tuple(payload.get("entrance_point_mm", [0, 0]))

    plan = FloorPlan(
        unit=A_UNIT,
        rooms=rooms,
        entrance_point_mm=entrance
    )

    # MD 규칙 검증 — 통과 못하면 에러 반환
    validator_instance = __import__("validator").FloorPlanValidator(plan)
    passed = validator_instance.validate_all()

    if not passed:
        return {
            "job_id": job_id,
            "status": "validation_failed",
            "errors": validator_instance.errors,
            "message": "MD 설계 규칙 위반. 수정 후 재시도하세요."
        }

    return {
        "job_id": job_id,
        "status": "queued",
        "message": "검증 통과. 3D 변환 중...",
        "rooms_count": len(rooms)
    }

# ─────────────────────────────────────────────────────
# 인테리어 프롬프트
# ─────────────────────────────────────────────────────
@app.post("/api/interior")
async def apply_interior(payload: dict):
    """
    프롬프트 입력 → 인테리어 스타일 적용.
    예: {"job_id": "abc", "prompt": "모던 미니멀 스타일"}
    """
    prompt = payload.get("prompt", "")
    job_id = payload.get("job_id", "")

    # TODO: FAISS 에셋 DB 검색 + Blender 재질 적용
    return {
        "job_id": job_id,
        "prompt": prompt,
        "status": "queued",
        "message": f"'{prompt}' 스타일 적용 중..."
    }

# ─────────────────────────────────────────────────────
# 실행
# ─────────────────────────────────────────────────────
if __name__ == "__main__":
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)
