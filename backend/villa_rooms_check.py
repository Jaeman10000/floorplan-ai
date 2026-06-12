"""
villa_rooms_check.py — pdf_parser 방 구획 + 방 이름 매칭 시각 검증

parse_pdf()가 나눈 rooms를 각각 다른 색으로 칠하고, 매칭된 방 이름을 그 위에
표시해 원본 PDF 렌더에 겹친다. 결과: backend/villa_rooms_check.png.
origin_mm으로 mm→렌더px 역투영하므로 fill/stroke 경로 모두 동작.

사용법: python villa_rooms_check.py [PDF경로] [page_index]
        (기본: 역곡동빌라.pdf, page 3)
"""
import os
import sys

import pdfplumber
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(__file__))
from pdf_parser import parse_pdf

sys.stdout.reconfigure(encoding="utf-8")

PDF = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "역곡동빌라.pdf")
PAGE = int(sys.argv[2]) if len(sys.argv) > 2 else 3
R = 150
OUT = os.path.join(os.path.dirname(__file__), "villa_rooms_check.png")

PALETTE = [
    (230, 25, 75), (60, 180, 75), (0, 130, 200), (245, 130, 48),
    (145, 30, 180), (70, 240, 240), (240, 50, 230), (210, 245, 60),
    (250, 190, 212), (0, 128, 128), (220, 190, 255), (170, 110, 40),
    (190, 60, 60), (128, 0, 0), (170, 255, 195), (128, 128, 0),
    (255, 140, 90), (0, 0, 128), (100, 200, 120), (255, 215, 25),
]

r = parse_pdf(PDF, page_index=PAGE)
rooms = r["rooms"]
s = r["scale_mm_per_pt"]
ox, oy = r["origin_mm"]

def mm_to_px(mx, my):
    return ((mx + ox) / s * R / 72.0, (my + oy) / s * R / 72.0)

page = pdfplumber.open(PDF).pages[PAGE]
render = page.to_image(resolution=R).original.convert("RGB")
Wpx, Hpx = render.size

# 방별 색칠
overlay = Image.new("RGBA", (Wpx, Hpx), (0, 0, 0, 0))
od = ImageDraw.Draw(overlay)
for rm in rooms:
    col = PALETTE[rm["id"] % len(PALETTE)]
    poly = [mm_to_px(x, y) for x, y in rm["polygon_mm"]]
    od.polygon(poly, fill=col + (115,), outline=col + (255,))
comp = Image.alpha_composite(render.convert("RGBA"), overlay).convert("RGB")

# 방 이름 + id 라벨(중심)
d = ImageDraw.Draw(comp)
for rm in rooms:
    poly = [mm_to_px(x, y) for x, y in rm["polygon_mm"]]
    cx = sum(p[0] for p in poly) / len(poly)
    cy = sum(p[1] for p in poly) / len(poly)
    label = "/".join(rm["names"]) if rm["names"] else f"#{rm['id']}"
    d.text((cx - 12, cy - 5), label, fill=(0, 0, 0))

# 건물 영역 크롭
opx = [mm_to_px(x, y) for x, y in r["building_outline_mm"]]
xs = [p[0] for p in opx]; ys = [p[1] for p in opx]
mx = (max(xs) - min(xs)) * 0.08; my = (max(ys) - min(ys)) * 0.08
box = (max(0, int(min(xs) - mx)), max(0, int(min(ys) - my)),
       min(Wpx, int(max(xs) + mx)), min(Hpx, int(max(ys) + my)))
comp.crop(box).save(OUT)

# 출력
named = [rm for rm in rooms if rm["names"]]
print(f"입력 PDF   : {PDF}  (page {PAGE})")
print(f"벽 표현    : {r['wall_repr']}")
print(f"외곽 면적  : {r['outline_area_m2']} m²  ({len(r['building_outline_mm'])}점)")
print(f"방 개수    : {len(rooms)}개  (이름 매칭 {len(named)}개)")
print("방별 이름/면적:")
for rm in rooms:
    nm = "/".join(rm["names"]) if rm["names"] else "(이름없음)"
    print(f"  room{rm['id']:>2}: {rm['area_m2']:>7.2f} m²  {nm}")
for w in r["warnings"]:
    print("  ⚠", w)
print(f"\n검증 이미지 저장: {OUT}")
