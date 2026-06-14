"""
설계 조언 '대략적 위치 존' 3D 오버레이 — 프런트 경로 검증 (역곡동빌라 page3 A세대).
/api/design-advice를 page.route로 목(텍스트 + zones 폴리곤 반환). 검증:
1. 조언 요청 → 모달에 텍스트 렌더 (기존 동작 유지).
2. designZones > 0, _designZoneMeshes 생성됨.
3. 존 폴리곤 정점 전부 designBoundary 안 (pointInPoly).
4. 토글 버튼: 클릭 → 존 메시 전부 invisible / 다시 → visible.
5. 존 위에 직사각형 방 그리기 정상(designRooms·designWalls 증가) + 방 타일 y(0.02) > 존 타일 y(0.012).
6. ★ zones 빈 응답이면 존 안 뜨고 조언 텍스트는 정상(graceful).
7. 페이지 에러 0.
"""
import sys, glob, os, json
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
FIND_RECT = """
() => {
  const b=designBoundary; let a=1e9,c=1e9,d=-1e9,e=-1e9;
  for(const p of b){a=Math.min(a,p[0]);d=Math.max(d,p[0]);c=Math.min(c,p[1]);e=Math.max(e,p[1]);}
  function dB(pt){let m=1e12;for(let i=0;i<b.length;i++){const A=b[i],C=b[(i+1)%b.length];
    const vx=C[0]-A[0],vy=C[1]-A[1],L=vx*vx+vy*vy;let t=L>1e-6?((pt[0]-A[0])*vx+(pt[1]-A[1])*vy)/L:0;t=Math.max(0,Math.min(1,t));
    m=Math.min(m,Math.hypot(pt[0]-(A[0]+t*vx),pt[1]-(A[1]+t*vy)));}return m;}
  const R=v=>Math.round(v/100)*100;
  for(let h=2400;h>=1500;h-=200)for(let w=2000;w>=1400;w-=200)
   for(let x=R(a)+200;x<=d-w-200;x+=300)for(let y=R(c)+200;y<=e-h-200;y+=300){
     const X=R(x),Y=R(y),W=R(w),H=R(h);const pts=[[X,Y],[X+W,Y],[X+W,Y+H],[X,Y+H]];
     if(pts.every(p=>pointInPoly(p,b)&&dB(p)>450))return{a:[X,Y],b:[X+W,Y+H]};}
  return null;
}
"""

# 목 응답 모드 — 전역으로 토글. mock_zones는 JS가 외곽 안에서 찾은 실제 폴리곤(아래 채움).
_return_zones = True
mock_zones = []

# 외곽 안에 완전히 들어가는 직사각형 2개를 찾는다(각 정점이 외곽서 충분히 떨어짐).
FIND_TWO = """
() => {
  const b=designBoundary; let a=1e9,c=1e9,d=-1e9,e=-1e9;
  for(const p of b){a=Math.min(a,p[0]);d=Math.max(d,p[0]);c=Math.min(c,p[1]);e=Math.max(e,p[1]);}
  function dB(pt){let m=1e12;for(let i=0;i<b.length;i++){const A=b[i],C=b[(i+1)%b.length];
    const vx=C[0]-A[0],vy=C[1]-A[1],L=vx*vx+vy*vy;let t=L>1e-6?((pt[0]-A[0])*vx+(pt[1]-A[1])*vy)/L:0;t=Math.max(0,Math.min(1,t));
    m=Math.min(m,Math.hypot(pt[0]-(A[0]+t*vx),pt[1]-(A[1]+t*vy)));}return m;}
  const R=v=>Math.round(v/100)*100;
  const found=[];
  for(let h=2600;h>=1400 && found.length<2;h-=200)
   for(let w=2200;w>=1400 && found.length<2;w-=200)
    for(let x=R(a)+200;x<=d-w-200 && found.length<2;x+=300)
     for(let y=R(c)+200;y<=e-h-200 && found.length<2;y+=300){
       const X=R(x),Y=R(y),W=R(w),H=R(h);
       const poly=[[X,Y],[X+W,Y],[X+W,Y+H],[X,Y+H]];
       if(poly.every(p=>pointInPoly(p,b)&&dB(p)>500)){
         // 이미 찾은 것과 겹치지 않게 x 간격 확보
         if(found.every(f=>Math.abs(f[0][0]-X)>W+800)) found.push(poly);
       }
     }
  return found;
}
"""


def handle_advice(route):
    zones = []
    if _return_zones and mock_zones:
        names = ["거실", "침실"]
        for i, poly in enumerate(mock_zones[:2]):
            zones.append({"name": names[i], "poly": poly, "area_m2": 10.0 + i})
    route.fulfill(status=200, content_type="application/json",
                  body=json.dumps({"answer": "• 남향에 거실, 북쪽에 욕실을 모으세요.\n• 현관은 동선 허브로.",
                                   "zones": zones, "context": "mock"}))


def screen(page, mm):
    return page.evaluate(PROJ, mm)


def click_mm(page, mm):
    p = screen(page, mm)
    page.mouse.move(p["x"], p["y"])
    page.mouse.click(p["x"], p["y"])
    page.wait_for_timeout(60)


with sync_playwright() as pw:
    browser = pw.chromium.launch()
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("dialog", lambda d: d.accept())
    page.route("**/api/design-advice", handle_advice)

    page.goto(URL)
    page.set_input_files("#file-input", PDF)
    page.wait_for_function("parsedData && parsedData.rooms && parsedData.rooms.length>0", timeout=60000)
    page.wait_for_timeout(700)
    page.evaluate("(r)=>{r.forEach(i=>{parsedData.rooms[i].unit='A';applyRoomBaseColor(i);});updateUnitCounts();}", A_ROOMS)

    page.click("#btn-design-mode")
    page.wait_for_function("designBoundary && designGroup && designUnit==='A'", timeout=20000)

    # 외곽 안에 실제로 들어가는 직사각형 2개를 찾아 목 응답 zones로 사용
    found = page.evaluate(FIND_TWO)
    assert found and len(found) == 2, f"외곽 안 직사각형 2개 못 찾음: {len(found) if found else 0}"
    mock_zones[:] = found
    print(f"[mock] 외곽 안 존 2개 확보")

    # ── 1/2. 조언 요청 → 모달 텍스트 + 존 생성 ──
    page.click("#btn-advice")
    page.wait_for_function("document.getElementById('advice-modal-body').textContent.includes('남향')", timeout=15000)
    modal_open = page.evaluate("document.getElementById('advice-modal-overlay').classList.contains('open')")
    nz = page.evaluate("designZones.length")
    nmesh = page.evaluate("_designZoneMeshes.length")
    print(f"[1 모달] open={modal_open}  [2 존] designZones={nz} 메시={nmesh}")
    assert modal_open, "모달이 안 열림"
    assert nz == 2, f"designZones 2개 기대, 실제 {nz}"
    assert nmesh >= nz, "존 메시가 안 만들어짐"
    # 모달 닫기
    page.keyboard.press("Escape"); page.wait_for_timeout(150)

    # ── 3. 존 정점 전부 외곽 안 ──
    allin = page.evaluate("""
      () => designZones.every(z => z.poly.every(p => pointInPoly(p, designBoundary)))
    """)
    print(f"[3 외곽 안] all_inside={allin}")
    assert allin, "존 정점이 외곽 밖에 있음"

    # ── 4. 토글 on/off ──
    vis0 = page.evaluate("_designZoneMeshes.every(m=>m.visible)")
    btn_disabled = page.evaluate("document.getElementById('btn-zone-toggle').disabled")
    print(f"[4 토글] 초기 visible={vis0} 버튼disabled={btn_disabled}")
    assert vis0 and btn_disabled is False, "초기 존 표시/버튼 상태 이상"
    page.click("#btn-zone-toggle"); page.wait_for_timeout(120)
    vis1 = page.evaluate("_designZoneMeshes.some(m=>m.visible)")
    state1 = page.evaluate("designZonesVisible")
    print(f"   off 후 any_visible={vis1} designZonesVisible={state1}")
    assert (not vis1) and state1 is False, "토글 off가 존을 안 숨김"
    page.click("#btn-zone-toggle"); page.wait_for_timeout(120)
    vis2 = page.evaluate("_designZoneMeshes.every(m=>m.visible)")
    print(f"   on 후 all_visible={vis2}")
    assert vis2, "토글 on이 존을 다시 안 보임"

    # ── 5. 존 위에 직사각형 방 그리기 (존이 방해 안 함) ──
    r = page.evaluate(FIND_RECT)
    assert r, "외곽 안 직사각형 못 찾음"
    rooms_b = page.evaluate("designRooms.length")
    walls_b = page.evaluate("designWalls.length")
    page.click("#btn-rect-mode")
    click_mm(page, r["a"])
    p = screen(page, r["b"]); page.mouse.move(p["x"], p["y"]); page.wait_for_timeout(40)
    click_mm(page, r["b"])
    rooms_a = page.evaluate("designRooms.length")
    walls_a = page.evaluate("designWalls.length")
    page.click("#btn-rect-mode")
    # y 높이: 방 타일(0.02) > 존 타일(0.012)
    tile_y = page.evaluate("""
      () => {
        let roomY=null, zoneY=null;
        for(const m of _designRoomMeshes) if(m.type==='Mesh'){roomY=m.position.y; break;}
        for(const m of _designZoneMeshes) if(m.type==='Mesh'){zoneY=m.position.y; break;}
        return {roomY, zoneY};
      }
    """)
    print(f"[5 그리기] 방 {rooms_b}→{rooms_a} 벽 {walls_b}→{walls_a}  y(room={tile_y['roomY']} zone={tile_y['zoneY']})")
    assert rooms_a > rooms_b and walls_a > walls_b, "존 위에서 직사각형 방 그리기 실패"
    assert tile_y["roomY"] is not None and tile_y["zoneY"] is not None, "타일 y 측정 실패"
    assert tile_y["roomY"] > tile_y["zoneY"], "방 타일이 존 타일보다 위에 있지 않음"

    # ── 6. ★ zones 빈 응답 → 존 안 뜨고 조언 텍스트는 정상 (graceful) ──
    globals()["_return_zones"] = False   # handle_advice가 참조하는 모듈 전역
    page.click("#btn-advice")
    # 응답 처리 완료 신호 = 존이 빈 응답으로 교체되어 0개 (모달 텍스트는 이전과 같아 신호로 못 씀)
    page.wait_for_function("designZones.length === 0", timeout=15000)
    nz2 = page.evaluate("designZones.length")
    zbtn2 = page.evaluate("document.getElementById('btn-zone-toggle').disabled")
    txt2 = page.evaluate("document.getElementById('advice-modal-body').textContent")
    print(f"[6 graceful] zones={nz2} 토글disabled={zbtn2} 텍스트있음={'남향' in txt2}")
    assert nz2 == 0, "빈 zones 응답인데 존이 생김"
    assert zbtn2 is True, "존 없는데 토글 버튼 활성"
    assert "남향" in txt2, "zones 없어도 조언 텍스트는 떠야 함"
    page.keyboard.press("Escape")

    print("page errors:", errors if errors else "없음")
    assert not errors, f"페이지 에러: {errors}"
    print("🎉 위치 존 오버레이 검증 통과 (모달·존생성·외곽안·토글·그리기공존·graceful·에러0)")
    browser.close()
