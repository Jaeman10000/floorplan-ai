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

## 다음 작업
- [ ] pdf_parser.py: 벡터 PDF에서 선/글자 좌표 추출
- [ ] 선 굵기로 외벽/내벽 분류 로직
- [ ] 방 폴리곤 + 방 이름 매핑
- [ ] 3D 렌더링에 내부 방 구획 반영
- [ ] 구조 편집 UI
