"""Step 2 폴리곤 근사 진단 — Vision API 없이 외곽 꼭짓점만 검증"""
import cv2
import numpy as np
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))
from extractor import (
    _step0_crop_floorplan, _step1_wall_mask, _step2_polygon, _shoelace_px2
)

IMG = os.path.join(os.path.dirname(__file__), "도면이미지.png")
buf = np.fromfile(IMG, dtype=np.uint8)
img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
if img is None:
    sys.exit("이미지 로드 실패")

warnings = []
print(f"원본: {img.shape[1]}x{img.shape[0]}")

img_work, cx, cy = _step0_crop_floorplan(img, warnings)
print(f"크롭: {img_work.shape[1]}x{img_work.shape[0]} offset=({cx},{cy})")

mask = _step1_wall_mask(img_work, warnings)
pts = _step2_polygon(img_work, mask, 0.01, 0.05, warnings)

print(f"\n=== Step2 폴리곤 ===")
print(f"꼭짓점 수: {len(pts)}")
for i, p in enumerate(pts):
    print(f"  v{i}: {p}")

# 변 길이 + 둘레 비율
n = len(pts)
print("\n변 길이 (둘레 대비 %):")
import math
lens = [math.hypot(pts[(i+1)%n][0]-pts[i][0], pts[(i+1)%n][1]-pts[i][1]) for i in range(n)]
peri = sum(lens)
for i in range(n):
    print(f"  e{i} v{i}->v{(i+1)%n}: {lens[i]:7.1f}px  ({lens[i]/peri*100:4.1f}%)")

print(f"\n면적(px²): {_shoelace_px2(pts):,.0f}")

sys.stdout.reconfigure(encoding="utf-8")
for w in warnings:
    print(" ", w)

# 시각화
vis = img_work.copy()
for i in range(n):
    cv2.line(vis, pts[i], pts[(i+1)%n], (0,0,255), 2)
for i, p in enumerate(pts):
    cv2.circle(vis, p, 7, (0,255,0), -1)
    cv2.putText(vis, str(i), (p[0]+8, p[1]), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255,0,0), 2)
cv2.imwrite(os.path.join(os.path.dirname(__file__), "test_step2.png"), vis)
print("\n시각화: test_step2.png")
