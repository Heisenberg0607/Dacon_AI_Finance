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


## V19 보고서 화면 RAG 챗봇 (Report Q&A Agent)

3단계 보고서 화면 우하단에 플로팅 챗봇을 추가했습니다. 사용자의 입력값, 분석결과 JSON,
PDF 코퍼스를 근거로 후속 질문에 답합니다. 기존 원칙을 그대로 승계합니다.

> 숫자는 Python이 만들고, LLM은 검색·해석·설명만 한다. 문서에 없는 값은 만들지 않는다.

### 동작

```text
보고서 화면 질문
      ↓
Report Q&A Agent (Qwen function calling, 최대 4턴)
      ↓
 ┌──────────────────────┬──────────────────────┬────────────────────┐
 ↓                      ↓                      ↓
search_pension_documents  simulate_what_if      get_analysis_section
 (선택 PDF / 전체 54개)    (Python 재계산)        (분석결과 원본)
 └──────────────────────┴──────────────────────┴────────────────────┘
      ↓
결정론적 가드레일 (Critic Agent와 동일 규칙)
      ↓ 위반 시 1회 재생성, 두 번째도 실패하면 차단
답변 + 근거 카드(E1, E2...) + 재계산 시나리오 카드
```

### 주요 특징

- **what-if 재계산**: "납입액을 1200만원으로 늘리면?", "은퇴를 65세로 늦추면?", "안전자산 20%면?"
  → `tools.py`의 기존 함수(`project_assets_by_return`, `monte_carlo_tool`, `_required_contribution`)로
  Python이 즉석 재계산하고, 현재값과 시나리오를 나란히 비교해 보여줍니다. LLM은 새 수식을 쓰지 않습니다.
- **입력 클램프**: 납입액은 0~연소득의 60%, 안전자산 비중 0~100%, 은퇴 나이는 모델 제약 범위로 제한하며,
  조정이 걸리면 그 사실을 답변에 명시합니다. 실제 원화 숫자(12,000,000)를 넣어도 내부 만원 스케일로 환산합니다.
- **DB형 분기**: DB에는 납입액·자산배분 시나리오를 적용하지 않고 비적용 사유를 안내합니다.
  은퇴 나이와 임금상승률 시나리오만 계산합니다.
- **전체 코퍼스 개방**: 다른 상품과 비교하는 질문은 54개 PDF 전체에서 검색합니다.
  다만 근거 카드마다 `내 상품` / `타 상품` 배지와 상품명을 항상 표시해, 다른 상품 내용이
  내 상품 설명처럼 읽히지 않도록 UI에서 구조적으로 구분합니다.
- **가드레일**: 억/만원 축약 표기, 수익 보장 표현, 존재하지 않는 근거 ID 인용을 Critic Agent와
  동일한 `backend/guardrails.py` 규칙으로 검사합니다.
- **demo 폴백**: Qwen API 키가 없어도 키워드 라우팅으로 목표달성률·확률·상품구성·자산배분·납입액·
  위험요인·근거출처 질문과 what-if 재계산이 모두 동작합니다.
- 챗봇은 `no-print`라서 `PDF로 저장 / 인쇄` 결과에는 포함되지 않습니다.

### API

```text
POST /api/analyze   → 응답에 analysis_id 추가 (분석 context를 서버 세션에 6시간 보관)
POST /api/chat      → {analysis_id, message, history[]}
                      {answer, evidence[], what_if, tool_trace[], search_scope, guardrail}
```

세션 저장소(`backend/session_store.py`)는 단일 프로세스 메모리입니다.
멀티 워커로 배포하려면 파일 또는 Redis 백엔드로 교체해야 합니다.

### 추가/변경 파일

```text
backend/chat_agent.py      Report Q&A Agent (도구 3종 · 가드레일 · demo 폴백)
backend/session_store.py   analysis_id 기반 분석결과 세션 저장소
backend/formatting.py      금액 포맷 유틸 (agents.py에서 분리, 챗봇과 공유)
backend/guardrails.py      결정론적 검사 (Critic Agent와 챗봇이 공유)
backend/rag.py             search()에 scope='selected'|'all' 추가 (기본값은 기존 동작)
app.py                     /api/chat 추가, /api/analyze에 analysis_id 추가
frontend/*                 플로팅 챗 패널 UI / 근거 칩 / 재계산 시나리오 카드
```


## V20 보고서 생성 소요시간 표시

3단계 보고서 화면에서 해당 보고서를 만드는 데 실제로 걸린 시간을 확인할 수 있습니다.
시간 역시 기존 원칙대로 **Python이 측정한 값만** 표시하며, 프런트에서 추정하지 않습니다.

### 측정 방식

`HybridAgenticWorkflow.run()`이 `TimedTrace`(list 서브클래스)로 trace를 수집합니다.

- `append(entry)` — 직전 단계 이후 경과시간을 `entry['elapsed_seconds']`에 기록
  (작업을 먼저 하고 결과를 append 하는 도구 단계용)
- `stamp_last()` — `'running'`으로 먼저 append 한 뒤 작업이 끝난 단계의 시간을 다시 기록
  (Recommendation / Critic Agent용)

`time.perf_counter()` 기준이므로 시스템 시계 변경의 영향을 받지 않습니다.

### 응답 스키마

`POST /api/analyze` 응답에 `timing`이 추가됩니다.

```json
{
  "timing": {
    "total_seconds": 93.4,
    "started_at": "2026-09-01T13:49:02",
    "finished_at": "2026-09-01T13:50:35",
    "stages": [
      {"stage": "monte_carlo", "tool": "run_monte_carlo", "seconds": 61.2, "runs": 2}
    ]
  }
}
```

- `stages`는 단계별 합산값입니다. Critic 재검토로 같은 단계가 두 번 실행되면 `runs: 2`로 합산됩니다.
- `trace`의 각 항목에도 `elapsed_seconds`가 함께 담깁니다.

### 화면 표시

| 위치 | 내용 |
| --- | --- |
| 3단계 보고서 표지 | `보고서 생성 소요시간 1분 33초` 칩 + `생성 완료 2026-09-01 13:49:02` |
| 위 칩 hover | 단계별 소요시간 breakdown (툴팁) |
| 2단계 워크플로우 로그 | 각 단계 줄 끝에 해당 단계 소요시간 |
| 2단계 완료 메시지 | `... 총 소요시간 1분 33초.` |

- 60초 미만은 `12.4초`, 60초 이상은 `1분 33초` 형식입니다.
- 표지 칩은 인쇄/PDF 저장 시에도 함께 출력됩니다.
- 서버가 `timing`을 주지 않으면 해당 영역은 숨겨집니다.

> 표시되는 시간은 **서버의 AI 분석 시간**입니다. 2단계 화면의 trace 리플레이 애니메이션
> 시간은 포함하지 않습니다.


## V20 대기 화면 남은 시간 게이지 (Remaining Wait Meter)

2단계 분석 화면에서 "얼마나 더 기다려야 하는지"를 시각적으로 보여줍니다.
사후에 표시되는 총 소요시간과 달리, 기다리는 동안 실시간으로 갱신됩니다.

숫자를 만들어내지 않는다는 원칙은 여기에도 그대로 적용됩니다.

> 예상 남은 시간의 근거는 서버가 `perf_counter`로 실측해 쌓은 과거 소요시간뿐이다.
> 근거가 없으면 숫자를 쓰지 않고 비확정 상태로 표시한다.

### 구성

- `backend/eta_store.py` — 분석이 끝날 때마다 실측 `total_seconds`를 누적한다.
  - `data/run_timings.json`에 최근 30회까지 저장 (gitignore)
  - 예상치는 **80분위수**(nearest-rank). 중앙값을 쓰면 정의상 과거 실행의 절반이 그 값을 넘어
    "예상 시간 초과"가 상시로 뜬다. 80분위수는 표본의 80%가 그 안에 끝났다는 뜻이라
    초과가 5회에 1번꼴로 줄고, 임의의 안전계수를 곱하지 않아 근거가 실측 표본 안에 그대로 남는다.
    (모의실험: 중앙값 약 50% → 80분위수 약 20%)
  - 버킷 분리: `운영유형(DB/DC/IRP) × 실행모드(qwen/demo)`
    DB는 상품 PDF 구조화 추출을 건너뛰고, demo fallback은 LLM 호출이 없어
    소요시간이 자릿수 단위로 다르다. 섞으면 추정이 무의미해진다.
- `GET /api/eta?operation_type=DC` — 어디에도 실측값이 없으면 `{"available": false}`

### 추정 근거 사다리

라이브 이력은 `.gitignore` 대상이라 새로 클론한 환경에서는 비어 있습니다.
그래도 첫 분석부터 남은 시간이 나오도록 근거를 아래 순서로 찾고,
어느 단계에서 나온 값인지를 응답의 `source`와 화면 문구에 그대로 밝힙니다.

| 순서 | `source` | 근거 | 화면 문구 |
| --- | --- | --- | --- |
| 1 | `measured` | 이 서버의 라이브 이력, 같은 버킷 | `최근 10회 중 80%가 1분 38초 이내 완료` |
| 2 | `baseline` | 저장소에 커밋된 실측 baseline | `기본 측정치 6회 중 80%가 1분 32초 이내 완료` |
| 3 | `related` | 같은 실행모드의 다른 운영유형 | `DC 실측 6회 중 80%가 1분 38초 이내 완료 (이 유형 이력 없음)` |
| 4 | — | 없음 → `available: false` | `예상 시간 산출 전` |

**실행모드 경계는 넘지 않습니다.** demo(0.1초대)와 Qwen(수십 초)을 섞으면 추정이 무의미해집니다.
운영유형 폴백 순서는 파이프라인 유사도를 따릅니다 — DC/IRP는 서로 가깝고,
상품 PDF 구조화 추출을 건너뛰는 DB는 마지막입니다.

### baseline 승격

`data/run_timings_baseline.json`은 저장소에 커밋되는 읽기 전용 실측 이력입니다.
서버는 여기에 쓰지 않고, 아래 스크립트로만 갱신합니다.

```bash
.venv/Scripts/python.exe scripts/save_timing_baseline.py
```

라이브 이력을 버킷 단위로 교체하므로 여러 번 실행해도 같은 측정값이 중복 누적되지 않습니다.

Qwen 모드로 한 번도 실행한 적이 없다면 `*|qwen` baseline도 없습니다.
없는 값을 지어내지 않으므로, `.env`에 API 키를 넣고 분석을 몇 번 돌린 뒤
위 스크립트를 실행해 커밋하면 그때부터 다른 PC에서도 첫 분석부터 예상 시간이 표시됩니다.
- 프런트 게이지 — AI 오브를 감싸는 링 + 남은 시간 + 진행 바 + 근거 문구

### 화면 상태

| 상태 | 조건 | 표시 |
| --- | --- | --- |
| 예상 남은 시간 | 실측 이력 있음, 예상치 이내 | `약 1분 8초` · 링/바가 경과 비율만큼 채워짐 |
| 예상 시간 초과 | 경과 > 예상치 | `마무리 중입니다` · 링/바가 노란색 진행중 애니메이션 |
| 예상 시간 산출 전 | 실측 이력 없음 (첫 분석) | `예상 시간 산출 전` · 진행중 애니메이션 |
| 분석 완료 | 응답 도착 | `실측 소요시간 N초` |

### 두 가지 소요시간

같은 분석에 대해 서로 다른 두 값을 잽니다. 일부러 다르게 둔 것이므로 맞추지 마세요.

| 값 | 재는 범위 | 쓰이는 곳 |
| --- | --- | --- |
| `timing.total_seconds` | `workflow.run()`만 | 보고서 표지의 "보고서 생성 소요시간" |
| 대기 이력에 기록되는 값 | `/api/analyze` 핸들러 전체 | 2단계 대기 게이지의 예상 남은 시간 |

게이지의 경과시간은 화면에서 요청을 보내기 **전부터** 재기 시작하므로,
`workflow.run()`만 잰 값을 기준으로 삼으면 구조적으로 항상 예상 시간을 초과합니다.

근거 문구에 `경과 41.0초 · 최근 10회 중 80%가 1분 38초 이내 완료`처럼
표본 수와 기준값을 함께 노출해, 표시된 숫자가 어디서 왔는지 확인할 수 있게 했습니다.

예상치 조회는 `/api/analyze` 요청과 병렬로 나가므로 분석 시작을 지연시키지 않습니다.
조회에 실패해도 게이지는 비확정 상태로 계속 동작합니다.


## V21 보고서 화면 상품 비교 (Product Comparison)

3단계 보고서의 `03 은퇴자산 전망 및 시뮬레이션` 위에 상품 선택 드롭다운을 넣어,
다른 상품을 골랐을 때 전망이 어떻게 달라지는지 바로 비교할 수 있게 했습니다.

**사업자는 가입 상품 기준으로 고정**되고, 바꿀 수 있는 것은 상품뿐입니다.
사업자를 넘나드는 비교는 사용자가 당장 실행할 수 없는 선택지라, 같은 사업자 안에서
상품만 갈아보는 쪽이 실제로 행동으로 옮길 수 있는 비교이기 때문입니다.
목록에서 가입 상품 자체도 뺐습니다. 남겨두면 기본 선택이 곧 지금 화면이라 계산 버튼이
아무것도 바꾸지 않는 것처럼 보이고, 그 자리는 `가입 상품으로 되돌리기`가 이미 맡고 있습니다.
같은 사업자에 다른 상품이 없으면(예: 카탈로그상 하나은행) 드롭다운과 버튼이 비활성화됩니다.

### 범위: 그래프와 숫자만

바뀌는 것과 바뀌지 않는 것을 명확히 나눴습니다. 두 상품의 근거가 한 화면에서 섞이면 안 되기 때문입니다.

| 갱신함 | 유지함 (가입 상품 기준) |
| --- | --- |
| 전망 그래프, 목표달성률 | 종합 요약, 전략, 상품 분석, RAG 근거 |
| 예상 은퇴자산, 목표달성 확률 | 검증 결과, 위험 고지, 헤더 요약줄 |
| 최적화 자산배분, 최적화 목표달성률 | 챗봇 (계속 가입 상품 기준으로 답변) |

비교 중에는 차트 위에 `비교용 시뮬레이션 · {상품명} 기준` 배지가 뜨고,
`가입 상품으로 되돌리기`로 원본 수치가 복원됩니다. 인쇄(PDF 저장) 시에는 컨트롤이 빠집니다.

### 동작

```text
상품 선택 → POST /api/reproject {analysis_id, provider, product_name}
      ↓
세션의 기준 입력 + 상품만 교체 (investment_type은 카탈로그 risk_type을 따름)
      ↓
_extract_selected_product  (Qwen 1회, file_id 캐시 적중 시 0회)
      ↓
finance_engine_tool / monte_carlo_tool / portfolio_optimizer_tool  (순수 Python)
      ↓
renderProjection()으로 차트·숫자만 부분 갱신
```

본 분석이 쓰는 세 도구를 그대로 재사용하므로 계산 코드는 새로 만들지 않았고,
LLM 에이전트(추천·Critic·보고서)는 건너뜁니다. 세션은 읽기만 하고 덮어쓰지 않습니다.

### 소요시간

비용은 **상품 PDF 구조화 추출(Qwen 1회)에 전부** 몰려 있습니다. 나머지는 순수 Python입니다.

| 상황 | 소요 |
| --- | --- |
| 처음 고르는 상품 (Qwen 모드) | Qwen 추출 1회 — 본 분석의 `product_extraction` 단계와 같은 비용 |
| 같은 상품 재선택 | 0.1초 미만 (`ProductExtractionAgent`의 file_id 캐시) |
| demo 모드 (API 키 없음) | 0.1초 미만 (LLM 호출 없음) |

캐시는 성공한 Qwen 추출만 담습니다. fallback까지 캐시하면 일시적 API 오류로 생긴
빈약한 추출이 프로세스가 사는 내내 고착됩니다.

정확한 초 단위 값은 보고서 표지의 소요시간 칩 툴팁에 나오는 `product_extraction` 단계
실측치를 그대로 보시면 됩니다.

### demo 모드의 한계

API 키 없이 도는 최소 추출(`_fallback_extract`)은 구성비중은 읽지만
**자산군을 전부 원리금보장으로 분류**합니다. 그래서 기대수익률이 상품과 무관하게 3.20%로 같아지고,
예상 은퇴자산·목표달성률이 상품을 바꿔도 동일하게 나옵니다.
(최적화 자산배분은 투자유형을 따르므로 이때도 달라집니다.)

화면이 고장난 것으로 보이지 않도록, 이 경우 안내 문구를 함께 표시합니다.
Qwen 추출이 자산군을 제대로 분류하면 아래처럼 실제로 갈립니다.

| 구성 | 기대수익률 | 예상 은퇴자산 | 목표달성률 |
| --- | --- | --- | --- |
| 예금 100% | 3.20% | 243,806,300 | 32.5% |
| 예금 50 / 채권 30 / 주식 20 | 4.22% | 297,450,300 | 39.7% |
| 주식 70 / 채권 30 | 6.02% | 427,357,300 | 57.0% |

DB형은 개인 선택 상품이 없어 컨트롤이 표시되지 않습니다.


## V22 전망 그래프 범례 · 마우스 오버 툴팁

선이 두 개인데 무엇을 뜻하는지 화면 어디에도 적혀 있지 않았습니다. 색만 다르고 설명이 없으면
상품을 바꿔가며 비교해도 어느 선이 어느 상품인지 읽을 수 없어서, 범례와 툴팁 양쪽에
"무엇을 기준으로 계산한 선인지"를 상품명까지 붙여 적습니다.

### 범례 (`#chartLegend`)

| 표시 | 의미 |
| --- | --- |
| 하늘색 실선 | `가입 상품 기준 · {상품명}` — 비교 중에는 `비교 상품 기준 · {비교 상품명}`으로 바뀝니다 |
| 초록 실선 | `최적화 자산배분 기준` — 깨움이 추천한 배분을 따랐을 때 |
| 노랑 파선 | `목표 은퇴자산` — 금액과 4% 인출률 환산 근거 |

DB형은 `optimizer.series`가 `finance.series`와 같은 배열이라 두 선이 완전히 겹칩니다.
없는 구분을 범례에 적으면 거짓말이 되므로, 이때는 `예상 퇴직급여 (DB)` 한 줄로만 설명합니다.

### 툴팁

그래프 위에 마우스를 올리면 가장 가까운 연도로 스냅해 세로 십자선과 각 선의 점을 찍고,
`{N}년 후 · 만 {나이}세`와 계열별 금액을 함께 보여줍니다.

```text
22년 후 · 만 54세
■ 가입 상품 기준        182,447,900
■ 최적화 자산배분 기준   209,348,200
▪▪ 목표 은퇴자산         750,000,000
```

목표선은 연도와 무관하게 일정하지만, **전망과 목표의 거리**가 이 그래프의 핵심이라
매 지점에서 두 값을 나란히 읽을 수 있도록 목표 금액도 함께 적고 목표선 위에도 점을 찍습니다.

툴팁의 계열 이름은 `가입 상품 기준`처럼 짧은 쪽(`short`)을 씁니다. 툴팁은 커서를 따라다니며
그래프를 가리므로 상품명까지 넣으면 상자가 화면 절반을 덮고, 어느 상품인지는 바로 아래
범례에 전체 이름으로 이미 적혀 있기 때문입니다.

- 좌표 변환은 `svg.getScreenCTM().inverse()`를 씁니다. 차트가 `min-width:720px; width:100%`로
  늘어나고 `.chart-box`가 가로 스크롤되므로, 화면 좌표를 직접 계산하면 어긋납니다.
- 툴팁 상자는 오른쪽에 자리가 없으면 왼쪽으로 넘어가고, 세로로도 그림 영역 안에 가둡니다.
- SVG에는 텍스트 폭을 재는 수단이 없어 `svgTextWidth()`로 근사합니다(한글 ≈ 폰트 크기,
  숫자·라틴 ≈ 그 절반).
- 차트 내용은 다시 그릴 때마다 통째로 교체되므로 리스너는 살아남는 `<svg>`에 한 번만 겁니다.

비교 재계산 시에는 `r.user`가 원본 그대로라, 방금 고른 상품명을 `projectionProductName`으로
따로 넘겨야 범례와 툴팁이 거짓말을 하지 않습니다.


## V23 서버 응답 없음(`Failed to fetch`) 안내

`fetch()`는 서버가 응답을 주지 못하면 HTTP 상태코드 없이 `TypeError`로 거절되고,
그 `message`는 브라우저가 만든 영문 원문 **`Failed to fetch`** 입니다. 이 문자열이
상품 비교의 상태줄에 그대로 찍히면서, 실제 원인은 로컬 `uvicorn`이 떠 있지 않은 것뿐인데도
계산 기능 자체가 고장난 것처럼 보였습니다.

### 두 종류의 실패를 구분한다

| | 뜻 | 처리 |
|---|---|---|
| `fetch` 거절 | 서버에 연결 자체가 안 됨 (서버 미실행 / 재시작 중 / 포트 불일치) | `ServerUnreachableError` → 실행 여부 확인 안내 |
| `AbortError` | 연결은 됐지만 제한 시간(3분) 안에 응답 없음 | `ServerUnreachableError` → 서버 로그 확인 안내 |
| 4xx / 5xx | 서버는 살아 있고 요청이 거절됨 | 호출부가 상태코드별로 처리 (404 = 세션 만료 등) |

두 종류를 뭉뚱그려 한 문장으로 안내하면 사용자가 엉뚱한 곳을 보게 됩니다. 특히 분석 화면의
기존 안내였던 "`.env`의 Qwen 설정을 확인하세요"는 서버가 아예 안 떠 있는 경우에는 틀린 지시입니다.

### `postJson()`

`POST` 3곳(`/api/analyze`, `/api/reproject`, `/api/chat`)이 모두 이 헬퍼를 지나갑니다.

- `AbortController`로 **3분 상한**을 겁니다. 상품 비교의 첫 계산은 상품 PDF 구조화
  (Qwen 호출 1회)를 포함해 수십 초가 걸릴 수 있지만, 상한이 없으면 서버가 멈췄을 때
  버튼이 `계산 중...`에 갇혀 되돌릴 방법이 없습니다.
- `fetch`가 거절한 경우만 `ServerUnreachableError`로 바꿉니다. `fetch`는 4xx/5xx에서는
  거절하지 않으므로, 이 catch에 들어오는 것은 네트워크 수준 실패뿐입니다.

### 응답 본문을 그대로 화면에 찍지 않는다

`httpErrorMessage()`가 FastAPI의 `detail`(한국어 사유)만 골라 쓰고, 없으면
정해둔 안내와 상태코드로 대체합니다. 이전에는 `await res.text()`를 그대로 상태줄에 넣어서
500 트레이스백이나 JSON 원문이 노출됐습니다.

같은 이유로 상품 비교의 `catch`는 **직접 만든 예외**(`ServerUnreachableError` /
`CompareError`)의 메시지만 화면에 씁니다. 렌더 도중 발생한 JS 오류까지 `err.message`로
흘리면 다시 영문 내부 문구가 화면에 찍힙니다.

### 확인한 경로

demo 모드(`DASHSCOPE_API_KEY` 없음)에서 서버를 실제로 죽였다 살리며 네 경로를 확인했습니다.

- 정상 재계산 → 비교 상태줄 정상 갱신
- 서버 중지 후 `이 상품으로 계산` → 실행 여부 확인 안내, 버튼 원상 복구
- 서버 중지 후 챗봇 질문 → 같은 안내 (기존의 "잠시 후 다시 시도"는 서버가 꺼져 있으면 영원히 틀린 안내)
- 서버 재시작 후 만료된 `analysis_id`로 재계산 → 404 → 세션 만료 안내


## V23-b 가입 사업자·상품 조회 안내

`가입 사업자`와 `가입 상품명`은 이 앱이 **추정할 수 없는 유일한 입력**입니다. 나이·적립금·납입액은
틀려도 결과가 그만큼 어긋날 뿐이지만, 상품을 잘못 고르면 분석 전체가 **다른 상품 PDF 기준**으로
계산됩니다. 구성비중·수수료·자산군이 모두 그 PDF에서 나오기 때문입니다.

그래서 두 입력란 바로 아래에 조회처를 적습니다.

- 금융감독원 **통합연금포털 '내연금조회'** (`https://www.fss.or.kr/fss/lifeplan/anntyLogin/list.do?menuNo=200945`)
- 본인인증 또는 로그인이 필요하다는 점을 함께 밝힙니다. 링크만 걸어두면 눌러본 뒤에야 알게 됩니다.

`#dcIrpFields` 안에 두었으므로 DB형을 고르면 함께 사라집니다. DB형은 개인이 상품을 고르지 않아
사업자·상품 입력 자체가 없습니다.

`.lookup-note`는 2열 폼에서 `grid-column:1/-1`로 전체 폭을 차지해, 사업자·상품명 한 줄 바로
아래에 붙고 `투자 유형`은 새 줄에서 시작합니다. 외부로 나가는 유일한 링크라 밑줄을 남겨
눌리는 곳임을 분명히 하고, `target="_blank"`에는 `rel="noopener noreferrer"`를 함께 답니다.


## V24 2단계 스테퍼: 거짓 '완료'와 되감기 제거

2단계 대기 화면에는 타이머가 둘 있습니다. **대기 애니메이션**은 서버 응답을 기다리는 동안
단계를 추정해서 넘기고, **완료 처리**는 응답이 온 뒤 실제 트레이스로 확정합니다.
이 둘의 경계에서 문제가 두 개 있었습니다.

### 1. 마지막 단계가 끝나기도 전에 '완료'가 뜬다

대기 애니메이션은 900ms마다 앞 단계를 `done`으로 넘겼습니다. 9단계를 모두 지나면
마지막 tick이 `report`(RP)까지 `done`으로 찍어버려서, **약 9초 뒤부터는 서버가 아직
보고서를 만들고 있는데도** 카드 오른쪽 위에 `완료`가 떠 있었습니다. Qwen 모드는 수십 초가
걸리므로 사용자는 완료된 화면을 한참 바라보게 됩니다.

이 애니메이션은 응답을 기다리는 동안 도는 **추정**이라 마지막 단계의 완료를 선언할 근거가
없습니다. 지금은 앞 단계만 `done`으로 넘기고 마지막 단계는 `running`으로 남겨둔 뒤
타이머를 멈춥니다. 마지막 단계의 완료는 실제 응답이 도착한 `finishTrace()`만 찍습니다.

```
   기존   ... 07 RA 완료 → 08 CR 완료 → 09 RP 완료   (응답은 아직 안 옴)
   지금   ... 07 RA 완료 → 08 CR 완료 → 09 RP 진행 중 → (응답 도착) → 09 RP 완료
```

### 2. 완료 시 전체 레일이 한 번 되감겼다가 다시 돈다

기존 `replayTrace()`는 `resetNodes()`로 진행 상황을 **통째로 지운 뒤** 트레이스 전체를
180ms 간격으로 다시 걸어갔습니다. 대기 중에 이미 한 번 진행한 레일이 완료 직후 처음으로
되감겼다가 다시 도니까, 서버가 같은 파이프라인을 두 번 실행하는 것처럼 보였습니다.

`finishTrace()`는 되감지 않고 **대기 애니메이션이 멈춘 지점에서 이어서** 마무리합니다.
이미 `done`/`retry`로 확정된 단계는 건드리지 않고, 아직 열려 있는 단계만 서버 트레이스의
실제 결과로 닫습니다. 레일은 화면에 떠 있는 동안 정확히 한 번만 흘러갑니다.

| 실행 모드 | 응답 시점의 레일 | `finishTrace`가 하는 일 |
|---|---|---|
| Qwen (수십 초) | 이미 마지막 단계까지 진행 | 마지막 칸만 완료로 닫음 |
| demo (0.1초 미만) | 첫 단계에 머물러 있음 | 남은 단계를 120ms 간격으로 한 번 흘림 |

한 단계가 재시도로 두 번 실행되면 트레이스에 두 번 등장하므로, **마지막 항목**을 그 단계의
최종 상태로 씁니다. 레일에 없는 stage(`planner` 등)는 건너뜁니다.

### 확인한 경로

`fetch`를 지연시켜 Qwen 모드의 소요시간을 재현하고, 레일 상태를 `MutationObserver`로
빠짐없이 기록해 확인했습니다.

- 8초 지연: `R........` → `DR.......` → … → `DDDDDDDDR` → (응답) → `DDDDDDDDD`
  되감김(`.........`) 없음, `완료`는 마지막에만 등장
- 14초 지연: RP 도달 후 응답까지 6초 동안 계속 `09 RP 진행 중` 유지
- demo 모드(지연 없음): 첫 단계에서 이어받아 남은 8단계를 한 번만 통과
