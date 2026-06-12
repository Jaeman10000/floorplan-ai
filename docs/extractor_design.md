# extractor.py 설계 문서

## 핵심 원칙

각 도구는 **잘하는 것만** 담당한다. 역할 혼용 금지.

| 도구 | 역할 |
|------|------|
| OpenCV 형태학 연산 | 선 굵기 분리 → 벽 마스크 → 폴리곤 좌표 계산 |
| Tesseract OCR | 치수 숫자 읽기 → mm/px 스케일 (숫자 읽기) |
| Claude Vision API | 방 이름 / 세대 분류 / 공용부 (의미 이해) |

> **딥러닝 모델(CubiCasa5K/HuggingFace)은 제거됨.** 가중치 다운로드·환경
> 의존성이 크고, 검증된 형태학적 선 굵기 분리(Ahmed et al.)로 대체했다.

## 파이프라인

```
도면 이미지
    ↓
[Step 0] 도면 영역 자동 크롭 (용지 테두리·제목란 제거)
    ↓
[Step 1] 선 굵기 분리 (Ahmed et al.) → 벽 마스크
    실패 시 → Step 2에서 크롭 이미지 직접 사용 (OpenCV fallback)
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

## Step 1: 선 굵기 분리 (Ahmed et al.)

검증된 사실: **외벽/벽체는 치수선·인출선보다 두껍다.** 형태학적 opening으로
얇은 선(치수선 1-2px)을 제거하고 벽 픽셀만 남긴다.

```
grayscale → THRESH_BINARY_INV (검은 선 → 흰 픽셀)
    ↓
커널 k ∈ {5,7,9,…,31} 순회하며 MORPH_OPEN
    ↓
남은 픽셀 비율이 0.5~12%인 가장 작은 k 채택 → 벽 마스크
```

한국 CAD 이중선 도면은 외벽/내벽 모두 1-5px로 비슷해 **완전한 외벽-only 분리는
불가능**하다. 따라서 Step 1은 치수선 제거 수준에서 멈추고, 건물 외곽 형태는
Step 2의 closing + 외곽 contour가 책임진다. 적절한 커널이 없으면 `None`을
반환해 Step 2의 OpenCV fallback 경로로 넘어간다.

## Step 2: OpenCV 폴리곤

마스크 있을 때 (`_polygon_from_mask`):
1. `MORPH_CLOSE` (≈`min(h,w)//10`px, 1회) — 흩어진 벽 선을 건물 blob으로 병합
2. `findContours(RETR_EXTERNAL)` — 외곽만 추출(내부 방 구멍 자동 무시)
3. **가장 큰 contour** 채택 → 페이지 테두리 잔여선 등 소면적 노이즈 제외
4. `_simplify_contour()` — 이진 탐색, 면적 오차 2% 이내 최소 꼭짓점

> flood-fill로 내부를 채우지 않는다. RETR_EXTERNAL이 이미 외곽만 반환하므로
> 방 구멍을 메울 필요가 없고, 오목한 외곽(사선 컷 등)도 그대로 보존된다.

마스크 없을 때 (OpenCV fallback, `_polygon_from_image`):
1. `adaptiveThreshold` → 이진화
2. Morphological close + open
3. `findContours` → 면적 필터

## Step 3: Tesseract OCR 스케일

스케일 결정 우선순위:
1. `scale_hint_mm_per_px` 파라미터 (직접 지정)
2. OCR 치수 토큰을 외곽 변에 매칭 → 가중 클러스터 방식
3. `known_area_m2`로 역산: `scale = sqrt(area_m2 × 1e6 / area_px2)`
4. 이미지 너비 = 10,000mm 가정

### 변-치수 매칭 (`_match_dims_to_edges`)

폴리곤 변 = 외벽 전체 길이 → 그 변의 mm 길이는 **전체 치수**다.
한 변 근처에는 전체 치수(예 13000)와 bay 분할 치수(3000/3300/…)가 함께
배치된다. bay는 변의 부분구간일 뿐이므로 변에 매칭하면 스케일이 틀어진다
(3300/381px = 8.66 ≠ 정답 34.1).

→ 각 변마다 근처(perp < diag×0.25, 중앙부 t∈[0.2,0.8]) 토큰 중
**가장 큰 값**(=전체 치수)만 채택한 뒤, 변 길이로 가중 클러스터링.
도면이미지.png 검증: 13000mm ÷ 381px = **34.12 mm/px**, 면적 207 m²
(세대 189 + 공용부 18 ≈ 일치).

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
| + 선 굵기 분리 마스크 성공 | 0.85 |
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
| 2026.06.12 | **딥러닝 제거** — Step1을 형태학적 선 굵기 분리(Ahmed et al.)로 교체 |
|            | Step2: closing + RETR_EXTERNAL 가장 큰 contour (flood-fill 제거) |
|            | Step3: 변마다 '가장 큰 치수'만 채택해 bay 오매칭 방지 |
|            | 도면이미지.png 검증: 7각형, 34.12 mm/px, 207 m² |
