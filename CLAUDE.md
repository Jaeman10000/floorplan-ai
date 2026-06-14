# floorplan-ai 프로젝트 컨텍스트

## 프로젝트 개요
시공업자용 도면 3D 시각화 + AI 공간 재설계 플랫폼
- 설계사가 보낸 도면(벡터 PDF/DXF) → 정확한 3D → 구조 편집 → AI 배치 조언 → 인테리어 시각화
- 사용자: 시공업자 (땅/주택 매입 후 신축). 설계사에게 도면 파일을 받는 위치
- GitHub: Jaeman10000/floorplan-ai
- Railway: floorplan-ai-production-c244.up.railway.app

## ⚠️ 핵심 전략 — 입력은 벡터 데이터로 받는다

### 절대 원칙
**이미지(JPG/PNG)에서 도면을 인식하려 하지 마라.**
- 이미지 자동 인식은 GPU 수십 대로 며칠 학습하는 전문 연구 분야 (1인 개발 불가)
- 지금까지 시도한 OpenCV/Vision API/CubiCasa5K 전부 실패 — 더 시도하지 말 것

### 올바른 입력
설계사가 보내는 PDF는 대부분 **벡터 PDF** (CAD에서 내보낸 것).
벡터 PDF에는 선/사각형/곡선/글자가 전부 정확한 좌표로 들어있음.
- 실측 확인됨: 역곡동 도면 PDF에 선 326개, 곡선 1449개, 글자 509개, 좌표 포함
- 선 굵기(linewidth)로 외벽/내벽/치수선 구분 가능 (0.84~1.02 = 주요벽체, 0.3 이하 = 보조선)

### 파이프라인 (이미지 인식 완전 제거)
```
벡터 PDF / DXF 업로드
    ↓
[Step 1] pdfplumber로 선·사각형·글자 좌표 추출 (DXF는 ezdxf)
    ↓
[Step 2] 선 굵기로 외벽/내벽 분류 → 벽체 폴리곤 구성
    ↓
[Step 3] 글자 좌표 → 방 이름/치수/면적 매핑
    ↓
[Step 4] PDF 좌표 → 실제 mm 변환 (스케일 1/100)
    ↓
[Step 5] 방 폴리곤 + 외곽 + 방이름 → 구조화 데이터
    ↓
3D 렌더링 (외곽 + 내벽 + 방별 구분)
```

### 입력 형식 처리
- 벡터 PDF: pdfplumber로 직접 파싱 (메인 경로)
- DXF/DWG: ezdxf로 파싱 (있으면 가장 정확)
- 래스터 PDF/이미지: "벡터 원본을 요청하세요" 안내 (자동 인식 시도 안 함)

## 핵심 기능 (우선순위)
1. 벡터 PDF → 정확한 2D 구조 추출 (벽/방/치수/이름)
2. 2D 구조 → 3D 렌더링 (외곽 + 내부 방 구획)
3. 구조 편집 (벽/방 드래그로 이동, 추가, 삭제)
4. AI 배치 조언 (방/화장실/주방 동선 최적화 — Claude의 설계 판단)
5. 인테리어 시각화 (구조 고정, 분위기만 입힘)

## 파일 구조
```
backend/
  server.py          — FastAPI 서버
  pdf_parser.py      — [신규] 벡터 PDF 파싱 (pdfplumber)
  dxf_parser.py      — [신규] DXF 파싱 (ezdxf)
  extractor.py       — [폐기 예정] 이미지 인식 (사용 안 함)
  dxf_builder.py     — 좌표 → DXF 변환
  blender_builder.py — 좌표 → Blender 스크립트
  validator.py       — 설계 규칙 검증
  config.py          — 수치 기준값
docs/
  extractor_design.md — 설계 문서
```

## 도면 해석 도메인 지식 (시공업자 JJ 제공)
- **가구/설비는 내벽이 아니므로 3D에 올리지 않는다 (구조벽만):**
  - 주방 안의 사각형 = 아일랜드 식탁
  - 주방 안의 ㄷ자/ㄱ자 형태 = 싱크대/조리대
  - 현관의 작은 빈 공간 = 신발장
  → "외곽−방(구멍) 압출" 방식은 이들을 자연 제외하지만, 방으로 안 잡힌 닫힌
    사각 가구가 작은 블록으로 3D에 남던 문제가 있었음(식탁·욕조·변기 등).
  → **해결(pdf_parser `_merge_furniture_into_rooms`):** 3D 솔리드로 남는 건
    (건물내부 − 방폴리곤) = 구조벽 + 가구 footprint. 구조벽은 가늘고 길어(선형)
    opening(침식→팽창)에서 사라지고, 가구/설비는 2D 덩어리라 살아남는다. 살아남은
    덩어리(_FURN_BLOB_MM=250mm↑ 굵기, _FURN_MAX_M2=4㎡↓)를 인접 방 폴리곤에 shapely
    union → 그 방이 footprint를 덮어 블록 소멸. 가는 벽은 안 건드려 방 병합·누수
    없음(구조 보존). 검증: 빌라 page3 가구 9개 제거, 외곽 197.77㎡·30방 그대로.
    ⚠️ 한계: 두 방향 모두 250mm↑로 굵은 구조 기둥은 가구로 오인 가능.
- **도면 오타 가능성 → 사용자 수정 UI 필수:**
  - 역곡동빌라 A세대: 안방이 "현관"으로 잘못 표기됨(설계사 오타).
    현관·파우더룸·욕실로 둘러싸인 그 공간이 실제로는 안방.
  - 자동 매칭된 방 이름은 틀릴 수 있으므로, 사용자가 방을 클릭해 이름을
    직접 고치는 기능이 반드시 필요.
- 세대 구분: A/B/C 세대로 나뉨.

## 핵심 규칙
- 이미지 자동 인식 시도 금지 (검증된 실패)
- 노트북에서는 Obsidian MD 수정 안 함
- 작업 완료 후 git push
- 특정 도면에만 맞는 하드코딩 금지

## 개발 환경
| 환경 | 도구 |
|------|------|
| PC (집) | Claude Code + Obsidian + Blender MCP |
| 노트북 (직장) | Claude Code만 |
| 공통 | GitHub 동기화 |

## Git
```bash
git pull origin main   # 시작 전
git add . && git commit -m "내용" && git push origin main  # 완료 후
```

## 실측 발견 (테스트용.pdf — 도움건축사 오피스텔 평면도)
- 건물 외곽: 단일 curve 아님. '가장 큰 검은 curve'는 stroke만 된 **대지경계선**.
  → **채움(fill=True) 검은 벽 strip 수백 개**를 래스터-합집합+외곽 contour로 추출.
  (shapely 합집합은 strip 미세간격 때문에 실패) → 6점, 221 m² 검증 완료.
- 스케일: 페이지=A3 + 'SCALE 1/100' → mm = pt × 25.4/72 × 100 = pt × 35.2778 (정확값).
- ⚠️ **도면 내부 면적/방 라벨이 텍스트가 아니라 벡터 아웃라인(curve).**
  건물영역 char=0. 실제 텍스트(chars)는 제목란뿐 → 글자좌표→방이름 매핑 불가.
  방 이름은 OCR 또는 도면 규약 필요.
- 굵기(linewidth)로는 벽 분류 불가: lw=1.02는 제목란 표, lw=0.84는 해칭 조각.

## 실측 발견 (역곡동빌라.pdf — 도면마다 벽 표현이 다름!)
- **벽을 채움이 아니라 얇은 선(stroke)으로 그림** → 테스트용의 fill 방식이 전멸
  (외곽 4.49 m²). 도면 제작 방식이 회사·도면마다 다르다는 핵심 교훈.
- **방 이름이 진짜 텍스트**(거실/침실/욕실/주방/현관/발코니/파우더룸 등)로 들어
  있어 좌표와 함께 추출됨(지상2~3층 28개). 테스트용은 벡터 곡선이라 불가였음.
- 6페이지(지하1/지상1/지상2~3/4/5층). 페이지마다 따로 파싱.
- → pdf_parser가 **두 벽 표현 모두 분기 처리**: fills 외곽 면적<30m²이면 stroke
  경로(검은 잉크 래스터→자유공간 연결요소 중 경계 안 닿는 것=방, 방 합집합
  최대 blob=건물로 가짜 치수방 제거). 빌라 page3: 30개 방, 외곽 197.8 m².

### linewidth로 구조벽 분류 시도 — **실패(데이터로 확인, 2026-06-13)**
JJ 가설 "굵은 선=벽, 얇은 선=창문/문/싱크대/신발장"을 page3 전체 선/곡선을
linewidth별로 색칠해 검증(backend/linewidth_check.png). lw값은 0.0/0.12/0.24/
0.3/0.84/1.02 6종.
- **lw≥0.84(굵음)는 벽이 아니다**: lw=1.02=대지경계선·제목란 테두리,
  lw=0.84(212개)=치수 숫자/치수선. → "굵은 선=벽" 가설은 이 도면에서 거짓.
- **실제 벽 = 검은 fill 곡선 + lw=0.3 stroke**. 검증: 벽마스크를
  · fills만 → 방 0개(채움 strip이 문/틈으로 끊겨 방을 못 가둠)
  · fills+lw0.3 → 32방·208m²(○)  · fills+lw0.12 → 3방  · fills+lw≥0.84 → 0방.
  즉 굵기 임계는 0.3이고, 그것도 "굵어서"가 아니라 그 stroke들이 방 경계선이라서.
- **lw=0 판정**: lw=0 **fill**(1039개)=벽. lw=0 **stroke**(~1150개)=가구/문/창/
  글자 글리프 등 비구조. → lw=0은 fill/stroke로 갈려 그 자체로는 모호.
- ❌ **그러나 linewidth 필터는 가구 제거에 무력**: 싱크대 ㄷ자=lw0×16 + **lw0.3×12**
  로 벽과 같은 lw0.3을 공유. fills+lw0.3로 걸러도 3D블록 안 줄고(2→3개) 벽 잉크만
  깎임(33→24m²). → **가구/벽은 굵기로 못 가른다. 형태(가는 선형 벽 vs 2D 덩어리
  가구)로 가르는 게 맞다** = `_merge_furniture_into_rooms`(어제 구현)가 올바른 방향.
- ⚠️ 위 "벽=얇은 stroke" 서술 보정: 빌라 벽은 fill+lw0.3 혼합이며, fill-only
  외곽이 4.49m²로 죽은 건 fill strip이 문틈으로 안 이어져 최대 연결요소가
  작아졌기 때문(굵기 문제 아님).

## ⚠️ 핵심 로드맵 — 구조 변경은 "편집 도구 먼저, AI 자동화 나중"

### 최종 목적 (JJ)
세대별로 건물 모양에 맞는 최적 내부구조를 AI가 제안 + 인테리어까지 입혀
실제 공사 전 프리뷰. → 단순 "기존 도면 3D화"가 아니라 "더 나은 구조 제안·시각화"가 핵심.

### 왜 "조언→바로 3D 적용" 버튼을 지금 만들면 안 되는가
AI는 "거실을 넓혀라" 같은 **판단**은 잘하지만, "벽을 (3200,1500)→(3200,4800)
으로 옮겨라" 같은 **정밀 좌표 생성은 약함**(프로젝트 초반 도면인식 실패와 같은 이유).
조언을 그대로 3D로 자동 변환하면 벽 어긋남·방 겹침 등 엉망 결과가 나옴.

### 올바른 순서 (이 순서 지킬 것)
1. **구조 편집 도구 먼저** — JJ가 3D에서 직접 벽 이동/방 분할·병합. 시공업자라
   정확한 치수 감각 있음. JJ가 처음부터 원한 "내가 직접 바꾸며 3D로 본다"와 일치.
2. **AI 조언을 편집 도구와 연결** — 조언 보며 JJ가 그 자리에서 구조 변경.
   조언 텍스트의 방 이름 클릭 → 3D 해당 방 하이라이트(기존 클릭-하이라이트 재활용).
3. **AI 초안 자동 생성은 마지막** — AI가 구조 초안을 그리면 JJ가 편집도구로 보정
   하는 반자동. 또는 "벽은 격자 위에만, 방은 직사각형만" 제약으로 AI 자유도 제한.

→ 1번(편집 도구) 없이 3번(자동 적용)부터 만들면 결과를 손볼 수가 없어 실패함.

## ⚠️ 설계 방향 전환 — "벽 이동" 폐기 → "내부 설계 모드 (빈 외곽에 새로 그리기)"

### 왜 벽 이동을 폐기하나
벽 이동(기존 내벽을 조금씩 미는 방식)은 원본 구조에 갇혀 JJ가 원하는 새 배치를
못 만든다. 벽이 8개 잡히든 전부 잡히든, 미는 것만으론 완전히 새 구조 설계 불가.
→ JJ 결정: 기존 내벽을 미는 게 아니라, **싹 지우고 빈 외곽에 새로 그린다.**

### 내부 설계 모드 (새 방향, JJ 설계)
세대 묶기로 한 세대 지정 → "내부 설계 모드" 진입 →
**그 세대의 내벽이 전부 삭제되고 외곽 벽만 남은 빈 공간**이 됨 (빈 도화지).
여기서 두 갈래:
1. **JJ가 직접 그리기** (먼저 구현) — 페인트 앱처럼 마우스로 선을 그어 벽 생성.
   텍스트로 "3m 수직벽"식 입력은 의도를 정확히 못 살리므로 안 씀. 손으로 긋는다.
2. **AI가 최적 구조 생성** (나중 구현) — 빈 외곽을 보고 AI가 방 배치를 그림.
   내벽이 없어 AI가 구조를 짜기 쉬움. 단 AI는 정밀좌표 약하므로 초안만, JJ가 편집.

### 핵심 요구 (JJ + Claude 보완)
- 마우스로 자유롭게 긋되 **스냅 보조**: 거의 수직/수평이면 직각으로 정렬,
  기존 벽 끝점 근처면 거기 붙임 (벽이 안 만나면 방이 안 닫히므로 필수).
- **길이 실시간 표시**: 그으면서 "2.4m" 보이게 → 손의 자유 + 치수 정확.
- **닫힌 공간 자동 방 인식**: 벽으로 닫힌 영역이 생기면 새 방으로 인식·면적계산·색칠.
  (이게 그리기의 진짜 완성. 안 그러면 선만 그어진 것)
- **undo**: 잘못 그은 것 되돌리기.
- AI 생성 결과도 JJ가 바로 편집 가능해야 함.
- **두께**: v1은 선만 그어 공간만 나눔(두께 무시). 나중에 입힐 때 외벽보다 얇게.

### 구현 순서 (한 번에 다 만들지 말 것)
1. **직접 그리기 도구 먼저**: 내벽 삭제 → 빈 외곽 → 손으로 벽 긋기 + 스냅 +
   길이표시 + 닫힌공간 방인식 + undo. (= 이번 단계)
2. AI 자동 구조 생성은 그리기 도구가 탄탄해진 다음 얹음 (같은 벽 생성 메커니즘 재사용).

### 폐기/대체
- 기존 "벽 이동(editMode, buildWallModel, 좌표 클러스터 페어링, 벽 드래그)"은
  이 방향으로 대체됨. 코드 재활용 가능한 부분(세대 스코핑, undo 스냅샷 패턴,
  orbit 충돌 해결책=wallDragging 가드)은 살리되, "기존 벽을 민다"는 개념은 버림.

## 다음 작업
- [x] pdf_parser.py: 벡터 PDF에서 선/curve/글자 좌표 추출 + 외곽 + 스케일
- [x] 방 폴리곤 추출 (fill 벽 도면) — 테스트용.pdf 20개 (rooms_check.py)
      ⚠️ 문 개구부에서 벽이 끊기면 인접 공간이 한 방으로 병합됨(v1 한계)
- [x] stroke 벽 도면 대응 + 방 이름 텍스트 매칭 — 빌라 page3 30개, 23개 이름매칭
      (villa_rooms_check.py). 테스트용 fill 경로는 무변경 보존.
- [x] 3D 뷰어 연결 — server.py `POST /api/parse-pdf`(PDF+page_index → 파싱 dict),
      index.html PDF 업로드→파싱→renderParsed(바닥+외벽+내벽+방색+이름라벨).
      벽=외곽−방(구멍) 압출(반투명)이라 방 안 가구는 자연 제외. theta=0,
      Vector3(x/1000,0,y/1000) 유지. 빌라 page3 헤드리스 Chrome 렌더 검증.
- [x] 방 이름 사용자 직접 수정 UI (자동 매칭 오류·도면 오타 대응 — 예: 안방이
      "현관"으로 오기된 케이스) — index.html: 3D 방 타일 클릭(raycaster)→선택,
      금색 케이지 하이라이트(바닥링+상단링2.8m+수직기둥, depthTest:false+
      renderOrder 999로 반투명 벽 너머도 보임)+바닥 금색, 사이드바 드롭다운
      (거실/안방/침실/욕실/주방/현관/발코니/파우더룸/다용도실/테라스)+직접입력→
      라벨 스프라이트 즉시 교체. ⚠️ 함정: 하이라이트를 y=0.03 바닥 1px 링으로
      두면 2.8m 벽 우물에 묻혀 안 보임(로직은 정상인데 시각만 실패) — 클릭/
      raycaster/scene반영은 playwright로 직접 검증 후 가시성만 고쳤음.
- [x] AI 배치 조언 — server.py `POST /api/ai-advice`(question+rooms+외곽 →
      _build_advice_context로 면적·세대수추정·방별 이름/면적/도면위치(상하좌우)
      정리 → Claude claude-sonnet-4-6 호출). 시스템프롬프트: 시공업자 관점
      동선·채광·배관집중 조언, **방위 미상 먼저 경고**(남향 질문 대응), 구조벽
      이동 비용/내력벽 현실 지적, 방이름 오타 가능성 언급. index.html 사이드바
      "🧠 AI 배치 조언" textarea+예시칩3+응답박스. 빌라 page3 검증: AI가 현관
      10.5㎡ 이상(오타 가능성)·욕실6개 배관집중·세대4개 LDK분리 정확 분석.
      → 후속 개선: 응답이 위치코드(상앙/중우)·장문이라 사용자가 3D에서 방을 못
      찾던 문제 → 시스템프롬프트를 "방이름(면적)으로 식별, 핵심 3~4개, 방이름→
      어떻게→왜" 포맷으로, max_tokens 2048→1024. 사이드바 "도면 위쪽 방위"
      드롭다운(모름/북/남/동/서)→building_orientation 전송, 값 있으면 채광 조언·
      없으면 1줄 경고. _room_position "상앙"→"상단 중앙".
- [x] 세대 묶기(그룹핑) — 자동분류 부정확 → JJ가 직접 방을 세대(A/B/C/공용)로
      묶음. 모든 세대별 AI제안·인테리어의 기반 데이터. index.html: ① 사이드바
      "🏢 세대 묶기" 섹션(모드 토글 버튼+세대버튼4개+칸수 카운트). ② 두 모드 분기
      (충돌방지): 일반모드=방클릭→금색케이지+이름수정패널+그 방 세대 전체 외곽
      하이라이트, 세대묶기모드=세대선택후 방클릭→지정/같은세대재클릭=해제. 클릭
      핸들러 한곳(raycaster mouseup)에서 `unitMode` 분기. ③ 색: UNIT_COLORS
      A파랑3b82f6/B초록22c55e/C주황f97316/공용회색9ca3af, 지정방은 타일=세대색·
      opacity0.72, 미지정 팔레트0.55. 타일 origColor를 세대색으로 갱신(금색해제시
      세대색 복원). ④ 세대 외곽 하이라이트=같은세대 방들 바닥+상단링 LineSegments
      depthTest:false·renderOrder998(금색999 아래). ⑤ 데이터: rooms[i].unit 저장,
      AI페이로드에도 unit 동봉(세대별 제안 대비). 검증(_verify_units.py, 빌라
      page3 30방): 실제 마우스클릭이 unitMode분기→assignUnit 타고 A3/B2/공용1 지정·
      타일색·카운트·토글해제·일반모드 외곽하이라이트(400세그)·이름패널공존 전부 통과.
- [~] **구조 편집 도구 (로드맵 1단계, 최우선)** — 3D에서 JJ가 직접: 벽 이동/추가/
      삭제, 방 분할·병합. ✅ 벽 이동(v1) 완료(아래 상세). 남음: 벽 추가·삭제, 방 분할·병합.
- [ ] AI 조언 ↔ 편집도구 연결 (로드맵 2단계): 조언 텍스트 방이름 클릭→3D 하이라이트
- [ ] 세대별 AI 구조 제안 (로드맵 3단계, 반자동): unit별 방그룹+건물모양→구조초안
- [ ] 인테리어 시각화 (구조 고정, 분위기만): 재질·조명·색감
- [ ] 방 병합 개선: 문 개구부 처리(문 심볼 위치로 가상벽 닫기 등)
- [ ] 벡터 라벨 도면(테스트용) 방 이름: OCR 경로
- [ ] dxf_parser.py (ezdxf) — DXF 입력 경로
- [x] 구조 편집 UI — 1단계 "벽 이동"(v1). ⚠️핵심 실측: 방 폴리곤은 래스터 외곽선
      추출이라 **정점 5~45개(평균13.3)·축정렬 57%뿐(43%는 계단 노이즈)**, 인접
      방끼리 공유 edge 없음 → "edge 클릭해 옮기기"는 노이즈에 무너짐. **하지만
      정점 X·Y좌표를 클러스터(tol80mm)하면 실벽 라인 ~20~30개가 깨끗이 떨어짐**
      (빌라page3 X클러스터 정점4+ 23개). → **좌표 클러스터링 방식(Path A, 프런트
      전용)** 채택. 벽=평행한 두 좌표선 쌍(두 방의 면+사이 벽두께). 이동=그 선들에
      속한 모든 방 정점을 같은 d만큼 평행이동 → 양쪽 방 폴리곤이 자동 변형(노이즈
      정점도 같이 이동해 모양 보존). index.html: ① editMode 토글(기존 unitMode 패턴,
      클릭핸들러 editMode>unitMode>select 분기로 충돌방지). ② buildWallModel:
      clusterCoord+pairWalls(간격 90~350mm, 수직겹침≥700mm, 다른 방 소속, 멤버≥3).
      ③ **편집 가능한 페어 내벽에만 주황 BoxPicker** 띄워 만질 수 있는 것만 또렷
      (비축정렬·외벽은 피커 없음=v1 미지원 안내). ④ 선택=주황 EdgesGeometry 케이지
      (depthTest:false). ⑤ 드래그: capture단계 mousedown으로 orbit 가로채고
      (stopImmediatePropagation), 포인터를 y=0 평면에 교차(groundPoint)→법선성분 d만
      취해 평행이동. window mousemove/up은 싱글톤(캔버스 재생성 중복적용 방지).
      applyWallDelta가 정점이동+면적<1㎡ 클램프+타일/링/벽압출 라이브재생성. commit시
      라벨·면적 갱신 후 모델/피커 재구성. 좌표계 유지(mm, Vector3(x/1000,0,y/1000),
      theta0). 프런트 parsedData에만 반영(백엔드저장·AI재조언 다음단계). 검증
      (_verify_walls.py, 빌라page3): ①헤드리스 — 수직내벽(멤버25,접한방7)에 +300mm→
      방면적 일부+(0.47/0.80/0.36)/일부−(0.34/0.76/0.24/0.46)·외곽197.77㎡불변·NaN없음.
      ②실제드래그 — 피커클릭선택→마우스드래그→방#1 18.47→19.02㎡ 즉시변화, 에러0.
      ⚠️한계: 긴 그리드선 벽은 접한 방 다수가 함께 이동(정상), 외곽 닿는 단일면벽·
      대각벽·벽추가/삭제·undo·스냅 미지원. ↔ Path B(백엔드 벽그래프)는 정확도 필요시.
- [x] 구조 편집 v1.1 — **세대 스코핑 + 드래그 버그 수정**(실사용 실패 2건 대응).
      문제1: 30방 전체 클러스터링→다른 세대 벽이 같은 좌표선에 우연히 걸려 유령 벽이
      도면 전체에 깔림(세대 3개 섞임). 문제2: 드래그해도 벽이 안 움직임.
      ★드래그 원인(중요 교훈): orbit과 벽드래그 mousedown이 같은 canvas에 등록 →
      **타깃 엘리먼트에선 capture 플래그 무시하고 "등록 순서"대로 실행됨**(capture
      우선은 조상 엘리먼트일 때만). attachOrbit이 먼저 등록돼 orbit mousedown이
      먼저 돌아 isDragging=true → 내 stopImmediatePropagation은 이미 늦음 → mousemove에서
      카메라가 회전하며 벽 이동을 덮음(지난 헤드리스 검증은 면적만 봐서 카메라
      회전을 못 잡음). **수정: attachOrbit mousemove 맨앞 `if(wallDragging)return`**.
      세대 스코핑: buildWallModel(unit)이 그 세대 방 정점만 클러스터→세대 내벽만
      피커, 세대경계벽(한쪽 면만 잡힘)·외벽·타세대 자동 잠금. 게이팅: 세대 미지정
      시 진입 차단+안내. UI: 편집섹션에 "편집할 세대" eu-btn(지정 세대만 활성)+자동
      선택. 드래그 UX: 피커 누르는 즉시 선택+드래그(한 동작), 실제 이동시만 commit.
      검증(_verify_walls.py): ①게이팅 차단 ②A세대 지정후 피커 5개 전부 A방 소속
      (전체도면X) ③실제 드래그 방#1 18.47→18.96㎡ 변화 **+ 카메라 이동량 0.00000**
      (orbit 충돌 해결 직접 확인), 에러0.
- [x] 구조 편집 v1.2 — 페어링 완화 + ★피커 미소멸 + Undo(실사용 실패 3건).
      ①페어링 너무 엄격(A세대 5개만): 실벽인데 B면 멤버2개·겹침<700mm라 탈락.
      A세대 정점 클러스터 실측으로 완화 — THICK 90~350→70~400, OVERLAP 700→300,
      MIN_MEMB 3→2 → A세대 8개로 증가(세대 스코핑이라 완화해도 유령 안 생김).
      ②★피커 사라짐(핵심): commitWallMove가 매 mouseup마다 buildWallModel+
      createWallPickers로 모델·피커 통째 재구성 → destroyWallPickers가 다 dispose,
      재클러스터링이 그 벽을 페어에서 떨궈 피커 소멸. member는 (방idx·정점idx)
      참조라 좌표만 바뀌어도 유효 → **commit에서 재구성 제거, 세션 내내 모델·피커
      유지**(applyWallDelta가 피커 position만 따라 옮김 → 데이터↔피커 항상 동기).
      선택도 유지해 이어 드래그 가능. ③Undo: snapshotRooms(전 방 폴리곤 깊은복사),
      드래그 시작 시 스냅샷→실제 이동시 editHistory push(최대50), 되돌리기 버튼/
      Ctrl+Z로 pop+restore, 편집 시작시 editOriginal로 "전체 초기화". 재구성은
      undo/reset/세대전환/진입 때만(드래그 중 아님). 검증: 완화 8개·반복 드래그
      (같은벽3회+다른벽)에도 피커 8개 유지·전부 씬내, 단일/3단 undo·Ctrl+Z·전체
      초기화 모두 면적 원복(18.47), 에러0.
- [~] **벽 이동 폐기 → 내부 설계 모드 v1**(빈 외곽에 손으로 새로 그리기, 로드맵 재설정).
      JJ 결정: 기존 내벽을 미는 건 원본 구조에 갇혀 새 배치를 못 만듦 → **세대 묶고
      "내부 설계 모드" 진입하면 그 세대 내벽이 전부 사라지고 빈 외곽만 남아 거기 새로
      그린다.** (벽이동 editMode 코드는 회귀위험0이라 제거 안 하고 그대로 둠.)
      **세 핵심:**
      ①(a)빈 외곽 = **백엔드 shapely** `POST /api/unit-boundary`: 세대 방폴리곤들
        unary_union→buffer(+160)(−160) 모폴로지 close(내벽 슬릿·계단노이즈 메움)→
        simplify(120)→exterior. (방폴리곤은 래스터추적 노이즈+공유edge없음이라 JS union
        대신 shapely가 정답. 빌라 A세대 외곽 43정점·75.6㎡.) 진입 시 원본
        타일/링/라벨/wallExtrudeMesh `.visible=false`(원본 데이터 보존, 종료 시 복원).
      ②(b)드래그 드로우+스냅: 좌클릭 드래그=벽1개(우클릭=카메라). snapPoint 우선순위
        **정점>선분>직각**(연결이 직각보다 우선—안 만나면 방이 안 닫힘). 정점/선분
        SNAP=350mm, 직각 ±7°. 미리보기 선+길이 스프라이트(makeLabel 재활용) 실시간.
      ③(c)닫힌 면=방: **평면그래프 면 순회**(프런트 ~120줄). segments=외곽edge+designWalls
        →노드병합(50mm)→선분X교차분할+**노드-온-세그먼트 분할(T자 접합)**→half-edge에서
        도착노드의 "가장 시계방향 다음 edge" 따라 최소면 수집→signed area로 외부면/외곽전체
        면(>97%) 제거→면적<0.5㎡ 제거. **댕글링 벽(한쪽만 연결)은 면 안 생김**(왕복 흡수)
        =요구 "벽이 정확히 만나야 방이 닫힘"과 일치. 면→ROOM_PALETTE 타일+면적라벨.
      벽=얇은 수직 평면(두께0). undo=designWalls 깊은복사 스택+Ctrl+Z, "빈 외곽으로" 초기화.
      orbit 충돌: 좌버튼 designMode면 orbit mousedown 무시+mousemove에 `designDrawing` 가드.
      검증(_verify_design.py, 빌라 A세대, **실제 마우스 드래그**): 진입(43정점·원본숨김)·
      스냅(선분0mm·직각dy0)·★실드래그 수평벽→**닫힌 방2개(33.79+41.81㎡)·길이라벨·
      카메라이동0**·★T자 수직벽→**방3개(33.62+11.89+29.92)**·우드래그=카메라0.6·undo·
      초기화·종료복원 전부 통과, 에러0.
      ⚠️ v1 범위=그리기 도구까지. 그린 결과를 parsedData/백엔드 반영(적용 버튼)은 다음 증분
      (지금은 종료 시 원본 복귀). 클릭-클릭 점찍기는 v1.1.
- [x] **내부 설계 v1.1 — 저장 & 이어 편집 + 벽 이동(editMode) 전면 삭제**.
      ①저장: 설계 모드 "💾 저장" → designWalls+방을 전역 `unitDesigns[세대]`(parsedData와
        분리, 재파싱 때만 리셋)에 보관. 종료해도 유지.
      ②To-Be 패널 렌더(renderToBe): unitDesigns의 모든 세대를 한 씬에 자기 mm좌표로 그림
        (A자리·B자리 그대로=위치가 곧 합쳐짐). 빈바닥+외곽벽+그린벽(수직면)+방타일+면적/
        세대 라벨. 카메라=전 저장 외곽 합집합 bbox. 원본 As-Is는 종료 시 복원(가시성 토글,
        parsedData 불변).
      ③재진입 로드(enterDesignMode 분기): `unitDesigns[u]` 있으면 그 외곽·벽 깊은복사로
        불러와 recompute→이어 그림(백엔드 재요청 X). 없으면 빈 외곽 fetch.
      ④두 초기화: "그리던 것 지우기"(clearDesignWalls)=현재 작업만 빈 외곽, 저장본·To-Be
        불변 / "이 세대 원본으로"(revertDesignToOriginal)=`delete unitDesigns[u]`+작업 비움
        +renderToBe(이 세대 제거).
      ⑤beforeunload: unitDesigns 비어있지 않거나 그리던 중이면 새로고침/이탈 경고(작업 보호).
      ⑥★벽 이동(editMode) 전면 삭제: HTML 섹션·CSS(#btn-edit-mode/#edit-info)·JS
        (setEditMode/buildWallModel/pairWalls/clusterCoord/createWallPickers/selectWall/
        applyWallDelta/setTileGeometry/setRingGeometry/rebuildWallExtrude/refreshLabel/
        showEditAreas/commitWallMove/snapshotRooms/restoreSnapshot/undoWallMove/resetAllEdits/
        updateEditActions/resetEditUI/buildEditUnitButtons/selectEditUnit/rayHits + 벽드래그
        핸들러 + editMode 전역/상수) 제거. ⚠️**공유 함수는 보존**: groundPoint/shoelaceArea/
        makeLabel/polyCentroid/_groundPlane/roomRenderer/roomRings/wallExtrudeMesh/selectRoom/
        applyRoomBaseColor + orbit 가드 `designDrawing`. `.eu-btn`/`.edit-hint` CSS는 설계
        모드가 재사용하므로 유지.
      검증(_verify_design_v11.py, 빌라 A·B세대, **실제 마우스 드래그**): editMode 흔적0+공유함수
        보존·A 그려저장(To-Be 메시52)·B 저장(키[A,B]·메시65)·종료(As-Is복원+To-Be유지)·
        재진입 A저장본 로드(벽1방2)→이어그려 재저장(벽2)·"그리던것지우기"(작업0·저장본2 유지)·
        "원본으로"(unitDesigns.A 삭제·키[B])·에러0.
      ⚠️ 한계: unitDesigns는 페이지 메모리뿐(새로고침 시 소멸—beforeunload로 경고). 손그림은
        휘발성으로 두고, 영속화 대신 **결과를 파일로 내보내기**로 방향 전환(아래).
- [x] **내보내기(3D PNG / 2D 평면도 PDF) — 현재 세대 결과를 파일로**.
      ★방향 전환: "unitDesigns 영속화 / JSON 저장 라이브러리(designs 폴더·저장·목록·불러오기·
      비교)"는 **폐기**(구현 안 함). 손그림은 휘발성 유지, 대신 그 세대 결과를 두 형식으로 다운로드.
      ①3D PNG(프런트 `capture3dPng`): 설계 모드 메인 패널(roomScene+designGroup=현재 세대만)을
        캡처. ★함정: 모든 WebGLRenderer가 preserveDrawingBuffer 꺼져 있어 toDataURL이 빈 이미지
        위험 → renderParsed asis 렌더러에 `preserveDrawingBuffer:true` 한 플래그 + 캡처 직전
        같은 턴에서 render→toDataURL(2중 안전). dataURL→Blob 다운로드.
      ②2D 평면도 PDF(백엔드 `POST /api/export-plan-pdf`, reportlab): 현재 세대 외곽+내벽+방을
        보내면 A4 세로에 bbox fit(Y뒤집기=도면상단이 PDF위)으로 그려 PDF 바이트 반환. 그리는 것=
        외곽선(굵게)+내벽(각 선분 길이라벨 "2.40 m")+방 면적라벨("12.3 m²")+**전체 바운딩 치수만**
        (가로×세로)+스케일바+SCALE 근사(fit배율 역산). ★외곽 변별 치수는 **안 넣음**(래스터추적
        43정점 노이즈선까지 치수 박혀 지저분) — 깨끗이 스냅된 내벽 길이만 의미. 한글=reportlab
        내장 CID폰트 `HYSMyeongJo-Medium`(폰트파일 동봉 불필요), 면적단위 ㎡(CJK합자) 대신
        m²(U+00B2)로 글리프 누락 회피. 입력검증 boundary≥3점. requirements에 reportlab>=4.0.
      ③UI: 설계 액션영역에 버튼 2개(🖼️ 3D PNG/📄 2D PDF), 그린 벽 있을 때만 활성
        (updateDesignActions). 파일명 `{세대}_{YYYYMMDD_HHMM}`.
      기존 설계 모드(designDrawing 가드·rAF루프·renderToBe·beforeunload) 무변경.
      검증: ①백엔드 단독 — 더미좌표→%PDF헤더·pdfplumber 1페이지·텍스트추출. ②playwright
        (_verify_export.py, 빌라 page3 A세대) — 벽1방2·버튼활성·toDataURL 63710·
        PNG파일 색분산99.2(비단색)·PDF %PDF 1페이지·파일명규칙·에러0. ③PDF를 pypdfium2로
        이미지렌더해 육안확인(외곽·내벽6.91m·방면적·전체가로10.20m/세로16.25m·스케일바 깨끗).
- [x] **벽 그리기 드래그→클릭-클릭 전환 + 직각 스냅 기본(±20°)+Shift 자유각**.
      ★문제: 누른 채 드래그→릴리스 방식은 떼는 순간 손떨림이 끝점에 박혀 벽이 틀어짐.
      → **클릭-클릭 상태머신**: idle─좌클릭▶시작점확정─이동▶hover미리보기─좌클릭▶끝점확정─▶idle.
      ①클릭/드래그 구분: mousedown은 점 안 찍고 화면좌표(`_drawDownXY`)만 기록, mouseup에서
        이동량 ≤`_CLICK_MAX_PX`(5px)면 "진짜 클릭"만 점을 찍음(>5px=드래그=무시). 끝점은 클릭
        위치(=hover 미리보기와 동일)라 손떨림 제거. ②직각 기본: `_ANGLE_DEG` 7→**20**, 시작점
        기준 0/90 ±20°면 수평/수직 자동 정렬. `snapPoint(raw,from,allowOrtho)` 3번째 인자 추가,
        **Shift 누르면 allowOrtho=false=자유각**(미리보기·커밋 둘 다 `!e.shiftKey`). 정점>선분>직각
        우선순위 유지(벽 만나야 방 닫힘). ③취소: **Esc** 또는 **우클릭(≤5px)** → `cancelDraw`
        (시작점·미리보기만 버림, 저장벽 불변). 우드래그(>5px)는 카메라(기존 contextmenu
        preventDefault로 브라우저 메뉴 억제). ④오클릭 보호: 둘째 클릭 len<`_DRAW_MIN_MM`(100mm)
        이면 커밋 안 하고 **진행 유지**(시작점 안 날림). ⑤orbit 충돌 해결: 클릭-클릭이라 두 클릭
        사이 좌버튼 떼어진 상태(isDragging=false)→hover로 카메라 안 움직임. orbit mousemove의
        `if(designDrawing)return` 가드 **제거**(진행중 우드래그 카메라 허용). 확정 시 기존
        undo(designHistory)·recomputeDesignRooms·렌더 경로 무변경. design-hint 문구 갱신.
      검증(_verify_clickdraw.py, 빌라 page3 A세대, **실제 클릭-클릭**): 직각 ortho_y==from_y·
        Shift free_y==raw_y / 클릭-클릭 벽1방2·좌클릭중 카메라이동0 / undo벽0 / Esc취소 /
        ★첫점 후 우드래그 카메라이동1.44>0·시작점고정→이어 확정 / 짧은클릭 진행유지(시작점보존) /
        에러0. + _verify_export.py도 클릭-클릭 헬퍼로 갱신(내보내기 회귀 없음).
      ⚠️ 과거 `_verify_design.py`·`_verify_design_v11.py`는 드래그(mouse.down→move→up) 기반이라
        **stale**(클릭-클릭에선 >5px 드래그=무시되어 깨짐). 삭제 안 하고 보존만. 신규 검증은
        `_verify_clickdraw.py`/`_verify_export.py` 사용.
- [x] **AI 구조 초안 생성 — 빈 외곽 + (JJ 입력) 방/화장실 개수 → 격자·직사각형 내벽 초안**.
      백엔드 `POST /api/generate-layout`(boundary_mm, unit, rooms, baths): ai-advice의 Anthropic
      호출 패턴 재사용(claude-sonnet-4-6, max_tokens 2048). 시스템프롬프트 제약 — ①벽은 수평/수직
      축정렬 직선 ②좌표 100mm 격자 ③외곽 안에만 ④끝점 같은 격자점에서 만남 ⑤요청 개수 준수(임의
      변경 금지) ⑥JSON `{"walls":[{"a":[x,y],"b":[x,y]}]}`만(산문 금지).
      ★**방/화장실 개수는 AI가 정하지 않고 JJ가 입력** — 비우면 백엔드 `_default_room_counts`가
        전용면적 기반 기본값(~50㎡↓ 방2, 60~85㎡ 방3, 그 이상 방4 / 방3↑이면 화장실2). 프런트
        gen-rooms·gen-baths 숫자입력(placeholder="자동"), 빈칸이면 미전송→백엔드 기본값.
      후처리(결정적 안전망, AI 정밀좌표 약점 흡수): `_parse_walls_json`(펜스 제거+parse, 실패 시
        첫 `{...}` 재시도, 깨지면 [])→100mm 격자 스냅→degenerate(<100mm) 제거→dedup(방향무관)→
        **shapely로 외곽 buffer(50) 클립**(LineString∩Polygon, MultiLineString 조각화, 밖 구간
        잘라냄, <100mm 조각 버림)→재dedup. **walls 0개·파싱실패면 422**(designWalls 덮어쓰지
        않음, 기존 작업 보존).
      프런트 `generateLayout()`: 기존 designWalls 있으면 confirm 후 designHistory에 깊은복사 push→
        designWalls=aiWalls(통째 대체)→recompute/render/updateDesignActions. **AI 초안=undo 1단위**
        (Ctrl+Z 한 번에 초안 통째 취소). 로딩(버튼 disabled+"생성 중…"). 버튼은 design-unit-picker
        안(개수입력 2칸 + 🤖 버튼), designBoundary 있을 때 활성.
      ⚠️ **"끝점 공유 자동 충족" 가정 안 함**: 외곽은 격자가 아니라(래스터추적 43정점) 둘레 방은
        안 닫힐 수 있음 — **정상, JJ가 끝점을 외곽에 클릭-클릭으로 붙여 마감**. 격자스냅·클립은
        안전망일 뿐 완성 아님. AI는 초안 도구이고 결과는 JJ가 수동 보정·확정하는 구조(로드맵 3단계
        반자동 원칙 그대로). 기존 클릭-클릭/스냅/undo/renderToBe/내보내기/beforeunload 무수정.
      ⚠️ **ANTHROPIC_API_KEY 필요**(ai-advice와 동일). 현재 머신엔 키 미설정(backend/.env 없음)→
        실제 호출은 500. JJ가 키 설정해야 실제 AI 초안 동작. 자동검증은 키 없이 가능하게 분리.
      검증: ①(자동) `_verify_genlayout_backend.py` — 후처리(격자스냅 3017→3000·degenerate0·외곽밖
        클립0·일부밖 안쪽만·dedup)·파싱(펜스/산문/깨짐)·기본값(40→방2화1·70→방3화2·120→방4화2)·
        프롬프트 개수명시 전부 통과. ②(자동) `_verify_genlayout.py`(빌라 page3 A세대) — 키 없어
        `/api/generate-layout`을 page.route로 **목**(외곽 bbox 안 격자 mock 벽)해 **프런트 경로**만
        검증: 개수입력→AI버튼→주입(벽5·전부외곽안)·이어 클릭-클릭 편집(+1)·Ctrl+Z 2회(마지막 벽→
        초안 통째0)·PNG 비단색72418·PDF 유효·에러0. **방 개수는 검증 안 함**(AI 산출이라). ③(JJ
        수동·필수) 실제 키로 브라우저: 방3 화장실2 입력→AI 초안→벽 외곽 안·클릭-클릭 편집·PNG/PDF·
        Ctrl+Z 초안 취소·개수 비우고 기본값 동작. **오피스텔로도 1회**(하드코딩 없음). 통과 전 미완료.
- [x] **AI 초안 v2 — AI 출력을 "벽 선분"→"라벨 붙은 직사각형"으로 전환**(v1의 자유좌표 불안정
      해결: 외곽 이탈·방 이름 없음·엉성한 배치). AI가 벽 대신 방을 직사각형으로 출력:
      `{"rooms":[{"name","x","y","w","h"}]}`(mm, 100mm 격자). `_parse_rooms_json` 신규.
      **방 프로그램 규칙(프롬프트)**: "방"=침실만. 침실 정확히 N·욕실 정확히 M(개수 변경 금지) +
        한국 주거 기본 공용공간 **필수**(현관·거실·주방 또는 LDK·다용도실). ★**거실=동선 허브**:
        현관·주방·욕실·모든 침실이 거실에 직접 접함(빌라 규모라 복도 없이 거실이 동선 겸함).
        **막힌 방 금지**(다른 침실 거쳐야만 들어가는 구조 X, 거실/현관서 직접 접근). ★**방 사이
        빈틈 금지**(인접 방은 변을 정확히 공유하며 맞붙음; 빈틈은 "방들과 불규칙 외곽선 사이"에만
        허용)·겹침 금지·외곽 bbox 안·100mm 격자.
      백엔드 `_rects_to_rooms_and_walls`: 각 rect 격자스냅 → **shapely 정확 intersection**(buffer
        없이 — 외곽 밖 절대 통과 금지)·**area<1㎡ drop**·`representative_point`로 중심(L자여도 내부
        보장) → 생존 rect의 격자 사각형 4변을 모아 기존 `_postprocess_walls`(격자스냅·buffer50 클립·
        degenerate·dedup)로 벽 생성(**인접 공유변은 dedup으로 1개**). 반환
        `{walls, rooms:[{name,cx,cy,area_m2}], count, bedrooms, baths}`. rooms 0개·생존 0개면 **422**
        (designWalls 보존). ※벽은 buffer50 클립(경계 붙은 변 보존), 방면적은 buffer 없는 정확 클립.
      프런트 **이름 매칭(best effort)**: 응답 rooms→전역 `designRoomNames[{name,cx,cy}]` 저장,
        `recomputeDesignRooms` 끝에서 각 면에 대해 그 안에 든 AI 중심좌표의 이름을 `pointInPoly`
        (신규 ray-casting)로 부여→`renderDesignRooms` 라벨=`이름\n면적`(이름 없으면 면적만).
        saveDesign/enterDesignMode saved 분기에서 roomNames 유지. 상태텍스트 `data.rooms`(정수)→
        `data.bedrooms`. walls 포맷 불변이라 클릭-클릭/undo(초안=1단위)/내보내기/renderToBe/
        beforeunload/unit-boundary/export-plan-pdf **전부 무수정**.
      ⚠️ **이름 매칭 한계**: JJ가 벽을 옮겨 면이 바뀌면(특히 벽이 AI 중심을 가로지르면) 이름이
        어긋나거나 사라질 수 있음. JJ가 새로 그은 벽으로 생긴 면엔 이름 없음(면적만). = 참고용,
        최종 이름은 방이름수정 UI가 정석(설계모드 방엔 미적용=별도 증분). AI가 빈틈 없이 채우리란
        보장도 없음 → 틈 생기면 planarFaces가 인접 면 병합(JJ 클릭-클릭 마감). **초안이지 완성 아님.**
      ⚠️ **ANTHROPIC_API_KEY**: backend/.env에 한 줄(UTF-8, prefix 중복 주의 — `sk-ant-sk-ant-`처럼
        두 번 들어가면 401). `anthropic` 패키지 venv에 설치됨(0.109.1). 실제 키로 호출 확인 완료
        (빌라 더미 10×8m: 현관·다용도실·거실·주방·욕실·침실1·침실2·LDK확장, 침실수=입력2, 전부
        100mm 격자).
      검증: ①(자동) `_verify_genlayout_backend.py`(직사각형판) — 격자스냅·외곽밖 통과0·<1㎡ drop·
        침실수=입력 보존·공유변 dedup(벽21<4×6)·중심 면내부·`_parse_rooms_json`(펜스/산문/깨짐/w0)·
        기본값·프롬프트(개수+빈틈) 전부 통과. ②(자동) `_verify_genlayout.py` — `/api/generate-layout`
        page.route 목({walls,rooms} 새 형식)→주입·전부외곽안·**이름 라벨 매칭(거실·침실)**·클릭-클릭
        편집(+1)·Ctrl+Z 통째취소·PNG72614·PDF유효·에러0. **배치 품질/방개수 미검증.** ③(JJ 수동·
        필수) 빌라 page3 A세대 침실2 욕실1: 방 외곽 안·**방 사이 빈틈 없음**·현관/거실/주방/침실/
        욕실/다용도실 라벨·**거실 통해 모든 공간 접근**·클릭-클릭 편집·Ctrl+Z 통째취소. **오피스텔로도
        1회**. 통과 전 미완료.
- [x] **고정 방(Fixed Room) — JJ가 위치 아는 공간(현관 등)을 직접 그려 잠금**(AI 배치는 무수정,
      다음 증분에서 이 고정 방을 받아 나머지만 채움 — 이번엔 데이터 구조·잠금 UI까지).
      흐름: 클릭-클릭으로 닫힌 방 그림 → **🔒 고정 방 지정** 서브모드 토글 → 그 면 클릭(raycast)
        선택 → 이름(드롭다운 현관/다용도실/발코니/팬트리/창고/계단실/보일러실/드레스룸 + 직접입력)
        → **🔒 고정으로 잠금**. 여러 개 가능. 사이드바 "고정 방" 목록 + 항목별 🔓 해제.
      구현: 전역 `designFixedRooms=[{name, poly(mm)}]`(designWalls/designRooms와 별도 레지스트리;
        고정 방 벽은 designWalls에 그대로 있음). 서브모드 `designFixMode`(unitMode 패턴) — ON이면
        mouseup 상태머신 **맨 앞 분기**에서 좌클릭=`_designRoomTiles` raycast 면 선택 후 return(벽
        그리기 안 탐). ★토글 ON 시 **진행중 클릭-클릭 선 취소**(drawStart 비움). `renderDesignRooms`가
        타일에 `userData.faceIdx` 부여 + `_designRoomTiles[]` 별도 보관. `recomputeDesignRooms`에
        잠금 매칭(면 centroid가 fixed poly 안이면 `face.locked/face.name`, 기존 AI 이름 매칭과 같은
        자리)+**스테일 lock reconcile**(해당 면 사라지면 자동 해제). 잠금 면=잠금색(`_LOCK_COLOR`
        슬레이트)+라벨 `🔒 이름\n면적`, 선택 면=금색.
      잠금 의미(**완전한 편집 차단 아님** — 거짓 보장 금지): ①이름+레지스트리 ②**clear("그리던 것
        지우기")에서 보존**(`ensureLockedWalls`가 잠긴 poly 변을 designWalls에 재주입, 재주입 전
        `dedupDesignWalls`) ③다음 증분 AI 핸드오프 데이터. **`undoDesignWall`은 literal 무수정** —
        잠금 이전까지 undo로 밀면 그 면이 사라져 reconcile이 자동 해제(의도적, undo 단순 유지). ⚠️
        **잠긴 방 위에 새 벽을 그어 면을 쪼개는 건 아직 못 막음**(가드는 다음 증분). 노이즈 외곽에선
        손그림 방이 여러 면으로 쪼개져 한 고정 영역이 여러 잠금 면으로 보일 수 있음(영역 보존이 핵심).
      저장/복원: `saveDesign`→`unitDesigns[unit].fixedRooms`, `enterDesignMode` saved 분기 복원,
        `exitDesignMode`/`revertDesignToOriginal` 리셋·전체삭제. `resetFixModeUI`로 모드 버튼/패널 초기화.
      **AI 핸드오프 payload(구조만, 호출은 다음 증분)**: `POST /api/generate-layout` body에
        `fixed_rooms:[{name, poly:[[x,y]...]}]` 추가 예정 → 백엔드가 `사용가능 영역 = 외곽 −
        ∪(fixed poly)`(shapely difference) 계산해 빈 영역만 AI에 주고 응답에 고정 방 합쳐 반환.
        **이번 증분은 `generateLayout` 무수정**(designFixedRooms 구조만 완비, payload 미전송).
      무수정 확인: 클릭-클릭/snapPoint/`undoDesignWall`/renderToBe/내보내기/**generateLayout**/
        beforeunload/api 전부 안 건드림. 신규 헬퍼만 추가(ensureLockedWalls/dedupDesignWalls/
        setFixMode/resetFixModeUI/selectDesignFace/lockSelectedFace/unlockFixedRoom/renderFixedList).
      검증: ①(자동) `_verify_fixedroom.py`(빌라 page3 A세대, **실제 클릭**) — 닫힌 방 그림·진행중 선
        찍고 토글 시 **취소 확인**·면 raycast 선택·이름"현관" 잠금(designFixedRooms=1·name·poly)·
        잠금색·둘째 방 추가(=2)·**clear 후 보존(=2)**·🔓 해제(=1)·저장→종료→재진입 복원(다용도실)·
        에러0. 회귀(_verify_clickdraw/_verify_genlayout/_verify_export) 전부 통과. ②(JJ 수동·필수)
        실제 브라우저: 현관 그리기→면 클릭→이름→고정 잠금 ①잠금 시각(자물쇠/색) ②clear에도 남는지
        ③🔓 해제 ④다용도실 1개 더 ⑤**저장→재진입 유지** ⑥(참고)undo로 잠금 이전 밀면 풀림.
        **오피스텔로도 1회**. 통과 전 미완료.
- [x] **외곽 스냅샷·잠금 + 무파괴 모드 전환 + 묶기 변경 경고 + 묶기 localStorage**(JJ가 겪은
      "세대 묶기 갔다 오면 고정 방·벽 사라짐" 해결). ★실제 원인(진단): 저장 안 한 작업(designWalls·
      designFixedRooms)은 `exitDesignMode`에서 휘발 → `enterDesignMode`의 saved 분기는
      `unitDesigns[unit]` 있을 때만(=💾 저장 눌렀을 때만) 탐 → 저장 안 하고 모드 오가면 else 분기로
      빠져 빈 외곽 새로 fetch → 벽·고정방 소멸. (unitDesigns·rooms[i].unit 자체는 모드 전환으로 안
      지워짐; 휘발하는 건 "현재 세션 미저장 작업".)
      **규칙2(핵심·무파괴)**: 신규 `autosaveDesign()` — `designWalls.length||designFixedRooms.length`
        이면 현재 작업을 `unitDesigns[designUnit]`에 자동 보관(buildDesignSnapshot 공용 헬퍼 = 💾와
        동일 포맷+`roomIds`) + `renderToBe()`(To-Be 항상 동기화). `exitDesignMode` **맨 앞**에서 호출
        → 모든 종료 경로(모드전환·세대전환·재파싱) 커버. revert는 walls·fixed 둘 다 비운 뒤 delete라
        autosave 가드 통과 못해 재저장 안 됨. clear는 fixed 보존 → autosave가 고정방만 저장(의도).
      **규칙1(외곽 스냅샷)**: autosave/saveDesign이 `boundary_mm` 스냅샷 → 재진입 saved 분기가 그
        스냅샷 외곽 사용. 묶기가 바뀌어도 설계는 자기(진입 시점) 외곽 유지.
      **규칙3(묶기 변경 경고)**: `unitDesigns[unit].roomIds`(정렬된 방 id 집합) 필드. `enterDesignMode`
        `const saved`→`let useSaved` 플래그. 현재 묶인 id집합 !== saved.roomIds이면
        `confirm("구역이 바뀌었습니다. 확인=기존 설계 유지(이전 외곽)/취소=새 외곽으로 다시 시작")`.
        확인=saved 분기, 취소=`delete unitDesigns[unit]`+`useSaved=false`→else 분기(새 외곽 fetch).
        안 바뀐 세대는 경고 없음. ★판정은 **방 id 집합 비교**(면적/bbox 아님) — 묶기변경의 근본이
        방 집합 변경이라 노이즈(외곽추적 정점흔들림) 없고 추가 fetch 불필요.
      **규칙4(묶기 localStorage)**: 키=`floorplan-units:{file.name}:{file.size}:{page_index}`(다른
        PDF·페이지 자연 분리). 형식=`{방id:unit}` 맵. ★**방 id 결정성 먼저 검증**(같은 PDF+page 두 번
        파싱 → id 완전 일치 확인: 빌라 page2 5방·테스트용 page0 20방 모두 일치) → **id 기반 그대로
        채택**(중심좌표 키 폴백 불필요). `assignUnit` 끝에 `saveUnitsToStorage()`(묶기 변경 자동 저장,
        전부 해제 시 removeItem). `parsePdf`+`renderParsed` 후 `loadUnitsFromStorage()`로 rooms[i].unit
        채우고 전 방 applyRoomBaseColor+updateUnitCounts+상태토스트("저장된 세대 묶기 N칸 복원됨").
        신규 `unitStorageKey`/`saveUnitsToStorage`/`loadUnitsFromStorage`/`currentUnitRoomIds`.
      ⚠️ **설계 unitDesigns는 디스크 저장 안 함**(묶기만 localStorage) — 새로고침 시 묶기만 복원, 설계는
        세션/내보내기로 유지(beforeunload 경고 그대로).
      무수정 확인: 클릭-클릭(mouseup)·snapPoint·undoDesignWall·고정방(setFixMode/lockSelectedFace/
        ensureLockedWalls)·capture3dPng/exportPlanPdf·planarFaces·recomputeDesignRooms·beforeunload·
        generateLayout 안 건드림. saveDesign은 buildDesignSnapshot 공용화(roomIds 포함)만.
      검증: ①(자동) `_verify_autosave.py`(빌라 page3 A세대, 실제 클릭/모드전환) — ①autosave: 벽4+현관
        고정(💾 안 누름)→세대묶기 모드 갔다 옴→재진입 시 벽4·고정방1·name"현관" 유지 ②경고: A에 방1개
        추가→재진입 confirm, accept=외곽·벽 그대로/dismiss=unitDesigns.A 삭제+빈 외곽 ③localStorage:
        저장 키·새로고침+재업로드→8칸 복원+토스트, 에러0. 회귀(_verify_fixedroom/_verify_clickdraw/
        _verify_genlayout/_verify_export) 전부 통과. ②(JJ 수동·필수) 실제 브라우저: A 묶기→설계(현관
        고정, 저장 안 함)→묶기 모드 갔다 옴→설계 유지 / 새로고침+같은 PDF 재업로드→묶기 복원+토스트 /
        세대 경계 바꿔 재진입→경고 confirm 유지·다시시작 둘 다. **오피스텔로도 1회**. 통과 전 미완료.
- [x] **세대별 AI 구조 제안 — 고정 방 연결**(generate-layout이 고정 방을 빼고 나머지만 배치).
      흐름: JJ가 위치 아는 공간(현관 등)을 직접 그려 🔒 잠금 → AI 초안 생성 시 그 고정 영역을
      빼고 남은 빈 공간에만 AI가 방 배치 → 고정 방은 그대로 보존·합쳐 표시.
      **백엔드 `generate_layout`**: payload에 `fixed_rooms:[{name, poly}]` 받음. 유효 poly(≥3점)만
        shapely Polygon화(buffer(0) 보정)→`unary_union`→`avail = bpoly.difference(fixed_union).buffer(0)`.
        **avail.is_empty 또는 area<2㎡면 422**(배치 공간 없음, 기존 작업 보존). avail.bounds로 avail_bbox.
        **★사용가능영역 MultiPolygon 처리**: AI엔 정밀 폴리곤을 안 주고(따라 그리길 기대 안 함, MultiPolygon
        이면 더 헷갈림) **avail bbox + 고정 영역 bbox·이름 + "침범 금지"**만 명시. 실제 방어선은 백엔드
        이중 안전망 — `_rects_to_rooms_and_walls(.., clip_poly=avail)`·`_postprocess_walls(.., clip_poly=avail)`로
        rect/벽을 avail로 교차 클립(MultiPolygon이어도 intersection 그대로 동작 → 영역 전체로 클립,
        고정 영역 겹치는 부분 잘림). **두 함수에 `clip_poly` 옵션 인자 추가(기본=None=기존 bpoly 동작
        → 기존 호출부 무영향)**. 응답 형식 불변(walls=AI 벽만, 고정 방 벽은 프런트가 재주입).
      **프롬프트**(`_build_layout_prompt`에 fixed_rooms·avail_bbox 인자): 고정 있으면 외곽 폴리곤 대신
        avail bbox + "[이미 고정된 공간 — 침범·재생성 금지]" 섹션(이름·bbox). 만들 공용공간 목록에서
        **이미 고정된 이름 제외**(현관 고정 시 요청줄에서 현관 빠지고 거실·주방·다용도실만). `_LAYOUT_SYSTEM`
        규칙2·6에 "이미 고정된 용도 재생성 금지"·"고정 영역 침범 금지" 명시.
      **프런트 `generateLayout`**: body에 `fixed_rooms=designFixedRooms.map({name,poly})` 추가(고정 없으면
        빈 배열=무영향). 성공 시 `designWalls=aiWalls` 후 **`ensureLockedWalls()`**(고정 방 poly 변 재주입
        +dedup, 고정 없으면 no-op)로 고정 방 벽 보존. confirm 문구="그린 벽(고정 방 제외)이 AI 초안으로
        대체됩니다. 고정 방은 보존됩니다." designHistory push 그대로(=AI 초안 undo 1단위, Ctrl+Z로 생성
        직전=고정방만 남은 상태로 복귀). 이름 매칭: `recomputeDesignRooms`가 고정 면=잠금 이름(AI보다
        우선)·AI 면=AI 이름, `designRoomNames`엔 AI 방만(현 코드 그대로).
      무수정: 클릭-클릭/snapPoint/undo/고정방 잠금(lockSelectedFace 등)/autosave/묶기 localStorage/
        내보내기/planarFaces/renderToBe/beforeunload 안 건드림. clip_poly 옵션은 기본값=기존 동작.
      검증: ①(자동) `_verify_fixedlayout_backend.py` — avail=difference(고정2개 80−8=72㎡·점
        포함판정)·MultiPolygon avail 클립(가운데 코어 빼면 좌/우 2조각, 가로지르는 rect 큰조각 생존·중심
        코어 밖)·clip_poly로 고정 영역 겹치는 rect 16→12㎡ 잘림(clip_poly=None이면 16 전부=기존 보존)·
        벽도 고정 내부 구간 제거·avail<2㎡ 판정·프롬프트 고정 이름 제외, 21/21 통과. ②(자동)
        `_verify_fixedlayout.py`(빌라 page3 A세대, **실제 클릭** + page.route 목) — 현관 그려 잠금→AI
        생성 시 **요청 body에 fixed_rooms 실림**(name·poly)·주입 후 고정 방 보존(designFixedRooms=1·
        현관 잠금 면 존재)·벽 전부 외곽 안·Ctrl+Z로 AI 초안만 취소(고정 방 보존)·에러0. 회귀
        (_verify_fixedroom/_verify_clickdraw/_verify_genlayout/_verify_export/_verify_genlayout_backend)
        전부 통과. ③(JJ 수동·필수) 빌라 page3 A세대: 현관 그려 고정→침실2 욕실1→AI 생성→ⓐ현관 보존
        ⓑAI 방이 현관 침범 안 함 ⓒ현관 뺀 나머지만 채움 ⓓ현관 중복 생성 안 됨 ⓔ클릭-클릭 편집·Ctrl+Z.
        고정 2개(현관+다용도실)로도 1회. **오피스텔로도 1회**. 통과 전 미완료.
      ⚠️ **ANTHROPIC_API_KEY 필요**(ai-advice·genlayout과 동일). 자동검증은 목으로 키 없이 가능.
      ⚠️ **잠긴 방 위에 새 벽 그어 면 쪼개기 가드는 아직 없음**(다음 증분).
- [x] **AI 배치에 건축 규칙 강제 + 방위(향) 반영**(AI가 건축 상식 없이 막 배치하던 문제 — 거실이
      한가운데 갇혀 채광 없음·욕실 1.7㎡ 규격 미달 등 해결). **프롬프트 명문화 + 백엔드 검증/재생성
      (HARD 위반 시 최대 3회) + 방위 기반 배치**의 이중 구조. 외부 데이터 학습 안 함(규칙 코드화).
      **건축 규격 상수**(`_ARCH`, 보편 기준·하드코딩 아님): 침실 폭2400/7㎡/채광HARD, 욕실 1500×2000/
        3㎡, 주방 폭1800, 거실 한 변3300/12㎡/채광HARD, 동선900(프롬프트만). `_room_category`(★주방을
        '방'보다 먼저 매칭 — '주방'⊃'방').
      **채광 판정**(`_touches_outer`): 방 exterior ∩ 세대외곽(bpoly) exterior 공유 변 길이≥1m. 기준=
        **세대 외곽 bpoly**(avail 아님), buffer(50) 노이즈 보정. 거실·침실=채광 필수(HARD), 욕실·주방·
        다용도=내부 가능.
      **검증/재생성**(`_validate_layout`→`_generate_with_retries`, caller 주입 가능=키 없이 단위검증):
        HARD=거실 채광+규격·침실 각각 채광+규격·욕실 규격·주방 폭(+거실 없음). HARD 0이면 즉시 채택,
        아니면 `_violations_feedback`(구체 위반+직전 방 좌표)를 붙여 재요청, 3회 후 best(min HARD,SOFT,
        −방수)+`ok=false`. 서버 콘솔에 attempt별 위반 로그. 422(파싱·생존0) 보존. SOFT=배관 인접
        (`_water_connected`)·개수 불일치·방위 부적합(과한 HARD는 루프 폭주라 SOFT). ★"침실 채광 독점·
        거실 가두기 금지"는 **거실 채광 HARD에 자동 포함**(거실이 외벽에 닿아야 하므로) — 별도 룰 없음.
      **방위**(이미 ai-advice엔 building_orientation으로 있던 걸 generate-layout에 연결): `_edge_directions`
        ("도면 위쪽=orientation"→외곽 4변 방위, 시계 북→동→남→서; mm는 작은 y=상단). `_room_facings`로
        방이 닿는 변 방향. 방향별 SOFT+프롬프트: 남=거실/침실, 북=물공간, 동=침실, 서=거실 과열주의.
        **모름이면 방향 강화 생략**(채광 유무만). 프런트: AI 초안 박스에 방위 셀렉트 신설(`#design-
        orientation-select`), 기존 `#orientation-select`와 **양방향 동기화**, generateLayout이 설계
        셀렉트 우선 읽어 body.building_orientation 전송.
      **공용화**: `_clipped_rooms(rects, clip_region)`→[{name,poly,area_m2,grid_bbox}] 신설, 검증과
        `_rects_to_rooms_and_walls`가 같은 기하 사용(반환 **2-tuple 보존** → 기존 호출부/테스트 무영향).
      **프런트 로딩**: 문구 "채광·규격·방위·동선 검증·재생성 — 최대 ~40초", 버튼 경과초, AbortController
        90초. 응답 `warnings/ok/attempts` 추가(기존 키 유지=하위호환), `data.warnings||[]`·`ok!==false`
        안전 처리, 경고 있으면 "⚠️ 일부 규칙 미충족:…직접 보정". `_LAYOUT_SYSTEM`에 규격·채광·방위·배관·
        거실 가두기 금지 명문화(규칙11~15). `_build_layout_prompt(building_orientation,feedback)` — 방위
        변별 방향+좌표 힌트, 재생성 피드백 지점. **기존 문구(침실N/욕실M/빈틈) 보존**.
      무수정: 고정방 빼기(avail)·clip_poly·_postprocess_walls·클릭클릭·undo·autosave·묶기 localStorage·
        내보내기·renderToBe·beforeunload·recomputeDesignRooms·ensureLockedWalls. _rects_to_rooms_and_walls arity 보존.
      검증: ①(자동) `_verify_archrules_backend.py`(40/40) — _room_category 우선순위·_touches_outer(외벽T/
        가운데F)·_edge_directions 4방위 회전·_room_facings·_validate_layout(나쁜배치 HARD 4종 검출/좋은
        배치 HARD0/방위남 거실북향만→SOFT/모름이면 orient 없음)·_violations_feedback·_generate_with_retries
        (나쁨2+좋음→3회째 멈춤 ok=True / 나쁨×3→best+ok=false warn5 / 첫시도좋음 1회 / 전부파싱실패 None).
        ②(자동) `_verify_genlayout.py` 확장 — body에 building_orientation='남' 실림·방위 셀렉트 동기화·
        warnings/ok=false여도 주입+경고 상태표시. 회귀(_verify_genlayout_backend 23·_verify_fixedlayout_
        backend 23·_verify_fixedlayout·_verify_fixedroom·_verify_clickdraw·_verify_export) 전부 통과.
        ③(JJ 수동·필수, 실제 키) **방위 남→A세대 침실3 욕실1 생성**→ⓐ거실·침실 남쪽·물공간 북쪽 ⓑ거실
        채광 닿음 ⓒ욕실≥3㎡·침실 쓸 폭 ⓓ경고 내용 ⓔ**서버 콘솔 attempt 1/2/3 재생성 로그** / 방위 모름
        1회 / 고정 2개·**오피스텔** 각 1회. 통과 전 미완료.
      ⚠️ **채광 한계(다음 증분 후보)**: 채광=세대 외곽 접함만 봄 → 옆세대와 맞붙는 경계벽도 채광으로
        오판. **세대 묶기 정보로 맞붙는 변=경계벽=채광 제외** 계산 가능(다음 증분). 비직사각 외곽의
        방향 facing·L자 clip 폭은 bbox 근사(AI는 직사각형이라 대부분 정확). 동선 0.9m는 프롬프트만.
- [x] **AI 초안 — 내접 직사각형 앵커 + 산문 방지(별도 예산) + 용량/형상 경고**(AI가 ㄱ자/협소
      세대에서 방을 외곽 밖으로 흩뿌리거나·산문을 뱉어 파싱 실패·물리적으로 불가능한 침실 수를
      욱여넣던 문제). **3축 보강:**
      ①**내접 안전영역 앵커**(`_max_inscribed_rect(region, target_cells≈56)`): avail(고정 없으면
        외곽=bpoly)에 최대 축정렬 내접 직사각형을 격자샘플링+히스토그램DP로 1회 계산. ★**보수 판정
        =셀 '네 모서리'가 모두 `prep().covers`(경계 포함)인 셀만 사용** — 셀 중심만 보면 빗변에서
        사각형이 영역 밖으로 삐져나옴(삼각형 검증으로 실측 확인). 직사각형은 covers가 경계점도 True라
        100% 보존. mm 환산 후 안쪽 100mm 스냅, MultiPolygon/퇴화 대응(None). 빌라 A세대(ㄱ자
        75.6㎡)→내접 16.8㎡(22%). 프롬프트에 "[안전 배치 영역 …]=N㎡ 이 안은 안 잘림, 거실·큰 침실
        먼저 앵커, 멀어질수록 잘림 위험" 1줄(D1=전부 가두기 아닌 '큰 방 우선'). 기존 bbox·외곽
        폴리곤·고정방 제외 섹션 전부 유지. 성능 <20ms(큰 30×20m도).
      ②**산문 방지**: `_LAYOUT_SYSTEM` "첫 글자 반드시 '{', 산문·사고과정·영어·마크다운 금지" +
        유저 프롬프트 끝 1줄 재강조. **D4: 산문/파싱 실패는 max_tries(3) 미차감, 별도 `prose_budget`
        (+2회) 소예산으로 재시도** — `_generate_with_retries`를 while+valid_tries로 재작성. 예산 내
        실패는 attempts_log에 안 쌓이고(정상 시도 보존), 예산 소진 후 또 실패하면 그때 정상 시도 1회로
        쳐 종료 보장(전부 산문이면 최대 호출=예산2+정상3=5회 후 None=422). 검증: 산문2+좋음→호출3·
        attempts=1·ok=True(정상 시도 미차감 확인).
      ③**트리거1(용량) AI 호출 전 422**(D2): `need = 침실×7 + 거실12 + 욕실×3`. need>avail_area면
        AI 호출도 안 하고 즉시 422("전용 N㎡로는 침실M+거실+욕실 최소 need㎡ 못 담음, 개수 줄이세요").
        ⚠️ api_key 체크가 422보다 먼저라 자동검증은 더미 키 세팅 후 호출(트리거1은 호출 전 short-circuit).
      ④**트리거2(형상) 경고 prepend**(D3): 재생성 3회 후에도 ok=False이고 best HARD가 '침실 폭/면적'에
        몰리면, 규격 충족 침실수 K(폭≥2.4m·면적≥7㎡·채광OK) 세어 요청 M>K이면 warnings 최상단에
        "이 세대 N㎡·형상상 규격 침실 M개 어려움(적합 K개), 좁은 모서리 직접 마감" prepend. best 벽은
        그대로 반환(반환 6-tuple·응답 키 불변=프런트 generateLayout 무수정, 422는 기존 !res.ok 처리).
      무수정: 고정방 빼기(avail)·clip_poly·_postprocess_walls·_clipped_rooms·_rects_to_rooms_and_walls
        (2-tuple)·HARD/SOFT 검증·방위·클릭클릭·undo·autosave·묶기 localStorage·내보내기·renderToBe·
        beforeunload. _generate_with_retries 시그니처는 끝에 prose_budget=2만 추가(기존 positional 호출
        전부 호환).
      검증: ①(자동) `_verify_inscribed_backend.py` 35/35 — 내접(직사각형 100%·ㄱ자/삼각형/Multi 실영역
        내 contains·100mm 스냅·<100ms·퇴화 None)·트리거1(작은 세대 422+메시지/충분 세대 통과)·산문
        별도 예산(산문2+좋음→정상시도 1·전부산문 5회 후 None)·트리거2(나쁨×3→형상경고 prepend+best
        반환/좋음은 경고 없음)·프롬프트 안전영역·산문금지 문구. ②(자동) 회귀 _verify_archrules_backend
        40·_verify_genlayout_backend·_verify_fixedlayout_backend 23·_verify_genlayout(playwright)
        전부 그린. ③(실제 키) `_diag_genlayout.py`(A세대 ㄱ자 침실3 욕실1, **inner_rect 적용**): 프롬프트에
        안전영역 16.8㎡·방위 남 실림, attempt 1/2/3 재생성 로그, 최종 ok=False·walls=23·rooms=8,
        **warnings[0]="이 세대 76㎡·형상상 규격 침실 3개는 어렵습니다(적합 0개)…"** 형상 경고 발동 실측 확인.
        (A세대는 10.2×16.25m bbox에 75.6㎡뿐인 극단 ㄱ자라 침실3 물리적 한계가 정상 검출됨.)
      ④(JJ 수동·필수, 실제 키) 직사각형 세대→무회귀 정상 / 빌라 A세대 침실3→안전영역 앵커·침실 폭
        개선·안 되면 형상경고·침실2로 낮추면 통과·서버 콘솔 attempt 로그 / C세대 삼각형→안전배치+모서리
        직접 마감 / **오피스텔** 1회. 통과 전 미완료.
- [x] **수동 설계 B 강화 — 직사각형 방 빠르게 그리기 + 방 이름 붙이기**(AI 자동배치는 ㄱ자 한계가
      명확 → JJ가 직접 빠르게 그리는 도구 강화. 이번은 B의 첫 조각, AI generate-layout 무수정).
      **서브모드 3개 상호배타**(`_offOtherSubModes(except)` 코디네이터 — 한 모드 켤 때 나머지 끄고 진행중
        drawStart·rectStart 취소): 벽 클릭-클릭(기존)·**▭ 직사각형 방(designRectMode)**·**✏️ 방 이름
        (designNameMode)**·🔒 고정 방(designFixMode). design-actions에 토글 3개 나란히. mouseup 상태머신에
        `if(designFixMode)…/ if(designNameMode)…/ if(designRectMode)…` 분기를 벽 클릭-클릭 앞에 추가 →
        서브모드 중엔 벽 그리기 자동 비활성.
      **①직사각형(designRectMode)**: 대각선 2점 클릭. `rectStart`=첫 모서리, hover=점선 사각형 미리보기
        +"가로W×세로H" 라벨, 둘째 클릭=두 대각 모서리 min/max로 **axis-aligned 4변 생성**(나머지 두 모서리
        파생→항상 직각). **`snapCorner(raw)`**=정점(_SNAP_MM 350)→선분(350)→**100mm 격자**(직각 스냅 안 씀
        —대각선엔 ortho 해로움). W or H<_DRAW_MIN_MM(100)이면 재클릭. **designHistory.push 1회=undo 1단위**
        →4변 push→`dedupDesignWalls()`(인접 사각형 공유변 중복 제거)→recompute/render. Esc/우클릭=cancelRect.
      **②이름(designNameMode)**: 면 raycast 선택(`_pickDesignFaceIdx` 공용 헬퍼)→`selectNameFace`(금색
        하이라이트+패널)→`applyDesignRoomName`(드롭다운 거실/주방/침실/욕실/현관/다용도실/발코니/드레스룸
        +직접입력). **저장처=기존 `designRoomNames`[{name,cx,cy}] 재사용**(AI 이름과 합침)→recompute가
        면 중심 매칭으로 라벨 부여→`buildDesignSnapshot.roomNames`·`enterDesignMode` 복원으로 **저장·재진입
        유지 공짜**. 잠긴 면은 이름 모드로 못 바꿈(고정 해제 필요). 한계: 면 가로지르게 벽 바꾸면 이름
        어긋날 수 있음(AI 이름과 동일 best-effort).
      **③PDF 이름 포함**(결정): `exportPlanPdf` payload rooms에 `name` 추가, 백엔드 `_build_plan_pdf`가
        이름 있으면 `이름`(위)+`면적`(아래) 2줄, 없으면 면적만. PNG는 라이브 캡처라 이름 자동 포함.
      ★**핵심 버그 2건(실측 발견·수정)**:
        (a)**`shoelaceArea` 절댓값 → 외곽에 안 닿고 떠 있는 닫힌 루프가 면 2개로 잡힘**(정·역방향). 단일
          사각형=중복 1개, 인접 2사각형=둘레 perimeter면까지 phantom. → `shoelaceSigned` 신설,
          recomputeDesignRooms에서 **음수(외부/역방향)면 제외**(내부=양수 확인). 벽-외곽 연결 방은
          기존대로(외부 큰 면은 음수+97% 둘 다로 제외). 기존 클릭-클릭 회귀 전부 그린.
        (b)**`applyRoomName` 이름 충돌** — As-Is 방이름 수정용 함수가 이미 존재(2868줄). 함수 선언 호이스팅
          으로 내 설계용이 덮어써져 호출이 엉뚱한 함수로 감 → **`applyDesignRoomName`으로 개명**.
      무수정: 클릭-클릭 벽·고정 방(setFixMode는 `_offOtherSubModes("fix")` 1줄만)·AI생성(generate-layout)·
        undo/clear/revert·autosave·묶기 localStorage·renderToBe·beforeunload·planarFaces. recompute는
        signed-area 제외 1줄 추가(떠 있는 루프 버그 수정, 기존 동작 보존).
      검증: ①(자동) `_verify_rectroom.py`(빌라 page3 A세대, **실제 클릭**) — 서브모드 상호배타·직사각형
        모드 중 벽 클릭 비활성·대각2점→닫힌 방1·축정렬·면적·인접 공유변 dedup(벽7 방2)·undo 1단위·이름
        지정(designRoomNames 반영·면 라벨·recompute 후 유지)·좌클릭중 카메라0·우드래그 카메라>0·에러0.
        회귀 _verify_clickdraw/_verify_fixedroom/_verify_export/_verify_genlayout/_verify_autosave/
        _verify_fixedlayout 전부 그린 + PDF 이름 백엔드 스모크(거실 추출 OK). ②(JJ 수동·필수) 직사각형으로
        방 여러 개(인접 포함)→이름 붙이기(드롭다운+직접입력)→저장·재진입 유지→PNG/PDF 내보내기(이름
        보이는지)→Ctrl+Z→클릭-클릭 벽과 혼용. **오피스텔도 1회**. 통과 전 미완료.
- [x] **설계 시작 조언 — `POST /api/design-advice`(AI=머리/조언 텍스트만, 손=JJ)**. 설계 모드 진입
      시 빈 외곽뿐이라 어디서 시작할지 난감 → AI가 좌표/벽을 그리는 대신(계속 실패) **전문가 배치 조언을
      텍스트로만** 주고 JJ가 그 조언을 보고 직사각형 방 도구로 직접 그림. generate-layout·ai-advice 무수정.
      **신규 엔드포인트**(기존 ai-advice는 rooms 필수·As-Is 시스템프롬프트라 재사용 부적합 → 신규):
        입력 `boundary_mm`(필수,<3→400)·`unit`·`building_orientation`·`fixed_rooms[{name,poly}]`·
        `bedrooms`/`baths`(선택)·`trend`(자유텍스트, [:2000] cap)·`current_rooms[{name,area_m2,cx,cy}]`
        (★JJ가 이미 그린 방). `_build_design_advice_context` + `_DESIGN_ADVICE_SYSTEM`으로 claude-sonnet-4-6
        1회(재생성 없음 — 조언은 텍스트). `{answer, context}` 반환. 키 없으면 500.
      **형상 판정 `_classify_shape`**(대략, 단정 아님): 채움비율(면적/bbox)≥0.85=정형 / 0.55~0.85=ㄱ자·요철
        비정형 / <0.55=삼각형·이형, 종횡비≥2면 "좁고 긴" 부가. 라벨+원시수치(채움·종횡비·정점수·bbox)+
        **외곽 폴리곤 좌표**+**`_max_inscribed_rect` 안 잘리는 큰 직사각형(주 생활공간 영역)**+
        **`_edge_directions` 방위 라벨**(남=상단변 등)을 전부 AI에 줘 AI가 최종 판단.
      **조언 내용**(`_DESIGN_ADVICE_SYSTEM`): ①형상·면적→주 생활공간 적합 위치 ②방위 채광(남=거실/침실,
        북=물공간; 모르면 일반 채광 원칙) ③침실N 구성·동선(현관→거실→방)·물공간 모으기 ④**트렌드 경계**:
        JJ 입력 있으면 우선 반영, 없으면 일반 트렌드(알파룸·팬트리·드레스룸)만 언급+"구체적 최신 시장
        트렌드는 별도 확인 필요" 정직 명시(지어내기 금지) ⑤고정 방·**이미 그린 방 전제로 이어서 조언**
        ("거실 15㎡ 그렸으니 다음은 침실을 북동쪽에" 식). 정밀 좌표·치수 단정 금지(방향성만). 한국어·불릿.
      **프런트**: 설계 패널에 "💡 설계 시작 조언" 박스(trend textarea + `btn-design-advice` + 읽기용
        `design-advice-response`). 방위·개수는 기존 입력(design-orientation-select·gen-rooms·gen-baths)
        재사용. `requestDesignAdvice()`=boundary·방위·fixed·개수·trend·**current_rooms(designRooms 중심좌표)**
        전송→텍스트만 렌더, **designWalls 불변**(벽 생성 안 함), 로딩+AbortController(60s). `updateDesignActions`에
        버튼 활성 1줄(designBoundary≥3) 추가.
      무수정: generate-layout·직사각형 방·이름·클릭-클릭·고정방·undo·autosave·묶기 localStorage·내보내기·
        renderToBe·beforeunload·**기존 ai-advice**(`_ADVICE_SYSTEM`/`_build_advice_context`).
      검증: ①(자동) `_verify_design_advice_backend.py` 22/22 — `_classify_shape`(정형/비정형/이형·종횡비·면적)·
        `_build_design_advice_context`(면적·bbox·방위 남→상단변=남·내접영역·구성·고정방·**그린 방 반영(area0
        제외)**·트렌드 유무 분기)·입력검증(boundary<3→400·trend cap·키없음 500). ②(자동) `_verify_design_advice.py`
        (빌라 A세대, **실제 클릭** + page.route 목) — 버튼 활성·요청 body에 boundary/orientation/fixed/개수/
        trend/current_rooms 실림·목 응답 패널 렌더·**designWalls 0 유지**·빈 트렌드 동작·직사각형 1개 그린 뒤
        재요청 시 current_rooms 반영·에러0. 회귀 _verify_rectroom/_verify_clickdraw/_verify_fixedroom/
        _verify_genlayout/_verify_export/_verify_autosave 전부 그린. ③(실제 키 스모크) A세대 ㄱ자 방위 남+거실
        18㎡+트렌드 "4베이/팬트리" → 형상·남향·그린 방·개수·트렌드 모두 반영한 방향성 조언 확인(좌표 단정 없음).
      ④(JJ 수동·필수, 실제 키) A세대(ㄱ자) 방위 남 조언→형상·방위·개수 반영·트렌드 입력 반영·빈 입력 단서·
        벽 안 그려짐 / 방 몇 개 그린 뒤 다시 조언→이어서 조언하는지 / **오피스텔(정형)로도 1회**. 통과 전 미완료.
      ⚠️ 후속: 조언 모달화(사이드바 텍스트→화면 중앙 모달, Esc/오버레이/× 닫기·복사·다시보기 캐시) +
        btn-advice/advice-response로 통합(위 `btn-design-advice`/`design-advice-response`는 stale).
- [x] **설계 조언 '대략적 위치 존' 3D 오버레이 — AI 좌표 부정확 인정, 방향 가이드만**(텍스트 조언이
      "남쪽 외벽 서쪽 끝 안방"이라 해도 JJ가 3D에서 어딘지 매칭 못 하던 문제 → 대략 위치를 색 존으로 표시.
      JJ는 존 보고 직사각형 방 도구로 직접 그림. 존=가이드, 실벽·방 아님, designWalls 안 건드림, 저장 안 함 휘발).
      **백엔드 design-advice 응답 확장**(`{answer, context}`→`{answer, zones, context}`):
        ①`_DESIGN_ADVICE_SYSTEM`에 "조언 텍스트 뒤 ```json {\"zones\":[{name,x,y,w,h}]}``` 펜스" 규칙 추가
          (대략값·외곽 범위·100mm 격자, 펜스 밖 JSON 금지). max_tokens 1200→1600.
        ②`_split_advice_and_zones(text)`→(prose, json_str): ```json 펜스 우선, 없으면 "zones" 포함 {...} 블록
          분리. **prose에 JSON 안 남김**(모달 깔끔). 펜스/JSON 없으면 (원문, "").
        ③`_parse_zones_json`=`_parse_rooms_json` 클론(키 'zones', 펜스/산문혼합/깨짐/w0 robust). **기존 함수 무수정.**
        ④`_clipped_rooms(rects, bpoly)` **재사용**으로 외곽 클립(<1㎡ drop·밖 잘림·MultiPolygon 최대조각)→
          `zones=[{name, poly:[[x,y]...], area_m2}]`. ★**zones 추출 전체를 try/except**로 감싸 실패해도
          `zones=[]`, **answer(조언 텍스트)는 절대 안 잃음**(graceful degradation, prose 비면 raw 폴백).
      **프런트 가이드 레이어**(방 타일·벽과 별개): 전역 `designZones`/`_designZoneMeshes`/`designZonesVisible`.
        `renderDesignZones()`=흐린 반투명 면(opacity 0.18, **y=0.012**로 방 타일 0.02 아래→JJ 그린 방이 위에
        보임)+`LineDashedMaterial`+computeLineDistances 점선 테두리+`makeLabel("이름 (대략)")`(y=0.45). **raycast
        배열(_designRoomTiles)에 안 넣어** 고정/이름 모드 클릭 간섭 0. `recomputeDesignRooms`/`renderDesignRooms`/
        `renderDesignWalls` **무수정**(각자 자기 배열만 dispose). `toggleDesignZones()`=메시 `.visible` 일괄.
      **연결**: `requestDesignAdvice` 성공부에 `designZones=data.zones||[]; renderDesignZones()` 몇 줄 + 토글
        버튼 텍스트 갱신(모달 텍스트·캐시·복사·다시보기 **무수정**). 조언 카드에 `btn-zone-toggle`("🗺️ 위치 존
        표시/숨김", designZones 있을 때만 활성=updateDesignActions 1줄)+"점선=AI 추정 대략 위치, 정확 X" 안내.
        enter/exitDesignMode에 존 리셋(휘발). `saveDesign`/`unitDesigns`/`renderToBe` 무수정(To-Be에 존 안 들어감).
      무수정 확인: 직사각형방/이름/클릭클릭/고정방/AI생성(generate-layout)/undo/clear/autosave/묶기 localStorage/
        내보내기/조언 모달 텍스트·캐싱·복사/renderToBe/beforeunload + 기존 백엔드(_parse_rooms_json·_clipped_rooms
        시그니처·generate_layout). 변경=신규 추가 + 기존 라인 삽입 4곳(design_advice 응답·requestDesignAdvice 성공부·
        updateDesignActions 1줄·enter/exit 리셋).
      검증: ①(자동) `_verify_zones_backend.py` 24/24 — `_split_advice_and_zones`(펜스/산문혼합 분리·prose에 JSON
        안 남음·JSON 없을 때 원문 보존)·`_parse_zones_json`(정상/펜스/산문/깨짐→[]/w0 제외)·`_clipped_rooms`
        (외곽 안 생존·삐짐 클립·<1㎡ drop·완전 밖 drop)·**★graceful(깨진 zones여도 prose 보존·zones=[])**.
        ②(자동) `_verify_zones.py`(빌라 A세대, **실제 클릭**+page.route 목) — 모달 텍스트+designZones 2개·메시
        생성·**존 정점 전부 외곽 안**·토글 on/off(.visible)·**존 위에 직사각형 그리기 정상**(방 y0.02>존 y0.012)·
        **★빈 zones 응답→존 0·토글 비활성·조언 텍스트는 정상**·에러0. 회귀 7종(design_advice/clickdraw/rectroom/
        fixedroom/genlayout/export/autosave)+design_advice_backend 22 전부 그린.
      ③(JJ 수동·필수, 실제 키) A세대 조언→ⓐ존이 **이름과 함께** 3D에 뜸 ⓑ외곽 안 ⓒ토글 on/off ⓓ존(점선·흐림)이
        실벽과 구분 ⓔ**존 보며 직사각형 그리기**(존이 가이드로 보이고 그린 방이 위에 얹힘) ⓕ새 조언 교체·세대
        나가면 사라짐. **오피스텔로도 1회**. 통과 전 미완료.
      ⚠️ **ANTHROPIC_API_KEY 필요**(design-advice와 동일). 자동검증은 목으로 키 없이.
      ⚠️ 한계: 존은 AI 추정 대략값(정밀 보장 X, 클립으로 외곽 안엔 유지). zones 안 주거나 깨지면 텍스트만(존 없음).
- [x] **A-2 정형 영역 규격 자동 분할 — 알고리즘이 좌표, AI 안 씀(키 불필요)**(AI 좌표 방식이 규격 미달
      1.16m 침실·욕여넣기로 계속 실패 → 좌표 계산을 코드로 넘기고 규격을 100% 강제. generate-layout 공존).
      ★**구현 전 프로토타입 측정**(`_proto_partition.py`, 실제 A외곽 추출 `_a_boundary.json`):
        ①면적비례 treemap(squarify)·guillotine은 얇은 칸(침실 폭 1.66/1.88m) **위반 1~2건**.
        ②**규격 인지 BSP(분할점 k×절단방향 v/h 전수 시도, 칸별 위반합+0.01×종횡비 최소 선택)는 정형에서
          위반0**: 48㎡ 침실2·욕1, 70㎡ 침실3·욕2 모두 위반0·빈틈없음·<22ms. → **BSP 채택.**
        ③**★내접+분할은 정형 전용**: A세대(심한 ㄱ자) 내접=**6.0×2.8m=16.8㎡(외곽 75.6㎡의 22%, 깊이 2.8m)**
          뿐 → 침실2·욕1(필요33㎡) 거부, **"거실만" 16.8㎡만 통과**, 잔여 58.8㎡. A세대는 사실상 자동분할
          불가(거실 1칸+큰 잔여). **A-2 실효 타깃=오피스텔·직사각 정형**, ㄱ자는 zones 가이드+수동이 현실.
      **백엔드 `POST /api/partition-layout`**(키 불필요, 결정적): boundary+fixed+개수+방위 →
        ①고정방 차감 avail → ②`_max_inscribed_rect(avail)` 내접 → ③`_partition_feasible`(min_area합+zero-min방
          ×4㎡ > 내접이면 **거부 ok:False**, 우겨넣지 않음) → ④`_partition_bsp`(가중치 거실2.2/침실1.35/주방1.0/
          욕실0.6) → ⑤`_assign_roles_by_facing`(`_edge_directions`/`_touches_outer`/`_room_facings` 재사용, 채광
          필요 거실·침실=외곽접함·남향 우선, 물공간=내부/북향, **좌표 안 건드림**, HARD>0이면 **거부**) →
          ⑥칸 rect → 기존 **`_rects_to_rooms_and_walls`/`_clipped_rooms`/`_postprocess_walls` 재사용**(격자스냅·
          외곽클립·벽 dedup, walls 포맷 불변) → `{ok, walls, rooms, residual_zones, inner_area_m2, residual_area_m2,
          reason}`. 잔여=`avail.difference(내접)` 폴리곤들(2㎡↑)을 residual_zones로(거부여도 표시). 서버 콘솔에
          feasibility·HARD·거부 로그.
      **프런트**: 자동배치 카드(③) 안에 신규 **`btn-partition-layout`("📐 규격 자동 분할 (정형 세대용)")를 기존
        `btn-generate-layout` 위에** 배치(측정상 정형엔 우월)+각 버튼 설명 1줄. 방/욕실/방위 입력 공유. `partitionLayout()`:
        호출→**ok:False면 벽 안 건드리고 사유 표시(기존 작업 보존)**, ok면 designWalls 대체+`ensureLockedWalls`
        (고정방 보존)+`recomputeDesignRooms`/렌더, **designHistory push(undo 1단위)**. residual_zones는 ok 무관하게
        **기존 `designZones`/`renderDesignZones` 재사용**으로 "잔여 — 다용도/발코니 추천(직접 마감)" 표시. 정직한
        reason 문구(정형="자동 분할 완료" / 비정형="일부만 N㎡, 나머지 M㎡ 가이드" / 거부=사유). `updateDesignActions`
        에 활성 1줄.
      무수정: generate-layout·직사각형방/이름/클릭클릭/고정방/조언·존/undo/clear/autosave/묶기 localStorage/
        내보내기/renderToBe/recomputeDesignRooms/beforeunload + 기존 백엔드 함수(`_rects_to_rooms_and_walls`/
        `_clipped_rooms`/`_postprocess_walls`/`_validate_layout` 시그니처). 변경=신규 추가(_partition_*·
        _assign_roles_by_facing·partition-layout·partitionLayout·버튼) + updateDesignActions/핸들러 와이어 1줄.
      검증: ①(자동) `_verify_partition_backend.py` 29/29 — _partition_bsp(48㎡/70㎡ 위반0·빈틈없음·<200ms)·
        feasibility 거부(14㎡/침실3·30㎡/침실2·A내접16.8㎡ 풀프로그램 거부·거실만 통과)·**A내접=외곽 22% 측정**·
        방위 배정(남향 거실·침실 위쪽·HARD0)·다운스트림(BSP칸→방5 벽17 dedup·NaN0)·엔드포인트(정형48㎡ ok·
        작은14㎡ 거부+벽보존·A세대 거부+잔여존1·정형 잔여<8㎡·boundary<3→400). 회귀 8종(zones/clickdraw/
        rectroom/fixedroom/genlayout/export/autosave/design_advice) 전부 그린. ②(프런트 스모크) A세대 클릭→거부·
        designWalls 0 보존·잔여존1·에러0.
      ③(JJ 수동·필수, 실제) **A세대 침실2 욕1→내접에 규격 칸 빈틈없이·1.16m 미달 없음·ㄱ자 잔여 가이드·
        침실3 거부(콘솔 사유)** / **오피스텔(정형)→침실N 규격 분할 깔끔(A-2 진짜 타깃)** / 서버 콘솔 feasibility·
        위반·거부 로그. 통과 전 미완료.
      ⚠️ 한계: 내접+분할은 정형 전용(측정). ㄱ자는 거실 정도만+큰 잔여(수동). 칸은 격자스냅 후 미세 오차 가능
        (다운스트림 클립이 흡수). AI 이름 배정(c2)은 현재 미적용(결정적 배정만, 키 불필요) — 필요 시 다음 증분.
- [측정·폐기] **A-1 비정형 폴리곤 규격 분할 프로토타입**(`_proto_partition_a1.py`) — 본 구현 안 함.
      비정형 외곽 자체를 규격 맞춰 분할(non-convex rectangular partitioning) 가능성만 측정. 3방식(직사각형
      분해+BSP / inscribe-and-carve / 최대내접 반복)을 실제 A세대(채움0.46)·합성 직사각(1.0)·합성 완만ㄱ자(0.89)에
      적용. **결과: 성공 여부는 채움비율에 직결** — 정형(1.0) a방식 위반0·채움99%, 완만ㄱ자(0.89) a방식 **위반0**·
      채움76%, **A세대 severe ㄱ자(0.46)는 전 방식 위반4~5(주로 채광)** → A-1로도 불가. a(분해+BSP)가 최선이나
      실효는 채움~0.85↑(정형~완만). severe ㄱ자는 zones 가이드+수동이 현실. → **A-1 본 구현 보류**(수동 강화로 전환).
- [x] **수동 드로잉 규격 피드백 — 치수 상시 표시 + 이름별 규격 경고 + 가이드**(자동배치는 각진 세대서 불가
      확정 → JJ 직접 그리기가 현실. 그릴 때 실제 가로×세로·사람이 살 수 있는 크기인지 알게 함).
      **규격 단일 소스 `GET /api/arch-spec`**(순수 조회, 자동배치 무관): `{specs:_ARCH, categories:[{cat,keywords}]}`
        반환. `_room_category`를 **신규 `_CATEGORY_KEYWORDS` 상수에서 파생**하도록 리팩터(동작 동일, 진짜 단일소스).
        프런트는 설계 진입 시 1회 fetch, **실패 시 baked-in 폴백**(현재 _ARCH값) → 오프라인 안전.
      **프런트**: `roomCategoryOf(name)`(백엔드 매칭 순서 그대로 JS 재현, **주방⊃방 먼저**)·`checkRoomSpec(name,W,H,area)`
        (카테고리 spec 있으면 **short=min(W,H)**로 폭·면적 비교→미달 항목 배열, 없거나 spec 0이면 빈)·`roomDims(poly,area)`
        (bbox W·H + area/bbox≥0.95면 isRect 정확/미만 근사). **거실=짧은변(최소변) 기준**(사용자 명시).
      **renderDesignRooms 라벨 확장**(텍스트만, makeLabel·recompute 시그니처 불변): `[🔒/⚠️] 이름` → `W × H m`
        (비직사각 `~W × H (bbox)`) → `N㎡` → 미달 시 `폭 2.1<2.4m · 면적 6.3<7㎡`. 우선순위 잠금>경고, 잠긴 방 검사 안 함.
      **규격 가이드** `design-spec-guide` div: setRectMode(true) 표시·(false)/_offOtherSubModes/resetFixModeUI 숨김.
        _archSpec에서 "침실 폭2.4m·7㎡ / 욕실 1.5×2.0m·3㎡ / 거실 최소변3.3m·12㎡ / 주방 폭1.8m" + bbox 근사 안내.
      ⚠️ **거실 변 기준 의도적 비동기**: 수동 피드백은 **짧은변≥3300**(사용자 명시 '최소변'), 백엔드 `_validate_layout`
        (자동배치)은 거실을 **긴변**으로 검사하는 quirk. 둘 다 같은 `_ARCH` 숫자(3300)를 쓰되 거실 변 선택만 다름.
        자동배치 백엔드는 안 건드림(의도적). bbox 근사: 비직사각 방 치수는 bbox 과대평가(면적은 shoelace 정확).
      무수정: 직사각형 그리기/이름/클릭클릭/고정방/조언·존/undo/autosave/묶기 localStorage/내보내기/renderToBe/
        recomputeDesignRooms/beforeunload/partition-layout/generate-layout. 변경=라벨 텍스트 확장+신규 헬퍼+arch-spec
        조회/캐시+가이드 토글. `_room_category`는 동작동일 리팩터(파생).
      검증: ①(자동) `_verify_archspec_backend.py` 17/17 — arch-spec==_ARCH 숫자 드리프트0·categories==_CATEGORY_KEYWORDS
        순서·프런트 roomCategoryOf 재현==백엔드(대표 27이름)·주방⊃방·검사제외(현관/다용도/발코니/기타) 0. ②(자동)
        `_verify_specfeedback.py` — archSpec 로드·checkRoomSpec(침실 폭2.1/면적6.3 미달·충족·욕실/거실/주방 경계·현관/
        다용도/이름없음 제외)·roomDims(합성 직사각 isRect True·L자 False)·가이드 토글 표시/숨김·실제 직사각 그림→방·치수·
        라벨 sprite 생성·에러0. 회귀 9종(rectroom/clickdraw/fixedroom/zones/genlayout/partition_backend/export/autosave/
        design_advice) 전부 그린.
      ③(JJ 수동·필수) 직사각형 방 그리기→**가로×세로·면적 라벨**·침실 7㎡/2.4m **미달 ⚠️**·충족 정상·욕실(3㎡/1.5m)·
        거실(12㎡/3.3m)·주방(1.8m) 확인·현관/다용도 검사 안 함·**직사각형 모드 규격 가이드표**·L자 bbox 근사(~). **오피스텔·빌라**. 통과 전 미완료.
- [x] **방 크기 숫자 변경 + 드래그 이동 — 경고 뜬 방을 그 자리에서 고침**(규격 ⚠️ 뜬 방을 지우고
      다시 그릴 필요 없이 크기를 숫자로 바꾸고 위치를 드래그로 옮겨 경고를 없앰). 신규 서브모드 ✥ 크기·이동.
      ★**데이터 모델 핵심**: 방은 저장된 사각형이 아니라 `planarFaces(외곽+designWalls)`가 매번 계산하는 **파생 면**.
        → 크기/이동 = **그 방 폴리곤 정점과 일치(tol 60mm)하는 designWall 끝점만 변환**하는 한 메커니즘으로 통일.
        변경 후 recompute가 면을 새로 만들어 인덱스가 바뀌므로 `_reselectFaceNear(기준점)`로 재선택.
      **신규 서브모드 `designEditMode`**(rect/name/fix와 상호배타 — `_offOtherSubModes`에 edit 분기): 선택은 기존
        `selectedDesignFace` 재사용(금색 하이라이트 공짜), `_pickDesignFaceIdx` 재사용, `selectEditFace`가
        `design-edit-panel`에 현재 W/H(roomDims) prefill. 비직사각이면 W/H 입력 disable+"비직사각은 이동만"(이동은 가능).
      ①**크기 적용**(`applyRoomSize`): designHistory.push(깊은복사)=undo 1단위 → **BL(min x,min y) 고정**,
        우변(x≈x1)→x0+W·상변(y≈y1)→y0+H, **선택 방 정점에 닿는 끝점만**(`_isRoomVertex`) 변환 → dedup →
        recompute → **BL 근접 재선택** → render. 100mm 이상·변화 있을 때만.
      ②**드래그 이동**(카메라 충돌 = ★3게이트 분리): (a)**capture mousedown** — 좌다운이 선택 방 위면
        `beginEditDrag`+`stopImmediatePropagation`+`preventDefault`(카메라·선택 차단), 그 외엔 통과(=orbit이 카메라).
        (b)**orbit mousedown** — `designMode && button0 && !designEditMode`면 return(edit 모드만 빈 곳 좌드래그 카메라 허용).
        (c)**window mousemove** — `editDragging`이면 delta(100mm 격자 스냅) 강체 평행이동 라이브(recompute+센트로이드 추적 재선택).
        **window mouseup** edit 분기: editDragging이면 `finishEditDrag`(시작 전 스냅샷 push=undo 1단위, **이동 없으면 선택
        해제=탭 토글**, 이동 있으면 centroid 재선택), 아니면 ≤5px=클릭 선택 토글/>5px=카메라(무시).
      ③**규격 경고 실시간**: 크기/이동 모두 recompute 경유라 `renderDesignRooms` 라벨이 `checkRoomSpec`로 자동 갱신(⚠️ 붙고/사라짐).
      ④**undo**: designHistory 재사용. `undoDesignWall`에 **edit-가드 1줄**(edit 모드일 때만 선택 정리+refreshEditPanel) — 타 모드 무영향.
      ⚠️ **공유 벽 한계**: 끝점 변환은 선택 방 정점에 닿는 벽만 옮김 → 옆 방과 **변 공유** 시 공유 정점이 함께 이동
        (겹침/빈틈 가능). 고립 방(주 케이스: 새로 그린 작은 침실)은 깔끔. 패널에 "옆 방과 붙어 있으면 공유 벽이 함께
        움직입니다" 안내. **비직사각 방은 크기변경 불가**(이동만). ⚠️ **비정형(ㄱ자) 외곽은 깨끗한 직사각 방을 그릴
        clear 영역이 작음**(A세대 최대 2.8m 정사각) — 외곽 노치가 사각형 내부를 가로지르면 면이 잘려 isRect=false.
      무수정: 직사각형 그리기/이름/클릭클릭/고정방/조언·존/규격피드백/autosave/묶기 localStorage/내보내기/renderToBe/
        recomputeDesignRooms/planarFaces/beforeunload/generate-layout/partition-layout/백엔드. 삽입=capture mousedown·orbit
        게이트·window mousemove·window mouseup·`_offOtherSubModes`/`resetFixModeUI`/`exitDesignMode` 리셋·undo 가드 1줄.
      검증: ①(자동) `_verify_editroom.py`(빌라 page3 A세대, **실제 클릭/드래그**) — 2×2 침실 그려 이름→⚠️·edit 선택 W/H
        prefill≈2000·**2.8×2.8 적용→7.84㎡·BL 불변·⚠️ 사라짐**·Ctrl+Z→4㎡·⚠️ 복귀·**선택 방 드래그 이동(centroid 2217mm
        이동·면적 보존·카메라이동 0.00000)**·**빈 곳 좌드래그=카메라 회전 29.8·우드래그=pan 1.7**·탭 토글·에러0. 회귀 8종
        (rectroom/clickdraw/fixedroom/specfeedback/zones/genlayout/export/autosave) 전부 그린.
      ②(JJ 수동·필수) 작은 침실 ⚠️→가로·세로 숫자로 키워 ⚠️ 사라짐→방 드래그 이동→빈 곳 좌드래그 카메라 회전·우드래그
        pan→undo. **빌라**(+오피스텔 있으면). 통과 전 미완료.
- [ ] **채광 정밀화(경계벽 제외)**: 세대 묶기로 옆세대와 맞붙는 변을 외벽에서 빼고 채광 판정.
- [ ] (구 구조편집 2단계 아이디어) 그리드 스냅·연속 체이닝·벽 두께(외벽>내벽)
- [ ] 잠긴 방 위 그리기 가드(면 쪼개기 방지)


