# AI Pension Copilot - Hybrid Agentic AI Workflow

사용자가 아래 정보를 입력하고 **확인 · AI 분석 시작**을 누르면, 화면에서 Agent Workflow를 거쳐 최종 퇴직연금 보고서를 생성하는 실행 가능한 웹 프로젝트입니다.

- 나이
- 은퇴 나이
- 현재 적립금
- 연간 납입액
- 가입 사업자
- 가입 상품명
- 매년 수입
- 투자 유형
- 운영 유형(DB/DC/IRP)
- 은퇴 후 희망 월 income

업로드해 준 `해커톤 데이터 모음(1).zip`은 프로젝트 안의 `data/source_documents.zip`으로 포함했고, 실제 PDF 54개를 파싱해 338개 RAG chunk를 만들어 두었습니다.

## 1. 실행 구조

```text
사용자 입력
   ↓
Pension AI Agent (Qwen Function Calling)
   ↓ 상황 분석 / Tool 선택
 ┌─────────────┬─────────────┬──────────────────┐
 ↓             ↓             ↓
Profile Tool   RAG Tool      Finance Engine
                               ↓
                         Monte Carlo
                               ↓
                     Portfolio Optimizer
 └─────────────┴─────────────┴──────────────────┘
   ↓
Recommendation Agent (Qwen)
   ↓
Critic Agent (Qwen + deterministic guardrail)
   ↓ PASS / RETRY
Report Generator (Qwen)
   ↓
웹 보고서 + 브라우저 PDF 저장
```

핵심은 **LLM에게 숫자 계산을 맡기지 않는 것**입니다. Qwen은 작업 계획, 도구 선택, 추천 문장, Critic 검증, 보고서 작성에 사용하고, 은퇴자산 계산/Monte Carlo/Optimizer는 Python deterministic engine이 수행합니다.

## 2. 가장 빠른 실행 - Windows

압축을 푼 뒤 `start_windows.bat`를 실행하면 됩니다.

직접 실행하려면 PowerShell에서:

```powershell
cd hybrid_agentic_pension_qwen
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
uvicorn app:app --reload
```

브라우저:

```text
http://127.0.0.1:8000
```

API Key가 아직 없어도 **demo fallback**으로 화면/Finance/RAG/Monte Carlo/Optimizer/Report 전체 동작을 테스트할 수 있습니다.

## 3. Qwen API 연결

프로젝트 루트에 `.env`를 만들고 아래 값을 넣습니다.

```env
APP_MODE=auto
DASHSCOPE_API_KEY=sk-여기에_본인_API_KEY
QWEN_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
QWEN_MODEL=qwen3.6-plus
QWEN_EMBEDDING_MODEL=text-embedding-v4
QWEN_EMBEDDING_DIM=1024
```

`DASHSCOPE_API_KEY`는 절대 `frontend/app.js`나 `index.html`에 넣지 마세요. 키는 FastAPI 백엔드에서만 읽습니다.

Alibaba Cloud Model Studio의 workspace 전용 endpoint를 쓰는 경우 `.env`의 `QWEN_BASE_URL`을 본인 workspace/region URL로 바꾸면 됩니다.

예: Beijing workspace endpoint 형태

```text
https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/compatible-mode/v1
```

## 4. RAG 동작

현재 기본 상태에서도 다음 방식으로 바로 검색됩니다.

```text
가입 사업자 + 가입 상품명 + 투자유형
          ↓
Metadata boost + lexical retrieval
          ↓
해당 PDF page/chunk 검색
          ↓
Recommendation Agent에 E1/E2/... 근거 전달
```

따라서 별도 Vector DB가 없어도 업로드된 PDF를 실제로 사용합니다.

### Qwen semantic RAG까지 켜기

Qwen API Key를 설정한 후 1회 실행:

```powershell
python scripts/build_semantic_index.py
```

그러면 `text-embedding-v4`로 338개 chunk를 임베딩하여 `data/embeddings.npy`를 만들고, 이후 검색은:

```text
Qwen semantic similarity + lexical + 사업자/상품 metadata boost
```

의 hybrid RAG로 자동 전환됩니다.

## 5. PDF 데이터 다시 넣기

새 ZIP으로 교체하려면 새 파일을:

```text
data/source_documents.zip
```

으로 바꾸고:

```powershell
python scripts/rebuild_corpus.py
```

실행하면 `catalog.json`과 `chunks.jsonl`이 다시 생성됩니다.

Semantic embedding을 사용 중이라면 기존 `data/embeddings.npy`를 삭제하고 `build_semantic_index.py`를 다시 실행하세요.

## 6. 주요 파일

```text
app.py                          FastAPI entry point
backend/agents.py               Pension Agent / Recommendation / Critic / Report
backend/tools.py                Finance / Monte Carlo / Optimizer / Profile
backend/rag.py                  PDF RAG 검색
backend/qwen_client.py          Qwen OpenAI-compatible API 연결
backend/models.py               사용자 입력 schema
frontend/index.html             입력 → Workflow → Report 화면
frontend/styles.css             UI 디자인
frontend/app.js                 화면 로직 / Agent 진행 애니메이션 / report chart
scripts/rebuild_corpus.py       PDF ZIP → catalog/chunks
scripts/build_semantic_index.py Qwen embedding index 생성
data/source_documents.zip       사용자가 업로드한 원본 상품 PDF ZIP
```

## 7. 현재 계산 가정

공모전 데모를 위해 다음 값을 코드에 명시적으로 분리했습니다 (`backend/tools.py`).

- 희망 은퇴 월소득 → 목표 은퇴자산: 4% 인출률 가정
- 안전자산 기대수익률: 연 3.2%
- 성장자산 기대수익률: 연 6.8%
- 안전/성장 자산 변동성 역시 데모용 가정

이 값들은 실제 미래수익률 예측값이 아닙니다. 최종 공모전 버전에서는 공식 자본시장가정, 상품 위험등급, 수수료, 실제 수익률 데이터 등으로 교체하는 것을 권장합니다.

## 8. 실제 Agent 여부

Qwen API Key가 설정된 경우 `backend/agents.py`에서 Qwen이 Function Calling으로 다음 Tool을 선택/호출할 수 있습니다.

- `analyze_profile`
- `search_product_rag`
- `run_finance_engine`
- `run_monte_carlo`
- `optimize_retirement_strategy`

다만 금융 서비스의 안정성을 위해 Qwen이 어떤 Tool을 누락해도 백엔드가 필수 Tool을 채우는 **Hybrid controlled agentic workflow**로 구현했습니다. 그 뒤 Recommendation Agent와 Critic Agent도 Qwen으로 동작하며, Critic 실패 시 한 번 재생성합니다.

## 9. 보고서

분석 완료 후 같은 웹페이지에서 보고서가 생성됩니다.

- Executive Summary
- AI Profile Diagnosis
- 현재 상품 RAG 분석
- 예상 은퇴자산 / 목표자산 / 목표달성률
- Monte Carlo 목표달성확률
- Optimizer 전략
- RAG 근거 카드(E1, E2, ...)
- Critic Agent 검증 결과
- Risk Notes / Assumptions

상단 `PDF로 저장 / 인쇄` 버튼을 누르면 브라우저의 인쇄 기능으로 PDF 저장할 수 있습니다.


## v3 입력 UX 규칙
- `은퇴 후 희망 월 소득`으로 한글화했습니다.
- 운영유형이 `DB`이면 개인 `가입 상품명`과 `투자 유형` 입력을 비활성화합니다.
- 현재 업로드된 상품 DB는 디폴트옵션 중심이므로 상품으로 `DB`는 배제할 수 있지만, 동일 상품이 DC와 IRP에서 사용될 수 있어 상품명만으로 `DC`와 `IRP`를 확정하지 않습니다.
- DC/IRP에서는 상품 메타데이터의 `risk_type`을 투자유형에 자동 반영합니다.

## v7 입력폼 분기
- 공통: 현재 나이, 은퇴 나이, 현재 연소득, 은퇴 후 희망 월 소득
- 운영 유형은 DB / DC / IRP 버튼으로 선택
- DB: 현재 근속연수, 자동 계산 추가 근속연수, 예상 임금상승률(직접 입력 또는 깨움 추정), 업종/직군, 회사규모, 최근 연봉 이력
- DC/IRP: 현재 적립금, 연간 납입액, 가입 사업자, 가입 상품명, 상품 메타데이터 기반 투자유형
- DB에서는 현재 적립금/연간 납입액/개인 상품 입력을 사용하지 않으며, DC/IRP에서는 DB 전용 입력을 숨깁니다.


## V11 UI 변경
- 현재 나이/은퇴 나이에 `세` 단위 표시
- DB형 / DC·IRP 분석 정보 제목을 민트색 굵은 20px로 확대
- DB 임금상승률 추정 안내와 예상 추가 근속연수 자동계산 안내 글씨 확대


## V12 UI 변경
- 상단 분석 파이프라인을 몬테카를로 시뮬레이션 뒤에서 강제 줄바꿈
- DB 입력 순서 변경: 근속연수 → 업종/직군+회사규모 → 최근 연봉 이력 → 예상 임금상승률
- 예상 임금상승률은 마지막 행 전체폭으로 배치


## V13 UI 변경
- DC/IRP의 가입 상품명 아래 메타데이터 안내문 삭제
- 투자 유형 아래 자동 인식 안내문 삭제


## V14 한국어 UI 패치
- 2단계 Agent Workflow 전체 한국어화
- WAIT/RUN/DONE/RETRY → 대기/진행/완료/재검토
- 각 Agent/Tool 명칭 한국어화
- Agent 실행 기록과 상태 메시지 한국어화
- 3단계 보고서의 영문 섹션 제목 및 상태 문구 한국어화
- PASS / REVISED / WARNING, Target, Monte Carlo 등 사용자 노출 문구 한국어화

## V15 — 선택 상품 PDF → Qwen 구조화 → Python 계산

DC/IRP 분석의 기준선을 변경했습니다.

1. 사용자가 가입 사업자와 상품명을 선택합니다.
2. `catalog.json`에서 **제목 exact match**로 공식 PDF 하나를 식별합니다.
3. RAG 검색도 전체 PDF DB가 아니라 **선택된 그 PDF 내부에서만** 수행합니다.
4. `Product Extraction Agent(Qwen)`가 해당 PDF 전체의 파싱 텍스트를 읽고 다음을 JSON으로 구조화합니다.
   - 구성상품명
   - 구성비중
   - 자산군 분류
   - 원리금보장 여부
   - 문서상 금리/수익률/변동성(실제로 기재된 경우만)
   - 수수료/운용전략/근거 페이지
5. Python Finance Engine이 구조화 JSON을 계산 입력으로 사용합니다.
6. PDF에 미래 기대수익률/변동성이 없는 경우 **상품구성 비중은 PDF 그대로 유지**하고, 시뮬레이션에 필요한 자산군 CMA만 별도 보완합니다.
7. Critic Agent가 exact PDF 매칭, 구성비중 추출 성공, Python 계산 기준 사용 여부를 검증합니다.

### 중요
- V14까지의 `중립투자형 → 안전자산 50%` 같은 임의 비율은 **현재 가입상품 기준선 계산에서 제거**했습니다.
- `SAFE_RATIO_BY_TYPE`은 V15에서 최적화 후보의 탐색범위/비상 fallback에만 사용합니다.
- PDF에 없는 미래 수익률을 Qwen이 만들어내지 않도록 프롬프트와 validation을 넣었습니다.
- `/api/product-extraction`으로 선택 상품의 PDF 매칭/추출 결과를 별도 확인할 수 있습니다.


## V16 금액 표시 패치
- 계산 엔진의 내부 금액 스케일은 변경하지 않음
- 결과 화면은 억/만원 단위로 재변환하지 않고 Python 숫자를 그대로 표시
  - 예: 75000 → 75,000
  - 예: 32060.6 → 32,060.6
- 차트 Y축도 동일한 raw 숫자 스케일로 표시
- Recommendation/Report Agent가 금액을 억/만원/원으로 재환산하지 못하도록 프롬프트와 Critic 검사를 강화
- Qwen 보고서가 금액 단위를 임의 변환하면 deterministic 보고서 fallback 사용


## V17 검증 강화 패치
- Product Extraction Agent가 포트폴리오 위험등급을 상품명/투자유형/깨진 표 위치로 추론하지 못하도록 강화했습니다.
- `risk_level_document`는 PDF 텍스트에서 해당 상품 자체의 단일 위험등급이 명확히 검증될 때만 유지하며, 애매하면 `null`로 처리합니다.
- `risk_level_verified`, `risk_level_evidence_pages`를 추가해 위험등급의 검증 여부와 근거 페이지를 추적합니다.
- `db_personal_optimization_applicable`를 제거하고 `portfolio_optimization_applicable`로 정리했습니다. DB=false, DC/IRP=true입니다.
- 추천 자산배분 합계 100%, 현재/추천 안전자산 방향, 계산과 추천 문구의 방향 일치 여부를 deterministic Critic이 검사합니다.
- 최적화 후보의 화면 표시 자산배분(원리금보장/현금성·채권형·성장형)과 기대수익률/변동성/Monte Carlo 계산을 동일한 CMA 입력으로 맞췄습니다.
- optimizer에 `allocation_direction`, `candidate_safe_ratio_range_pct`, `optimization_objective`, `selected_score_breakdown`를 추가했습니다.

### 버전 적용
- V17 전체 프로젝트 ZIP은 V16 변경사항을 모두 포함하므로 V16을 먼저 설치할 필요가 없습니다.
- V17 증분 patch는 V16 → V17용이므로 V16 위에 덮어써야 합니다.


## V18 실제 원화 숫자 표시
- 입력 및 Python 계산 엔진의 내부 금액 스케일은 기존 `만원`을 유지합니다.
- 3단계 사용자 화면과 LLM 보고서에 노출되는 금액만 `내부값 × 10,000`으로 변환합니다.
- 예시:
  - 3,500 → 35,000,000
  - 5,000 → 50,000,000
  - 36,761.87 → 367,618,700
  - 75,000 → 750,000,000
- `3,500만원`, `7.5억원`처럼 축약하지 않고 실제 원화 숫자를 그대로 표시합니다.
- 차트 Y축도 동일하게 실제 원화 숫자로 표시합니다.
