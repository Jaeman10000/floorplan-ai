"""
외곽 스냅샷·잠금 + 무파괴 모드 전환 + 묶기 변경 경고 + 묶기 localStorage 검증.
(역곡동빌라 page3 A세대, 실제 클릭/모드 전환)

① autosave: A 묶기→설계(벽+현관 고정, 💾 안 누름)→세대 묶기 모드 갔다 옴→
   설계 재진입 시 벽·고정방 유지.
② 묶기 변경 경고: 묶기에 방 1개 추가→재진입 시 confirm.
   - 유지(accept) → 기존 설계 그대로(이전 외곽·벽 유지).
   - 다시시작(dismiss) → 그 세대 설계 삭제 + 새 외곽.
③ localStorage: saveUnitsToStorage 후 키 존재 → 새로고침+같은 PDF 재업로드 →
   loadUnitsFromStorage가 rooms[i].unit 복원 + 토스트.
④ 페이지 에러 0.
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
EXTRA_ROOM = 3   # 묶기 변경 테스트용으로 A에 추가할 방

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
  const A=[[g(cx-2600),g(cy-1300)],[g(cx-300),g(cy-1300)],[g(cx-300),g(cy+1300)],[g(cx-2600),g(cy+1300)]];
  return {A, centerA:[g(cx-1450),cy]};
}
"""


def screen(page, mm):
    return page.evaluate(PROJ, mm)


def click_mm(page, mm):
    p = screen(page, mm)
    page.mouse.move(p["x"], p["y"]); page.mouse.click(p["x"], p["y"]); page.wait_for_timeout(70)


def hover_mm(page, mm):
    p = screen(page, mm)
    page.mouse.move(p["x"], p["y"]); page.wait_for_timeout(45)


def draw_rect(page, corners):
    for i in range(4):
        a, b = corners[i], corners[(i + 1) % 4]
        click_mm(page, a); hover_mm(page, b); click_mm(page, b)


def assign_A(page, idxs):
    page.evaluate("(r)=>{r.forEach(i=>{parsedData.rooms[i].unit='A';applyRoomBaseColor(i);});updateUnitCounts();saveUnitsToStorage();}", idxs)


def upload_and_unit(page):
    page.set_input_files("#file-input", PDF)
    page.wait_for_function("parsedData && parsedData.rooms && parsedData.rooms.length>0", timeout=60000)
    page.wait_for_timeout(700)


with sync_playwright() as pw:
    browser = pw.chromium.launch()
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    # dialog는 케이스별로 갈아끼움 — 기본은 accept
    dialog_mode = {"v": "accept"}
    def on_dialog(d):
        if dialog_mode["v"] == "dismiss":
            d.dismiss()
        else:
            d.accept()
    page.on("dialog", on_dialog)

    page.goto(URL)
    # localStorage 깨끗이 (이전 실행 잔재 제거)
    page.evaluate("() => { try { localStorage.clear(); } catch(e){} }")
    upload_and_unit(page)
    assign_A(page, A_ROOMS)

    # ── localStorage 저장 확인 ──
    key = page.evaluate("unitStorageKey()")
    stored = page.evaluate("(k)=>localStorage.getItem(k)", key)
    print(f"[③ localStorage 저장] key={key}")
    print(f"  값={stored[:80] if stored else None}")
    assert key and stored, "묶기 localStorage 저장 안 됨"
    import json as _json
    stored_map = _json.loads(stored)
    assert len(stored_map) == len(A_ROOMS), f"저장 칸수 불일치 {len(stored_map)}!={len(A_ROOMS)}"

    # ── ① autosave: 설계(벽+고정방, 💾 안 누름) → 모드 갔다 옴 → 유지 ──
    page.click("#btn-design-mode")
    page.wait_for_function("designBoundary && designGroup && designUnit==='A'", timeout=20000)
    rc = page.evaluate(RECTS)
    draw_rect(page, rc["A"])
    walls_drawn = page.evaluate("designWalls.length")
    print(f"\n[① autosave] 벽 그림 designWalls={walls_drawn} designRooms={page.evaluate('designRooms.length')}")
    assert walls_drawn > 0, "벽이 안 그려짐"

    # 고정 방 잠금 (💾 저장은 안 누름)
    page.click("#btn-fix-mode"); page.wait_for_timeout(80)
    click_mm(page, rc["centerA"])
    sel = page.evaluate("selectedDesignFace")
    assert sel >= 0, "면 선택 실패"
    page.fill("#design-fix-input", "현관")
    page.click("#btn-lock-fixed"); page.wait_for_timeout(120)
    nfix = page.evaluate("designFixedRooms.length")
    print(f"[① autosave] 고정방 잠금 designFixedRooms={nfix} (💾 저장 안 누름)")
    assert nfix == 1, "고정 방 잠금 실패"
    # 아직 unitDesigns엔 저장 전 (저장 안 눌렀으니)
    saved_before = page.evaluate("!!unitDesigns.A")
    print(f"[① autosave] 모드 전환 전 unitDesigns.A 존재={saved_before} (autosave는 종료 시)")

    # 세대 묶기 모드 ON (→ setDesignMode(false) → exitDesignMode → autosave)
    page.click("#btn-unit-mode"); page.wait_for_timeout(200)
    saved_after_exit = page.evaluate("!!unitDesigns.A")
    saved_walls = page.evaluate("unitDesigns.A ? unitDesigns.A.walls.length : 0")
    saved_fixed = page.evaluate("unitDesigns.A ? unitDesigns.A.fixedRooms.length : 0")
    print(f"[① autosave] 묶기모드 진입(=설계종료) 후 unitDesigns.A={saved_after_exit} 벽={saved_walls} 고정방={saved_fixed}")
    assert saved_after_exit and saved_walls == walls_drawn and saved_fixed == 1, "autosave가 작업을 안 보관함"

    # 세대 묶기 모드 OFF → 설계 모드 재진입
    page.click("#btn-unit-mode"); page.wait_for_timeout(150)
    page.click("#btn-design-mode")
    page.wait_for_function("designBoundary && designGroup && designUnit==='A'", timeout=20000)
    page.wait_for_timeout(200)
    re_walls = page.evaluate("designWalls.length")
    re_fixed = page.evaluate("designFixedRooms.length")
    re_fname = page.evaluate("designFixedRooms[0] && designFixedRooms[0].name")
    print(f"[① autosave] 설계 재진입 designWalls={re_walls} designFixedRooms={re_fixed} name={re_fname}")
    assert re_walls == walls_drawn and re_fixed == 1 and re_fname == "현관", "재진입 시 벽·고정방 미복원 (autosave 실패)"
    print("  ✓ 💾 저장 안 했는데도 모드 전환 후 벽·고정방 유지됨")

    # ── ② 묶기 변경 경고: A에 방 1개 추가 → 재진입 confirm ──
    # 설계 종료(다시 묶기 모드로 나가서 방 추가)
    page.click("#btn-unit-mode"); page.wait_for_timeout(150)   # 설계 종료 + autosave
    saved_boundary_before = page.evaluate("JSON.stringify(unitDesigns.A.boundary_mm)")
    saved_walls_before = page.evaluate("unitDesigns.A.walls.length")
    # A에 방 추가 (묶기 변경)
    page.evaluate("(i)=>{parsedData.rooms[i].unit='A';applyRoomBaseColor(i);updateUnitCounts();saveUnitsToStorage();}", EXTRA_ROOM)
    page.click("#btn-unit-mode"); page.wait_for_timeout(120)   # 묶기 모드 OFF

    # 재진입 — confirm 유지(accept)
    dialog_mode["v"] = "accept"
    page.click("#btn-design-mode")
    page.wait_for_function("designBoundary && designGroup && designUnit==='A'", timeout=20000)
    page.wait_for_timeout(200)
    keep_walls = page.evaluate("designWalls.length")
    keep_boundary = page.evaluate("JSON.stringify(designBoundary)")
    print(f"\n[② 경고-유지] confirm accept 후 designWalls={keep_walls} (기존 {saved_walls_before})")
    assert keep_walls == saved_walls_before, "유지 선택인데 벽이 바뀜"
    assert keep_boundary == saved_boundary_before, "유지 선택인데 외곽이 바뀜(스냅샷 미사용)"
    print("  ✓ 유지: 기존 외곽·벽 그대로")

    # 재진입 — confirm 다시시작(dismiss). 묶기는 여전히 추가된 상태라 다시 경고 뜸.
    page.click("#btn-unit-mode"); page.wait_for_timeout(150)   # 설계 종료(autosave — roomIds 갱신됨!)
    # ⚠️ autosave가 roomIds를 현재(추가된) 묶기로 갱신하므로, 다시시작 테스트를 위해
    #    roomIds를 옛날 값으로 되돌려 강제로 "변경됨" 상태 재현
    page.evaluate("() => { if (unitDesigns.A) unitDesigns.A.roomIds = [0,1,2]; }")
    dialog_mode["v"] = "dismiss"
    page.click("#btn-design-mode")
    page.wait_for_function("designBoundary && designGroup && designUnit==='A'", timeout=20000)
    page.wait_for_timeout(300)
    restart_saved = page.evaluate("!!unitDesigns.A")
    restart_walls = page.evaluate("designWalls.length")
    print(f"[② 경고-다시시작] confirm dismiss 후 unitDesigns.A존재={restart_saved} designWalls={restart_walls}")
    assert not restart_saved, "다시시작인데 unitDesigns.A가 안 지워짐"
    assert restart_walls == 0, "다시시작인데 빈 외곽이 아님 (벽 남음)"
    print("  ✓ 다시시작: 그 세대 설계 삭제 + 빈 외곽")

    # ── ③ localStorage 복원: 새로고침 + 같은 PDF 재업로드 ──
    page.click("#btn-design-mode"); page.wait_for_timeout(150)   # 설계 종료
    page.reload()
    page.wait_for_timeout(500)
    # 새로고침 후 unit 다 사라졌는지(메모리 소멸) → 재업로드 → localStorage 복원
    upload_and_unit(page)
    restored_units = page.evaluate("parsedData.rooms.filter(r=>r.unit==='A').length")
    print(f"\n[③ localStorage 복원] 새로고침+재업로드 후 A세대 방수={restored_units}")
    # A_ROOMS(7) + EXTRA_ROOM(1) = 8 저장돼 있었음
    assert restored_units == len(A_ROOMS) + 1, f"묶기 복원 실패 ({restored_units}!={len(A_ROOMS)+1})"
    status_text = page.evaluate("document.getElementById('status-msg') ? document.getElementById('status-msg').textContent : ''")
    print(f"  상태텍스트: {status_text[:120]}")
    print("  ✓ localStorage에서 묶기 복원됨")

    print(f"\npage errors: {errors if errors else '없음'}")
    assert not errors, f"페이지 에러: {errors}"
    print("🎉 autosave·외곽스냅샷·묶기경고·localStorage 검증 전 항목 통과")
    browser.close()
