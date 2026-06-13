"""
내부 설계 모드 검증 (역곡동빌라 page3 A세대)

핵심: 실제 브라우저에서 좌클릭 드래그로 벽을 긋고 → 닫힌 면(방)이 생기는지까지 확인.
(지난 orbit 충돌처럼 자동 검증이 놓치는 걸 잡기 위해 카메라 좌표도 직접 비교)

1. 진입: A세대 지정 → 내부 설계 모드 → 빈 외곽(designBoundary) 로드 +
   원본 방 타일 전부 숨김 + designGroup 존재.
2. 스냅(단위 함수): 외곽 근처 raw → 외곽 위로 붙음 / 거의 수평 → dy=0 직각.
3. ★실제 드래그 드로우: 외곽을 가로지르는 수평 벽을 실제 마우스로 긋고
   닫힌 방 2개 생성 확인 + 드래그 중 길이 라벨 표시 확인.
4. orbit 비간섭: 좌드래그=그리기(카메라 불변), 우드래그=카메라 이동.
5. undo / 빈 외곽 초기화.
6. 페이지 에러 0.
"""
import sys
from playwright.sync_api import sync_playwright

sys.stdout.reconfigure(encoding="utf-8")
PDF = r"C:\Users\Jeff\Documents\GitHub\floorplan-ai\backend\역곡동빌라.pdf"
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
# bbox 세로 중앙에서 외곽을 가로지르는 수평 현(chord)의 좌/우 끝점(외곽 위)
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

with sync_playwright() as pw:
    browser = pw.chromium.launch()
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))

    page.goto(URL)
    page.set_input_files("#file-input", PDF)
    page.wait_for_function("parsedData && parsedData.rooms && parsedData.rooms.length>0", timeout=60000)
    page.wait_for_timeout(700)

    # A세대 지정
    page.evaluate(
        "(rooms)=>{rooms.forEach(i=>{parsedData.rooms[i].unit='A'; if(typeof applyRoomBaseColor==='function')applyRoomBaseColor(i);}); updateUnitCounts();}",
        A_ROOMS)

    # ── 1. 진입 ──
    page.click("#btn-design-mode")
    page.wait_for_function("designBoundary && designGroup", timeout=20000)
    nb = page.evaluate("designBoundary.length")
    tiles_hidden = page.evaluate("roomTiles.every(t=>!t.visible)")
    wall_hidden = page.evaluate("!wallExtrudeMesh || !wallExtrudeMesh.visible")
    print(f"[진입] 빈 외곽 정점 {nb}개 / 원본타일 전부숨김={tiles_hidden} / 외곽벽숨김={wall_hidden}")
    assert nb >= 4, f"외곽 정점 부족: {nb}"
    assert tiles_hidden, "원본 방 타일이 안 숨겨짐"
    assert wall_hidden, "원본 벽 압출이 안 숨겨짐"
    print("✅ 1. 진입 — 빈 외곽 + 원본 숨김\n")

    # ── 2. 스냅 단위 함수 ──
    # 외곽 첫 edge 중점에서 안쪽으로 200mm 들어간 raw → 외곽 위로 붙어야(거리<=tol)
    snap_on = page.evaluate("""
      () => {
        const b=designBoundary, a=b[0], c=b[1];
        const mid=[(a[0]+c[0])/2,(a[1]+c[1])/2];
        // edge 법선 방향으로 200mm 안쪽 이동
        const ex=c[0]-a[0], ey=c[1]-a[1], L=Math.hypot(ex,ey);
        const nx=-ey/L, ny=ex/L;
        const raw=[mid[0]+nx*200, mid[1]+ny*200];
        const s=snapPoint(raw, null);
        // s가 외곽 edge(a-c) 위에 얼마나 가까운지
        const r=_projOnSeg(s, a, c);
        return {dist_to_edge:r.d, moved:Math.hypot(s[0]-raw[0],s[1]-raw[1])};
      }
    """)
    print(f"[스냅:선분] raw(외곽 200mm 안)→스냅점의 외곽거리={snap_on['dist_to_edge']:.1f}mm (이동 {snap_on['moved']:.0f}mm)")
    assert snap_on["dist_to_edge"] < 60, f"선분 스냅 실패: 외곽에서 {snap_on['dist_to_edge']}mm"

    # 거의 수평(5도) → 직각 스냅으로 dy=0 (외곽에서 충분히 먼 빈 공간에서 격리 테스트)
    snap_h = page.evaluate("""
      () => {
        const b=designBoundary;
        let minX=1e9,minY=1e9; for(const p of b){minX=Math.min(minX,p[0]);minY=Math.min(minY,p[1]);}
        const from=[minX-20000, minY-20000];               // 외곽에서 20m 밖
        const raw=[from[0]+3000, from[1]+3000*Math.tan(5*Math.PI/180)]; // 5도
        const s=snapPoint(raw, from);
        return {dy:s[1]-from[1]};
      }
    """)
    print(f"[스냅:직각] 빈공간 5도 raw → dy={snap_h['dy']:.1f} (수평이면 0)")
    assert abs(snap_h["dy"]) < 1, f"직각 스냅 실패: dy={snap_h['dy']}"
    print("✅ 2. 스냅(선분/직각)\n")

    # ── 3. ★실제 드래그로 벽 긋기 → 닫힌 방 ──
    chord = page.evaluate(CHORD)
    assert chord, "현(chord)을 못 구함"
    pL = page.evaluate(PROJ, chord["left"])
    pR = page.evaluate(PROJ, chord["right"])
    rooms_before = page.evaluate("designRooms.length")
    cam_before = page.evaluate("roomCamera.position.toArray()")

    page.mouse.move(pL["x"], pL["y"])
    page.mouse.down()
    len_seen = 0
    for k in range(1, 11):
        page.mouse.move(pL["x"] + (pR["x"]-pL["x"])*k/10.0,
                        pL["y"] + (pR["y"]-pL["y"])*k/10.0)
        page.wait_for_timeout(25)
        if k == 6:  # 드래그 도중 길이 라벨 떠 있는지
            len_seen = page.evaluate("drawLenLabel ? 1 : 0")
            drawing = page.evaluate("designDrawing")
    page.mouse.up()
    page.wait_for_timeout(200)

    rooms_after = page.evaluate("designRooms.length")
    walls_after = page.evaluate("designWalls.length")
    areas = page.evaluate("designRooms.map(r=>r.area_m2)")
    cam_after = page.evaluate("roomCamera.position.toArray()")
    cam_move = max(abs(cam_before[i]-cam_after[i]) for i in range(3))
    print(f"[드래그] 벽 {walls_after}개 / 방 {rooms_before}→{rooms_after}개 면적{areas}")
    print(f"[드래그] 드래그중 길이라벨={len_seen} designDrawing(중)={drawing} / 카메라 이동량={cam_move:.6f}")
    assert walls_after == 1, f"벽이 1개 안 생김: {walls_after}"
    assert rooms_after >= 2, f"닫힌 방이 2개 이상 안 생김: {rooms_after}"
    assert len_seen == 1, "드래그 중 길이 라벨이 안 떴음"
    assert cam_move < 0.001, f"좌드래그인데 카메라가 움직임(orbit 충돌!): {cam_move}"
    assert all(a > 0.5 for a in areas), f"방 면적이 비정상: {areas}"
    print("✅ 3. 실제 드래그 → 닫힌 방 2개 + 길이표시 + orbit 비간섭\n")

    # ── 3.5 ★T자 접합: 첫 벽(수평) 중간에서 위쪽 외곽으로 수직벽 → 방 3개 ──
    tjoin = page.evaluate("""
      () => {
        const w=designWalls[0];
        const midX=(w.a[0]+w.b[0])/2, ym=w.a[1];
        const b=designBoundary;
        // x=midX 에서 y<ym(위쪽) 외곽 교차점 중 가장 가까운 것
        let topY=null,bd=1e9;
        for(let i=0;i<b.length;i++){const p=b[i],q=b[(i+1)%b.length];
          if((p[0]-midX)*(q[0]-midX)<0){const t=(midX-p[0])/(q[0]-p[0]);const y=p[1]+t*(q[1]-p[1]);
            if(y<ym && ym-y<bd){bd=ym-y;topY=y;}}}
        return topY===null?null:{start:[midX,ym], end:[midX,topY]};
      }
    """)
    assert tjoin, "T자 수직벽 좌표 계산 실패"
    sS = page.evaluate(PROJ, tjoin["start"])
    sE = page.evaluate(PROJ, tjoin["end"])
    page.mouse.move(sS["x"], sS["y"]); page.mouse.down()
    for k in range(1, 11):
        page.mouse.move(sS["x"]+(sE["x"]-sS["x"])*k/10.0, sS["y"]+(sE["y"]-sS["y"])*k/10.0)
        page.wait_for_timeout(20)
    page.mouse.up(); page.wait_for_timeout(200)
    rooms_t = page.evaluate("designRooms.length")
    areas_t = page.evaluate("designRooms.map(r=>r.area_m2)")
    print(f"[T자접합] 벽 2개 → 방 {rooms_t}개 면적{areas_t}")
    assert rooms_t >= 3, f"T자 접합으로 방이 3개 안 됨: {rooms_t} (평면그래프 노드-온-세그먼트 분할 실패)"
    print("✅ 3.5 T자 접합 — 닫힌 방 3개\n")
    # 이후 테스트(undo)가 깨끗하도록 T자 벽 되돌림
    page.click("#btn-undo-design"); page.wait_for_timeout(150)
    assert page.evaluate("designWalls.length") == 1

    # ── 4. 우드래그 = 카메라 이동 ──
    cam_b = page.evaluate("roomCamera.position.toArray()")
    cx = (pL["x"]+pR["x"])/2; cy = (pL["y"]+pR["y"])/2
    page.mouse.move(cx, cy)
    page.mouse.down(button="right")
    for k in range(1, 6):
        page.mouse.move(cx + 12*k, cy); page.wait_for_timeout(20)
    page.mouse.up(button="right")
    page.wait_for_timeout(100)
    cam_a = page.evaluate("roomCamera.position.toArray()")
    cam_orbit = max(abs(cam_b[i]-cam_a[i]) for i in range(3))
    print(f"[우드래그] 카메라 이동량={cam_orbit:.4f} (움직여야 정상)")
    assert cam_orbit > 0.001, "우드래그로 카메라가 안 움직임"
    # 우드래그가 벽을 만들지 않았는지
    assert page.evaluate("designWalls.length") == 1, "우드래그가 벽을 생성함"
    print("✅ 4. 우드래그=카메라 / 벽 생성 안 함\n")

    # ── 5. undo / 초기화 ──
    page.click("#btn-undo-design")
    page.wait_for_timeout(150)
    after_undo_w = page.evaluate("designWalls.length")
    after_undo_r = page.evaluate("designRooms.length")
    print(f"[undo] 벽 {after_undo_w}개 / 방 {after_undo_r}개")
    assert after_undo_w == 0 and after_undo_r == 0, "undo가 빈 외곽으로 안 돌림"

    # 다시 그리고 초기화 버튼
    page.mouse.move(pL["x"], pL["y"]); page.mouse.down()
    for k in range(1, 11):
        page.mouse.move(pL["x"]+(pR["x"]-pL["x"])*k/10.0, pL["y"]+(pR["y"]-pL["y"])*k/10.0)
        page.wait_for_timeout(15)
    page.mouse.up(); page.wait_for_timeout(150)
    assert page.evaluate("designWalls.length") == 1
    page.click("#btn-reset-design")
    page.wait_for_timeout(150)
    assert page.evaluate("designWalls.length") == 0 and page.evaluate("designRooms.length") == 0, "초기화 실패"
    print("✅ 5. undo / 빈 외곽 초기화\n")

    # ── 6. 모드 종료 → 원본 복원 ──
    page.click("#btn-design-mode")
    page.wait_for_timeout(150)
    restored = page.evaluate("roomTiles.every(t=>t.visible) && (!designGroup)")
    print(f"[종료] 원본 타일 복원 + designGroup 정리={restored}")
    assert restored, "모드 종료 후 원본 복원 실패"

    print("\npage errors:", errors if errors else "없음")
    assert not errors, f"페이지 에러: {errors}"
    print("🎉 내부 설계 모드 전 항목 통과 (진입/스냅/실제드래그-닫힌방/orbit/undo/복원)")
    browser.close()
