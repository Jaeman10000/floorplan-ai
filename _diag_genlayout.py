# -*- coding: utf-8 -*-
"""
generate-layout 직접 진단 — 빌라 page3(index=3) 30방 중 면적 5㎡+ 방들로 외곽 구성,
실제 AI 호출(ANTHROPIC_API_KEY 필요)로 attempt별 로그와 최종 응답을 확인한다.
"""
import sys, os, glob, json
sys.stdout.reconfigure(encoding="utf-8")

_root = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_root, "backend"))

# .env 로드 (ANTHROPIC_API_KEY)
env_path = os.path.join(_root, "backend", ".env")
if os.path.exists(env_path):
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

api_key = os.getenv("ANTHROPIC_API_KEY", "")
if not api_key:
    print("ERROR: ANTHROPIC_API_KEY 없음. backend/.env 확인.")
    sys.exit(1)
print(f"[키] prefix={api_key[:16]}...  len={len(api_key)}")
if api_key.startswith("sk-ant-sk-ant-"):
    print("WARNING: prefix 중복!")

import server as S
from shapely.geometry import Polygon, MultiPolygon
from shapely.ops import unary_union

# ── 1) 빌라 PDF page_index=3 파싱 ────────────────────────────────────────────
cands = [p for p in glob.glob(os.path.join(_root, "backend", "*.pdf"))
         if "빌라" in p or "역곡" in p]
assert cands, "backend/역곡동빌라.pdf 없음"
PDF = cands[0]
print(f"\n[PDF] {os.path.basename(PDF)}")
from pdf_parser import parse_pdf
data = parse_pdf(PDF, page_index=3)
rooms_data = data["rooms"]
print(f"  page_index=3 방 수={len(rooms_data)}")

# ── 2) 실제 A세대 방으로 외곽 계산 (unit-boundary 엔드포인트와 동일 로직) ──
# page_index=3(기본값), A_ROOMS = [0,1,2,11,20,21,24] (verify 스크립트와 동일)
A_ROOMS = [0, 1, 2, 11, 20, 21, 24]
print(f"\n[A세대] 방 인덱스={A_ROOMS}")
geoms = []
for i in A_ROOMS:
    if i >= len(rooms_data):
        print(f"  [{i}] 범위 초과"); continue
    r = rooms_data[i]
    poly = r.get("polygon_mm") or []
    if len(poly) < 3:
        continue
    try:
        p = Polygon([(float(x), float(y)) for x, y in poly])
        if not p.is_valid:
            p = p.buffer(0)
        if not p.is_empty and p.area > 0:
            geoms.append(p)
            area = r.get("area_m2") or round(p.area/1e6, 1)
            print(f"  [{i}] '{r.get('name','?')}' {area}㎡")
    except Exception as e:
        print(f"  [{i}] 폴리곤 오류: {e}")

assert geoms, "유효 방 폴리곤 없음"
merged = unary_union(geoms)
closed = merged.buffer(160).buffer(-160)
if closed.is_empty:
    closed = merged
if isinstance(closed, MultiPolygon):
    closed = max(closed.geoms, key=lambda g: g.area)
closed = closed.simplify(120, preserve_topology=True)

boundary = [[round(x, 1), round(y, 1)] for x, y in closed.exterior.coords[:-1]]
area_m2 = round(closed.area / 1e6, 1)
bpoly = Polygon([(float(x), float(y)) for x, y in boundary])
xs = [p[0] for p in boundary]
ys = [p[1] for p in boundary]
bbox = (min(xs), min(ys), max(xs), max(ys))

print(f"\n[외곽 결과] 정점={len(boundary)} 면적={area_m2}㎡")
print(f"  bbox: x={bbox[0]:.0f}~{bbox[2]:.0f}  y={bbox[1]:.0f}~{bbox[3]:.0f}")
print(f"  bbox 크기: {(bbox[2]-bbox[0])/1000:.1f}m × {(bbox[3]-bbox[1])/1000:.1f}m")

rooms_req, baths_req = 3, 1
orientation = "남"

# 내접 직사각형(안전 배치 영역) — 엔드포인트와 동일하게 avail(=bpoly, 고정 없음)로 계산
inner_rect = S._max_inscribed_rect(bpoly)
if inner_rect:
    ia = (inner_rect[2]-inner_rect[0])*(inner_rect[3]-inner_rect[1])/1e6
    print(f"\n[내접 안전영역] {inner_rect}  = {ia:.1f}㎡ (외곽 {area_m2}㎡의 {ia/area_m2*100:.0f}%)")

# 트리거1 용량 판정
need = rooms_req*7 + 12 + baths_req*3
print(f"[용량] need={need}㎡ vs avail={area_m2}㎡ → {'422 조기반환(AI 호출 안 함)' if need > area_m2 else 'AI 호출 진행'}")

# ── 3) 프롬프트 미리보기 ──────────────────────────────────────────────────────
prompt0 = S._build_layout_prompt(boundary, bbox, area_m2, "A", rooms_req, baths_req,
                                  inner_rect=inner_rect, building_orientation=orientation)
print(f"\n[프롬프트 앞 700자]\n{prompt0[:700]}\n{'='*60}")

# ── 4) AI caller (응답 전문 캡처) ─────────────────────────────────────────────
call_n = [0]

def _caller(prompt_text):
    call_n[0] += 1
    print(f"\n{'─'*50}")
    print(f"  >> AI 호출 #{call_n[0]} (prompt={len(prompt_text)}자)")
    from anthropic import Anthropic
    client = Anthropic(api_key=api_key)
    msg = client.messages.create(
        model="claude-sonnet-4-6", max_tokens=2048, system=S._LAYOUT_SYSTEM,
        messages=[{"role": "user", "content": prompt_text}],
    )
    text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
    print(f"  << 응답 {len(text)}자:")
    print(f"  {text[:1200]}")
    rects = S._parse_rooms_json(text)
    print(f"\n  파싱된 rect 수={len(rects)}")
    for r in rects:
        print(f"    {r}")
    clipped = S._clipped_rooms(rects, bpoly)
    print(f"  클립 후 생존={len(clipped)}")
    for c in clipped:
        print(f"    '{c['name']}' {c['area_m2']}㎡  bbox={c['grid_bbox']}")
    return text

def _bp(feedback):
    return S._build_layout_prompt(
        boundary, bbox, area_m2, "A", rooms_req, baths_req,
        inner_rect=inner_rect, building_orientation=orientation, feedback=feedback,
    )

# ── 5) 재생성 루프 실행 ───────────────────────────────────────────────────────
print(f"\n{'='*60}")
print(f"[generate-layout] rooms={rooms_req} baths={baths_req} orientation={orientation}")
print(f"{'='*60}")
try:
    result = S._generate_with_retries(
        _bp, bpoly, bpoly, boundary, None,
        rooms_req, baths_req, orientation, _caller, max_tries=3,
    )
except Exception as e:
    import traceback
    print(f"\n[예외] {type(e).__name__}: {e}")
    traceback.print_exc()
    sys.exit(1)

print(f"\n{'='*60}")
if result is None:
    print("[최종] None → 422 (3회 모두 파싱 또는 생존 실패)")
else:
    room_list, walls, warnings, ok, attempts, clipped = result
    print(f"[최종] walls={len(walls)} rooms={len(room_list)} ok={ok} attempts={attempts}")
    print(f"  warnings={warnings}")
    for r in room_list:
        print(f"  방: {r.get('name','?')} {r.get('area_m2')}㎡")
    if not room_list:
        print("  ⚠️  room_list 비어있음 → 422 (엔드포인트 조건)")
    if not walls:
        print("  ⚠️  walls 비어있음 → 422 (엔드포인트 조건)")
print(f"\n총 AI 호출={call_n[0]}")
