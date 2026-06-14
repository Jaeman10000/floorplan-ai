"""
직사각형 방 그리기 + 이름 붙이기 검증 — 역곡동빌라 page3 A세대 (실제 클릭).

1. 서브모드 토글 상호배타: ▭/✏️/🔒 한 번에 하나만.
2. 직사각형 모드 중엔 벽 클릭-클릭 비활성(첫 클릭=rectStart, drawStart는 null).
3. 대각선 2점 → 닫힌 방·면적·축정렬(4변 전부 수평/수직).
4. 인접 사각형 공유변 dedup (벽 4+4-1=7, 방 2).
5. 직사각형 = undo 1단위 (Ctrl+Z 한 번에 4변 통째 제거).
6. 이름 모드: 면 클릭→이름 지정→designRoomNames 반영·라벨·recompute 후 유지.
7. 좌클릭(2점) 중 카메라 이동 0 / 우드래그 카메라 이동 > 0.
8. 페이지 에러 0.
"""
import sys, glob, os
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

# 외곽 안쪽에 인접한 두 직사각형(공유변)을 찾는다. 모든 꼭짓점이 외곽서 450mm+ 떨어져
# snapCorner가 정점/선분(350mm) 대신 100mm 격자로만 스냅 → 좌표 예측 가능.
FIND_DOUBLE = """
() => {
  const b = designBoundary;
  let minX=1e9,minY=1e9,maxX=-1e9,maxY=-1e9;
  for(const p of b){minX=Math.min(minX,p[0]);maxX=Math.max(maxX,p[0]);minY=Math.min(minY,p[1]);maxY=Math.max(maxY,p[1]);}
  function dB(pt){let d=1e12;for(let i=0;i<b.length;i++){const a=b[i],c=b[(i+1)%b.length];
    const vx=c[0]-a[0],vy=c[1]-a[1],L2=vx*vx+vy*vy;let t=L2>1e-6?((pt[0]-a[0])*vx+(pt[1]-a[1])*vy)/L2:0;t=Math.max(0,Math.min(1,t));
    d=Math.min(d,Math.hypot(pt[0]-(a[0]+t*vx),pt[1]-(a[1]+t*vy)));}return d;}
  const R=v=>Math.round(v/100)*100;
  for(let h=2400; h>=1500; h-=200)
   for(let w1=1800; w1>=1400; w1-=200)
    for(let w2=1800; w2>=1400; w2-=200)
     for(let x=R(minX)+200; x<=maxX-(w1+w2)-200; x+=300)
      for(let y=R(minY)+200; y<=maxY-h-200; y+=300){
        const X=R(x),Y=R(y),W1=R(w1),W2=R(w2),H=R(h);
        const pts=[[X,Y],[X+W1+W2,Y],[X+W1+W2,Y+H],[X,Y+H],[X+W1,Y],[X+W1,Y+H]];
        if(pts.every(p=>pointInPoly(p,b)&&dB(p)>450))
          return {r1:{a:[X,Y],b:[X+W1,Y+H]}, r2:{a:[X+W1,Y],b:[X+W1+W2,Y+H]}};
      }
  return null;
}
"""


def screen(page, mm):
    return page.evaluate(PROJ, mm)


def click_mm(page, mm):
    p = screen(page, mm)
    page.mouse.move(p["x"], p["y"])
    page.mouse.click(p["x"], p["y"])
    page.wait_for_timeout(60)


def hover_mm(page, mm):
    p = screen(page, mm)
    page.mouse.move(p["x"], p["y"])
    page.wait_for_timeout(40)


def draw_rect(page, a_mm, b_mm):
    click_mm(page, a_mm)     # 첫 모서리
    hover_mm(page, b_mm)     # 미리보기
    click_mm(page, b_mm)     # 반대 모서리 확정


def click_face(page, idx):
    c = page.evaluate("(i)=>polyCentroid(designRooms[i].poly)", idx)
    click_mm(page, c)


def cam(page):
    return page.evaluate("() => ({x:roomCamera.position.x, y:roomCamera.position.y, z:roomCamera.position.z})")


def cam_dist(a, b):
    return ((a["x"]-b["x"])**2 + (a["y"]-b["y"])**2 + (a["z"]-b["z"])**2) ** 0.5


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

    dbl = page.evaluate(FIND_DOUBLE)
    assert dbl, "외곽 안 인접 직사각형 두 개를 찾지 못함"
    r1, r2 = dbl["r1"], dbl["r2"]
    print(f"[region] r1={r1} r2={r2}")

    # ── 1. 서브모드 토글 상호배타 ──
    page.click("#btn-rect-mode")
    st = page.evaluate("({rect:designRectMode, name:designNameMode, fix:designFixMode})")
    print(f"[1 rect on] {st}")
    assert st["rect"] and not st["name"] and not st["fix"], "rect 토글 상호배타 실패"

    # ── 2. 직사각형 모드 중 벽 클릭-클릭 비활성 ──
    click_mm(page, r1["a"])
    gate = page.evaluate("({rectStart:rectStart!==null, drawStart:drawStart})")
    print(f"[2 first click] rectStart set={gate['rectStart']} drawStart={gate['drawStart']}")
    assert gate["rectStart"] and gate["drawStart"] is None, "rect 모드 첫 클릭이 벽 그리기를 탐"
    page.keyboard.press("Escape")
    page.wait_for_timeout(80)
    assert page.evaluate("rectStart===null"), "Esc로 rectStart 취소 실패"

    # ── 3. 대각선 2점 → 닫힌 방·축정렬, 좌클릭 중 카메라 0 ──
    cam0 = cam(page)
    draw_rect(page, r1["a"], r1["b"])
    cam1 = cam(page)
    walls = page.evaluate("designWalls.length")
    rooms = page.evaluate("designRooms.length")
    axis = page.evaluate("designWalls.every(w => Math.abs(w.a[0]-w.b[0])<1 || Math.abs(w.a[1]-w.b[1])<1)")
    area1 = page.evaluate("designRooms.length ? designRooms[0].area_m2 : 0")
    print(f"[3 rect1] 벽={walls} 방={rooms} 축정렬={axis} 면적={area1} 카메라이동={cam_dist(cam0,cam1):.5f}")
    assert walls == 4 and rooms == 1, f"직사각형 1개 생성 실패: 벽{walls} 방{rooms}"
    assert axis, "벽이 축정렬(수평/수직)이 아님"
    assert area1 > 0, "방 면적 0"
    assert cam_dist(cam0, cam1) < 1e-4, "직사각형 2점 클릭 중 카메라 이동"

    # ── 4. 인접 사각형 공유변 dedup ──
    draw_rect(page, r2["a"], r2["b"])
    walls2 = page.evaluate("designWalls.length")
    rooms2 = page.evaluate("designRooms.length")
    print(f"[4 rect2 인접] 벽={walls2}(공유변 dedup→7) 방={rooms2}")
    assert walls2 == 7, f"공유변 dedup 실패: 벽{walls2} (기대 7)"
    assert rooms2 == 2, f"방 2개 인식 실패: {rooms2}"

    # ── 5. undo 1단위 (직사각형 통째) ──
    page.keyboard.press("Control+z")
    page.wait_for_timeout(150)
    wu = page.evaluate("designWalls.length")
    ru = page.evaluate("designRooms.length")
    print(f"[5 undo] 벽={wu} 방={ru} (rect2 통째 제거→4/1)")
    assert wu == 4 and ru == 1, "직사각형 undo 1단위 실패"
    # 복구: rect2 다시
    draw_rect(page, r2["a"], r2["b"])
    assert page.evaluate("designRooms.length") == 2

    # ── 6. 이름 모드: 상호배타 + 면 선택 + 이름 지정 + recompute 유지 ──
    page.click("#btn-name-mode")
    st2 = page.evaluate("({rect:designRectMode, name:designNameMode, fix:designFixMode})")
    print(f"[6 name on] {st2}")
    assert st2["name"] and not st2["rect"] and not st2["fix"], "name 토글 상호배타 실패"
    click_face(page, 0)
    sel = page.evaluate("selectedDesignFace")
    en = page.evaluate("!document.getElementById('btn-apply-name').disabled")
    print(f"   면 선택 idx={sel} 적용버튼활성={en}")
    assert sel >= 0 and en, "이름 모드 면 선택/버튼 활성 실패"
    page.fill("#design-name-input", "거실")
    page.click("#btn-apply-name")
    page.wait_for_timeout(120)
    names = page.evaluate("designRoomNames.map(n=>n.name)")
    has_named = page.evaluate("designRooms.some(r=>r.name==='거실')")
    print(f"[6 이름지정] designRoomNames={names} 면에반영={has_named}")
    assert "거실" in names, "designRoomNames에 이름 반영 안 됨"
    assert has_named, "면 라벨에 이름 반영 안 됨"
    # recompute 후 유지
    page.evaluate("recomputeDesignRooms(); renderDesignRooms();")
    still = page.evaluate("designRooms.some(r=>r.name==='거실')")
    print(f"[6 recompute 후] 이름유지={still}")
    assert still, "recompute 후 이름 사라짐"

    # ── 7. 고정 모드 토글 → 이름/직사각형 OFF (상호배타) ──
    page.click("#btn-fix-mode")
    st3 = page.evaluate("({rect:designRectMode, name:designNameMode, fix:designFixMode})")
    print(f"[7 fix on] {st3}")
    assert st3["fix"] and not st3["rect"] and not st3["name"], "fix 토글이 다른 모드를 안 끔"
    page.click("#btn-fix-mode")  # 끄기

    # ── 8. 우드래그 = 카메라 이동 (rect 모드 재진입 후) ──
    page.click("#btn-rect-mode")
    rect = page.evaluate("""() => { const r=document.querySelector('#panel-asis canvas.three-canvas').getBoundingClientRect();
      return {l:r.left, t:r.top, w:r.width, h:r.height}; }""")
    camA = cam(page)
    cx_, cy_ = rect["l"] + rect["w"]*0.5, rect["t"] + rect["h"]*0.5
    page.mouse.move(cx_, cy_)
    page.mouse.down(button="right")
    for k in range(1, 8):
        page.mouse.move(cx_ - 18*k, cy_ - 10*k)
        page.wait_for_timeout(15)
    page.mouse.up(button="right")
    page.wait_for_timeout(80)
    moved = cam_dist(camA, cam(page))
    print(f"[8 우드래그] 카메라이동={moved:.4f} (>0이어야)")
    assert moved > 1e-3, "우드래그로 카메라가 안 움직임"

    print("page errors:", errors if errors else "없음")
    assert not errors, f"페이지 에러: {errors}"
    print("🎉 직사각형 방 + 이름 붙이기 전 항목 통과")
    browser.close()
