"""
방 크기 숫자 변경 + 드래그 이동 검증 — 역곡동빌라 page3 A세대 (실제 클릭/드래그).
1. 작은 침실(2×2) 그려 이름 붙임 → checkRoomSpec 미달(⚠️).
2. ✥ 크기·이동 모드 진입 → 방 클릭 선택 → 패널 W/H prefill≈2000.
3. 가로3000·세로3000 적용 → 면적≈9·BL 불변·⚠️ 사라짐.
4. Ctrl+Z → 면적≈4·⚠️ 복귀.
5. 선택 방 드래그 이동 → centroid 이동·면적 불변·카메라 position 불변.
6. 빈 곳 좌드래그 → 카메라 회전(position 변함). 우드래그 → pan(position 변함).
7. 클릭(이동≤5px) → 선택 토글.
8. 페이지 에러 0.
"""
import sys, glob, os, math
from playwright.sync_api import sync_playwright

sys.stdout.reconfigure(encoding="utf-8")
_root = os.path.dirname(os.path.abspath(__file__))
_cands = [p for p in glob.glob(os.path.join(_root, "backend", "*.pdf"))
          if ("빌라" in p or "역곡" in p)]
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
# W×H 영역 전체가 외곽 안에 깨끗이 들어가는 자리(BL) 찾기.
# 비정형(ㄱ자) 외곽이 사각형 내부를 가로지르지 않도록: 5×5 격자 전부 내부 + 외곽 정점이
# 사각형 안에 없음 + 4모서리 외곽서 300mm+ 까지 확인.
FIND_RECT = """
(dim) => {
  const W = dim.w, H = dim.h;
  const b=designBoundary; let a=1e9,c=1e9,d=-1e9,e=-1e9;
  for(const p of b){a=Math.min(a,p[0]);d=Math.max(d,p[0]);c=Math.min(c,p[1]);e=Math.max(e,p[1]);}
  function dB(pt){let m=1e12;for(let i=0;i<b.length;i++){const A=b[i],C=b[(i+1)%b.length];
    const vx=C[0]-A[0],vy=C[1]-A[1],L=vx*vx+vy*vy;let t=L>1e-6?((pt[0]-A[0])*vx+(pt[1]-A[1])*vy)/L:0;t=Math.max(0,Math.min(1,t));
    m=Math.min(m,Math.hypot(pt[0]-(A[0]+t*vx),pt[1]-(A[1]+t*vy)));}return m;}
  function clear(X,Y){
    // 5×5 격자 전부 외곽 내부 (노치가 영역을 가로지르지 않게)
    for(let i=0;i<=4;i++)for(let j=0;j<=4;j++){
      if(!pointInPoly([X+W*i/4, Y+H*j/4],b)) return false;
    }
    // 4모서리는 외곽서 400mm+ (스냅 350보다 커서 안 붙음)
    for(const p of [[X,Y],[X+W,Y],[X+W,Y+H],[X,Y+H]]) if(dB(p)<400) return false;
    // 외곽 정점이 사각형 안에 들어오면(노치 침범) 탈락
    for(const v of b) if(v[0]>X-1&&v[0]<X+W+1&&v[1]>Y-1&&v[1]<Y+H+1) return false;
    return true;
  }
  const R=v=>Math.round(v/100)*100;
  for(let x=R(a)+200;x<=d-W-200;x+=100)for(let y=R(c)+200;y<=e-H-200;y+=100){
    const X=R(x),Y=R(y);
    if(clear(X,Y))return{a:[X,Y],b:[X+W,Y+H]};}
  return null;
}
"""


def screen(page, mm):
    return page.evaluate(PROJ, mm)


def click_mm(page, mm):
    p = screen(page, mm)
    page.mouse.move(p["x"], p["y"]); page.mouse.click(p["x"], p["y"]); page.wait_for_timeout(60)


def cam(page):
    return page.evaluate("[roomCamera.position.x, roomCamera.position.y, roomCamera.position.z]")


def camdist(a, b):
    return math.sqrt(sum((a[i]-b[i])**2 for i in range(3)))


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
    page.wait_for_timeout(500)

    # ── 1. 2.8×2.8 들어갈 자리 찾아 그 BL에 2×2 침실 그림 (A세대 최대 clear=2.8m) ──
    GROW = 2800   # 키울 목표 변(mm): 2.8×2.8=7.84㎡(≥7㎡)·폭2.8m(≥2.4m) → 규격 통과
    GAREA = (GROW / 1000.0) ** 2
    spot = page.evaluate(FIND_RECT, {"w": GROW, "h": GROW})
    assert spot, f"{GROW}×{GROW} 들어갈 자리 못 찾음"
    BL = spot["a"]
    small_b = [BL[0] + 2000, BL[1] + 2000]
    page.click("#btn-rect-mode")
    page.wait_for_timeout(80)
    click_mm(page, BL)
    p = screen(page, small_b); page.mouse.move(p["x"], p["y"]); page.wait_for_timeout(40)
    click_mm(page, small_b)
    page.click("#btn-rect-mode")   # rect 모드 종료
    nrooms = page.evaluate("designRooms.length")
    print(f"[1 그리기] 방 {nrooms}개 (2×2 at BL={BL})")
    assert nrooms >= 1, "2×2 방 생성 실패"

    # 이름 '침실' 붙이기 (name 모드)
    center = [BL[0] + 1000, BL[1] + 1000]
    page.click("#btn-name-mode"); page.wait_for_timeout(80)
    click_mm(page, center)
    page.fill("#design-name-input", "침실")
    page.click("#btn-apply-name"); page.wait_for_timeout(80)
    page.click("#btn-name-mode"); page.wait_for_timeout(80)
    # 면 인덱스(중심으로) + 미달 확인
    info = page.evaluate("""(c)=>{
      let bi=-1,bd=1e12; for(let i=0;i<designRooms.length;i++){const cc=polyCentroid(designRooms[i].poly);
        const d=Math.hypot(cc[0]-c[0],cc[1]-c[1]); if(d<bd){bd=d;bi=i;}}
      const rm=designRooms[bi]; const dm=roomDims(rm.poly,rm.area_m2);
      return {idx:bi, name:rm.name, area:rm.area_m2, W:dm.W, H:dm.H,
              iss:checkRoomSpec(rm.name||'', dm.W, dm.H, rm.area_m2)};
    }""", center)
    print(f"[1 이름·미달] {info}")
    assert info["name"] == "침실", "이름 매칭 실패"
    assert len(info["iss"]) >= 1, "2×2 침실인데 규격 미달 경고가 없음"

    # ── 2. ✥ 크기·이동 모드 → 방 클릭 선택 → W/H prefill ──
    page.click("#btn-edit-mode"); page.wait_for_timeout(80)
    assert page.evaluate("designEditMode") is True
    click_mm(page, center)
    sel = page.evaluate("selectedDesignFace")
    wv = page.evaluate("document.getElementById('edit-w-input').value")
    hv = page.evaluate("document.getElementById('edit-h-input').value")
    print(f"[2 선택·prefill] sel={sel} W={wv} H={hv}")
    assert sel >= 0, "edit 모드 면 선택 실패"
    assert abs(int(wv) - 2000) <= 60 and abs(int(hv) - 2000) <= 60, "W/H prefill ≈2000 아님"

    # ── 3. 가로·세로 GROW 적용 → 면적≈GAREA·BL 불변·⚠️ 사라짐 ──
    page.fill("#edit-w-input", str(GROW))
    page.fill("#edit-h-input", str(GROW))
    page.click("#btn-apply-size"); page.wait_for_timeout(120)
    res = page.evaluate("""()=>{
      const i=selectedDesignFace; const rm=designRooms[i]; const dm=roomDims(rm.poly,rm.area_m2);
      let x0=1e12,y0=1e12; for(const p of rm.poly){x0=Math.min(x0,p[0]);y0=Math.min(y0,p[1]);}
      return {area:rm.area_m2, W:dm.W, H:dm.H, x0, y0,
              iss:checkRoomSpec(rm.name||'', dm.W, dm.H, rm.area_m2)};
    }""")
    print(f"[3 크기적용] {res} (목표 {GAREA:.2f}㎡)")
    assert abs(res["area"] - GAREA) < 0.6, f"면적 ≈{GAREA} 아님: {res['area']}"
    assert abs(res["x0"] - BL[0]) <= 60 and abs(res["y0"] - BL[1]) <= 60, "BL(왼쪽아래) 이동됨"
    assert len(res["iss"]) == 0, f"{GROW}×{GROW} 침실인데 경고 남음: {res['iss']}"

    # ── 4. Ctrl+Z → 면적≈4·⚠️ 복귀 ──
    page.keyboard.press("Control+z"); page.wait_for_timeout(120)
    # undo 후 선택 해제됨 → 중심으로 다시 찾음
    res2 = page.evaluate("""(c)=>{
      let bi=-1,bd=1e12; for(let i=0;i<designRooms.length;i++){const cc=polyCentroid(designRooms[i].poly);
        const d=Math.hypot(cc[0]-c[0],cc[1]-c[1]); if(d<bd){bd=d;bi=i;}}
      const rm=designRooms[bi]; const dm=roomDims(rm.poly,rm.area_m2);
      return {area:rm.area_m2, iss:checkRoomSpec(rm.name||'', dm.W, dm.H, rm.area_m2)};
    }""", center)
    print(f"[4 undo] {res2}")
    assert abs(res2["area"] - 4.0) < 0.6, f"undo 후 면적 ≈4 아님: {res2['area']}"
    assert len(res2["iss"]) >= 1, "undo 후 경고 복귀 안 됨"

    # ── 5. 선택 방 드래그 이동 → centroid 이동·면적 불변·카메라 불변 ──
    click_mm(page, center)   # 다시 선택 (2×2)
    assert page.evaluate("selectedDesignFace") >= 0
    cen_before = page.evaluate("polyCentroid(designRooms[selectedDesignFace].poly)")
    area_before = page.evaluate("designRooms[selectedDesignFace].area_m2")
    cam_before_move = cam(page)
    sp = screen(page, center)
    tp = screen(page, [center[0] + 1500, center[1] + 1500])
    page.mouse.move(sp["x"], sp["y"]); page.mouse.down()
    page.mouse.move((sp["x"]+tp["x"])/2, (sp["y"]+tp["y"])/2); page.wait_for_timeout(40)
    page.mouse.move(tp["x"], tp["y"]); page.wait_for_timeout(40)
    page.mouse.up(); page.wait_for_timeout(120)
    cen_after = page.evaluate("polyCentroid(designRooms[selectedDesignFace].poly)")
    area_after = page.evaluate("designRooms[selectedDesignFace].area_m2")
    cam_after_move = cam(page)
    moved = math.hypot(cen_after[0]-cen_before[0], cen_after[1]-cen_before[1])
    print(f"[5 이동] centroid {cen_before}→{cen_after} (이동 {moved:.0f}mm) 면적 {area_before}→{area_after} 카메라이동 {camdist(cam_before_move, cam_after_move):.5f}")
    assert moved > 300, f"방이 이동 안 됨 ({moved}mm)"
    assert abs(area_after - area_before) < 0.3, "이동 중 면적 변함"
    assert camdist(cam_before_move, cam_after_move) < 1e-4, "드래그 이동 중 카메라가 움직임(충돌)"

    # ── 6. 빈 곳 좌드래그 → 카메라 회전 / 우드래그 → pan ──
    cvrect = page.evaluate("""()=>{const cv=document.querySelector('#panel-asis canvas.three-canvas');const r=cv.getBoundingClientRect();return {l:r.left,t:r.top,w:r.width,h:r.height};}""")
    empty = {"x": cvrect["l"] + 40, "y": cvrect["t"] + 40}
    # 빈 곳인지 확인 (raycast -1)
    idx_empty = page.evaluate("""(p)=>{
      const rect = roomRenderer.domElement.getBoundingClientRect();
      const mouse = new THREE.Vector2(((p.x-rect.left)/rect.width)*2-1, -((p.y-rect.top)/rect.height)*2+1);
      const ray = new THREE.Raycaster(); ray.setFromCamera(mouse, roomCamera);
      return ray.intersectObjects(_designRoomTiles).length ? 1 : -1;
    }""", empty)
    print(f"[6 빈곳] idx={idx_empty} at {empty}")
    cam_b = cam(page)
    page.mouse.move(empty["x"], empty["y"]); page.mouse.down()
    page.mouse.move(empty["x"]+120, empty["y"]+60); page.wait_for_timeout(40)
    page.mouse.move(empty["x"]+220, empty["y"]+110); page.wait_for_timeout(40)
    page.mouse.up(); page.wait_for_timeout(80)
    cam_rot = cam(page)
    print(f"   좌드래그 카메라이동 {camdist(cam_b, cam_rot):.4f}")
    assert camdist(cam_b, cam_rot) > 1e-3, "빈 곳 좌드래그인데 카메라 회전 안 함"
    # 우드래그 pan
    cam_b2 = cam(page)
    page.mouse.move(empty["x"], empty["y"]); page.mouse.down(button="right")
    page.mouse.move(empty["x"]+150, empty["y"]+80); page.wait_for_timeout(40)
    page.mouse.up(button="right"); page.wait_for_timeout(80)
    cam_pan = cam(page)
    print(f"   우드래그 카메라이동 {camdist(cam_b2, cam_pan):.4f}")
    assert camdist(cam_b2, cam_pan) > 1e-3, "우드래그 pan 안 함"

    # ── 7. 클릭(이동 없음) → 선택 토글 (이동된 방의 현재 centroid 사용) ──
    cur_c = page.evaluate("selectedDesignFace>=0 ? polyCentroid(designRooms[selectedDesignFace].poly) : null")
    assert cur_c, "이동 후 선택 방 없음"
    page.evaluate("selectEditFace(-1)")  # reset
    click_mm(page, cur_c)
    sel_on = page.evaluate("selectedDesignFace")
    click_mm(page, cur_c)
    sel_off = page.evaluate("selectedDesignFace")
    print(f"[7 토글] 선택 {sel_on} → 재클릭 {sel_off}")
    assert sel_on >= 0 and sel_off < 0, "클릭 선택 토글 실패"

    print("page errors:", errors if errors else "없음")
    assert not errors, f"페이지 에러: {errors}"
    print("🎉 크기 변경·드래그 이동 검증 통과 (prefill·BL고정·⚠️갱신·undo·이동·카메라분리·토글·에러0)")
    browser.close()
