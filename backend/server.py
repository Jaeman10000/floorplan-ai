# server.py
import tempfile, uuid, os, shutil, collections
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from extractor import extract_outline, result_to_dict
from dxf_builder import build_dxf_from_result, build_dxf, DXFBuildConfig
from blender_builder import generate_script, RoomData, BlenderBuildConfig, save_script
from pdf_parser import parse_pdf

app = FastAPI(title="AI 도면 분석 3D 공간 재설계 플랫폼")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)

# 프론트엔드 정적 파일 서빙
_FRONTEND = os.path.join(os.path.dirname(__file__), "..", "frontend")
if os.path.isdir(_FRONTEND):
    app.mount("/static", StaticFiles(directory=_FRONTEND), name="static")

TMPDIR = tempfile.gettempdir()


@app.get("/")
def health():
    _index = os.path.join(os.path.dirname(__file__), "..", "frontend", "index.html")
    if os.path.isfile(_index):
        return FileResponse(_index)
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


# ─── 벡터 PDF 파싱 (외곽 + 내벽 + 방 구획 + 방 이름) ────────────────────────

@app.post("/api/parse-pdf")
async def parse_pdf_endpoint(file: UploadFile = File(...), page_index: int = 0):
    name = (file.filename or "").lower()
    if not name.endswith(".pdf"):
        raise HTTPException(400, "PDF 파일만 업로드 가능합니다.")

    job_id = str(uuid.uuid4())[:8]
    tmp_path = os.path.join(TMPDIR, f"{job_id}.pdf")
    with open(tmp_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    try:
        data = parse_pdf(tmp_path, page_index=page_index)
    except Exception as e:
        raise HTTPException(500, f"PDF 파싱 실패: {str(e)}")

    data["job_id"] = job_id
    data["pdf_path"] = tmp_path
    data["page_index"] = page_index
    return JSONResponse(data)


# ─── AI 배치 조언 (Claude) ──────────────────────────────────────────────────

_ADVICE_SYSTEM = """당신은 한국의 주택 평면 설계·인테리어 배치 전문가입니다.
의뢰인은 땅/주택을 매입해 신축하는 시공업자(JJ)입니다. 설계사 도면을 받아
3D로 확인하는 단계이며, 방 배치를 어떻게 개선할지 실무 조언을 원합니다.

답변 원칙:
- 제공된 [평면도 구조 데이터]의 방 이름·면적·위치(도면 기준 상/하/좌/우)만을
  근거로 구체적으로 조언하세요. 데이터에 없는 치수를 지어내지 마세요.
- 동선(현관→거실→주방, 공용/사적 영역 분리), 채광·환기, 사생활, 면적 효율,
  습식공간(욕실·주방) 배관 집중 등 실무 관점을 활용하세요.
- ⚠️ 방위(동/서/남/북)는 도면에 없으면 '미상'입니다. "남향" 같은 질문에는
  방위 정보가 없음을 먼저 알리고, 사용자가 방위를 알려주면 더 정확하다고
  안내하되, 도면상 상대 위치로 가능한 조언을 주세요.
- 구조벽 이동은 비용·구조안전(내력벽 여부) 문제가 크다는 점을 현실적으로 짚되,
  가능한 대안(비내력벽/경량벽 조정, 가구 배치 변경)도 제시하세요.
- 자동 추출된 방 이름은 오타가 있을 수 있습니다(예: 안방이 '현관'으로 표기).
  배치가 이름과 안 맞으면 그 가능성도 언급하세요.
- 한국어로, 핵심부터 간결하게. 불릿과 짧은 단락 사용. 과한 일반론은 피하세요."""


def _room_position(cx, cy, bx0, by0, bx1, by1):
    """방 중심을 외곽 bbox 기준 도면 상/하/좌/우로 분류 (작은 y = 도면 상단)."""
    w = (bx1 - bx0) or 1.0
    h = (by1 - by0) or 1.0
    fx = (cx - bx0) / w
    fy = (cy - by0) / h
    vert = "상" if fy < 0.34 else ("하" if fy > 0.66 else "중")
    horiz = "좌" if fx < 0.34 else ("우" if fx > 0.66 else "앙")
    return vert + horiz


def _build_advice_context(rooms, outline, area_m2):
    """파싱된 방 데이터를 Claude용 텍스트 컨텍스트로 정리."""
    xs = [p[0] for p in outline] if outline else [0]
    ys = [p[1] for p in outline] if outline else [0]
    bx0, bx1 = min(xs), max(xs)
    by0, by1 = min(ys), max(ys)

    # 세대 수 추정 — 현관/거실/주방 중 최다 개수
    name_count = collections.Counter()
    for r in rooms:
        for nm in (r.get("names") or []):
            name_count[nm] += 1
    anchors = [name_count.get(k, 0) for k in ("현관", "거실", "주방")]
    units = max(anchors) if max(anchors) > 0 else None

    # 면적 큰 순으로 정렬해 중요한 방 먼저
    items = sorted(rooms, key=lambda r: r.get("area_m2", 0), reverse=True)
    lines = []
    for r in items:
        poly = r.get("polygon_mm") or []
        if poly:
            cx = sum(p[0] for p in poly) / len(poly)
            cy = sum(p[1] for p in poly) / len(poly)
            pos = _room_position(cx, cy, bx0, by0, bx1, by1)
        else:
            pos = "?"
        nm = "·".join(r.get("names") or []) or f"(이름없음 #{r.get('id')})"
        lines.append(f"- {nm} · {r.get('area_m2', '?')}㎡ · 도면 위치 {pos}")

    named = sum(1 for r in rooms if r.get("names"))
    counts_str = ", ".join(f"{k}×{v}" for k, v in name_count.most_common()) or "(이름 매칭 없음)"

    return (
        "[평면도 구조 데이터]\n"
        f"전체 외곽 면적: {area_m2}㎡\n"
        f"방 개수: {len(rooms)}개 (이름 매칭 {named}개)\n"
        f"추정 세대 수: {units if units else '미상'} "
        "(현관/거실/주방 개수로 추정 — 부정확할 수 있음)\n"
        f"방 종류별 개수: {counts_str}\n"
        "방위(동서남북): 미상 — 도면에 방위 정보 없음. 아래 위치는 '도면 기준' "
        "상/하/좌/우(상=도면 위쪽).\n\n"
        "[방 목록] (면적 큰 순)\n" + "\n".join(lines)
    )


@app.post("/api/ai-advice")
async def ai_advice(payload: dict):
    question = (payload.get("question") or "").strip()
    if not question:
        raise HTTPException(400, "질문을 입력하세요.")

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise HTTPException(500, "ANTHROPIC_API_KEY가 설정되지 않았습니다 (backend/.env 확인).")

    rooms = payload.get("rooms") or []
    outline = payload.get("building_outline_mm") or []
    area_m2 = payload.get("outline_area_m2")
    if not rooms:
        raise HTTPException(400, "방 데이터가 없습니다. 먼저 PDF를 파싱하세요.")

    context = _build_advice_context(rooms, outline, area_m2)

    try:
        from anthropic import Anthropic
        client = Anthropic(api_key=api_key)
        msg = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2048,
            system=_ADVICE_SYSTEM,
            messages=[{
                "role": "user",
                "content": f"{context}\n\n[시공업자 질문]\n{question}",
            }],
        )
        answer = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
    except Exception as e:
        raise HTTPException(500, f"AI 조언 생성 실패: {str(e)}")

    return JSONResponse({"answer": answer, "context": context})


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
