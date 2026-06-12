"""
rooms_check.py — pdf_parser 방 구획 시각 검증

parse_pdf()가 나눈 rooms 각각을 다른 색으로 칠해 원본 PDF 렌더 위에
반투명 겹침. 방 경계가 실제 벽과 맞는지 한눈에 확인용.
결과: backend/rooms_check.png + 방 개수/면적 출력.

사용법: python rooms_check.py [PDF경로]  (기본 테스트용.pdf)
"""
import os
import sys

import pdfplumber
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(__file__))
from pdf_parser import parse_pdf, _building_outline_pts_pt

sys.stdout.reconfigure(encoding="utf-8")

PDF = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "테스트용.pdf")
R = 150  # 렌더 dpi

# 구분 잘 되는 색 팔레트
PALETTE = [
    (230, 25, 75), (60, 180, 75), (0, 130, 200), (245, 130, 48),
    (145, 30, 180), (70, 240, 240), (240, 50, 230), (210, 245, 60),
    (250, 190, 212), (0, 128, 128), (220, 190, 255), (170, 110, 40),
    (255, 250, 200), (128, 0, 0), (170, 255, 195), (128, 128, 0),
    (255, 215, 180), (0, 0, 128), (128, 128, 128), (255, 225, 25),
]

r = parse_pdf(PDF)
rooms = r["rooms"]
s = r["scale_mm_per_pt"]

# mm → 렌더 px (외곽 pt 최소값으로 오프셋 복원)
outline_pt = _building_outline_pts_pt(pdfplumber.open(PDF).pages[0])
min_x_pt = min(x for x, _ in outline_pt)
min_y_pt = min(y for _, y in outline_pt)

def mm_to_px(mx, my):
    return ((mx / s + min_x_pt) * R / 72.0, (my / s + min_y_pt) * R / 72.0)

page = pdfplumber.open(PDF).pages[0]
render = page.to_image(resolution=R).original.convert("RGB")
Wpx, Hpx = render.size

# 방별 색칠(반투명) 레이어
overlay = Image.new("RGBA", (Wpx, Hpx), (0, 0, 0, 0))
od = ImageDraw.Draw(overlay)
for rm in rooms:
    col = PALETTE[rm["id"] % len(PALETTE)]
    poly = [mm_to_px(x, y) for x, y in rm["polygon_mm"]]
    od.polygon(poly, fill=col + (110,), outline=col + (255,))

comp = Image.alpha_composite(render.convert("RGBA"), overlay).convert("RGB")

# 방 id + 면적 라벨(중심)
d = ImageDraw.Draw(comp)
for rm in rooms:
    poly = [mm_to_px(x, y) for x, y in rm["polygon_mm"]]
    cx = sum(p[0] for p in poly) / len(poly)
    cy = sum(p[1] for p in poly) / len(poly)
    d.text((cx - 8, cy - 4), f"{rm['id']}", fill=(0, 0, 0))

# 건물 영역 크롭
opx = [mm_to_px(x, y) for x, y in r["building_outline_mm"]]
xs = [p[0] for p in opx]; ys = [p[1] for p in opx]
mx = (max(xs) - min(xs)) * 0.08; my = (max(ys) - min(ys)) * 0.08
box = (max(0, int(min(xs) - mx)), max(0, int(min(ys) - my)),
       min(Wpx, int(max(xs) + mx)), min(Hpx, int(max(ys) + my)))
comp.crop(box).save(os.path.join(os.path.dirname(__file__), "rooms_check.png"))

# 출력
total = sum(rm["area_m2"] for rm in rooms)
print(f"입력 PDF   : {PDF}")
print(f"외곽 면적  : {r['outline_area_m2']} m²")
print(f"방 개수    : {len(rooms)}개  (방 면적 합계 {total:.1f} m² — 차이는 벽 두께)")
print("방별 면적:")
for rm in rooms:
    print(f"  room{rm['id']:>2}: {rm['area_m2']:>7.2f} m²  ({len(rm['polygon_mm'])}각형)")
print(f"\n검증 이미지 저장: {os.path.join(os.path.dirname(__file__), 'rooms_check.png')}")
