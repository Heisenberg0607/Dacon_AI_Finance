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
  - 예상치는 **중앙값**. 한 번의 이상치(네트워크 지연 등)에 덜 흔들린다.
  - 버킷 분리: `운영유형(DB/DC/IRP) × 실행모드(qwen/demo)`
    DB는 상품 PDF 구조화 추출을 건너뛰고, demo fallback은 LLM 호출이 없어
    소요시간이 자릿수 단위로 다르다. 섞으면 중앙값이 무의미해진다.
- `GET /api/eta?operation_type=DC` — 이력이 없으면 `{"available": false}`
- 프런트 게이지 — AI 오브를 감싸는 링 + 남은 시간 + 진행 바 + 근거 문구

### 화면 상태

| 상태 | 조건 | 표시 |
| --- | --- | --- |
| 예상 남은 시간 | 실측 이력 있음, 예상치 이내 | `약 1분 8초` · 링/바가 경과 비율만큼 채워짐 |
| 예상 시간 초과 | 경과 > 예상치 | `마무리 중입니다` · 링/바가 노란색 진행중 애니메이션 |
| 예상 시간 산출 전 | 실측 이력 없음 (첫 분석) | `예상 시간 산출 전` · 진행중 애니메이션 |
| 분석 완료 | 응답 도착 | `실측 소요시간 N초` |

근거 문구에 `경과 41.0초 · 최근 8회 실측 중앙값 1분 40초 기준`처럼
표본 수와 기준값을 함께 노출해, 표시된 숫자가 어디서 왔는지 확인할 수 있게 했습니다.

예상치 조회는 `/api/analyze` 요청과 병렬로 나가므로 분석 시작을 지연시키지 않습니다.
조회에 실패해도 게이지는 비확정 상태로 계속 동작합니다.
