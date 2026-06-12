# extractor.py 설계 문서

## 핵심 원칙

각 도구는 **잘하는 것만** 담당한다. 역할 혼용 금지.

| 도구 | 역할 |
|------|------|
| CubiCasa5K / HuggingFace | 벽 픽셀 마스크 생성 (의미 이해) |
| OpenCV | 마스크 → 폴리곤 좌표 계산 (수치 계산) |
| Tesseract OCR | 치수 숫자 읽기 → mm/px 스케일 (숫자 읽기) |
| Claude Vision API | 방 이름 / 세대 분류 / 공용부 (의미 이해) |

## 파이프라인

```
도면 이미지
    ↓
[Step 1] 딥러닝 → 벽 픽셀 마스크
    실패 시 → Step 2에서 원본 이미지 직접 사용
    ↓
[Step 2] OpenCV → 마스크(or 원본) → 외곽 폴리곤 pts_px
    ↓
[Step 3] Tesseract OCR → 치수 숫자 → scale_mm_per_px
    실패 시 → known_area_m2 역산 → 이미지 폭 가정
    ↓
[Step 4] pts_px × scale → pts_mm (좌상단 (0,0) 정규화)
    ↓
[Step 5] Vision API → rooms / units / common_areas 의미 정보
    실패 시 → 빈 목록으로 계속
    ↓
ExtractionResult 반환
```

## Step 1: 딥러닝 벽 마스크

### CubiCasa5K (우선 시도)

```bash
pip install floortrans  # 또는 pip install cubicasa5k
```

환경 변수:
- `CUBICASA5K_WEIGHTS` — 로컬 가중치 파일 경로
- `CUBICASA5K_HF_REPO` — HuggingFace 저장소 (기본: `CubiCasa/CubiCasa5K`)

가중치 없으면 `huggingface_hub.hf_hub_download()`로 자동 다운로드.  
출력: 클래스 2(wall) 픽셀을 255로 마스킹한 그레이스케일 이미지.

### HuggingFace SegFormer (차선)

```bash
pip install transformers
```

환경 변수:
- `FLOORPLAN_DL_MODEL` — HuggingFace 모델 ID (미설정 시 이 단계 건너뜀)

```
예: FLOORPLAN_DL_MODEL=nvidia/segformer-b2-finetuned-ade-512-512
```

`id2label`에서 "wall" 포함 클래스 자동 탐색 → 없으면 ADE20K class 0 사용.

## Step 2: OpenCV 폴리곤

마스크 있을 때:
1. `MORPH_CLOSE` (7×7, 4회) — 벽 픽셀 연결
2. Flood-fill 내부 채우기 → solid polygon
3. `findContours(RETR_EXTERNAL)` → 가장 큰 윤곽
4. `_simplify_contour()` — 이진 탐색 면적 오차 2% 이내 최소 꼭짓점

마스크 없을 때 (OpenCV fallback):
1. `adaptiveThreshold` → 이진화
2. Morphological close + open
3. `findContours` → 면적 필터

## Step 3: Tesseract OCR 스케일

스케일 결정 우선순위:
1. `scale_hint_mm_per_px` 파라미터 (직접 지정)
2. OCR 치수 토큰을 외곽 변에 매칭 → 가중 클러스터 방식
3. `known_area_m2`로 역산: `scale = sqrt(area_m2 × 1e6 / area_px2)`
4. 이미지 너비 = 10,000mm 가정

## Step 4: pts_mm 정규화

```python
raw_mm = [(x * scale, y * scale) for x, y in pts_px]
min_x = min(p[0] for p in raw_mm)
min_y = min(p[1] for p in raw_mm)
pts_mm = [(x - min_x, y - min_y) for x, y in raw_mm]
# → P0 근처가 항상 (0,0) 기준
```

DXF / Blender에 넘기기 전 절대 픽셀 위치 의존성 제거.

## Step 5: Vision API 의미 정보

**Vision API에게 시키는 것:**
- 방 이름 읽기 (거실, 침실, 욕실, 주방 등)
- 세대 분류 (A/B/C, 면적 m²)
- 공용부 이름 (계단실, 엘리베이터홀, 복도)

**Vision API에게 절대 시키지 않는 것:**
- 좌표 추정
- 치수선 계산
- 픽셀 측정

응답 JSON 예시:
```json
{
  "units": [
    {"name": "A", "area_m2": 59.76, "rooms": ["거실", "침실", "욕실", "주방"]}
  ],
  "common_areas": ["계단실", "엘리베이터홀"],
  "common_area_m2": 18.41,
  "confidence": 0.9
}
```

## Confidence 산정

| 조건 | confidence |
|------|-----------|
| 기본 (OpenCV fallback) | 0.50 |
| + OCR 치수 있음 | 0.70 |
| + 딥러닝 마스크 성공 | 0.85 |
| + Vision API 성공 | +0.05 (max 0.95) |

## 세대 규칙

- 반드시 3개 (A, B, C)
- 30m² 미만 세대 분류 금지
- 공용부(계단실·엘리베이터·복도)는 common_areas로만

## 변경 이력

| 날짜       | 내용 |
|------------|------|
| 2026.06.10 | OpenCV 픽셀 추출 방식으로 시작 |
| 2026.06.11 | Vision API 통합 (치수 읽기 → 좌표 계산) |
| 2026.06.12 | outline 배열 구조로 재설계 |
| 2026.06.12 | Vision API 픽셀 좌표 직접 추적 방식 |
| 2026.06.12 | **완전 재설계** — 딥러닝+OpenCV+OCR+Vision 역할 분리 |
|            | Step1: CubiCasa5K/HuggingFace 벽 마스크 |
|            | Step2: OpenCV 마스크→폴리곤 |
|            | Step3: Tesseract OCR 스케일 |
|            | Step4: pts_mm 정규화 |
|            | Step5: Vision API 의미 정보만 |
