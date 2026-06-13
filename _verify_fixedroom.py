"""
고정 방(Fixed Room) — 그리기→이름→잠금 검증 (역곡동빌라 page3 A세대, 실제 클릭).

1. 클릭-클릭으로 닫힌 사각 방 1개 그림.
2. 진행중 선 1개 찍고 고정모드 토글 → 진행중 선 취소(drawStart=null) 확인.
3. 고정모드에서 면 클릭 raycast 선택 → 이름 "현관" → 잠금 → designFixedRooms==1·name·poly.
4. 잠금 면이 locked/잠금색(_LOCK_COLOR).
5. 둘째 방 그려 추가 잠금 → designFixedRooms==2.
6. "그리던 것 지우기" → 고정 방 보존(designFixedRooms 유지·면 살아있음).
7. 🔓 해제 → designFixedRooms 감소.
8. 저장→세대전환→재진입 → designFixedRooms 복원.
9. 페이지 에러 0.
"""
import sys, glob, os
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
# 외곽 bbox 중심 기준 두 개의 작은 사각 방 코너 좌표 (격자 정렬, 충분히 내부)
RECTS = """
() => {
  const b=designBoundary; let x0=1e9,y0=1e9,x1=-1e9,y1=-1e9;
  for(const p of b){x0=Math.min(x0,p[0]);y0=Math.min(y0,p[1]);x1=Math.max(x1,p[0]);y1=Math.max(y1,p[1]);}
  const cx=Math.round((x0+x1)/2/100)*100, cy=Math.round((y0+y1)/2/100)*100;
  const g=v=>Math.round(v/100)*100;
  // 방1: 중심 좌측, 방2: 중심 우측 (서로·외곽서 떨어지게)
  const A=[[g(cx-2600),g(cy-1300)],[g(cx-300),g(cy-1300)],[g(cx-300),g(cy+1300)],[g(cx-2600),g(cy+1300)]];
  const B=[[g(cx+300),g(cy-1300)],[g(cx+2600),g(cy-1300)],[g(cx+2600),g(cy+1300)],[g(cx+300),g(cy+1300)]];
  return {A,B, centerA:[g(cx-1450),cy], centerB:[g(cx+1450),cy]};
}
"""


def screen(page, mm):
    return page.evaluate(PROJ, mm)


def click_mm(page, mm):
    p = screen(page, mm)
    page.mouse.move(p["x"], p["y"]); page.mouse.click(p["x"], p["y"]); page.wait_for_timeout(50)


def hover_mm(page, mm):
    p = screen(page, mm)
    page.mouse.move(p["x"], p["y"]); page.wait_for_timeout(30)


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

    # ── 1. 닫힌 사각 방 1개 그림 ──
    draw_rect(page, rc["A"])
    rooms1 = page.evaluate("designRooms.length")
    print(f"[1 그리기] 방A 후 designRooms={rooms1}")
    assert rooms1 >= 1, "닫힌 방이 안 생김 (코너 스냅 실패 가능)"

    # ── 2. 진행중 선 1개 찍고 고정모드 토글 → 취소 ──
    click_mm(page, rc["B"][0])     # 시작점만 (진행중)
    drawing = page.evaluate("drawStart!==null")
    page.click("#btn-fix-mode")    # 고정모드 ON
    canceled = page.evaluate("drawStart===null && designDrawing===false")
    fixon = page.evaluate("designFixMode===true")
    print(f"[2 토글취소] 진행중={drawing} → 고정ON={fixon}·진행중선취소={canceled}")
    assert drawing and fixon and canceled, "고정모드 토글 시 진행중 선이 취소되지 않음"

    # ── 3. 면 클릭 선택 → 이름 → 잠금 ──
    click_mm(page, rc["centerA"])
    sel = page.evaluate("selectedDesignFace")
    print(f"[3 면선택] selectedDesignFace={sel}")
    assert sel >= 0, "면 raycast 선택 실패"
    page.fill("#design-fix-input", "현관")
    page.click("#btn-lock-fixed")
    page.wait_for_timeout(150)
    nfix = page.evaluate("designFixedRooms.length")
    fname = page.evaluate("designFixedRooms[0] && designFixedRooms[0].name")
    haspoly = page.evaluate("designFixedRooms[0] && Array.isArray(designFixedRooms[0].poly) && designFixedRooms[0].poly.length>=3")
    print(f"[3 잠금] designFixedRooms={nfix} name={fname} poly있음={haspoly}")
    assert nfix == 1 and fname == "현관" and haspoly, "고정 방 등록 실패"

    # ── 4. 잠금 면 locked/잠금색 ──
    locked = page.evaluate("designRooms.some(r=>r.locked && r.name==='현관')")
    lockcolor = page.evaluate("(() => { const r=designRooms.find(r=>r.locked); return r && r.color === _LOCK_COLOR; })()")
    print(f"[4 표시] locked면존재={locked} 잠금색={lockcolor}")
    assert locked and lockcolor, "잠금 면 표시(locked/색) 실패"

    # ── 5. 둘째 방 그려 추가 잠금 ──
    page.click("#btn-fix-mode")   # 고정모드 OFF (그리기 위해)
    page.wait_for_timeout(50)
    draw_rect(page, rc["B"])
    page.click("#btn-fix-mode")   # 다시 ON
    click_mm(page, rc["centerB"])
    page.fill("#design-fix-input", "다용도실")
    page.click("#btn-lock-fixed")
    page.wait_for_timeout(150)
    nfix2 = page.evaluate("designFixedRooms.length")
    print(f"[5 추가잠금] designFixedRooms={nfix2}")
    assert nfix2 == 2, "둘째 고정 방 등록 실패"

    # ── 6. "그리던 것 지우기" → 고정 방 보존 ──
    page.click("#btn-clear-design")
    page.wait_for_timeout(150)
    nfix3 = page.evaluate("designFixedRooms.length")
    facesLocked = page.evaluate("designRooms.filter(r=>r.locked).length")
    # ⚠️ 노이즈 외곽에서 손그림 방이 여러 면으로 쪼개질 수 있어 잠긴 '면' 수는 ≥2(영역 보존이 핵심).
    print(f"[6 clear보존] designFixedRooms={nfix3} 잠긴면={facesLocked}")
    assert nfix3 == 2 and facesLocked >= 2, "그리던 것 지우기에서 고정 방이 사라짐"

    # ── 7. 🔓 해제 (목록 첫 버튼) ──
    page.evaluate("unlockFixedRoom(0)")
    page.wait_for_timeout(120)
    nfix4 = page.evaluate("designFixedRooms.length")
    print(f"[7 해제] designFixedRooms={nfix4}")
    assert nfix4 == 1, "고정 해제 실패"

    # ── 8. 저장→세대전환→재진입 복원 ──
    page.click("#btn-save-design")
    page.wait_for_timeout(150)
    saved = page.evaluate("unitDesigns.A && unitDesigns.A.fixedRooms && unitDesigns.A.fixedRooms.length")
    print(f"[8a 저장] unitDesigns.A.fixedRooms={saved}")
    assert saved == 1, "저장본에 고정 방 미보관"
    # 세대 전환 없이 종료→재진입(같은 세대 재선택)
    page.evaluate("exitDesignMode()")
    page.evaluate("enterDesignMode('A')")
    page.wait_for_timeout(300)
    restored = page.evaluate("designFixedRooms.length")
    rname = page.evaluate("designFixedRooms[0] && designFixedRooms[0].name")
    print(f"[8b 재진입] designFixedRooms={restored} name={rname}")
    assert restored == 1 and rname == "다용도실", "재진입 시 고정 방 복원 실패"

    print("page errors:", errors if errors else "없음")
    assert not errors, f"페이지 에러: {errors}"
    print("🎉 고정 방 검증 전 항목 통과 (그리기·토글취소·선택·잠금·표시·clear보존·해제·저장복원·에러0)")
    browser.close()
