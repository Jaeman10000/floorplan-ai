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
- 시장 트렌드: [시공업자 질문]에 구체적인 트렌드 정보가 포함된 경우 그 내용을 우선 반영.
  그 외에는 "N년 트렌드 1위", "요즘 분양 1위", "필수 마케팅 포인트" 등 시장을 단정하는 표현을 쓰지 말 것.
  시장 수치·순위·분양가·유행 평면 등이 필요하면 "구체적 최신 시장 트렌드(분양가·유행 평면 등)는 실거래 데이터 확인이 필요합니다"라고 단서를 달 것.
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


# ─── 설계 시작 조언 (내부 설계 모드 — 텍스트 조언만, 좌표/벽 생성 안 함) ─────────

_DESIGN_ADVICE_SYSTEM = """당신은 한국의 주거 평면 설계 전문가입니다.
의뢰인은 신축하는 시공업자(JJ)이고, 지금 '빈 외곽'(벽 없는 세대 내부)에 방을 직접 그려 설계하려 합니다.
당신은 손이 아니라 머리입니다 — 좌표나 벽을 그리지 말고, JJ가 직접 그릴 수 있게 '방향성 있는 텍스트 조언'만 줍니다.

조언 규칙:
- 정밀 좌표는 단정하지 마세요(예: "거실을 (3200,1500)에" 금지). 대신 방위·위치·대략 크기로 힌트를 주세요.
  예: "남쪽 외벽 동쪽 끝에 안방(약 3×3.5m, 10~11㎡)". _edge_directions가 준 방위 라벨(남/북/동/서·상단변=남 등)을 근거로 "남쪽 외벽 동쪽 끝", "북서 모서리" 식으로 표현.
  방위가 미상이면 "외벽 접한 쪽", "안쪽 코너" 등 상대적 표현으로.
- 외곽 형상·면적을 먼저 분석: 어디가 주 생활공간(거실 등)으로 적합한지, 어디가 잘려 방 만들기 불리한지.
- 방위가 있으면 채광 원칙을 구체적으로: 남향 외벽=거실·침실, 북향=욕실·주방·다용도실. 방위가 미상이면 일반 채광 원칙(외벽 접한 면에 거실·침실)으로.
- 침실 개수가 주어지면 그 기준으로 구성·동선(현관→거실→각 방)·물 쓰는 공간(욕실·주방·다용도) 모으기(배관 효율)를 제안.
- '이미 고정된 공간'이나 '지금까지 그린 방'이 있으면 그것을 전제로 이어서 조언("거실 15㎡를 그렸으니 다음은 침실을 북동쪽에" 식). 아무것도 없으면 어디서부터 시작할지.
- 시장 트렌드: [JJ가 입력한 트렌드/요구]가 있으면 그 내용을 우선 반영.
  없으면 일반 건축 원칙으로만 조언. "N년 트렌드 1위", "요즘 분양 1위", "필수 마케팅 포인트" 같이 시장을 단정하는 표현 금지.
  시장 수치·순위·분양가·유행 평면이 필요하면 "구체적 최신 시장 트렌드(분양가·유행 평면 등)는 실거래 데이터 확인이 필요합니다"라고 정직하게 단서를 달 것.
- 한국어, 불릿 위주, 간결하게. 핵심 5~7줄 이내.

[필수] 조언 텍스트를 모두 쓴 뒤, 맨 마지막에 각 방의 '대략적 위치 존'을 아래 형식으로 출력하라.
- 반드시 ```json 펜스로 감싸고, 그 안에 zones 배열만: {"zones":[{"name":"거실","x":0,"y":0,"w":4000,"h":3500}, ...]}
- x,y,w,h는 mm. [외곽 폴리곤 좌표] 범위 안에서 각 방의 대략 위치·크기. 100mm 격자 근사.
- 이 좌표는 '대략값'이며 정밀하지 않아도 된다(JJ가 보고 직접 그리는 방향 가이드). 겹쳐도 되고, 외곽을 살짝 벗어나도 서버가 자른다.
- 조언에서 언급한 방들(거실·침실·주방·욕실·현관 등)을 zones에 포함하라. 펜스 밖에는 JSON을 두지 마라."""


def _classify_shape(boundary):
    """외곽 ring(mm) → 대략적 형상 라벨 + 원시 수치(단정 아님, AI 보조용)."""
    from shapely.geometry import Polygon
    poly = Polygon([(float(x), float(y)) for x, y in boundary])
    if not poly.is_valid:
        poly = poly.buffer(0)
    x0, y0, x1, y1 = poly.bounds
    bw = (x1 - x0) or 1.0
    bh = (y1 - y0) or 1.0
    bbox_area = bw * bh
    fill = (poly.area / bbox_area) if bbox_area else 0.0
    longside, shortside = max(bw, bh), min(bw, bh)
    aspect = (longside / shortside) if shortside else 1.0
    if fill >= 0.85:
        label = "직사각형에 가까운 정형"
    elif fill >= 0.55:
        label = "ㄱ자·요철 등 비정형"
    else:
        label = "삼각형·이형(많이 잘린 형태)"
    if aspect >= 2.0:
        label += ", 좁고 긴 형태"
    return {"label": label, "fill": round(fill, 2), "aspect": round(aspect, 2),
            "vertices": len(boundary), "bw_mm": bw, "bh_mm": bh,
            "area_m2": round(poly.area / 1e6, 1)}


def _build_design_advice_context(boundary, unit, orientation, fixed_rooms,
                                 bedrooms, baths, current_rooms, trend):
    """설계 모드 컨텍스트: 형상·크기·방위·안전영역·외곽좌표·구성·고정방·이미 그린 방·트렌드."""
    from shapely.geometry import Polygon
    sh = _classify_shape(boundary)
    bx0 = min(p[0] for p in boundary); by0 = min(p[1] for p in boundary)
    bx1 = max(p[0] for p in boundary); by1 = max(p[1] for p in boundary)

    lines = [f"[세대] {unit or '미지정'}"]
    lines.append(f"[외곽 형상] {sh['label']} (채움비율 {sh['fill']}, 종횡비 {sh['aspect']}, 정점 {sh['vertices']}개)")
    lines.append(f"[크기] 전용면적 약 {sh['area_m2']}㎡, bbox 가로 {round(sh['bw_mm']/1000,2)}m × 세로 {round(sh['bh_mm']/1000,2)}m")

    ed = _edge_directions(orientation)
    if ed:
        lines.append(
            f"[방위] 도면 위쪽={orientation}쪽. 외곽 상단변={ed['top']}, 하단변={ed['bottom']}, "
            f"좌변={ed['left']}, 우변={ed['right']} (남향 외벽=거실·침실, 북향=물 쓰는 공간 권장)")
    else:
        lines.append("[방위] 미상 — 일반 채광 원칙으로 조언 (도면 위쪽 방위를 입력하면 더 정확)")

    try:
        ir = _max_inscribed_rect(Polygon([(float(x), float(y)) for x, y in boundary]).buffer(0))
    except Exception:
        ir = None
    if ir:
        iw = (ir[2] - ir[0]) / 1000; ih = (ir[3] - ir[1]) / 1000
        lines.append(f"[안 잘리는 가장 큰 직사각형 영역] 약 {round(iw,2)}m × {round(ih,2)}m = {round(iw*ih,1)}㎡ (주 생활공간 두기 좋은 영역)")

    coords = ", ".join(f"[{int(round(x))},{int(round(y))}]" for x, y in boundary)
    lines.append(f"[외곽 폴리곤 좌표(mm)]\n[{coords}]")

    if bedrooms or baths:
        parts = []
        if bedrooms: parts.append(f"침실 {bedrooms}개")
        if baths: parts.append(f"욕실 {baths}개")
        lines.append(f"[원하는 구성] {', '.join(parts)}")
    else:
        lines.append("[원하는 구성] 미지정 (면적·형상에 맞는 적정 구성을 제안)")

    if fixed_rooms:
        fl = []
        for fr in fixed_rooms:
            nm = str(fr.get("name") or "고정방")
            poly = fr.get("poly") or []
            if len(poly) >= 3:
                cx = sum(p[0] for p in poly) / len(poly); cy = sum(p[1] for p in poly) / len(poly)
                fl.append(f"{nm}({_room_position(cx, cy, bx0, by0, bx1, by1)})")
            else:
                fl.append(nm)
        lines.append(f"[이미 고정된 공간] {', '.join(fl)} — 이 위치를 전제로 나머지를 조언")

    drawn = [r for r in (current_rooms or []) if (r.get("area_m2") or 0) > 0]
    if drawn:
        rl = []
        for r in drawn:
            nm = (r.get("name") or "").strip() or "(이름없음)"
            cx, cy = r.get("cx"), r.get("cy")
            pos = _room_position(cx, cy, bx0, by0, bx1, by1) if (cx is not None and cy is not None) else "위치미상"
            rl.append(f"{nm}({r.get('area_m2')}㎡, {pos})")
        lines.append(f"[지금까지 그린 방] {len(drawn)}개: {', '.join(rl)} — 이걸 이어받아 '다음에 무엇을 어디에' 식으로 조언")
    else:
        lines.append("[지금까지 그린 방] 없음 (빈 외곽 — 어디서부터 시작할지 조언)")

    t = (trend or "").strip()
    if t:
        lines.append(f"[JJ가 입력한 트렌드/요구]\n{t}\n→ 위 내용을 우선 반영해 조언")
    else:
        lines.append("[트렌드] JJ 입력 없음 — 일반 주거 트렌드(알파룸·팬트리·드레스룸 등)만 언급하고 '구체적 최신 시장 트렌드는 별도 확인 필요'라고 명시")

    return "\n".join(lines)


@app.post("/api/design-advice")
async def design_advice(payload: dict):
    """설계 모드 '시작 조언' — 빈 외곽(+이미 그린 방·고정 방·트렌드)을 보고 AI가 텍스트 조언만.
    좌표·벽 생성 안 함(generate-layout과 별개). 기존 ai-advice 무수정."""
    boundary = payload.get("boundary_mm") or []
    if len(boundary) < 3:
        raise HTTPException(400, "boundary_mm 좌표가 3개 미만입니다.")
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise HTTPException(500, "ANTHROPIC_API_KEY가 설정되지 않았습니다 (backend/.env 확인).")

    unit = (payload.get("unit") or "").strip() or None
    orientation = (payload.get("building_orientation") or "").strip() or None
    fixed_rooms = payload.get("fixed_rooms") or []
    current_rooms = payload.get("current_rooms") or []

    def _as_int(v):
        try:
            n = int(v)
            return n if n > 0 else None
        except (TypeError, ValueError):
            return None
    bedrooms = _as_int(payload.get("bedrooms"))
    baths = _as_int(payload.get("baths"))
    trend = (payload.get("trend") or "").strip()[:2000]   # 길이 cap

    context = _build_design_advice_context(
        boundary, unit, orientation, fixed_rooms, bedrooms, baths, current_rooms, trend)

    try:
        from anthropic import Anthropic
        client = Anthropic(api_key=api_key)
        msg = client.messages.create(
            model="claude-sonnet-4-6", max_tokens=1600,
            system=_DESIGN_ADVICE_SYSTEM,
            messages=[{"role": "user", "content": context}],
        )
        raw_answer = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
    except Exception as e:
        raise HTTPException(500, f"설계 조언 생성 실패: {str(e)}")

    # 텍스트 조언 + zones JSON 분리. zones는 '대략적 위치 존'(가이드) — 실패해도 조언은 살림.
    answer, zones = raw_answer, []
    try:
        prose, json_str = _split_advice_and_zones(raw_answer)
        answer = prose or raw_answer        # prose가 비면 원문 폴백(조언은 절대 안 잃음)
        if json_str:
            from shapely.geometry import Polygon
            bpoly = Polygon([(float(x), float(y)) for x, y in boundary])
            if not bpoly.is_valid:
                bpoly = bpoly.buffer(0)
            for c in _clipped_rooms(_parse_zones_json(json_str), bpoly):
                ring = list(c["poly"].exterior.coords)
                zones.append({
                    "name": c["name"],
                    "poly": [[round(px, 1), round(py, 1)] for px, py in ring],
                    "area_m2": c["area_m2"],
                })
    except Exception as ze:
        # 존 추출 실패는 조언을 막지 않는다 (graceful degradation)
        print(f"[design-advice] zones 추출 실패(무시): {ze}")
        zones = []

    return JSONResponse({"answer": answer, "zones": zones, "context": context})


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

# ── 건축 규격 최소 기준 (mm·㎡, 보편 주거 기준 — 도면 무관, 하드코딩 아님) ──────
#   min_w: 폭 최소(거실은 '한 변'=긴 변, 그 외는 '짧은 변') / min_area: 면적 최소
#   daylight: 외벽(세대 외곽)에 접해 채광 가능해야 하는가 (HARD)
_ARCH = {
    "bedroom": {"min_w": 2400, "min_area": 7.0,  "daylight": True},
    "bath":    {"min_w": 1500, "min_area": 3.0,  "daylight": False},
    "kitchen": {"min_w": 1800, "min_area": 0.0,  "daylight": False},
    "living":  {"min_w": 3300, "min_area": 12.0, "daylight": True},
    "entry":   {"min_w": 0,    "min_area": 0.0,  "daylight": False},
    "utility": {"min_w": 0,    "min_area": 0.0,  "daylight": False},
    "balcony": {"min_w": 0,    "min_area": 0.0,  "daylight": False},
    "other":   {"min_w": 0,    "min_area": 0.0,  "daylight": False},
}
_DAYLIGHT_MIN_LEN = 1000      # 채광면 인정 최소 공유 변 길이(mm)
_WATER_CATS = ("bath", "kitchen", "utility")   # 물 쓰는 공간(배관 인접 대상)
_CORRIDOR_MIN = 900           # 동선 폭 최소(mm) — 프롬프트만, 자동검증 안 함


def _room_category(name):
    """방 이름 → 용도 카테고리. ★순서 주의: '주방'은 '방'을 포함하므로 주방을 먼저 매칭."""
    n = (name or "").strip()
    nu = n.upper()
    if any(k in n for k in ("주방", "부엌")):
        return "kitchen"
    if ("거실" in n) or ("LDK" in nu) or ("리빙" in n):
        return "living"
    if any(k in n for k in ("욕실", "화장실", "세면", "샤워")):
        return "bath"
    if any(k in n for k in ("다용도", "팬트리", "창고", "드레스", "보일러", "세탁")):
        return "utility"
    if "현관" in n:
        return "entry"
    if any(k in n for k in ("발코니", "베란다")):
        return "balcony"
    if any(k in n for k in ("침실", "안방", "방")):
        return "bedroom"
    return "other"


def _poly_sides(poly):
    x0, y0, x1, y1 = poly.bounds
    return (x1 - x0), (y1 - y0)


def _short_side(poly):
    w, h = _poly_sides(poly)
    return min(w, h)


def _long_side(poly):
    w, h = _poly_sides(poly)
    return max(w, h)


def _line_len(geom):
    """LineString/MultiLineString/GeometryCollection의 선 길이 합 (점은 무시)."""
    if geom is None or geom.is_empty:
        return 0.0
    gt = geom.geom_type
    if gt == "LineString":
        return geom.length
    if gt in ("MultiLineString", "GeometryCollection"):
        return sum(g.length for g in geom.geoms if g.geom_type in ("LineString", "MultiLineString"))
    return 0.0


def _touches_outer(room_poly, outline_poly, min_len=_DAYLIGHT_MIN_LEN):
    """방이 세대 외곽 경계에 min_len(기본 1m) 이상 접하면 채광면 있음 → True.
    기준=outline_poly(세대 외곽 bpoly, avail 아님). 노이즈 대비 buffer(50) 보정."""
    try:
        shared = room_poly.exterior.intersection(outline_poly.exterior)
        if _line_len(shared) >= min_len:
            return True
        band = outline_poly.exterior.buffer(50)
        return _line_len(room_poly.exterior.intersection(band)) >= min_len
    except Exception:
        return False


def _edge_directions(orientation):
    """'도면 위쪽=orientation'에서 외곽 bbox 4변의 방위.
    mm 규약: top=작은 y(도면 상단), bottom=큰 y, left=작은 x, right=큰 x.
    시계방향(top→right→bottom→left) = 나침반(북→동→남→서). 모름이면 None."""
    o = (orientation or "").strip()
    compass = ["북", "동", "남", "서"]
    if o not in compass:
        return None
    i = compass.index(o)
    return {
        "top":    compass[i],
        "right":  compass[(i + 1) % 4],
        "bottom": compass[(i + 2) % 4],
        "left":   compass[(i + 3) % 4],
    }


def _room_facings(room_poly, outline_poly, edge_dirs, min_len=_DAYLIGHT_MIN_LEN, tol=120):
    """방이 외곽 bbox의 어느 변에 (실제 외벽 길이로) 접하는지 → 방위 집합.
    edge_dirs None이거나 채광 접함이 없으면 빈 set. (비직사각 외곽은 bbox 변 근사.)"""
    if not edge_dirs:
        return set()
    if not _touches_outer(room_poly, outline_poly, min_len):
        return set()
    ox0, oy0, ox1, oy1 = outline_poly.bounds
    rx0, ry0, rx1, ry1 = room_poly.bounds
    rw, rh = (rx1 - rx0), (ry1 - ry0)
    f = set()
    if abs(ry0 - oy0) <= tol and rw >= min_len:
        f.add(edge_dirs["top"])
    if abs(ry1 - oy1) <= tol and rw >= min_len:
        f.add(edge_dirs["bottom"])
    if abs(rx0 - ox0) <= tol and rh >= min_len:
        f.add(edge_dirs["left"])
    if abs(rx1 - ox1) <= tol and rh >= min_len:
        f.add(edge_dirs["right"])
    return f


def _water_connected(polys):
    """물 쓰는 공간들이 서로 인접(배관 효율)하면 True. buffer(60)로 격자 근접 인정."""
    from shapely.ops import unary_union
    try:
        u = unary_union([p.buffer(60) for p in polys])
        return u.geom_type == "Polygon"
    except Exception:
        return True   # 판정 불가 시 경고 안 냄


def _orientation_violations(clipped, outline_poly, edge_dirs):
    """방위별 배치 SOFT 검증: 거실이 남향 미접 / 물공간이 남향 점유."""
    V = []
    living_exists, living_faces, water_on_south = False, set(), []
    for c in clipped:
        cat = _room_category(c["name"])
        faces = _room_facings(c["poly"], outline_poly, edge_dirs)
        if cat == "living":
            living_exists = True
            living_faces |= faces
        if cat in _WATER_CATS and "남" in faces:
            water_on_south.append(c["name"])
    if living_exists and "남" not in living_faces:
        V.append({"severity": "soft", "cat": "living", "name": "거실", "rule": "orient",
                  "msg": "거실이 남향 외벽에 닿지 않음 (가능하면 남향 배치 권장)"})
    if water_on_south:
        nm = "·".join(water_on_south)
        V.append({"severity": "soft", "cat": "water", "name": nm, "rule": "orient",
                  "msg": f"물 쓰는 공간({nm})이 남향을 점유 (북향 권장)"})
    return V


def _validate_layout(clipped, outline_poly, bedrooms_req, baths_req, orientation=None):
    """건축 규칙 검증 → violations 리스트 [{severity:'hard'|'soft', cat, name, rule, msg}].
    채광 기준=outline_poly(세대 외곽). 방위 알면 방향별 SOFT 추가."""
    edge_dirs = _edge_directions(orientation)
    V, cats = [], []
    for c in clipped:
        cat = _room_category(c["name"])
        cats.append(cat)
        poly, nm = c["poly"], c["name"]
        area = poly.area / 1e6
        spec = _ARCH.get(cat, _ARCH["other"])
        side = _long_side(poly) if cat == "living" else _short_side(poly)
        if spec["min_w"] and side < spec["min_w"] - 1:
            V.append({"severity": "hard", "cat": cat, "name": nm, "rule": "width",
                      "msg": f"{nm} 폭 {side/1000:.2f}m < 최소 {spec['min_w']/1000:.1f}m"})
        if spec["min_area"] and area < spec["min_area"] - 0.05:
            V.append({"severity": "hard", "cat": cat, "name": nm, "rule": "area",
                      "msg": f"{nm} 면적 {area:.1f}㎡ < 최소 {spec['min_area']:.0f}㎡"})
        if spec["daylight"] and not _touches_outer(poly, outline_poly):
            V.append({"severity": "hard", "cat": cat, "name": nm, "rule": "daylight",
                      "msg": f"{nm}이(가) 외벽에 닿지 않아 채광이 없음"})
    if "living" not in cats:
        V.append({"severity": "hard", "cat": "living", "name": "거실", "rule": "missing",
                  "msg": "거실이 없음 (채광·동선 허브 필수)"})
    nbed = sum(1 for c in cats if c == "bedroom")
    nbath = sum(1 for c in cats if c == "bath")
    if nbed != bedrooms_req:
        V.append({"severity": "soft", "cat": "bedroom", "name": "", "rule": "count",
                  "msg": f"침실 {nbed}개 (요청 {bedrooms_req}개)"})
    if nbath != baths_req:
        V.append({"severity": "soft", "cat": "bath", "name": "", "rule": "count",
                  "msg": f"욕실 {nbath}개 (요청 {baths_req}개)"})
    water = [c["poly"] for c in clipped if _room_category(c["name"]) in _WATER_CATS]
    if len(water) >= 2 and not _water_connected(water):
        V.append({"severity": "soft", "cat": "water", "name": "", "rule": "plumbing",
                  "msg": "물 쓰는 공간(욕실·주방·다용도)이 서로 떨어져 배관 비효율"})
    if edge_dirs:
        V += _orientation_violations(clipped, outline_poly, edge_dirs)
    return V


def _violations_feedback(violations, clipped):
    """HARD 위반 → 재요청용 구체 피드백 텍스트(직전 배치 방 목록 동봉). HARD 없으면 ''."""
    hard = [v for v in violations if v["severity"] == "hard"]
    if not hard:
        return ""
    lines = ["[직전 시도의 규칙 위반 — 반드시 모두 고쳐라]"]
    for v in hard:
        lines.append(f"- {v['msg']}")
    if clipped:
        lines.append("[직전 배치 — 이 좌표를 수정해 위반을 해소하라]")
        for c in clipped[:12]:
            x0, y0, x1, y1 = c["grid_bbox"]
            lines.append(f"  · {c['name']}: x∈[{x0},{x1}], y∈[{y0},{y1}] ({c['area_m2']}㎡)")
    lines.append("위 위반을 모두 고쳐 다시 JSON rooms만 출력하라.")
    return "\n".join(lines)


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


def _max_inscribed_rect(region, target_cells=56):
    """region(Polygon/MultiPolygon) 안의 최대 축정렬 내접 직사각형 근사.
    긴 변을 target_cells개로 격자화 → 셀 중심 in region 불리언 → 히스토그램 DP로 최대
    1-직사각형 → mm 환산 후 '안쪽으로' 100mm 스냅(밖으로 안 삐져나가게). 퇴화 시 None.
    AI에게 '여기 안은 안 잘린다'는 안전 배치 영역을 주기 위함."""
    import math as _math
    from shapely.geometry import Point
    from shapely.prepared import prep
    if region is None or region.is_empty:
        return None
    minx, miny, maxx, maxy = region.bounds
    W, H = maxx - minx, maxy - miny
    if W <= 0 or H <= 0:
        return None
    cell = max(W, H) / target_cells
    if cell <= 0:
        return None
    cols = max(1, int(_math.ceil(W / cell)))
    rows = max(1, int(_math.ceil(H / cell)))
    pr = prep(region)
    # 셀의 '네 모서리'가 모두 영역에 덮이면(covers=경계 포함) 그 셀은 영역에 완전히 든다.
    # (중심만 보면 빗변 등에서 사각형이 영역 밖으로 삐져나옴 → 셀 모서리 기준으로 보수적 판정.)
    col_in = [[pr.covers(Point(minx + c * cell, miny + r * cell))
               for c in range(cols + 1)] for r in range(rows + 1)]
    grid = [[col_in[r][c] and col_in[r][c + 1] and col_in[r + 1][c] and col_in[r + 1][c + 1]
             for c in range(cols)] for r in range(rows)]
    best = (0, 0, 0, 0, 0)   # area_cells, r0, c0, r1, c1
    heights = [0] * cols
    for r in range(rows):
        for c in range(cols):
            heights[c] = heights[c] + 1 if grid[r][c] else 0
        stack = []   # 히스토그램 최대 직사각형(스택)
        c = 0
        while c <= cols:
            h = heights[c] if c < cols else 0
            start = c
            while stack and stack[-1][1] >= h:
                s_c, s_h = stack.pop()
                area = s_h * (c - s_c)
                if area > best[0]:
                    best = (area, r - s_h + 1, s_c, r, c - 1)
                start = s_c
            stack.append((start, h))
            c += 1
    if best[0] == 0:
        return None
    _, r0, c0, r1, c1 = best
    x0 = minx + c0 * cell
    x1 = minx + (c1 + 1) * cell
    y0 = miny + r0 * cell
    y1 = miny + (r1 + 1) * cell
    # 안쪽으로 100mm 스냅 (밖으로 안 삐져나가게)
    sx0 = _math.ceil(x0 / 100) * 100
    sy0 = _math.ceil(y0 / 100) * 100
    sx1 = _math.floor(x1 / 100) * 100
    sy1 = _math.floor(y1 / 100) * 100
    if sx1 <= sx0 or sy1 <= sy0:
        return None
    return (sx0, sy0, sx1, sy1)


_LAYOUT_SYSTEM = (
    "당신은 시공업자를 돕는 주거 평면 설계 보조다. 주어진 '빈 외곽'(한 세대의 벽 없는 "
    "내부 공간) 안에 방을 '축정렬 직사각형'으로 배치한 평면 초안을 만든다. 벽 선분이 "
    "아니라 '방 직사각형 목록'을 출력한다. 결과는 시공업자가 직접 편집할 출발점이다.\n"
    "[방 프로그램 — 반드시 지킴]\n"
    "1. '방'은 침실만 의미한다. 침실을 정확히 N개, 욕실(화장실)을 정확히 M개 둔다. "
    "개수를 임의로 바꾸지 마라.\n"
    "2. 침실 N·욕실 M에 더해 한국 주거의 기본 공용공간을 반드시 포함한다: "
    "현관, 거실, 주방(또는 거실과 합친 LDK), 다용도실. "
    "단 '이미 고정된 공간'으로 제시된 용도는 이미 배치된 것이니 다시 만들지 마라.\n"
    "3. 거실은 동선의 허브다. 현관·주방·욕실·모든 침실이 거실에 '직접 접하도록'(거실의 "
    "변과 맞붙도록) 배치한다. 빌라 규모이므로 별도 복도 없이 거실이 동선 공간을 겸한다.\n"
    "4. 막힌 방 금지: 어떤 방도 다른 침실을 거쳐야만 들어가는 구조여선 안 된다. 모든 방은 "
    "거실 또는 현관에서 직접 들어갈 수 있어야 한다.\n"
    "[기하 — 반드시 지킴]\n"
    "5. 모든 방은 축정렬 직사각형이며 좌표(x,y,w,h)는 100mm 격자(100의 배수)다.\n"
    "6. 모든 방은 외곽 bbox(가로 W × 세로 H) 안에만 둔다. "
    "'이미 고정된 공간'의 영역은 절대 침범하지 마라(겹치는 방을 만들지 마라).\n"
    "7. 인접한 방끼리는 변을 '정확히 공유'하며 맞붙는다(같은 좌표의 변을 공유). 방과 방 "
    "사이에 빈틈을 두지 마라 — 닫히지 않는 빈 공간이 생기면 안 된다.\n"
    "8. 빈틈은 '방들과 불규칙한 외곽선 사이'에만 허용된다(직사각형으로 외곽을 완벽히 채울 "
    "수 없으므로). 방과 방 사이에는 절대 빈틈이 없어야 한다.\n"
    "9. 방끼리 겹치지 마라(겹치면 면이 엉킨다).\n"
    "10. 일반 주거 상식: 거실/LDK는 크게, 침실 9~12㎡, 욕실 4~5㎡, 현관/다용도실은 작게.\n"
    "[건축 규격·채광·방위 — 반드시 지킴]\n"
    "11. 규격 최소치(미달 시 거부됨): 침실은 폭(짧은 변) 2.4m·면적 7㎡ 이상, "
    "욕실은 1.5m×2.0m(3㎡) 이상, 주방은 폭 1.8m 이상, 거실은 한 변 3.3m·면적 12㎡ 이상, "
    "통로(동선) 폭 0.9m 이상.\n"
    "12. 채광: 거실과 모든 침실은 외곽 경계(외벽)에 한 변이 닿아 창을 낼 수 있어야 한다. "
    "욕실·주방·다용도실은 내부(외벽 비접)에 둬도 된다.\n"
    "13. 거실을 건물 한가운데 가두지 마라: 침실이 외벽을 독점하고 거실이 속에 갇히면 안 된다. "
    "거실이 먼저 외벽 채광을 확보하고, 침실은 남는 외벽에 배치한다.\n"
    "14. 배관 효율: 물 쓰는 공간(욕실·주방·다용도실)은 서로 인접 배치한다.\n"
    "15. 방위가 주어지면: 남향 외벽=거실·침실 우선, 북향=욕실·주방·다용도실, "
    "동향=침실(아침 햇빛), 서향=거실 가능하나 과열 주의.\n"
    "[출력] 응답의 첫 글자는 반드시 '{'. 산문·사고과정·설명·영어·마크다운 펜스 금지, "
    "오직 JSON만 출력한다. 형식:\n"
    '{"rooms":[{"name":"거실","x":0,"y":0,"w":4000,"h":3000}, ...]}  '
    "(x,y=좌상단 mm, w=너비, h=높이, 모두 100의 배수 정수)"
)


def _build_layout_prompt(boundary, bbox, area_m2, unit, rooms, baths,
                         fixed_rooms=None, avail_bbox=None, inner_rect=None,
                         building_orientation=None, feedback=None):
    """레이아웃 생성 프롬프트.
    고정 방이 있으면 전체 외곽 bbox + 점유 구역을 100mm 격자 스냅 좌표 + 비겹침 조건으로
    명시. 'avail_bbox'는 고정방 제거 후에도 외곽과 거의 같아 AI에게 misleading → 사용 안 함.
    실제 방어선은 백엔드의 avail 교차 클립(이중 안전망).
    building_orientation: '북/남/동/서'면 외곽 변별 방위 + 방향별 배치 힌트 삽입(모름이면 생략).
    feedback: 재생성 시 직전 시도 위반 텍스트(있으면 맨 끝에 첨부)."""
    import math as _math
    bx0, by0, bx1, by1 = bbox
    fixed_rooms = fixed_rooms or []
    base_public = ["현관", "거실", "주방(또는 LDK)", "다용도실"]
    fixed_names = [str(fr.get("name") or "").strip() for fr in fixed_rooms]
    fixed_names = [n for n in fixed_names if n]
    public_todo = [p for p in base_public
                   if not any(n and (n in p or p.split("(")[0] in n) for n in fixed_names)]
    public_str = "·".join(public_todo) if public_todo else "(추가 공용공간 없음 — 이미 고정됨)"

    lines = [f"[세대] {unit or '미지정'}"]
    lines.append(
        f"[배치 가능 전체 영역 bbox] 좌상단 ({int(bx0)},{int(by0)}) ~ 우하단 ({int(bx1)},{int(by1)}) "
        f"= 가로 {round((bx1 - bx0) / 1000, 2)}m × 세로 {round((by1 - by0) / 1000, 2)}m, "
        f"전용면적 약 {area_m2}㎡"
    )

    # 안전 배치 영역(최대 내접 직사각형): 이 안은 절대 안 잘림 → 큰 방 우선 앵커
    if inner_rect:
        ix0, iy0, ix1, iy1 = inner_rect
        iarea = round((ix1 - ix0) * (iy1 - iy0) / 1e6, 1)
        lines.append(
            f"[안전 배치 영역] [{int(ix0)},{int(iy0)},{int(ix1)},{int(iy1)}] = {iarea}㎡. "
            f"이 직사각형 안은 절대 잘리지 않는다. 거실과 큰 침실을 먼저 이 영역 안에 "
            f"앵커로 배치하고, 나머지 방은 외곽 폴리곤을 참고하되 이 영역에서 멀어질수록 "
            f"잘릴 위험이 커진다."
        )

    # 방위: 외곽 4변에 방위 라벨 + 방향별 배치 힌트
    ed = _edge_directions(building_orientation)
    if ed:
        side_label = {"top": f"상단변 y≈{int(by0)}", "bottom": f"하단변 y≈{int(by1)}",
                      "left": f"좌변 x≈{int(bx0)}", "right": f"우변 x≈{int(bx1)}"}
        side_of = {v: k for k, v in ed.items()}   # 방위 → 변
        def _hint(d):
            return side_label[side_of[d]] if d in side_of else "?"
        lines.append(
            f"[방위] 도면 위쪽(y 작은 쪽)={building_orientation}쪽. "
            f"외곽 {side_label['top']}={ed['top']}, {side_label['bottom']}={ed['bottom']}, "
            f"{side_label['left']}={ed['left']}, {side_label['right']}={ed['right']}.\n"
            f"  · 거실·침실은 남향({_hint('남')}) 외벽에 붙여 채광을 확보하라.\n"
            f"  · 욕실·주방·다용도실은 북향({_hint('북')})/내부에 배치하라.\n"
            f"  · 동향({_hint('동')})=침실(아침 햇빛), 서향({_hint('서')})=거실 가능하나 과열 주의."
        )

    if fixed_rooms:
        lines.append(
            "[★이미 점유된 구역 — 아래 각 구역과 1mm라도 겹치는 방을 절대 만들지 마라]"
        )
        for fr in fixed_rooms:
            fxs = [float(p[0]) for p in fr.get("poly") or []]
            fys = [float(p[1]) for p in fr.get("poly") or []]
            if not fxs or not fys:
                continue
            # 100mm 격자 스냅: 아래쪽 floor, 위쪽 ceil → 고정 방 bbox를 완전히 포함
            gx0 = int(min(fxs) / 100) * 100
            gy0 = int(min(fys) / 100) * 100
            gx1 = _math.ceil(max(fxs) / 100) * 100
            gy1 = _math.ceil(max(fys) / 100) * 100
            name = str(fr.get("name") or "고정방")
            lines.append(
                f"  · [{name}] 점유 구역: x∈[{gx0},{gx1}], y∈[{gy0},{gy1}]\n"
                f"    새 방 rect(x,y,w,h)가 이 구역과 겹치지 않으려면:\n"
                f"    (x+w<={gx0}) 또는 (x>={gx1}) 또는 (y+h<={gy0}) 또는 (y>={gy1}) 중 하나 만족 필수."
            )
        fixed_label = "·".join(fixed_names) if fixed_names else "고정 공간"
        lines.append(
            f"[요청] 위 '배치 가능 전체 영역 bbox' 안에서, 위의 점유 구역을 침범하지 말고 "
            f"나머지 빈 공간에만 침실 {rooms}개, 욕실 {baths}개 + 공용공간({public_str})을 "
            f"직사각형으로 배치하라. {fixed_label}은 이미 배치됐으니 다시 만들지 마라. "
            f"거실을 동선 허브로 모든 공간이 거실에 접하게 하고, 방 사이 빈틈 없이 변을 공유하라. "
            f"JSON rooms만 출력."
        )
    else:
        coords = ", ".join(f"[{int(round(x))},{int(round(y))}]" for x, y in boundary)
        lines.append(f"[외곽 폴리곤 좌표(mm)]\n[{coords}]")
        lines.append(
            f"[요청] 이 외곽 안에 침실 {rooms}개, 욕실 {baths}개 + 기본 공용공간({public_str})을 "
            f"직사각형으로 배치하라. 거실을 동선 허브로 모든 공간이 거실에 접하게 하고, "
            f"방 사이 빈틈 없이 변을 공유하라. JSON rooms만 출력."
        )
    if feedback:
        lines.append(feedback)
    lines.append("출력은 첫 글자가 '{'인 JSON만. 산문·설명·사고과정·영어 금지.")
    return "\n".join(lines)


def _snap_grid(v):
    return int(round(v / _GRID_MM) * _GRID_MM)


def _parse_rooms_json(text):
    """AI 응답 텍스트 → rooms 리스트 [{name,x,y,w,h}]. 펜스 제거 후 parse, 실패 시 첫 {...} 재시도."""
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
    raw = obj.get("rooms") or []
    out = []
    for r in raw:
        try:
            x, y, w, h = float(r["x"]), float(r["y"]), float(r["w"]), float(r["h"])
            if w <= 0 or h <= 0:
                continue
            name = str(r.get("name") or "").strip() or "방"
            out.append({"name": name, "x": x, "y": y, "w": w, "h": h})
        except Exception:
            continue
    return out


def _split_advice_and_zones(text):
    """설계 조언 응답(텍스트 + zones JSON 펜스) → (prose, json_str).
    AI는 불릿 조언 뒤에 ```json {"zones":[...]}``` 블록을 붙인다. prose에는 JSON이
    남지 않게 떼어내고, JSON 문자열만 따로 반환. 펜스가 없으면 (원문, "")."""
    import re
    s = text or ""
    # 1) ```json ... ``` (또는 ``` ... ```) 펜스 우선
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", s, re.DOTALL)
    if m:
        prose = (s[:m.start()] + s[m.end():]).strip()
        return prose, m.group(1)
    # 2) 펜스 없이 "zones"를 포함한 마지막 {...} 블록 (산문 혼합 대비)
    candidates = list(re.finditer(r"\{[^{}]*\"zones\"[\s\S]*?\}\s*\]?\s*\}", s))
    if candidates:
        m = candidates[-1]
        prose = (s[:m.start()] + s[m.end():]).strip()
        return prose, m.group(0)
    # 3) "zones"가 어디엔가 있으면 첫 { 부터 끝까지를 JSON 후보로
    idx = s.find("\"zones\"")
    if idx != -1:
        brace = s.rfind("{", 0, idx)
        if brace != -1:
            prose = s[:brace].strip()
            return prose, s[brace:].strip()
    return s.strip(), ""


def _parse_zones_json(text):
    """zones JSON → [{name,x,y,w,h}]. _parse_rooms_json과 동일 로직(키만 'zones').
    펜스/산문혼합/깨짐/w0 robust. 실패 시 [] (조언 텍스트는 별도로 살린다)."""
    import json, re
    s = (text or "").strip()
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
    raw = obj.get("zones") or []
    out = []
    for r in raw:
        try:
            x, y, w, h = float(r["x"]), float(r["y"]), float(r["w"]), float(r["h"])
            if w <= 0 or h <= 0:
                continue
            name = str(r.get("name") or "").strip() or "방"
            out.append({"name": name, "x": x, "y": y, "w": w, "h": h})
        except Exception:
            continue
    return out


def _clipped_rooms(rects, clip_region):
    """AI rect 목록 → 생존 방 [{name, poly(shapely), area_m2, grid_bbox(x0,y0,x1,y1)}].
    격자 스냅 → clip_region 교차 클립(영역 밖·고정 영역 통과 금지) → <1㎡ drop →
    MultiPolygon이면 최대 조각. 검증·렌더가 같은 기하를 쓰도록 공용화한 헬퍼."""
    from shapely.geometry import Polygon
    out = []
    for r in rects:
        x0 = _snap_grid(r["x"]); y0 = _snap_grid(r["y"])
        x1 = _snap_grid(r["x"] + r["w"]); y1 = _snap_grid(r["y"] + r["h"])
        if x1 - x0 < _GRID_MM or y1 - y0 < _GRID_MM:
            continue
        rect = Polygon([(x0, y0), (x1, y0), (x1, y1), (x0, y1)])
        try:
            clip = rect.intersection(clip_region)   # buffer 없는 정확 클립
        except Exception:
            continue
        if clip.is_empty or clip.area < 1e6:   # <1㎡ 버림
            continue
        if clip.geom_type == "MultiPolygon":
            clip = max(clip.geoms, key=lambda g: g.area)
        out.append({
            "name": r["name"], "poly": clip,
            "area_m2": round(clip.area / 1e6, 2),
            "grid_bbox": (x0, y0, x1, y1),
        })
    return out


def _rects_to_rooms_and_walls(rects, boundary, clip_poly=None):
    """AI 직사각형 목록 → (생존 방 리스트, 벽 선분 리스트).
    _clipped_rooms로 생존 방을 구한 뒤 representative_point로 중심, 생존 rect의 격자 4변을
    모아 _postprocess_walls로 벽 생성(인접 공유변은 dedup으로 1개). 반환 2-tuple 보존.
    clip_poly: 주어지면(고정 방 제외 사용가능영역) 외곽 대신 이걸로 교차 클립(이중 안전망)."""
    from shapely.geometry import Polygon
    bpoly = Polygon([(float(x), float(y)) for x, y in boundary])
    if not bpoly.is_valid:
        bpoly = bpoly.buffer(0)
    clip_region = clip_poly if clip_poly is not None else bpoly

    clipped = _clipped_rooms(rects, clip_region)
    rooms_out, edge_segs = [], []
    for c in clipped:
        pt = c["poly"].representative_point()   # 항상 내부의 점
        rooms_out.append({
            "name": c["name"],
            "cx": round(pt.x, 1), "cy": round(pt.y, 1),
            "area_m2": c["area_m2"],
        })
        x0, y0, x1, y1 = c["grid_bbox"]   # 격자 사각형 4변 (클립 전) — 벽 선분 후보
        edge_segs.append({"a": [x0, y0], "b": [x1, y0]})
        edge_segs.append({"a": [x1, y0], "b": [x1, y1]})
        edge_segs.append({"a": [x1, y1], "b": [x0, y1]})
        edge_segs.append({"a": [x0, y1], "b": [x0, y0]})

    walls = _postprocess_walls(edge_segs, boundary, clip_poly=clip_poly)
    return rooms_out, walls


def _generate_with_retries(build_prompt, clip_region, outline_poly, boundary, clip_poly,
                           bedrooms_req, baths_req, orientation, caller, max_tries=3,
                           prose_budget=2):
    """AI 호출(caller(prompt)->텍스트)을 검증·재생성으로 감싼다.
    HARD 위반 0이면 즉시 채택, 아니면 _violations_feedback를 붙여 재요청(최대 max_tries).
    ★산문/파싱 실패는 정상 max_tries를 차감하지 않고 별도 prose_budget(기본 +2회)으로
    재시도(소예산 소진 후 또 실패하면 그때 정상 시도 1회로 친다 → 종료 보장).
    3회 후 best=min(HARD수, SOFT수, −방수). best가 ok=False이고 HARD가 침실 폭/면적에
    몰리면(형상 한계) warnings 최상단에 형상 경고를 prepend. caller 주입 가능(키 없이 단위검증).
    반환: (room_list, walls, warnings, ok, attempts, clipped) / 유효 시도 0이면 None
          (단, caller 예외만 있었으면 그 예외를 re-raise)."""
    feedback, last_exc = "", None
    attempts_log = []   # (hard, soft, nrooms, clipped, rects)
    valid_tries = 0
    while valid_tries < max_tries:
        prompt = build_prompt(feedback)
        n = len(attempts_log) + 1
        try:
            text = caller(prompt)
        except Exception as e:
            last_exc = e
            if prose_budget > 0:
                prose_budget -= 1
                feedback = "[직전 호출이 실패했다. 오직 {\"rooms\":[...]} JSON만 다시 출력하라.]"
                print(f"[generate-layout] call {n}: caller 예외 {e} (산문예산 사용, 정상시도 미차감)")
                continue
            attempts_log.append((999, 999, 0, [], []))
            valid_tries += 1
            feedback = "[직전 호출이 실패했다. 오직 {\"rooms\":[...]} JSON만 다시 출력하라.]"
            print(f"[generate-layout] call {n}: caller 예외 {e} (산문예산 소진)")
            continue
        rects = _parse_rooms_json(text)
        if not rects:
            if prose_budget > 0:
                prose_budget -= 1
                feedback = "[직전 출력이 JSON으로 파싱되지 않았다. 산문·펜스 없이 {\"rooms\":[...]} JSON만 출력하라.]"
                print(f"[generate-layout] call {n}: 파싱 실패 (산문예산 사용, 정상시도 미차감)")
                continue
            attempts_log.append((999, 999, 0, [], []))
            valid_tries += 1
            feedback = "[직전 출력이 JSON으로 파싱되지 않았다. 산문·펜스 없이 {\"rooms\":[...]} JSON만 출력하라.]"
            print(f"[generate-layout] call {n}: 파싱 실패 (산문예산 소진)")
            continue
        valid_tries += 1
        clipped = _clipped_rooms(rects, clip_region)
        V = _validate_layout(clipped, outline_poly, bedrooms_req, baths_req, orientation)
        hard = [v for v in V if v["severity"] == "hard"]
        soft = [v for v in V if v["severity"] == "soft"]
        attempts_log.append((len(hard), len(soft), len(clipped), clipped, rects))
        print(f"[generate-layout] attempt {valid_tries}/{max_tries}: HARD {len(hard)} · SOFT {len(soft)} · 방 {len(clipped)}")
        for v in hard:
            print(f"    HARD: {v['msg']}")
        for v in soft:
            print(f"    soft: {v['msg']}")
        if not hard:
            break
        feedback = _violations_feedback(V, clipped)

    valid = [a for a in attempts_log if a[2] > 0]
    if not valid:
        if last_exc:
            raise last_exc
        return None
    best = min(valid, key=lambda a: (a[0], a[1], -a[2]))
    hard_n, _soft_n, _n, clipped, rects = best
    room_list, walls = _rects_to_rooms_and_walls(rects, boundary, clip_poly=clip_poly)
    Vbest = _validate_layout(clipped, outline_poly, bedrooms_req, baths_req, orientation)
    warnings = [v["msg"] for v in Vbest]
    ok = (hard_n == 0)

    # 트리거2: 끝까지 ok=False이고 HARD가 '침실 폭/면적'에 몰리면 형상 한계 경고.
    # 규격 충족 침실(폭≥2.4m·면적≥7㎡·채광OK) 수 K를 세어 요청 침실 수 M > K이면 prepend.
    if not ok:
        bed_spec_hard = [v for v in Vbest if v["severity"] == "hard"
                         and v["cat"] == "bedroom" and v["rule"] in ("width", "area")]
        if bed_spec_hard:
            K = 0
            for c in clipped:
                if _room_category(c["name"]) != "bedroom":
                    continue
                poly = c["poly"]
                if (_short_side(poly) >= _ARCH["bedroom"]["min_w"] - 1
                        and poly.area / 1e6 >= _ARCH["bedroom"]["min_area"] - 0.05
                        and _touches_outer(poly, outline_poly)):
                    K += 1
            if bedrooms_req > K:
                N = round(outline_poly.area / 1e6)
                warnings.insert(
                    0,
                    f"이 세대 {N}㎡·형상상 규격 침실 {bedrooms_req}개는 어렵습니다"
                    f"(적합 {K}개). 좁은 모서리는 직접 마감하세요.",
                )

    return room_list, walls, warnings, ok, len(attempts_log), clipped


def _postprocess_walls(walls, boundary, clip_poly=None):
    """100mm 격자 스냅 → degenerate 제거 → dedup → 클립영역 buffer(50) 클립.
    클립 결과 MultiLineString은 조각화, 100mm 미만 조각은 버림.
    clip_poly: 주어지면 외곽 대신 사용가능영역(고정 방 제외)으로 벽을 클립."""
    from shapely.geometry import Polygon, LineString
    base = clip_poly if clip_poly is not None else Polygon([(float(x), float(y)) for x, y in boundary])
    bpoly = base.buffer(50)

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
    from shapely.ops import unary_union
    bpoly = Polygon([(float(x), float(y)) for x, y in boundary])
    if not bpoly.is_valid:
        bpoly = bpoly.buffer(0)
    area_m2 = round(bpoly.area / 1e6, 1)

    # 고정 방: JJ가 잠근 공간 [{name, poly}] — 사용가능영역에서 차감(이중 안전망의 클립 영역)
    fixed_in = payload.get("fixed_rooms") or []
    fixed_rooms = []
    fixed_polys = []
    for fr in fixed_in:
        poly = fr.get("poly") or []
        if len(poly) < 3:
            continue
        try:
            fp = Polygon([(float(p[0]), float(p[1])) for p in poly])
        except (TypeError, ValueError, IndexError):
            continue
        if not fp.is_valid:
            fp = fp.buffer(0)
        if fp.is_empty or fp.area <= 0:
            continue
        fixed_rooms.append({"name": str(fr.get("name") or "고정방").strip() or "고정방",
                            "poly": [[float(p[0]), float(p[1])] for p in poly]})
        fixed_polys.append(fp)

    # 사용가능영역 = 외곽 − ∪(고정 방). 고정 방 없으면 외곽 전체.
    avail = bpoly
    avail_bbox = None
    if fixed_polys:
        try:
            fixed_union = unary_union(fixed_polys)
            avail = bpoly.difference(fixed_union).buffer(0)
        except Exception as e:
            raise HTTPException(500, f"사용가능영역 계산 실패: {str(e)}")
        if avail.is_empty or avail.area < 2e6:   # 2㎡ 미만이면 배치할 공간이 없음
            raise HTTPException(
                422,
                "고정 방을 빼면 AI가 배치할 빈 공간이 거의 없습니다. 고정 방을 줄여 보세요.",
            )
        ab = avail.bounds   # (minx, miny, maxx, maxy)
        avail_bbox = (ab[0], ab[1], ab[2], ab[3])

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

    # 방위(향): 알면 방향별 배치 강화, 모름이면 채광 유무만
    orientation = (payload.get("building_orientation") or "").strip() or None

    # 트리거1(용량): 침실×7 + 거실12 + 욕실×3 최소 필요면적 > 사용가능면적이면 AI 호출 전 422.
    avail_area = round(avail.area / 1e6, 1)
    need = rooms * 7 + 12 + baths * 3
    if need > avail_area:
        raise HTTPException(
            422,
            f"전용 {avail_area}㎡로는 침실 {rooms}개+거실+욕실 {baths}개의 최소 {need}㎡를 "
            f"담을 수 없습니다. 방·욕실 개수를 줄여 보세요.",
        )

    # 안전 배치 영역(최대 내접 직사각형) — AI에게 '안 잘리는 구역' 힌트(큰 방 우선 앵커)
    inner_rect = _max_inscribed_rect(avail)

    clip_poly = avail if fixed_polys else None
    clip_region = avail   # 고정 없으면 avail==bpoly

    def _caller(prompt_text):
        from anthropic import Anthropic
        client = Anthropic(api_key=api_key)
        msg = client.messages.create(
            model="claude-sonnet-4-6", max_tokens=2048, system=_LAYOUT_SYSTEM,
            messages=[{"role": "user", "content": prompt_text}],
        )
        return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")

    def _bp(feedback):
        return _build_layout_prompt(
            boundary, bbox, area_m2, unit, rooms, baths,
            fixed_rooms=fixed_rooms, avail_bbox=avail_bbox, inner_rect=inner_rect,
            building_orientation=orientation, feedback=feedback,
        )

    # 검증·재생성 루프(최대 3회): HARD 위반 0이면 채택, 아니면 피드백 재요청.
    try:
        result = _generate_with_retries(
            _bp, clip_region, bpoly, boundary, clip_poly,
            rooms, baths, orientation, _caller, max_tries=3,
        )
    except Exception as e:
        raise HTTPException(500, f"AI 구조 생성 실패: {str(e)}")
    if result is None:
        raise HTTPException(
            422,
            "AI가 유효한 방을 만들지 못했습니다(파싱·생존 실패). 기존 작업은 그대로입니다. 다시 시도해 보세요.",
        )
    room_list, walls, warnings, ok, attempts, _clipped = result
    if not room_list or not walls:
        raise HTTPException(
            422,
            "AI 방이 외곽을 벗어나거나 너무 작아 남은 게 없습니다. 기존 작업은 그대로입니다. 다시 시도해 보세요.",
        )
    return JSONResponse({
        "walls": walls, "count": len(walls),
        "rooms": room_list, "bedrooms": rooms, "baths": baths,
        "warnings": warnings, "ok": ok, "attempts": attempts,
    })


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
        # 이름 + 면적 라벨 (centroid) — 이름 있으면 위에 이름, 아래에 면적
        cx = sum(p[0] for p in poly) / len(poly)
        cy = sum(p[1] for p in poly) / len(poly)
        area = rm.get("area_m2")
        name = (rm.get("name") or "").strip()
        c.setFillGray(0.15)
        if name:
            c.setFont(_KR_FONT, 9.5)
            c.drawCentredString(tx(cx), ty(cy) + 3, name)
            if area is not None:
                c.setFont(_KR_FONT, 8)
                c.setFillGray(0.35)
                c.drawCentredString(tx(cx), ty(cy) - 9, f"{area} m²")
            c.setFont(_KR_FONT, 9)
        elif area is not None:
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
