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
의뢰인은 땅/주택을 매입해 신축하는 시공업자(JJ)입니다.

답변 규칙:
- 방을 언급할 때는 반드시 이름과 면적으로 식별 (예: 거실(25.16㎡), 욕실A(2.97㎡)).
  도면 위치 코드(상앙·중우·하좌 등 약어)를 응답에 그대로 쓰지 마세요.
- 사용자 질문에 직접 답하는 핵심 조언 3~4개만. 전체 세대를 모두 분석하지 마세요.
- 각 조언은 "[방이름(면적)] → [어떻게] → [왜]" 형식으로 1~2줄 이내.
- [평면도 구조 데이터]의 방위 정보가 있으면 채광·환기를 구체적으로 조언하세요.
  방위 정보가 없으면 첫 줄에 "(방위 미상 — 도면 위쪽 방위를 입력하면 더 정확)" 한 줄만 쓰고 바로 조언으로 넘어가세요.
- 동선(현관→거실→주방), 배관 집중(습식공간 인접), 공용/사적 영역 분리 등 실무 관점 활용.
- 구조벽 이동 시 비용·내력벽 위험을 짚되 대안(비내력벽·가구 배치)도 제시.
- 자동 추출 방 이름은 오타일 수 있음 (예: 안방→현관 오기). 배치가 이름과 안 맞으면 언급.
- 한국어. 불릿 사용. 일반론 최소화."""


def _room_position(cx, cy, bx0, by0, bx1, by1):
    """방 중심을 외곽 bbox 기준 도면 상/하/좌/우로 분류 (작은 y = 도면 상단)."""
    w = (bx1 - bx0) or 1.0
    h = (by1 - by0) or 1.0
    fx = (cx - bx0) / w
    fy = (cy - by0) / h
    vert = "상단" if fy < 0.34 else ("하단" if fy > 0.66 else "중간")
    horiz = "좌측" if fx < 0.34 else ("우측" if fx > 0.66 else "중앙")
    return f"{vert} {horiz}"


def _build_advice_context(rooms, outline, area_m2, orientation=None):
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

    # 방위 라인
    if orientation and orientation not in ("모름", "?"):
        orientation_line = f"도면 방위: 도면 위쪽={orientation}쪽 (채광·환기 조언 시 이 기준 사용)\n"
    else:
        orientation_line = "도면 방위: 미상\n"

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
            pos = "위치미상"
        nm = "·".join(r.get("names") or []) or f"(이름없음 #{r.get('id')})"
        lines.append(f"- {nm}({r.get('area_m2', '?')}㎡) — 도면 {pos}")

    named = sum(1 for r in rooms if r.get("names"))
    counts_str = ", ".join(f"{k}×{v}" for k, v in name_count.most_common()) or "(이름 매칭 없음)"

    return (
        "[평면도 구조 데이터]\n"
        f"전체 외곽 면적: {area_m2}㎡\n"
        f"방 개수: {len(rooms)}개 (이름 매칭 {named}개)\n"
        f"추정 세대 수: {units if units else '미상'} (현관/거실/주방 개수로 추정)\n"
        f"방 종류별 개수: {counts_str}\n"
        f"{orientation_line}"
        "\n[방 목록] (면적 큰 순, 도면 위치는 도면 기준 상단/하단/중간 × 좌측/우측/중앙)\n"
        + "\n".join(lines)
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
    orientation = (payload.get("building_orientation") or "").strip() or None
    if not rooms:
        raise HTTPException(400, "방 데이터가 없습니다. 먼저 PDF를 파싱하세요.")

    context = _build_advice_context(rooms, outline, area_m2, orientation=orientation)

    try:
        from anthropic import Anthropic
        client = Anthropic(api_key=api_key)
        msg = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
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


@app.post("/api/unit-boundary")
async def unit_boundary(payload: dict):
    """세대 방 폴리곤들을 합쳐 '빈 외곽'(봉투) 하나를 반환 — 내부 설계 모드 진입용.

    방 폴리곤은 래스터 추적이라 정점 노이즈가 많고 인접 방끼리 공유 edge가 없다.
    unary_union 후 buffer(+t).buffer(-t) 모폴로지 close로 내벽 슬릿/미세간격을 메워
    깨끗한 외곽 ring 하나로 만든다. (그려 넣을 깨끗한 도화지 = 스냅 정확도의 기반)
    """
    polys = payload.get("polygons") or []
    if not polys:
        raise HTTPException(400, "polygons가 비어 있습니다.")

    from shapely.geometry import Polygon, MultiPolygon
    from shapely.ops import unary_union

    geoms = []
    for ring in polys:
        if not ring or len(ring) < 3:
            continue
        try:
            p = Polygon([(float(x), float(y)) for x, y in ring])
            if not p.is_valid:
                p = p.buffer(0)
            if not p.is_empty and p.area > 0:
                geoms.append(p)
        except Exception:
            continue
    if not geoms:
        raise HTTPException(400, "유효한 방 폴리곤이 없습니다.")

    merged = unary_union(geoms)
    # 모폴로지 close: 내벽 슬릿(보통 100~200mm)·미세간격 메움 + 외곽 계단노이즈 평활
    closed = merged.buffer(160).buffer(-160)
    if closed.is_empty:
        closed = merged

    # 가장 큰 조각만 (세대는 연속 영역)
    if isinstance(closed, MultiPolygon):
        closed = max(closed.geoms, key=lambda g: g.area)

    # 그려 넣을 깨끗한 도화지 — 래스터 추적 계단노이즈를 충분히 단순화
    closed = closed.simplify(120, preserve_topology=True)
    ring = [[round(x, 1), round(y, 1)] for x, y in closed.exterior.coords[:-1]]
    return JSONResponse({"boundary_mm": ring, "area_m2": round(closed.area / 1e6, 2)})


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
