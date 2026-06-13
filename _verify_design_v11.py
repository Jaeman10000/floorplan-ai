"""
내부 설계 v1.1 검증 — 저장 & 이어 편집 (역곡동빌라 page3)

실제 마우스 드래그로:
1. A세대 그려 저장 → unitDesigns.A 존재 + To-Be 패널에 방 렌더(tobeScene 메시).
2. B세대로 전환해 그려 저장 → To-Be에 A·B 둘 다.
3. 모드 종료 → As-Is 원본 복원(타일 visible) + To-Be 유지.
4. 재진입 → A 저장본 로드(designWalls 복원·방 복원) → 벽 1개 더 → 재저장(A walls 증가).
5. 두 초기화 구분: "그리던 것 지우기"=작업만 비고 저장본 유지 / "원본으로"=저장본 삭제.
6. editMode 흔적 없음(btn-edit-mode null, setEditMode undefined) + 에러 0.
"""
import sys
from playwright.sync_api import sync_playwright

sys.stdout.reconfigure(encoding="utf-8")
PDF = r"C:\Users\Jeff\Documents\GitHub\floorplan-ai\backend\역곡동빌라.pdf"
URL = "http://localhost:8000/"
A_ROOMS = [0, 1, 2, 11, 20, 21, 24]
B_ROOMS = [3, 4, 5, 12]

PROJ = """
(xy) => {
  const v = new THREE.Vector3(xy[0]/1000, 0.03, xy[1]/1000).project(roomCamera);
  const cv = document.querySelector('#panel-asis canvas.three-canvas');
  const r = cv.getBoundingClientRect();
  return { x:(v.x*0.5+0.5)*r.width + r.left, y:(-v.y*0.5+0.5)*r.height + r.top };
}
"""
CHORD = """
() => {
  const b = designBoundary;
  let minY=1e9,maxY=-1e9;
  for(const p of b){minY=Math.min(minY,p[1]);maxY=Math.max(maxY,p[1]);}
  const ym=(minY+maxY)/2;
  const xs=[];
  for(let i=0;i<b.length;i++){const a=b[i],c=b[(i+1)%b.length];
    if((a[1]-ym)*(c[1]-ym)<0){const t=(ym-a[1])/(c[1]-a[1]);xs.push(a[0]+t*(c[0]-a[0]));}}
  xs.sort((p,q)=>p-q);
  if(xs.length<2)return null;
  return {left:[xs[0],ym], right:[xs[xs.length-1],ym]};
}
"""

def draw_chord(page, frac=1.0):
    """현재 designBoundary를 가로지르는 수평 벽을 실제 드래그로 긋는다."""
    chord = page.evaluate(CHORD)
    assert chord, "chord 계산 실패"
    pL = page.evaluate(PROJ, chord["left"])
    pR = page.evaluate(PROJ, chord["right"])
    ex = pL["x"] + (pR["x"] - pL["x"]) * frac
    ey = pL["y"] + (pR["y"] - pL["y"]) * frac
    page.mouse.move(pL["x"], pL["y"]); page.mouse.down()
    for k in range(1, 11):
        page.mouse.move(pL["x"] + (ex - pL["x"]) * k / 10.0,
                        pL["y"] + (ey - pL["y"]) * k / 10.0)
        page.wait_for_timeout(18)
    page.mouse.up(); page.wait_for_timeout(160)

def enter_design(page):
    page.click("#btn-design-mode")
    page.wait_for_function("designBoundary && designGroup", timeout=20000)

def switch_unit(page, u):
    page.click(f'.du-btn[data-unit="{u}"]')
    page.wait_for_function(f"designUnit==='{u}' && designBoundary && designGroup", timeout=20000)

with sync_playwright() as pw:
    browser = pw.chromium.launch()
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("dialog", lambda d: d.accept())   # beforeunload 등

    page.goto(URL)
    page.set_input_files("#file-input", PDF)
    page.wait_for_function("parsedData && parsedData.rooms && parsedData.rooms.length>0", timeout=60000)
    page.wait_for_timeout(700)

    # ── editMode 흔적 없음 ──
    no_edit = page.evaluate("""() => ({
      btn: document.getElementById('btn-edit-mode')===null,
      fn: typeof setEditMode==='undefined' && typeof buildWallModel==='undefined' && typeof selectWall==='undefined',
      shared: typeof groundPoint==='function' && typeof shoelaceArea==='function' && typeof makeLabel==='function' && typeof polyCentroid==='function',
    })""")
    print(f"[editMode 삭제] 버튼없음={no_edit['btn']} 함수없음={no_edit['fn']} 공유함수보존={no_edit['shared']}")
    assert no_edit["btn"] and no_edit["fn"], "editMode 흔적 남음"
    assert no_edit["shared"], "공유 함수가 같이 지워짐!"
    print("✅ 0. editMode 전면 삭제 + 공유 함수 보존\n")

    # A·B 세대 지정
    page.evaluate("(r)=>{r.forEach(i=>{parsedData.rooms[i].unit='A';applyRoomBaseColor(i);});updateUnitCounts();}", A_ROOMS)
    page.evaluate("(r)=>{r.forEach(i=>{parsedData.rooms[i].unit='B';applyRoomBaseColor(i);});updateUnitCounts();}", B_ROOMS)

    # ── 1. A 그려 저장 ──
    enter_design(page)
    assert page.evaluate("designUnit") == "A"
    draw_chord(page)
    a_walls = page.evaluate("designWalls.length")
    a_rooms = page.evaluate("designRooms.length")
    assert a_walls == 1 and a_rooms >= 2, f"A 그리기 실패: 벽{a_walls} 방{a_rooms}"
    page.click("#btn-save-design")
    page.wait_for_timeout(200)
    saved_a = page.evaluate("!!unitDesigns.A && unitDesigns.A.walls.length")
    tobe_canvas = page.evaluate("!!document.querySelector('#panel-tobe canvas.three-canvas')")
    tobe_meshes = page.evaluate("tobeScene ? tobeScene.children.length : 0")
    print(f"[A 저장] unitDesigns.A walls={saved_a} / To-Be canvas={tobe_canvas} / tobeScene 메시={tobe_meshes}")
    assert saved_a == 1, "A 저장 안 됨"
    assert tobe_canvas and tobe_meshes > 5, "To-Be에 안 그려짐"
    print("✅ 1. A 그려 저장 → To-Be 표시\n")

    # ── 2. B 전환 → 그려 저장 → To-Be에 A·B 둘 다 ──
    switch_unit(page, "B")
    assert page.evaluate("designWalls.length") == 0, "B는 새 외곽이어야(빈 벽)"
    draw_chord(page)
    page.click("#btn-save-design")
    page.wait_for_timeout(200)
    keys = page.evaluate("Object.keys(unitDesigns).sort()")
    tobe_meshes2 = page.evaluate("tobeScene ? tobeScene.children.length : 0")
    print(f"[B 저장] unitDesigns 키={keys} / To-Be 메시={tobe_meshes2}(A보다 많아야)")
    assert keys == ["A", "B"], f"A·B 둘 다 저장 안 됨: {keys}"
    assert tobe_meshes2 > tobe_meshes, "To-Be에 B가 추가 안 됨"
    print("✅ 2. B 저장 → To-Be에 A·B 둘 다\n")

    # ── 3. 모드 종료 → As-Is 복원 + To-Be 유지 ──
    page.click("#btn-design-mode")
    page.wait_for_timeout(200)
    asis_restored = page.evaluate("roomTiles.every(t=>t.visible) && !designGroup && !designMode")
    tobe_kept = page.evaluate("!!document.querySelector('#panel-tobe canvas.three-canvas') && Object.keys(unitDesigns).length===2")
    print(f"[종료] As-Is 원본 복원={asis_restored} / To-Be 유지={tobe_kept}")
    assert asis_restored, "As-Is 원본 복원 실패"
    assert tobe_kept, "종료 후 To-Be 사라짐"
    print("✅ 3. 종료 → As-Is 복원 + To-Be 유지\n")

    # ── 4. 재진입 → A 저장본 로드 → 이어 그려 재저장 ──
    enter_design(page)
    assert page.evaluate("designUnit") == "A", "재진입 자동선택 A 아님"
    loaded_walls = page.evaluate("designWalls.length")
    loaded_rooms = page.evaluate("designRooms.length")
    print(f"[재진입] A 저장본 로드 — 벽={loaded_walls} 방={loaded_rooms}")
    assert loaded_walls == 1 and loaded_rooms >= 2, "저장본 로드 실패(빈 외곽으로 옴)"
    # 두 번째 벽을 첫 벽과 어긋나게(수평 위치만 절반 길이) 추가 — 어쨌든 walls는 +1
    draw_chord(page, frac=0.5)
    after2 = page.evaluate("designWalls.length")
    assert after2 == 2, f"이어 그리기 실패: {after2}"
    page.click("#btn-save-design")
    page.wait_for_timeout(150)
    re_saved = page.evaluate("unitDesigns.A.walls.length")
    print(f"[재저장] A walls={re_saved}")
    assert re_saved == 2, "재저장 반영 안 됨"
    print("✅ 4. 재진입 저장본 로드 → 이어 그리기 → 재저장\n")

    # ── 5a. "그리던 것 지우기" = 작업만 비고 저장본 유지 ──
    draw_chord(page, frac=0.7)   # 임시 3번째 벽
    page.click("#btn-clear-design")
    page.wait_for_timeout(150)
    cleared = page.evaluate("designWalls.length")
    saved_still = page.evaluate("!!unitDesigns.A && unitDesigns.A.walls.length")
    print(f"[그리던 것 지우기] 작업 벽={cleared} / 저장본 walls={saved_still}(유지돼야)")
    assert cleared == 0, "작업이 안 비워짐"
    assert saved_still == 2, "지우기가 저장본까지 날림(버그)"
    print("✅ 5a. 그리던 것 지우기 — 저장본 유지\n")

    # ── 5b. "이 세대 원본으로" = 저장본 삭제 + To-Be에서 제거 ──
    page.click("#btn-revert-design")
    page.wait_for_timeout(200)
    a_gone = page.evaluate("!unitDesigns.A")
    keys2 = page.evaluate("Object.keys(unitDesigns)")
    print(f"[원본으로] unitDesigns.A 삭제됨={a_gone} / 남은 키={keys2}")
    assert a_gone and keys2 == ["B"], f"원본 되돌리기 실패: {keys2}"
    print("✅ 5b. 이 세대 원본으로 — 저장본 삭제 + To-Be에서 제거\n")

    print("page errors:", errors if errors else "없음")
    assert not errors, f"페이지 에러: {errors}"
    print("🎉 v1.1 전 항목 통과 (저장/To-Be/종료복원/재진입로드/두 초기화/editMode삭제)")
    browser.close()
