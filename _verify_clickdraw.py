"""
클릭-클릭 그리기 + 직각 스냅 기본 검증 — 역곡동빌라 page3 A세대.

1. 클릭-클릭: 시작점 클릭 → hover 미리보기 → 끝점 클릭 → 벽 생성, 닫힌 방 ≥2.
2. 직각(±20°): 살짝 비스듬한 끝점도 수평/수직 정렬(snapPoint allowOrtho).
3. Shift=자유각: allowOrtho=false면 직교화 안 함(원래 각도 유지).
4. undo(Ctrl+Z)로 벽 원복.
5. Esc로 진행중 선 취소(시작점 버림).
6. 좌클릭-클릭 중 카메라 이동 0 / 우드래그 카메라 이동 > 0.
7. 첫 점 찍은 뒤 우드래그로 카메라 돌려도 시작점 고정 + 이어 둘째 클릭 확정 정상.
8. 너무 짧은 둘째 클릭은 진행 유지(시작점 안 날림).
9. 페이지 에러 0.
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


def screen(page, mm):
    return page.evaluate(PROJ, mm)


def click_mm(page, mm, shift=False):
    p = screen(page, mm)
    page.mouse.move(p["x"], p["y"])
    if shift:
        page.keyboard.down("Shift")
    page.mouse.click(p["x"], p["y"])
    if shift:
        page.keyboard.up("Shift")
    page.wait_for_timeout(60)


def hover_mm(page, mm):
    p = screen(page, mm)
    page.mouse.move(p["x"], p["y"])
    page.wait_for_timeout(40)


def draw_clickclick(page, a_mm, b_mm, shift=False):
    click_mm(page, a_mm)          # 시작점
    hover_mm(page, b_mm)          # 미리보기
    click_mm(page, b_mm, shift)   # 끝점 확정


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

    # ── 2/3. 직각 스냅(±20°) + Shift 자유각 (snapPoint 직접) — 벽 그리기 전, 경계서 먼 빈 점 ──
    snap = page.evaluate("""() => {
      const b = designBoundary;
      let cx=0,cy=0; for(const p of b){cx+=p[0];cy+=p[1];} cx/=b.length; cy/=b.length;
      function distToB(p){ let d=1e12;
        for(let i=0;i<b.length;i++){const a=b[i],c=b[(i+1)%b.length];
          const vx=c[0]-a[0],vy=c[1]-a[1],L2=vx*vx+vy*vy;
          let t=L2>1e-6?((p[0]-a[0])*vx+(p[1]-a[1])*vy)/L2:0; t=Math.max(0,Math.min(1,t));
          d=Math.min(d,Math.hypot(p[0]-(a[0]+t*vx),p[1]-(a[1]+t*vy)));}
        return d; }
      // 경계서 900mm+ 떨어진 from·raw 탐색 (스냅타깃 350mm 간섭 회피). raw=from+(1200,200)=9.5°<20°
      for(let r=0;r<4000;r+=200) for(let a=0;a<360;a+=30){
        const fx=cx+r*Math.cos(a*Math.PI/180), fy=cy+r*Math.sin(a*Math.PI/180);
        const from=[fx,fy], raw=[fx+1200, fy+200];
        if(distToB(from)>900 && distToB(raw)>900)
          return { from, raw, ortho: snapPoint(raw,from,true), free: snapPoint(raw,from,false) };
      }
      return null;
    }""")
    assert snap, "직각 테스트용 빈 공간 점을 못 찾음"
    fy = snap["from"][1]; ry = snap["raw"][1]
    print(f"[2 직각] from_y={fy:.0f} raw_y={ry:.0f} ortho_y={snap['ortho'][1]:.0f} (수평이면 from_y와 같음)")
    print(f"[3 Shift] free_y={snap['free'][1]:.0f} (자유각이면 raw_y와 같음)")
    assert abs(snap["ortho"][1] - fy) < 1.0, "±20° 안인데 직각(수평) 정렬 안 됨"
    assert abs(snap["free"][1] - ry) < 1.0, "Shift(자유각)인데 직교화됨"

    # ── 1. 클릭-클릭 벽 생성 + 닫힌 방 ──
    chord = page.evaluate(CHORD)
    assert chord, "chord 계산 실패"
    cam0 = cam(page)
    draw_clickclick(page, chord["left"], chord["right"])
    walls = page.evaluate("designWalls.length")
    rooms = page.evaluate("designRooms.length")
    cam1 = cam(page)
    print(f"[1 클릭-클릭] 벽={walls} 방={rooms} / 좌클릭중 카메라이동={cam_dist(cam0,cam1):.5f}")
    assert walls == 1 and rooms >= 2, f"클릭-클릭 그리기/방닫힘 실패: 벽{walls} 방{rooms}"
    assert cam_dist(cam0, cam1) < 1e-4, "좌클릭-클릭 중 카메라가 움직임"

    # ── 4. undo ──
    page.keyboard.press("Control+z")
    page.wait_for_timeout(150)
    after_undo = page.evaluate("designWalls.length")
    print(f"[4 undo] 벽={after_undo}")
    assert after_undo == 0, "undo로 벽 원복 안 됨"

    # ── 5. Esc 취소 (시작점만 찍고 취소) ──
    click_mm(page, chord["left"])
    started = page.evaluate("drawStart!==null")
    page.keyboard.press("Escape")
    page.wait_for_timeout(120)
    canceled = page.evaluate("drawStart===null && designDrawing===false")
    print(f"[5 Esc] 시작점찍힘={started} → 취소후 idle={canceled}")
    assert started and canceled, "Esc 취소 실패"

    # ── 6/7. 첫 점 후 우드래그 카메라 → 시작점 고정 + 이어 확정 ──
    rect = page.evaluate("""() => { const r=document.querySelector('#panel-asis canvas.three-canvas').getBoundingClientRect();
      return {l:r.left, t:r.top, w:r.width, h:r.height}; }""")
    click_mm(page, chord["left"])          # 첫 점
    start_before = page.evaluate("drawStart.slice()")
    camA = cam(page)
    # 우드래그
    cx_, cy_ = rect["l"] + rect["w"]*0.5, rect["t"] + rect["h"]*0.5
    page.mouse.move(cx_, cy_)
    page.mouse.down(button="right")
    for k in range(1, 8):
        page.mouse.move(cx_ - 18*k, cy_ - 10*k)
        page.wait_for_timeout(15)
    page.mouse.up(button="right")
    page.wait_for_timeout(80)
    camB = cam(page)
    start_after = page.evaluate("drawStart.slice()")
    moved_cam = cam_dist(camA, camB)
    same_start = abs(start_before[0]-start_after[0]) < 1 and abs(start_before[1]-start_after[1]) < 1
    print(f"[6/7 우드래그] 카메라이동={moved_cam:.4f} (>0이어야) / 시작점고정={same_start}")
    assert moved_cam > 1e-3, "우드래그로 카메라가 안 움직임"
    assert same_start, "우드래그 중 시작점이 흔들림"
    # 이어 둘째 클릭으로 확정
    draw_end = page.evaluate(CHORD)["right"]
    hover_mm(page, draw_end)
    click_mm(page, draw_end)
    walls2 = page.evaluate("designWalls.length")
    print(f"[7 이어 확정] 벽={walls2}")
    assert walls2 == 1, "우드래그 후 이어 그리기 확정 실패"

    # ── 8. 너무 짧은 둘째 클릭 = 진행 유지 ──
    page.evaluate("() => { undoDesignWall(); }")   # 깨끗이
    page.wait_for_timeout(100)
    start_mm = chord["left"]
    click_mm(page, start_mm)                       # 시작점
    near = [start_mm[0] + 30, start_mm[1] + 30]    # 30mm = _DRAW_MIN_MM(100) 미만
    click_mm(page, near)                           # 너무 짧음
    still = page.evaluate("drawStart!==null")
    nwall = page.evaluate("designWalls.length")
    print(f"[8 짧은클릭] 진행유지(drawStart!=null)={still} 벽={nwall}(안늘어야)")
    assert still and nwall == 0, "짧은 둘째 클릭이 시작점을 날리거나 벽을 추가함"
    page.keyboard.press("Escape")

    print("page errors:", errors if errors else "없음")
    assert not errors, f"페이지 에러: {errors}"
    print("🎉 클릭-클릭 + 직각스냅 전 항목 통과")
    browser.close()
