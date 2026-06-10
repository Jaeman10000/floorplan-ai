# dxf_builder.py
# pts_mm (List[Tuple[float, float]]) → DXF 파일 변환
# 레이어 구조: OUTLINE / DIMENSIONS / META

import math
import os
from typing import List, Tuple, Optional
from dataclasses import dataclass

import ezdxf
from ezdxf import units
from ezdxf.enums import TextEntityAlignment


@dataclass
class DXFBuildConfig:
    """DXF 변환 설정"""
    layer_outline: str = "OUTLINE"
    layer_dims: str = "DIMENSIONS"
    layer_meta: str = "META"
    color_outline: int = 7       # 흰색/검정 (배경에 따라)
    color_dims: int = 3          # 초록
    color_meta: int = 4          # 청록
    add_dimensions: bool = True  # 각 변 치수선 추가
    add_area_text: bool = True   # 면적 텍스트 추가
    dim_offset: float = 300.0    # 치수선 offset (mm)
    text_height: float = 200.0   # 텍스트 높이 (mm)
    close_polyline: bool = True  # 폴리라인 닫기


def build_dxf(
    pts_mm: List[Tuple[float, float]],
    output_path: str,
    area_m2: Optional[float] = None,
    config: Optional[DXFBuildConfig] = None,
    metadata: Optional[dict] = None,
) -> str:
    """
    pts_mm 좌표 목록을 DXF 파일로 변환한다.

    Args:
        pts_mm:      [(x, y), ...] 좌표 리스트 (단위: mm)
        output_path: 저장할 .dxf 파일 경로
        area_m2:     면적 (m²), 있으면 메타 텍스트에 삽입
        config:      DXFBuildConfig (None이면 기본값)
        metadata:    추가 메타정보 dict (예: {"source": "upload.png"})

    Returns:
        저장된 파일 경로
    """
    if not pts_mm or len(pts_mm) < 3:
        raise ValueError("pts_mm는 최소 3개 이상의 꼭짓점이 필요합니다.")

    cfg = config or DXFBuildConfig()
    meta = metadata or {}

    # ── DXF 문서 생성 (R2010 호환, mm 단위) ──────────────────────────
    doc = ezdxf.new("R2010")
    doc.units = units.MM
    msp = doc.modelspace()

    # ── 레이어 등록 ─────────────────────────────────────────────────
    doc.layers.add(cfg.layer_outline, color=cfg.color_outline, linetype="CONTINUOUS")
    doc.layers.add(cfg.layer_dims,    color=cfg.color_dims,    linetype="CONTINUOUS")
    doc.layers.add(cfg.layer_meta,    color=cfg.color_meta,    linetype="CONTINUOUS")

    # ── Y축 반전 (이미지 좌표계 → CAD 좌표계) ─────────────────────
    # 이미지는 위→아래 Y증가, CAD는 아래→위 Y증가
    max_y = max(p[1] for p in pts_mm)
    cad_pts = [(x, max_y - y) for x, y in pts_mm]

    # ── OUTLINE 폴리라인 ─────────────────────────────────────────────
    poly_pts = list(cad_pts)
    if cfg.close_polyline:
        poly_pts.append(cad_pts[0])   # 명시적으로 닫음

    msp.add_lwpolyline(
        poly_pts,
        dxfattribs={
            "layer": cfg.layer_outline,
            "closed": cfg.close_polyline,
            "lineweight": 50,  # 0.50mm
        },
    )

    # ── 치수선 (각 변 길이) ─────────────────────────────────────────
    if cfg.add_dimensions:
        _add_segment_dimensions(msp, cad_pts, cfg)

    # ── 면적 + 메타 텍스트 ──────────────────────────────────────────
    if cfg.add_area_text or meta:
        _add_meta_text(msp, cad_pts, area_m2, meta, cfg)

    # ── 저장 ────────────────────────────────────────────────────────
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    doc.saveas(output_path)
    return output_path


# ─────────────────────────────────────────────────────────────────────────────
# 내부 헬퍼
# ─────────────────────────────────────────────────────────────────────────────

def _add_segment_dimensions(msp, pts, cfg: DXFBuildConfig):
    """각 변에 선형 치수선 추가 (외곽 바깥쪽 방향)."""
    n = len(pts)
    centroid = _centroid(pts)

    for i in range(n):
        p1 = pts[i]
        p2 = pts[(i + 1) % n]
        length_mm = math.dist(p1, p2)

        if length_mm < 10:  # 너무 짧은 선은 생략
            continue

        # 변의 중점과 법선 방향 계산
        mx = (p1[0] + p2[0]) / 2
        my = (p1[1] + p2[1]) / 2
        dx = p2[0] - p1[0]
        dy = p2[1] - p1[1]
        length = math.hypot(dx, dy)
        nx, ny = -dy / length, dx / length  # 왼쪽 법선

        # 중심에서 멀어지는 방향으로 offset
        to_center_x = centroid[0] - mx
        to_center_y = centroid[1] - my
        if (nx * to_center_x + ny * to_center_y) > 0:
            nx, ny = -nx, -ny  # 바깥 방향으로 뒤집기

        off = cfg.dim_offset
        dim_pt = (mx + nx * off, my + ny * off)

        try:
            dim = msp.add_linear_dim(
                base=(dim_pt[0], dim_pt[1]),
                p1=(p1[0], p1[1]),
                p2=(p2[0], p2[1]),
                dxfattribs={"layer": cfg.layer_dims},
            )
            dim.render()
        except Exception:
            # 치수선 실패 시 단순 텍스트로 대체
            label = f"{length_mm:.0f}"
            msp.add_text(
                label,
                dxfattribs={
                    "layer": cfg.layer_dims,
                    "height": cfg.text_height * 0.7,
                    "insert": (dim_pt[0], dim_pt[1]),
                },
            )


def _add_meta_text(msp, pts, area_m2, meta: dict, cfg: DXFBuildConfig):
    """면적 및 메타정보 텍스트를 중앙 및 하단에 삽입."""
    centroid = _centroid(pts)
    lines = []

    if area_m2 is not None:
        lines.append(f"면적: {area_m2:.2f} m2")

    for k, v in meta.items():
        lines.append(f"{k}: {v}")

    if not lines:
        return

    h = cfg.text_height
    gap = h * 1.5
    start_y = centroid[1] + gap * (len(lines) - 1) / 2

    for i, line in enumerate(lines):
        msp.add_text(
            line,
            dxfattribs={
                "layer": cfg.layer_meta,
                "height": h,
                "insert": (centroid[0], start_y - i * gap),
                "halign": 1,  # center
            },
        )


def _centroid(pts: List[Tuple[float, float]]) -> Tuple[float, float]:
    n = len(pts)
    if n == 0:
        return (0.0, 0.0)
    return (sum(p[0] for p in pts) / n, sum(p[1] for p in pts) / n)


# ─────────────────────────────────────────────────────────────────────────────
# 편의 함수: ExtractionResult 에서 직접 변환
# ─────────────────────────────────────────────────────────────────────────────

def build_dxf_from_result(result, output_path: str, config: Optional[DXFBuildConfig] = None) -> str:
    """
    extractor.ExtractionResult 객체를 받아 DXF로 변환한다.

    Usage:
        from extractor import extract_outline
        from dxf_builder import build_dxf_from_result

        result = extract_outline("floorplan.png", known_area_m2=85.0)
        dxf_path = build_dxf_from_result(result, "output/floorplan.dxf")
    """
    meta = {}
    if result.scale_mm_per_px:
        meta["scale_mm_per_px"] = f"{result.scale_mm_per_px:.4f}"
    if result.confidence is not None:
        meta["confidence"] = f"{result.confidence:.2f}"
    if result.warnings:
        meta["warnings"] = " | ".join(result.warnings[:3])

    return build_dxf(
        pts_mm=result.pts_mm,
        output_path=output_path,
        area_m2=result.area_m2,
        config=config,
        metadata=meta,
    )
