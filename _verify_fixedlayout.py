"""
고정 방 ↔ AI 초안 연결 — 프런트 경로 검증 (역곡동빌라 page3 A세대, 실제 클릭).

⚠️ ANTHROPIC_API_KEY 없이 동작하도록 /api/generate-layout 을 page.route로 '목'한다.
검증 대상(프런트):
  - 고정 방(현관) 그려 잠금 → designFixedRooms==1
  - AI 초안 생성 시 요청 body에 fixed_rooms 가 실린다(name·poly)
  - 주입 후 ensureLockedWalls 로 고정 방 벽 보존 → 고정 면 여전히 locked·designFixedRooms 유지
  - AI 주입 walls 전부 외곽 안
  - Ctrl+Z 로 AI 초안만 통째 취소 → 고정 방 보존(undo는 생성 직전=고정방 상태로 복귀)
  - JS 에러 0
배치 품질/실제 호출/avail 차감 정확성은 백엔드(_verify_fixedlayout_backend.py)·JJ 수동.
"""
import sys, glob, os, json
from playwright.sync_api import sync_playwright

sys.stdout.reconfigure(encoding="utf-8")
_root = os.path.dirname(os.path.abspath(__file__))
_cands = [p for p in glob.glob(os.path.join(_root, "backend", "*.pdf")) if ("빌라" in p or "역곡" in p)]
assert _cands, "backend/역곡동빌라.pdf를 찾지 못함"
PDF = _cands[0]
URL = "http://localhost:8000/"
A_ROOMS = [0, 1, 2, 11, 20, 21, 24]

PROJ = """
(xy) => {
  const v = new THREE.Vector3(xy[0]/1000, 0.03, xy[1]/1000).project(roomCamera);
  const cv = document.querySelector('#panel-asis canvas.three-canvas');
  const r = cv.getBoundingClientRect();
  return { x:(v.x*0.5+0.5)*r.width + r.left, y:(-v.y*0.5+0.5)*r.height + r.top };
}
"""
RECTS = """
() => {
  const b=designBoundary; let x0=1e9,y0=1e9,x1=-1e9,y1=-1e9;
  for(const p of b){x0=Math.min(x0,p[0]);y0=Math.min(y0,p[1]);x1=Math.max(x1,p[0]);y1=Math.max(y1,p[1]);}
  const cx=Math.round((x0+x1)/2/100)*100, cy=Math.round((y0+y1)/2/100)*100;
  const g=v=>Math.round(v/100)*100;
  // 고정방: 중심 좌측 작은 사각 (mock AI 벽과 안 겹치게)
  const A=[[g(cx-2600),g(cy-1300)],[g(cx-300),g(cy-1300)],[g(cx-300),g(cy+1300)],[g(cx-2600),g(cy+1300)]];
  return {A, centerA:[g(cx-1450),cy]};
}
"""
# 외곽 bbox 안 격자 mock {walls,rooms} — 고정방(좌측) 안 건드리는 perimeter+우측 divider
MOCK_WALLS_JS = """
() => {
  const b=designBoundary; let x0=1e9,y0=1e9,x1=-1e9,y1=-1e9;
  for(const p of b){x0=Math.min(x0,p[0]);y0=Math.min(y0,p[1]);x1=Math.max(x1,p[0]);y1=Math.max(y1,p[1]);}
  const g=v=>Math.round(v/100)*100;
  const mx0=g(x0+(x1-x0)*0.1), mx1=g(x1-(x1-x0)*0.1);
  const my0=g(y0+(y1-y0)*0.1), my1=g(y1-(y1-y0)*0.1);
  const cx=g((x0+x1)/2), cy=g((y0+y1)/2);
  const walls=[
    {a:[mx0,my0],b:[mx1,my0]},{a:[mx1,my0],b:[mx1,my1]},
    {a:[mx1,my1],b:[mx0,my1]},{a:[mx0,my1],b:[mx0,my0]},
    {a:[cx,my0],b:[cx,my1]},
  ];
  const rooms=[{name:'거실',cx:g((mx0+cx)/2),cy:cy,area_m2:10.0},
               {name:'침실',cx:g((cx+mx1)/2),cy:cy,area_m2:8.0}];
  return {walls,rooms};
}
"""
INSIDE = """
(walls) => {
  const b=designBoundary;
  let x0=1e9,y0=1e9,x1=-1e9,y1=-1e9;
  for(const p of b){x0=Math.min(x0,p[0]);y0=Math.min(y0,p[1]);x1=Math.max(x1,p[0]);y1=Math.max(y1,p[1]);}
  for(const w of walls) for(const p of [w.a,w.b]){
    if(!(p[0]>=x0-80&&p[0]<=x1+80&&p[1]>=y0-80&&p[1]<=y1+80)) return false;
  }
  return true;
}
"""


def screen(page, mm):
    return page.evaluate(PROJ, mm)

def click_mm(page, mm):
    p = screen(page, mm); page.mouse.move(p["x"], p["y"]); page.mouse.click(p["x"], p["y"]); page.wait_for_timeout(50)

def hover_mm(page, mm):
    p = screen(page, mm); page.mouse.move(p["x"], p["y"]); page.wait_for_timeout(30)

def draw_wall(page, a, b):
    click_mm(page, a); hover_mm(page, b); click_mm(page, b)

def draw_rect(page, corners):
    for i in range(4):
        draw_wall(page, corners[i], corners[(i + 1) % 4])


with sync_playwright() as pw:
    browser = pw.chromium.launch()
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("dialog", lambda d: d.accept())

    page.goto(URL)
    page.set_input_files("#file-input", PDF)
    page.wait_for_function("parsedData && parsedData.rooms && parsedData.rooms.length>0", timeout=60000)
    page.wait_for_timeout(700)
    page.evaluate("(r)=>{r.forEach(i=>{parsedData.rooms[i].unit='A';applyRoomBaseColor(i);});updateUnitCounts();}", A_ROOMS)
    page.click("#btn-design-mode")
    page.wait_for_function("designBoundary && designGroup && designUnit==='A'", timeout=20000)

    rc = page.evaluate(RECTS)

    # ── 1. 고정 방 그려 잠금 ──
    draw_rect(page, rc["A"])
    assert page.evaluate("designRooms.length") >= 1, "닫힌 방이 안 생김"
    page.click("#btn-fix-mode")
    click_mm(page, rc["centerA"])
    assert page.evaluate("selectedDesignFace") >= 0, "면 선택 실패"
    page.fill("#design-fix-input", "현관")
    page.click("#btn-lock-fixed")
    page.wait_for_timeout(150)
    nfix = page.evaluate("designFixedRooms.length")
    print(f"[1 고정] designFixedRooms={nfix} name={page.evaluate('designFixedRooms[0]&&designFixedRooms[0].name')}")
    assert nfix == 1, "고정 방 등록 실패"
    page.click("#btn-fix-mode")   # 고정모드 OFF (생성 위해)
    page.wait_for_timeout(50)

    # ── 2. /api/generate-layout 목 + 요청 body 캡처 ──
    mock = page.evaluate(MOCK_WALLS_JS)
    captured = {}
    def handle_gen(route):
        try:
            captured["body"] = json.loads(route.request.post_data)
        except Exception:
            captured["body"] = None
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps({"walls": mock["walls"], "count": len(mock["walls"]),
                                       "rooms": mock["rooms"], "bedrooms": 2, "baths": 1}))
    page.route("**/api/generate-layout", handle_gen)

    page.fill("#gen-rooms", "2")
    page.fill("#gen-baths", "1")
    page.click("#btn-generate-layout")
    page.wait_for_function("designRoomNames.length > 0", timeout=15000)
    page.wait_for_timeout(150)

    # ── 3. 요청 body에 fixed_rooms 실림 ──
    body = captured.get("body") or {}
    fr = body.get("fixed_rooms") or []
    print(f"[3 body] fixed_rooms={len(fr)} name={fr[0]['name'] if fr else None} poly점={len(fr[0]['poly']) if fr else 0}")
    assert len(fr) == 1, "요청 body에 fixed_rooms 미포함"
    assert fr[0]["name"] == "현관" and len(fr[0]["poly"]) >= 3, "fixed_rooms 형식 불량"

    # ── 4. 주입 후 고정 방 보존 (ensureLockedWalls) ──
    inside = page.evaluate(INSIDE, page.evaluate("designWalls"))
    nfix2 = page.evaluate("designFixedRooms.length")
    lockedFaces = page.evaluate("designRooms.filter(r=>r.locked && r.name==='현관').length")
    print(f"[4 주입후] 벽전부외곽안={inside} designFixedRooms={nfix2} 현관잠금면={lockedFaces}")
    assert inside, "주입 벽이 외곽 밖"
    assert nfix2 == 1, "AI 주입 후 고정 방이 사라짐"
    assert lockedFaces >= 1, "AI 주입 후 현관 잠금 면이 사라짐"

    # ── 5. Ctrl+Z: AI 초안 통째 취소 + 고정 방 보존 ──
    page.keyboard.press("Control+z"); page.wait_for_timeout(150)
    nfix3 = page.evaluate("designFixedRooms.length")
    lockedAfterUndo = page.evaluate("designRooms.filter(r=>r.locked && r.name==='현관').length")
    print(f"[5 undo] designFixedRooms={nfix3} 현관잠금면={lockedAfterUndo}")
    assert nfix3 == 1, "Ctrl+Z 후 고정 방이 사라짐"
    assert lockedAfterUndo >= 1, "Ctrl+Z 후 현관 잠금 면이 사라짐"

    print("page errors:", errors if errors else "없음")
    assert not errors, f"페이지 에러: {errors}"
    print("🎉 고정 방↔AI 초안 연결 프런트 검증 통과 (body fixed_rooms·보존·외곽안·undo·에러0)")
    browser.close()
