# extractor.py
# 파이프라인 (CLAUDE.md 정의):
# Step 0: 도면 영역 자동 크롭 (용지 테두리·제목란 제거)
# Step 1: 딥러닝 (CubiCasa5K SegFormer) → 벽 픽셀 마스크
# Step 2: OpenCV → 마스크 or 크롭 이미지 → pts_px (크롭 좌표계)
# Step 3: Tesseract OCR → 치수 → scale_mm_per_px (크롭 좌표계)
# Step 4: pts_px (원본 좌표) × scale → pts_mm (0,0 정규화)
# Step 5: Claude Vision API → 방이름 / 세대 / 공용부 (원본 이미지)

import cv2
import numpy as np
import base64
import json
import math
import os
import re
import httpx
from typing import List, Tuple, Optional
from dataclasses import dataclass, field


# ─────────────────────────────────────────────────────────────────────────────
# 데이터 구조
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class RoomInfo:
    name: str
    polygon_mm: List[Tuple[float, float]]
    has_window: bool = False

@dataclass
class UnitInfo:
    name: str
    outline_mm: List[Tuple[float, float]]
    area_m2: float = 0.0
    rooms: List[RoomInfo] = field(default_factory=list)

@dataclass
class CommonAreaInfo:
    name: str
    polygon_mm: List[Tuple[float, float]]

@dataclass
class ExtractionResult:
    pts_px:          List[Tuple[int, int]]
    pts_mm:          List[Tuple[float, float]]
    scale_mm_per_px: float
    area_m2:         float
    confidence:      float
    ocr_dimensions:  List[float]
    warnings:        List[str]
    units:           List[UnitInfo]         = field(default_factory=list)
    common_areas:    List[CommonAreaInfo]   = field(default_factory=list)
    raw_vision:      Optional[dict]         = None


# ─────────────────────────────────────────────────────────────────────────────
# 메인 진입점
# ─────────────────────────────────────────────────────────────────────────────

def extract_outline(
    image_path: str,
    known_area_m2: Optional[float] = None,
    scale_hint_mm_per_px: Optional[float] = None,
    epsilon_ratio: float = 0.01,
    min_area_ratio: float = 0.05,
) -> ExtractionResult:
    warnings: List[str] = []

    buf = np.fromfile(image_path, dtype=np.uint8)
    img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError(f"이미지를 읽을 수 없습니다: {image_path}")

    # ── Step 0: 도면 영역 크롭 ────────────────────────────────────────────
    img_work, crop_x, crop_y = _step0_crop_floorplan(img, warnings)

    # ── Step 1: 딥러닝 벽 마스크 (크롭 이미지 사용) ───────────────────────
    mask = _step1_wall_mask(img_work, warnings)

    # ── Step 2: 외곽 폴리곤 (크롭 좌표계) ────────────────────────────────
    pts_px_crop = _step2_polygon(img_work, mask, epsilon_ratio, min_area_ratio, warnings)
    if not pts_px_crop:
        raise ValueError("외곽 폴리곤 추출 실패")

    # 크롭 좌표 → 원본 좌표로 변환
    pts_px = [(x + crop_x, y + crop_y) for x, y in pts_px_crop]

    # ── Step 3: OCR 스케일 (크롭 이미지 기준 — pts_px_crop과 동일 좌표계) ─
    gray_work = cv2.cvtColor(img_work, cv2.COLOR_BGR2GRAY)
    dim_tokens = _step3_ocr_tokens(gray_work, warnings)
    dimensions = sorted({t["value"] for t in dim_tokens}, reverse=True)

    scale = _resolve_scale(pts_px_crop, dim_tokens, known_area_m2,
                           scale_hint_mm_per_px, img_work.shape[1], warnings)

    # ── Step 4: pts_mm (원본 좌표 기반, 0,0 정규화) ───────────────────────
    pts_mm = _step4_pts_mm(pts_px, scale)

    area_m2 = _shoelace_m2(pts_mm)

    # ── Step 5: Vision API 의미 정보 (원본 이미지) ────────────────────────
    units, common_areas, raw_vision = _step5_vision_semantic(image_path, warnings)

    confidence = 0.5
    if dim_tokens:
        confidence = 0.7
    if mask is not None:
        confidence = 0.85
    if raw_vision:
        confidence = min(confidence + 0.05, 0.95)

    return ExtractionResult(
        pts_px=pts_px,
        pts_mm=pts_mm,
        scale_mm_per_px=scale,
        area_m2=round(area_m2, 2),
        confidence=confidence,
        ocr_dimensions=dimensions,
        warnings=warnings,
        units=units,
        common_areas=common_areas,
        raw_vision=raw_vision,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Step 0 — 도면 영역 자동 크롭
# ─────────────────────────────────────────────────────────────────────────────

def _step0_crop_floorplan(
    img: np.ndarray, warnings: list
) -> Tuple[np.ndarray, int, int]:
    """
    용지 테두리(흰 여백) + 우측 제목란을 제거하고 순수 평면도 영역만 크롭.

    전략:
    1. 프로젝션 프로파일: 어두운 픽셀이 있는 행/열 범위 → 콘텐츠 bounding box
    2. 우측 30% 구간에서 컬럼 밀도 분석 → 제목란 왼쪽 경계 탐지
    3. 크롭 후 (crop_x, crop_y) 반환 → Step 2 좌표 역변환에 사용

    반환: (cropped_img, crop_x, crop_y)
    실패 시 원본 이미지와 (0, 0) 반환.
    """
    H, W = img.shape[:2]
    gray   = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    binary = (gray < 220).astype(np.uint8)

    # ── 프로젝션 프로파일: 콘텐츠 경계 ─────────────────────────────────────
    row_sums = binary.sum(axis=1)
    col_sums = binary.sum(axis=0)

    active_rows = np.where(row_sums > W * 0.008)[0]
    active_cols = np.where(col_sums > H * 0.008)[0]

    if len(active_rows) < 10 or len(active_cols) < 10:
        warnings.append("Step0: 콘텐츠 영역 감지 실패 — 원본 사용")
        return img, 0, 0

    ry1, ry2 = int(active_rows[0]),  int(active_rows[-1])
    cx1, cx2 = int(active_cols[0]),  int(active_cols[-1])

    content_w = cx2 - cx1
    content_h = ry2 - ry1

    # ── 제목란 탐지: 우측 30% 구간의 컬럼 밀도 ────────────────────────────
    right_start = cx1 + int(content_w * 0.70)
    density = col_sums[right_start:cx2].astype(float) / H
    density_thresh = density.max() * 0.35

    title_col_mask = density > density_thresh
    title_left = cx2  # 제목란 없으면 cx2까지 포함
    if title_col_mask.any():
        title_left_local = int(np.argmax(title_col_mask))
        title_left = right_start + title_left_local

    # ── 최종 크롭 ────────────────────────────────────────────────────────
    PAD = 8
    x1 = max(0,    cx1        + PAD)
    y1 = max(0,    ry1        + PAD)
    x2 = min(W,    title_left - PAD)
    y2 = min(H,    ry2        + PAD)

    if (x2 - x1) < W * 0.30 or (y2 - y1) < H * 0.30:
        warnings.append("Step0: 크롭 영역 너무 작음 — 원본 사용")
        return img, 0, 0

    cropped = img[y1:y2, x1:x2]
    removed_title = (title_left < cx2)
    warnings.append(
        f"Step0: {W}x{H} → {x2-x1}x{y2-y1} (offset {x1},{y1}"
        + (f", 제목란 제거 x<{title_left})" if removed_title else ")")
    )
    return cropped, x1, y1


# ─────────────────────────────────────────────────────────────────────────────
# Step 1 — 딥러닝 벽 마스크
# ─────────────────────────────────────────────────────────────────────────────

def _step1_wall_mask(img: np.ndarray, warnings: list) -> Optional[np.ndarray]:
    """
    CubiCasa5K SegFormer → HuggingFace SegFormer 순으로 시도.
    둘 다 실패하면 None → Step 2에서 OpenCV fallback.
    입력은 크롭된 이미지(img_work).
    """
    mask = _try_cubicasa5k(img, warnings)
    if mask is not None:
        return mask

    mask = _try_huggingface(img, warnings)
    if mask is not None:
        return mask

    warnings.append("Step1: 딥러닝 모델 없음 — Step2 OpenCV fallback")
    return None


def _try_cubicasa5k(img: np.ndarray, warnings: list) -> Optional[np.ndarray]:
    """
    JessiP23/cubicasa-segformer-v2 (segmentation_models_pytorch + albumentations).
    Wall class = 1.  가중치는 HuggingFace cache에서 자동 다운로드.
    CUBICASA5K_WEIGHTS 환경변수로 로컬 경로 지정 가능.
    """
    try:
        import torch
        import segmentation_models_pytorch as smp
        import albumentations as A
        from albumentations.pytorch import ToTensorV2
        from huggingface_hub import hf_hub_download

        weights_path = os.environ.get("CUBICASA5K_WEIGHTS", "")
        if not weights_path or not os.path.isfile(weights_path):
            repo_id = os.environ.get(
                "CUBICASA5K_HF_REPO", "JessiP23/cubicasa-segformer-v2"
            )
            weights_path = hf_hub_download(
                repo_id=repo_id, filename="cubicasa_segformer_best.pt"
            )

        ck  = torch.load(weights_path, map_location="cpu", weights_only=False)
        enc = ck.get("encoder_name", "mit_b2")
        sz  = ck.get("img_size", 640)
        n   = ck.get("num_classes", 18)

        model = smp.Segformer(encoder_name=enc, classes=n)
        model.load_state_dict(ck["model_state_dict"])
        model.eval()

        img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        tf  = A.Compose([A.Resize(sz, sz), A.Normalize(), ToTensorV2()])
        inp = tf(image=img_rgb)["image"].unsqueeze(0)

        with torch.no_grad():
            pred_small = model(inp).argmax(1).squeeze().numpy()

        pred = cv2.resize(
            pred_small.astype(np.uint8),
            (img.shape[1], img.shape[0]),
            interpolation=cv2.INTER_NEAREST,
        )
        wall_mask = (pred == 1).astype(np.uint8) * 255  # Wall class = 1

        min_px = img.shape[0] * img.shape[1] * 0.02  # 최소 2%
        if wall_mask.sum() / 255 < min_px:
            warnings.append(
                f"CubiCasa5K 벽 마스크 부족 ({int(wall_mask.sum()//255)}px < {min_px:.0f}) — 건너뜀"
            )
            return None

        warnings.append(f"Step1: CubiCasa5K ({enc}) wall={int(wall_mask.sum()//255)}px")
        return wall_mask

    except ImportError as e:
        warnings.append(f"CubiCasa5K 의존성 없음 ({str(e)[:40]}) — HuggingFace 시도")
        return None
    except Exception as e:
        warnings.append(f"CubiCasa5K 실패: {str(e)[:80]}")
        return None


def _try_huggingface(img: np.ndarray, warnings: list) -> Optional[np.ndarray]:
    """
    HuggingFace SegFormer 세분화 모델.
    FLOORPLAN_DL_MODEL 환경 변수로 모델 ID 지정 필수 (미설정 시 건너뜀).
    """
    model_id = os.environ.get("FLOORPLAN_DL_MODEL", "")
    if not model_id:
        warnings.append("FLOORPLAN_DL_MODEL 미설정 — HuggingFace 건너뜀")
        return None

    try:
        import torch
        from PIL import Image as PILImage
        from transformers import SegformerImageProcessor, SegformerForSemanticSegmentation

        image = PILImage.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
        processor = SegformerImageProcessor.from_pretrained(model_id)
        model     = SegformerForSemanticSegmentation.from_pretrained(model_id)
        model.eval()

        inputs = processor(images=image, return_tensors="pt")
        with torch.no_grad():
            logits = model(**inputs).logits

        pred = logits.argmax(dim=1).squeeze().numpy().astype(np.uint8)
        pred_full = cv2.resize(pred, (img.shape[1], img.shape[0]),
                               interpolation=cv2.INTER_NEAREST)

        id2label = getattr(model.config, "id2label", {})
        wall_ids = [k for k, v in id2label.items() if "wall" in str(v).lower()]
        if not wall_ids:
            wall_ids = [0]

        wall_mask = np.isin(pred_full, wall_ids).astype(np.uint8) * 255

        min_px = img.shape[0] * img.shape[1] * 0.02
        if wall_mask.sum() / 255 < min_px:
            warnings.append(f"HuggingFace({model_id}) 벽 마스크 부족 — 건너뜀")
            return None

        warnings.append(f"Step1: HuggingFace {model_id}")
        return wall_mask

    except ImportError:
        warnings.append("transformers 미설치 — 딥러닝 건너뜀")
        return None
    except Exception as e:
        warnings.append(f"HuggingFace 실패: {str(e)[:80]}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Step 2 — 외곽 폴리곤 추출
# ─────────────────────────────────────────────────────────────────────────────

def _step2_polygon(
    img: np.ndarray,
    mask: Optional[np.ndarray],
    epsilon_ratio: float,
    min_area_ratio: float,
    warnings: list,
) -> List[Tuple[int, int]]:
    """마스크가 있으면 마스크에서, 없으면 원본 이미지에서 외곽 폴리곤 추출."""
    if mask is not None:
        pts = _polygon_from_mask(mask, epsilon_ratio, warnings)
        if pts:
            return pts
        warnings.append("마스크 폴리곤 추출 실패 — 원본 이미지로 재시도")

    return _polygon_from_image(img, epsilon_ratio, min_area_ratio, warnings)


def _polygon_from_mask(
    mask: np.ndarray, epsilon_ratio: float, warnings: list
) -> List[Tuple[int, int]]:
    """벽 마스크 → 외곽 폴리곤. 모폴로지로 벽 픽셀을 연결해 외곽선 추출."""
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (7, 7))
    closed = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=4)
    filled = _flood_fill_interior(closed)

    contours, _ = cv2.findContours(filled, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return []

    main = max(contours, key=cv2.contourArea)
    if cv2.contourArea(main) < mask.shape[0] * mask.shape[1] * 0.02:
        warnings.append("마스크 외곽 면적 너무 작음")
        return []

    approx = _simplify_contour(main, epsilon_ratio)
    return [(int(p[0][0]), int(p[0][1])) for p in approx]


def _polygon_from_image(
    img: np.ndarray, epsilon_ratio: float, min_area_ratio: float, warnings: list
) -> List[Tuple[int, int]]:
    """원본 이미지 → 이진화 → 외곽 폴리곤 (OpenCV fallback)."""
    h, w = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    thresh = cv2.adaptiveThreshold(
        blurred, 255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 11, 2,
    )
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    closed = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, kernel, iterations=2)
    opened = cv2.morphologyEx(closed, cv2.MORPH_OPEN,  kernel, iterations=1)

    contours, _ = cv2.findContours(opened, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        raise ValueError("OpenCV: 외곽선 없음")

    min_area = h * w * min_area_ratio
    valid = [c for c in contours if cv2.contourArea(c) > min_area]
    if not valid:
        valid = sorted(contours, key=cv2.contourArea, reverse=True)[:1]
        warnings.append("외곽 후보 부족 — 가장 큰 윤곽 사용")

    main = max(valid, key=cv2.contourArea)
    approx = _simplify_contour(main, epsilon_ratio)
    pts = [(int(p[0][0]), int(p[0][1])) for p in approx]

    warnings.append(f"Step2: OpenCV fallback — {len(pts)}각형")
    return pts


def _flood_fill_interior(mask: np.ndarray) -> np.ndarray:
    """벽 마스크 내부를 채워 solid polygon으로 만든다."""
    filled = mask.copy()
    h, w = filled.shape
    seed = np.zeros((h + 2, w + 2), np.uint8)
    cv2.floodFill(filled, seed, (0, 0), 255)
    filled_inv = cv2.bitwise_not(filled)
    return cv2.bitwise_or(mask, filled_inv)


# ─────────────────────────────────────────────────────────────────────────────
# Step 3 — Tesseract OCR → 치수 토큰
# ─────────────────────────────────────────────────────────────────────────────

def _step3_ocr_tokens(gray: np.ndarray, warnings: list) -> list:
    """
    Tesseract로 도면의 치수 숫자를 위치 정보와 함께 읽는다.
    반환: [{"value": float(mm), "cx": px, "cy": px, "conf": float}, ...]
    실패 시 빈 리스트 반환 (파이프라인 비중단).
    """
    try:
        import pytesseract
    except ImportError:
        warnings.append("pytesseract 미설치 — OCR 건너뜀")
        return []

    _configure_tesseract(pytesseract)

    try:
        pytesseract.get_tesseract_version()
    except Exception:
        warnings.append("Tesseract 바이너리 없음 — OCR 건너뜀")
        return []

    scale_up = 2
    big = cv2.resize(gray, None, fx=scale_up, fy=scale_up, interpolation=cv2.INTER_CUBIC)
    _, binimg = cv2.threshold(big, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    try:
        cfg = "--psm 11 -c tessedit_char_whitelist=0123456789,"
        data = pytesseract.image_to_data(
            binimg, config=cfg, output_type=pytesseract.Output.DICT
        )
    except Exception as e:
        warnings.append(f"Tesseract 실행 실패: {str(e)[:60]}")
        return []

    tokens = []
    for i in range(len(data.get("text", []))):
        raw = (data["text"][i] or "").strip().replace(",", "")
        if not raw.isdigit():
            continue
        try:
            conf = float(data["conf"][i])
        except (ValueError, TypeError):
            conf = -1.0
        if conf < 30:
            continue
        val = int(raw)
        if not (100 <= val <= 100_000):
            continue
        cx = (data["left"][i] + data["width"][i]  / 2.0) / scale_up
        cy = (data["top"][i]  + data["height"][i] / 2.0) / scale_up
        tokens.append({"value": float(val), "cx": cx, "cy": cy, "conf": conf})

    if not tokens:
        warnings.append("Step3: OCR 치수 숫자 없음")
    else:
        warnings.append(f"Step3: OCR {len(tokens)}개 치수 토큰")
    return tokens


def _resolve_scale(
    pts_px: list,
    dim_tokens: list,
    known_area_m2: Optional[float],
    scale_hint: Optional[float],
    img_w: int,
    warnings: list,
) -> float:
    """스케일(mm/px) 결정 우선순위: scale_hint → OCR 매칭 → known_area → 이미지 폭 가정."""
    if scale_hint and scale_hint > 0:
        warnings.append(f"스케일: 힌트 {scale_hint:.3f} mm/px")
        return scale_hint

    if dim_tokens and len(pts_px) >= 2:
        scale = _match_dims_to_edges(pts_px, dim_tokens, warnings)
        if scale:
            return scale

    if known_area_m2 and known_area_m2 > 0:
        area_px2 = _shoelace_px2(pts_px)
        if area_px2 > 0:
            s = math.sqrt(known_area_m2 * 1e6 / area_px2)
            warnings.append(f"스케일: known_area 역산 {s:.3f} mm/px")
            return s

    s = 10_000.0 / max(img_w, 1)
    warnings.append(f"스케일: 이미지 너비=10,000mm 가정 {s:.3f} mm/px")
    return s


# ─────────────────────────────────────────────────────────────────────────────
# Step 4 — pts_mm 계산 + 좌상단 정규화
# ─────────────────────────────────────────────────────────────────────────────

def _step4_pts_mm(
    pts_px: List[Tuple[int, int]], scale: float
) -> List[Tuple[float, float]]:
    """
    픽셀 좌표 × 스케일 후 min_x/min_y를 빼서 (0,0) 기준으로 정규화.
    DXF/Blender에서 절대 픽셀 위치 의존 제거.
    """
    raw = [(x * scale, y * scale) for x, y in pts_px]
    min_x = min(p[0] for p in raw)
    min_y = min(p[1] for p in raw)
    return [(x - min_x, y - min_y) for x, y in raw]


# ─────────────────────────────────────────────────────────────────────────────
# Step 5 — Vision API: 의미 정보만
# ─────────────────────────────────────────────────────────────────────────────

_VISION_SEMANTIC_PROMPT = """\
이 건축 도면 이미지에서 의미 정보만 읽어 JSON으로 반환하라.
좌표 추정 / 치수 계산 / 픽셀 측정은 절대 하지 마라.

읽어야 할 항목:
• units       : 세대 이름/면적/방 목록 (거실+침실 있고 30m² 이상만)
• common_areas: 공용부 이름 목록 (계단실, 엘리베이터홀, 복도 등)
• common_area_m2: 공용부 면적 합계 (없으면 0)
• confidence  : 0.0~1.0

응답 예시:
{
  "units": [
    {"name": "A", "area_m2": 59.76, "rooms": ["거실", "침실", "침실", "욕실", "주방"]},
    {"name": "B", "area_m2": 65.21, "rooms": ["거실", "침실", "침실", "침실", "욕실", "주방"]},
    {"name": "C", "area_m2": 64.00, "rooms": ["거실", "침실", "침실", "욕실", "주방", "발코니"]}
  ],
  "common_areas": ["계단실", "엘리베이터홀"],
  "common_area_m2": 18.41,
  "confidence": 0.9,
  "warnings": []
}"""


def _step5_vision_semantic(
    image_path: str, warnings: list
) -> Tuple[List[UnitInfo], List[CommonAreaInfo], Optional[dict]]:
    """Vision API 호출 — 방 이름 / 세대 / 공용부만. 실패해도 파이프라인 계속."""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        warnings.append("Step5: ANTHROPIC_API_KEY 없음 — 세대 정보 없이 진행")
        return [], [], None

    try:
        with open(image_path, "rb") as f:
            img_b64 = base64.standard_b64encode(f.read()).decode()

        ext = os.path.splitext(image_path)[1].lower()
        media = {".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                 ".png": "image/png", ".webp": "image/webp"}.get(ext, "image/png")

        resp = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-opus-4-8",
                "max_tokens": 2048,
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "image",
                         "source": {"type": "base64", "media_type": media, "data": img_b64}},
                        {"type": "text", "text": _VISION_SEMANTIC_PROMPT},
                    ],
                }],
            },
            timeout=60.0,
        )

        if resp.status_code != 200:
            warnings.append(f"Step5: Vision API {resp.status_code}")
            return [], [], None

        text = resp.json()["content"][0]["text"].strip()
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            warnings.append("Step5: Vision 응답 JSON 없음")
            return [], [], None

        data = json.loads(m.group())
        units, common_areas = _parse_semantic(data, warnings)
        return units, common_areas, data

    except Exception as e:
        warnings.append(f"Step5: Vision 실패 — {str(e)[:60]}")
        return [], [], None


def _parse_semantic(
    data: dict, warnings: list
) -> Tuple[List[UnitInfo], List[CommonAreaInfo]]:
    units: List[UnitInfo] = []
    for u in data.get("units", []):
        area = float(u.get("area_m2", 0))
        if area < 30:
            warnings.append(f"세대 {u.get('name','')} {area:.1f}m² 제외 (30m² 미만)")
            continue
        rooms = [RoomInfo(name=str(r), polygon_mm=[], has_window=False)
                 for r in u.get("rooms", [])]
        units.append(UnitInfo(
            name=u.get("name", ""),
            outline_mm=[],
            area_m2=area,
            rooms=rooms,
        ))
    if len(units) != 3:
        warnings.append(f"세대 수 {len(units)}개 — 예상 3개(A/B/C)와 다름")

    common_areas = [
        CommonAreaInfo(name=str(c), polygon_mm=[])
        for c in data.get("common_areas", [])
    ]
    return units, common_areas


# ─────────────────────────────────────────────────────────────────────────────
# server.py 직렬화 헬퍼
# ─────────────────────────────────────────────────────────────────────────────

def result_to_dict(result: ExtractionResult) -> dict:
    return {
        "pts_px":          result.pts_px,
        "pts_mm":          result.pts_mm,
        "scale_mm_per_px": result.scale_mm_per_px,
        "area_m2":         result.area_m2,
        "confidence":      result.confidence,
        "ocr_dimensions":  result.ocr_dimensions,
        "warnings":        result.warnings,
        "units": [
            {
                "name":       u.name,
                "area_m2":    u.area_m2,
                "outline_mm": u.outline_mm,
                "rooms": [{"name": r.name, "polygon_mm": r.polygon_mm,
                           "has_window": r.has_window} for r in u.rooms],
            }
            for u in result.units
        ],
        "common_areas": [
            {"name": c.name, "polygon_mm": c.polygon_mm}
            for c in result.common_areas
        ],
        "raw_vision": result.raw_vision,
    }


# ─────────────────────────────────────────────────────────────────────────────
# OpenCV 유틸
# ─────────────────────────────────────────────────────────────────────────────

def _simplify_contour(contour, epsilon_ratio=0.01, area_tol=0.02, max_vertices=24):
    """
    이진 탐색으로 '면적 오차 area_tol 이내 + 최소 꼭짓점' 다각형 탐색.
    오각형/L자형 오목 코너 보존.
    """
    peri = cv2.arcLength(contour, True)
    true_area = cv2.contourArea(contour)
    if peri <= 0 or true_area <= 0:
        return cv2.approxPolyDP(contour, epsilon_ratio * peri, True)

    def fits(eps):
        ap = cv2.approxPolyDP(contour, eps, True)
        if len(ap) < 3:
            return False, ap
        return abs(cv2.contourArea(ap) - true_area) / true_area <= area_tol, ap

    lo, hi = peri * 0.0005, peri * 0.05
    best = cv2.approxPolyDP(contour, lo, True)

    for _ in range(24):
        mid = (lo + hi) / 2.0
        ok, ap = fits(mid)
        if ok:
            best = ap
            lo = mid
        else:
            hi = mid

    if len(best) > max_vertices:
        for k in (0.01, 0.02, 0.03):
            ap = cv2.approxPolyDP(contour, k * peri, True)
            if len(ap) <= max_vertices:
                best = ap
                break

    return best


def _configure_tesseract(pytesseract):
    tess_cmd = os.environ.get("TESSERACT_CMD", "")
    if tess_cmd and os.path.exists(tess_cmd):
        pytesseract.pytesseract.tesseract_cmd = tess_cmd
    else:
        for cand in (
            r"C:\Program Files\Tesseract-OCR\tesseract.exe",
            r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        ):
            if os.path.exists(cand):
                pytesseract.pytesseract.tesseract_cmd = cand
                break

    if not os.environ.get("TESSDATA_PREFIX"):
        local = os.environ.get("LOCALAPPDATA", "")
        user = os.path.join(local, "Tesseract-OCR", "tessdata")
        if local and os.path.isdir(user):
            os.environ["TESSDATA_PREFIX"] = user


def _match_dims_to_edges(
    pts_px: list, tokens: list, warnings: list
) -> Optional[float]:
    """치수 토큰을 외곽 변에 매칭해 mm/px 스케일 추정 (가중 클러스터 방식)."""
    n = len(pts_px)
    edges = []
    for i in range(n):
        ax, ay = pts_px[i]
        bx, by = pts_px[(i + 1) % n]
        length = math.hypot(bx - ax, by - ay)
        if length > 0:
            edges.append((ax, ay, bx, by, length))
    if not edges:
        return None

    xs, ys = [p[0] for p in pts_px], [p[1] for p in pts_px]
    diag = math.hypot(max(xs) - min(xs), max(ys) - min(ys))
    max_dist = diag * 0.10

    cands = []
    for tk in tokens:
        best = None
        for ax, ay, bx, by, length in edges:
            d, t = _pt_seg_dist(tk["cx"], tk["cy"], ax, ay, bx, by)
            if t < 0.15 or t > 0.85:
                continue
            if best is None or d < best[0]:
                best = (d, length)
        if best and best[0] <= max_dist:
            c = tk["value"] / best[1]
            if c > 0:
                cands.append((c, best[1]))

    if not cands:
        return None

    tol = 0.15
    best_c, best_s = None, -1.0
    for ci, _ in cands:
        s = sum(w for cj, w in cands if abs(cj - ci) / ci <= tol)
        if s > best_s:
            best_s, best_c = s, ci

    cluster = [(c, w) for c, w in cands if abs(c - best_c) / best_c <= tol]
    tw = sum(w for _, w in cluster)
    scale = sum(c * w for c, w in cluster) / tw
    warnings.append(f"Step3: 치수 {len(cands)}개 매칭 / 채택 {len(cluster)}개 → {scale:.3f} mm/px")
    return scale


def _pt_seg_dist(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    seg2 = dx * dx + dy * dy
    if seg2 <= 0:
        return math.hypot(px - ax, py - ay), 0.0
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / seg2))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy)), t


def _shoelace_px2(pts) -> float:
    n = len(pts)
    a = 0.0
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        a += x1 * y2 - x2 * y1
    return abs(a) / 2.0


def _shoelace_m2(pts_mm) -> float:
    return _shoelace_px2(pts_mm) / 1e6
