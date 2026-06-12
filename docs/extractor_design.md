# extractor.py 설계 문서

## 핵심 원칙

각 도구는 **잘하는 것만** 담당한다. 역할 혼용 금지.

| 도구 | 역할 |
|------|------|
| OpenCV 형태학 연산 | 국소 잉크 밀도 → 건물 영역 마스크 → 폴리곤 좌표 계산 |
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
[Step 1] 국소 잉크 밀도 → 건물 영역(풋프린트) 마스크
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

## Step 1: 국소 잉크 밀도 → 건물 영역 (2026.06.12 재설계)

### 왜 '두께(opening)'에서 '밀도'로 바꿨나

기존 방식(Ahmed et al. 형태학적 선 굵기 분리)은 **"외벽이 항상 가장 두껍다"**고
가정해 opening으로 얇은 선을 지웠다. 그러나 두 번째 샘플(새도면.png)에서
**상단·우측 외벽이 좌·하단보다 얇아** opening이 통째로 지워버렸고, 외곽 루프가
닫히지 않아 폴리곤이 V스파이크로 붕괴(면적 0 m², 스케일 0.308 mm/px)했다.

opening 커널을 줄여도 안 된다 — 그러면 치수선·용지 테두리까지 살아남아
풋프린트가 마진까지 번진다. 즉 **'두께' 단일 신호로는 얇은 진짜 벽과 얇은
치수선을 분리 불가**(CLAUDE.md 명시). 두 도면 동시 실험으로 확정된 사실이다.

### 밀도 기반 검출

**핵심 성질:** 건물 내부는 벽·칸막이·가구·텍스트로 잉크가 빽빽하고,
치수선·인출선·용지 테두리는 외부에 희박하게 고립된다.

```
grayscale → THRESH_BINARY_INV (검은 선 → 흰 픽셀)
    ↓
국소 잉크 밀도 = boxFilter(ink, box) , box = min(h,w)×0.05 (홀수)
    ↓
density > 0.10 (박스 내 잉크 10% 초과) → 이진 밀도 마스크
    ↓
connectedComponentsWithStats → '가장 큰 연결요소' = 건물 본체
    (치수 숫자 더미 등은 별도 작은 성분으로 자동 배제)
    ↓
면적 < 2% 또는 성분 없음 → None (Step 2 OpenCV fallback)
```

파라미터(`_DENSITY_BOX_FRAC`, `_DENSITY_THRESHOLD`)는 도면 크기에 비례하며
특정 도면 하드코딩이 아니다.

### 알려진 한계 (희박한 큰 방 under-capture)

밀도 임계 이하인 **희박한 큰 방**(가구·텍스트가 적은 넓은 방)은 떨어져 나간다.
도면이미지.png는 하단 방 줄(침실·발코니·욕실)이 잘려 207 → 185 m²(-11%).

회복 시도는 모두 실패로 확인됨:
- **임계값 인하**(0.06/0.08): 좌측 치수 숫자 더미가 최대 CC에 흡수 → 면적 붕괴
- **CC 전 pre-closing**: 하단 방을 붙이는 거리가 곧 치수 더미에 닿는 거리 → 누설

→ 밀도 단일 신호의 본질적 천장. 희박한 방까지 살리려면 별도 알고리즘
(외곽벽 닫힌 루프 + 외부 flood-fill → 내부=건물) 또는 의미 분할 모델 필요.
현재는 "전멸(면적 0)보다 11% 부족"이 낫다는 판단으로 밀도 방식을 채택.

## Step 2: OpenCV 폴리곤

마스크 있을 때 (`_polygon_from_mask`):
1. `MORPH_CLOSE` (≈`min(h,w)//10`px, 1회) — 밀도 마스크의 문 개구부·작은 오목부 메움
2. `findContours(RETR_EXTERNAL)` — 외곽만 추출(내부 방 구멍 자동 무시)
3. **가장 큰 contour** 채택 → 페이지 테두리 잔여선 등 소면적 노이즈 제외
4. `_simplify_contour()` — epsilon 점진 증가, 면적 변화 3% 이내 최소 꼭짓점 + 노치 제거

> flood-fill로 내부를 채우지 않는다. RETR_EXTERNAL이 이미 외곽만 반환하므로
> 방 구멍을 메울 필요가 없고, 오목한 외곽(사선 컷 등)도 그대로 보존된다.

### 폴리곤 단순화 (`_simplify_contour`)

목표: 직사각형 단순화로 사라지기 쉬운 **비대칭 V자 컷** 같은 오목 코너를
보존하면서, 발코니 돌출 같은 작은 노치는 제거한다.

1. **epsilon 점진 증가** — `eps = i·0.001·peri` (i=1..99)로 `approxPolyDP`를
   반복. 원본 대비 **면적 변화 ≤ 3%**(`area_tol`)인 후보 중 **꼭짓점 최소**
   (동률이면 면적 오차 최소)를 채택. 이진 탐색 대신 전수 스캔이라 "면적을
   3% 이상 바꾸는 진짜 코너"는 자동 보존된다(V자 노치는 면적 ~9% 기여 → 생존).
2. **짧은 변 제거** (`_drop_short_edges`) — 둘레 5% 미만(`min_edge_ratio`)인
   변을 양옆 변의 연장 교차점 하나로 대체(`…A-B-C-D… → …A-X-D…`). 교차점이
   평행/과도하게 멀면 두 끝점 중점으로 collapse. 코너는 유지, 노치만 평탄화.
3. **안전망** — 여전히 `max_vertices`(24) 초과면 epsilon을 키워 강제 단순화.

> 검증(도면이미지.png): 5각형 — 상단 수평·우측 수직 + 하단 비대칭 V자.
> 좌측 사선 6.2%(짧음)·우측 사선 20.8%(긺), V자 꼭짓점이 건물 중심보다 왼쪽.
> 하단 욕실/발코니 돌출 노치는 제외됨.

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
| 2026.06.12 | **Step1 밀도 재설계** — 두께 opening → 국소 잉크 밀도 + 최대 연결요소 |
|            | 계기: 새도면.png(불균일 얇은 외벽)에서 두께 방식 전멸(면적 0) |
|            | 새도면.png: 7각형, 25.2 mm/px, 85.2 m² (정상화) |
|            | 도면이미지.png: 5각형, 32.5 mm/px, 185 m² (하단 방 -11%, 알려진 한계) |
