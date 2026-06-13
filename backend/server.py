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


# ─── AI 구조 초안 생성 (내부 설계 모드) ─────────────────────────────────────

_GRID_MM = 100  # 벽 좌표 스냅 격자


def _default_room_counts(area_m2):
    """세대 전용면적(㎡) 기반 방/화장실 기본값 — JJ가 개수를 비웠을 때만 사용.
    통상 주거 기준: ~50㎡↓ 방2, 60~85㎡ 방3, 그 이상 방3~4 / 방3↑이면 화장실2."""
    a = area_m2 or 0
    if a < 50:
        rooms = 2
    elif a <= 85:
        rooms = 3
    else:
        rooms = 4
    baths = 2 if rooms >= 3 else 1
    return rooms, baths


_LAYOUT_SYSTEM = (
    "당신은 시공업자를 돕는 주거 평면 설계 보조다. 주어진 '빈 외곽'(한 세대의 벽 없는 "
    "내부 공간) 안에 내벽을 그려 방을 나누는 '초안'을 만든다. 결과는 시공업자가 직접 "
    "편집할 출발점이므로 완벽할 필요는 없고, 아래 제약을 반드시 지켜라.\n"
    "[제약]\n"
    "1. 모든 벽은 수평 또는 수직의 축정렬 직선 선분이다(대각선 금지).\n"
    "2. 모든 좌표는 100mm 격자 위의 값(100의 배수)으로 한다.\n"
    "3. 모든 벽은 외곽 폴리곤 '안에만' 있어야 한다(밖으로 나가지 말 것).\n"
    "4. 벽의 끝점은 다른 벽이나 외곽선과 같은 격자점에서 만나게 해 방이 닫히도록 한다.\n"
    "5. 요청한 '방 N개, 화장실 M개'에 맞춰 구성한다. 개수를 임의로 바꾸지 마라.\n"
    "6. 일반 주거 상식(거실/LDK는 크게, 침실은 9~12㎡, 화장실은 4~5㎡ 정도)을 따른다.\n"
    "[출력] 오직 JSON만. 산문·설명·마크다운 펜스 금지. 형식:\n"
    '{"walls":[{"a":[x,y],"b":[x,y]}, ...]}  (좌표 단위 mm, 정수)'
)


def _build_layout_prompt(boundary, bbox, area_m2, unit, rooms, baths):
    bx0, by0, bx1, by1 = bbox
    coords = ", ".join(f"[{int(round(x))},{int(round(y))}]" for x, y in boundary)
    return (
        f"[세대] {unit or '미지정'}\n"
        f"[외곽 크기] 가로 {round((bx1 - bx0) / 1000, 2)}m × 세로 {round((by1 - by0) / 1000, 2)}m, "
        f"전용면적 약 {area_m2}㎡\n"
        f"[외곽 폴리곤 좌표(mm, 시계 또는 반시계)]\n[{coords}]\n"
        f"[요청] 이 외곽 안을 방 {rooms}개, 화장실 {baths}개로 나누는 내벽을 그려라. "
        f"위 제약을 지켜 JSON walls만 출력."
    )


def _snap_grid(v):
    return int(round(v / _GRID_MM) * _GRID_MM)


def _parse_walls_json(text):
    """AI 응답 텍스트 → walls 리스트. 펜스 제거 후 parse, 실패 시 첫 {...} 재시도."""
    import json, re
    s = (text or "").strip()
    # ```json ... ``` 펜스 제거
    s = re.sub(r"^```(?:json)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s).strip()
    obj = None
    try:
        obj = json.loads(s)
    except Exception:
        m = re.search(r"\{.*\}", s, re.DOTALL)
        if m:
            try:
                obj = json.loads(m.group(0))
            except Exception:
                obj = None
    if not isinstance(obj, dict):
        return []
    raw = obj.get("walls") or []
    out = []
    for w in raw:
        try:
            a, b = w["a"], w["b"]
            out.append({"a": [float(a[0]), float(a[1])], "b": [float(b[0]), float(b[1])]})
        except Exception:
            continue
    return out


def _postprocess_walls(walls, boundary):
    """100mm 격자 스냅 → degenerate 제거 → dedup → 외곽 buffer(50) 클립.
    클립 결과 MultiLineString은 조각화, 100mm 미만 조각은 버림."""
    from shapely.geometry import Polygon, LineString
    bpoly = Polygon([(float(x), float(y)) for x, y in boundary]).buffer(50)

    snapped = []
    for w in walls:
        a = [_snap_grid(w["a"][0]), _snap_grid(w["a"][1])]
        b = [_snap_grid(w["b"][0]), _snap_grid(w["b"][1])]
        if (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 < _GRID_MM ** 2:  # <100mm degenerate
            continue
        snapped.append((a, b))

    # dedup (방향 무관)
    seen, uniq = set(), []
    for a, b in snapped:
        key = tuple(sorted([tuple(a), tuple(b)]))
        if key in seen:
            continue
        seen.add(key)
        uniq.append((a, b))

    # 외곽 클립
    clipped = []
    for a, b in uniq:
        try:
            inter = LineString([tuple(a), tuple(b)]).intersection(bpoly)
        except Exception:
            continue
        if inter.is_empty:
            continue
        geoms = list(inter.geoms) if inter.geom_type == "MultiLineString" else [inter]
        for g in geoms:
            if g.geom_type != "LineString":
                continue
            cs = list(g.coords)
            for i in range(len(cs) - 1):
                pa = [_snap_grid(cs[i][0]), _snap_grid(cs[i][1])]
                pb = [_snap_grid(cs[i + 1][0]), _snap_grid(cs[i + 1][1])]
                if (pa[0] - pb[0]) ** 2 + (pa[1] - pb[1]) ** 2 < _GRID_MM ** 2:
                    continue
                clipped.append({"a": pa, "b": pb})

    # 클립 후 다시 dedup
    seen2, final = set(), []
    for w in clipped:
        key = tuple(sorted([tuple(w["a"]), tuple(w["b"])]))
        if key in seen2:
            continue
        seen2.add(key)
        final.append(w)
    return final


@app.post("/api/generate-layout")
async def generate_layout(payload: dict):
    """빈 외곽 + (JJ 입력) 방/화장실 개수 → AI가 격자·직사각형 제약으로 내벽 초안 생성.
    AI는 정밀좌표가 약하므로 '초안만' — 격자 스냅 + 외곽 클립이 안전망, 마감은 JJ가 편집."""
    boundary = payload.get("boundary_mm") or []
    if len(boundary) < 3:
        raise HTTPException(400, "boundary_mm 좌표가 3개 미만입니다.")
    unit = (payload.get("unit") or "").strip() or None

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise HTTPException(500, "ANTHROPIC_API_KEY가 설정되지 않았습니다 (backend/.env 확인).")

    # bbox·면적
    xs = [float(p[0]) for p in boundary]
    ys = [float(p[1]) for p in boundary]
    bbox = (min(xs), min(ys), max(xs), max(ys))
    from shapely.geometry import Polygon
    area_m2 = round(Polygon([(float(x), float(y)) for x, y in boundary]).area / 1e6, 1)

    # 방/화장실 개수: JJ 입력 우선, 없으면 면적 기반 기본값
    def _as_int(v):
        try:
            n = int(v)
            return n if n > 0 else None
        except (TypeError, ValueError):
            return None
    rooms = _as_int(payload.get("rooms"))
    baths = _as_int(payload.get("baths"))
    if rooms is None or baths is None:
        d_rooms, d_baths = _default_room_counts(area_m2)
        if rooms is None:
            rooms = d_rooms
        if baths is None:
            baths = d_baths

    try:
        from anthropic import Anthropic
        client = Anthropic(api_key=api_key)
        msg = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2048,
            system=_LAYOUT_SYSTEM,
            messages=[{
                "role": "user",
                "content": _build_layout_prompt(boundary, bbox, area_m2, unit, rooms, baths),
            }],
        )
        text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
    except Exception as e:
        raise HTTPException(500, f"AI 구조 생성 실패: {str(e)}")

    walls = _postprocess_walls(_parse_walls_json(text), boundary)
    if not walls:
        raise HTTPException(
            422,
            "AI가 유효한 벽을 만들지 못했습니다(파싱 실패 또는 외곽 밖). 기존 작업은 그대로입니다. 다시 시도해 보세요.",
        )
    return JSONResponse({"walls": walls, "count": len(walls), "rooms": rooms, "baths": baths})


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


# ─── 설계 결과 내보내기: 2D 평면도 PDF (reportlab) ──────────────────────────

_UNIT_LABELS = {"A": "A세대", "B": "B세대", "C": "C세대", "common": "공용"}
_KR_FONT = "HYSMyeongJo-Medium"   # reportlab 내장 한국어 CID 폰트 (폰트 파일 동봉 불필요)
_kr_font_ready = False


def _ensure_kr_font():
    """한국어 CID 폰트를 1회만 등록."""
    global _kr_font_ready
    if _kr_font_ready:
        return
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    pdfmetrics.registerFont(UnicodeCIDFont(_KR_FONT))
    _kr_font_ready = True


def _build_plan_pdf(unit, boundary, walls, rooms, when=""):
    """현재 세대 설계를 위에서 내려다본 2D 평면도 PDF 바이트로 생성.

    좌표는 전부 mm. A4 세로에 외곽 bbox를 여백 안에 맞춰 그리고,
    PDF 좌하단 원점에 맞춰 도면 Y(아래로 증가)를 뒤집는다(도면 상단=PDF 위).
    - 외곽선(굵게) + 그린 내벽(중간) + 방 면적 라벨(m²)
    - 전체 외곽 바운딩 치수(가로×세로)만 표기 (변별 치수는 노이즈라 생략)
    - 그린 내벽 각 선분 길이 라벨 (스냅된 깨끗한 선 = 시공 치수)
    - 스케일바 + SCALE 근사값 (배율은 fit에서 역산, 하드코딩 없음)
    """
    import io, math
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import A4

    _ensure_kr_font()
    PAGE_W, PAGE_H = A4                      # 595.28 x 841.89 pt
    MARGIN = 42.0
    TOP_RESERVE = 46.0                       # 제목 영역
    BOT_RESERVE = 40.0                       # 스케일바 영역

    xs = [p[0] for p in boundary]
    ys = [p[1] for p in boundary]
    bx0, bx1 = min(xs), max(xs)
    by0, by1 = min(ys), max(ys)
    bw_mm = (bx1 - bx0) or 1.0
    bh_mm = (by1 - by0) or 1.0

    avail_w = PAGE_W - 2 * MARGIN
    avail_h = PAGE_H - 2 * MARGIN - TOP_RESERVE - BOT_RESERVE
    scale = min(avail_w / bw_mm, avail_h / bh_mm)   # pt per mm

    draw_w = bw_mm * scale
    draw_h = bh_mm * scale
    off_x = MARGIN + (avail_w - draw_w) / 2.0
    off_y = MARGIN + BOT_RESERVE + (avail_h - draw_h) / 2.0

    def tx(x_mm):
        return off_x + (x_mm - bx0) * scale

    def ty(y_mm):
        # 도면 Y는 아래로 증가 → PDF는 위로 증가하므로 뒤집기
        return off_y + (by1 - y_mm) * scale

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)

    # 제목
    c.setFont(_KR_FONT, 14)
    title = f"{_UNIT_LABELS.get(unit, unit or '')} 설계안"
    c.drawString(MARGIN, PAGE_H - MARGIN - 6, title)
    if when:
        c.setFont(_KR_FONT, 9)
        c.setFillGray(0.4)
        c.drawRightString(PAGE_W - MARGIN, PAGE_H - MARGIN - 5, when)
        c.setFillGray(0.0)

    # 방 면적 채색 + 라벨
    c.setFont(_KR_FONT, 9)
    for rm in rooms or []:
        poly = rm.get("poly") or []
        if len(poly) < 3:
            continue
        path = c.beginPath()
        path.moveTo(tx(poly[0][0]), ty(poly[0][1]))
        for px, py in poly[1:]:
            path.lineTo(tx(px), ty(py))
        path.close()
        c.setFillColorRGB(0.90, 0.94, 0.99)
        c.setStrokeColorRGB(0.75, 0.82, 0.90)
        c.setLineWidth(0.4)
        c.drawPath(path, stroke=1, fill=1)
        # 면적 라벨 (centroid)
        cx = sum(p[0] for p in poly) / len(poly)
        cy = sum(p[1] for p in poly) / len(poly)
        area = rm.get("area_m2")
        if area is not None:
            c.setFillGray(0.15)
            c.drawCentredString(tx(cx), ty(cy) - 4, f"{area} m²")

    # 외곽선 (굵게)
    c.setStrokeColorRGB(0.10, 0.42, 0.96)
    c.setLineWidth(1.8)
    ring = c.beginPath()
    ring.moveTo(tx(boundary[0][0]), ty(boundary[0][1]))
    for px, py in boundary[1:]:
        ring.lineTo(tx(px), ty(py))
    ring.close()
    c.drawPath(ring, stroke=1, fill=0)

    # 내벽 + 길이 라벨
    c.setStrokeColorRGB(0.20, 0.22, 0.28)
    c.setLineWidth(1.3)
    for w in walls or []:
        a = w.get("a"); b = w.get("b")
        if not a or not b:
            continue
        ax, ay = tx(a[0]), ty(a[1])
        bxp, byp = tx(b[0]), ty(b[1])
        c.line(ax, ay, bxp, byp)
        length_mm = math.hypot(b[0] - a[0], b[1] - a[1])
        if length_mm < 1.0:
            continue
        mx, my = (ax + bxp) / 2.0, (ay + byp) / 2.0
        ang = math.degrees(math.atan2(byp - ay, bxp - ax))
        if ang > 90 or ang < -90:
            ang += 180   # 글자 뒤집힘 방지
        c.saveState()
        c.translate(mx, my)
        c.rotate(ang)
        c.setFont(_KR_FONT, 7.5)
        c.setFillGray(0.25)
        c.drawCentredString(0, 2.5, f"{length_mm/1000:.2f} m")
        c.restoreState()

    # 전체 외곽 바운딩 치수 (가로 × 세로) — 변별 치수는 노이즈라 생략
    c.setStrokeColorRGB(0.55, 0.55, 0.6)
    c.setLineWidth(0.5)
    c.setFont(_KR_FONT, 8.5)
    c.setFillGray(0.3)
    # 가로 (상단 위쪽)
    y_dim = ty(by0) - 14
    c.line(tx(bx0), y_dim, tx(bx1), y_dim)
    c.drawCentredString((tx(bx0) + tx(bx1)) / 2.0, y_dim + 3, f"전체 가로 {bw_mm/1000:.2f} m")
    # 세로 (좌측 바깥)
    x_dim = tx(bx0) - 16
    c.line(x_dim, ty(by0), x_dim, ty(by1))
    c.saveState()
    c.translate(x_dim - 3, (ty(by0) + ty(by1)) / 2.0)
    c.rotate(90)
    c.drawCentredString(0, 0, f"전체 세로 {bh_mm/1000:.2f} m")
    c.restoreState()

    # 스케일바 (1 m 기준, 너무 길면 0.5 m) + SCALE 근사
    c.setFillGray(0.0)
    bar_m = 1.0 if (1000 * scale) < (avail_w * 0.5) else 0.5
    bar_pt = bar_m * 1000 * scale
    sb_x = MARGIN
    sb_y = MARGIN + 6
    c.setLineWidth(2.0)
    c.setStrokeGray(0.0)
    c.line(sb_x, sb_y, sb_x + bar_pt, sb_y)
    c.line(sb_x, sb_y - 3, sb_x, sb_y + 3)
    c.line(sb_x + bar_pt, sb_y - 3, sb_x + bar_pt, sb_y + 3)
    c.setFont(_KR_FONT, 8)
    c.drawString(sb_x + bar_pt + 6, sb_y - 3, f"{bar_m:g} m")
    # SCALE 근사: 1 도면mm 가 PDF에서 scale pt = scale/72*25.4 mm → 축척 1/N
    real_per_paper = 1.0 / (scale * 25.4 / 72.0)   # 실제 mm per 종이 mm
    c.drawRightString(PAGE_W - MARGIN, sb_y - 3, f"SCALE 1/{round(real_per_paper)}")

    c.showPage()
    c.save()
    return buf.getvalue()


@app.post("/api/export-plan-pdf")
async def export_plan_pdf(payload: dict):
    from fastapi.responses import StreamingResponse
    import io

    boundary = payload.get("boundary_mm") or []
    if len(boundary) < 3:
        raise HTTPException(400, "boundary_mm 좌표가 3개 미만입니다.")
    unit = payload.get("unit") or ""
    walls = payload.get("walls") or []
    rooms = payload.get("rooms") or []
    when = (payload.get("when") or "").strip()

    try:
        pdf_bytes = _build_plan_pdf(unit, boundary, walls, rooms, when=when)
    except Exception as e:
        raise HTTPException(500, f"평면도 PDF 생성 실패: {str(e)}")

    fname = payload.get("filename") or f"plan_{unit or 'unit'}.pdf"
    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


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
