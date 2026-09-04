from __future__ import annotations

"""Qwen 모드 보고서 생성 소요시간을 N회 실측해 보고한다.

화면에 쓰는 예상시간은 backend/eta_store.py의 FIXED_ESTIMATE_SECONDS로 고정돼 있다.
이 스크립트는 그 고정값이 실제와 얼마나 맞는지 확인하는 용도이며, 서비스가 읽는 값을
직접 바꾸지 않는다. 결과를 보고 상수를 조정할지는 사람이 정한다.

왜 별도 스크립트인가
--------------------
2단계 대기 게이지의 '예상 남은 시간'은 근거가 실측값뿐이다(backend/eta_store.py).
그런데 demo 모드는 0.1초 미만, Qwen 모드는 수십 초라 자릿수가 다르므로,
Qwen 모드 숫자는 Qwen 모드에서 직접 재는 수밖에 없다.

이 스크립트는 값을 만들어내지 않는다. API 키가 없으면 아무것도 하지 않고 종료한다.

무엇을 재는가
-------------
서버의 /api/analyze 핸들러 함수를 그대로 호출한다. HTTP를 태우지 않을 뿐
workflow.run + analysis_store.put + run_times.record까지 서버와 완전히 같은 경로다.
브라우저 게이지와의 차이는 localhost 왕복(수 ms)뿐이라 무시할 수 있다.

핸들러가 스스로 run_times.record()를 부르므로 실측값은 data/run_timings.json에도
자동으로 쌓인다. 즉 이 스크립트를 돌린 뒤 save_timing_baseline.py를 실행하면
baseline 승격까지 이어진다.

사용법:
    .venv/Scripts/python.exe scripts/measure_qwen_eta.py                # DC 10회
    .venv/Scripts/python.exe scripts/measure_qwen_eta.py --runs 5
    .venv/Scripts/python.exe scripts/measure_qwen_eta.py --operation-type IRP
"""

import argparse
import json
import math
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.eta_store import FIXED_ESTIMATE_SECONDS, PERCENTILE
from backend.models import UserPensionInput

# 측정용 표준 입력. 매 회차 같은 값을 써야 회차 간 차이가 '모델 응답시간'만 남는다.
# 상품은 카탈로그에서 실제로 존재하는 것을 골라 채운다(아래 pick_product).
BASE_USER = {
    'age': 32,
    'retirement_age': 60,
    'annual_income': 5000,
    'desired_monthly_income': 250,
    'current_savings': 3500,
    'annual_contribution': 360,
    'investment_type': '중립투자형',
}


def pick_product(rag, operation_type: str) -> dict[str, str]:
    if operation_type == 'DB':
        return {}
    for item in rag.catalog:
        if item.get('provider') and item.get('title'):
            return {'provider': item['provider'], 'product_name': item['title']}
    raise SystemExit('카탈로그에 상품이 없습니다. scripts/rebuild_corpus.py를 먼저 실행하세요.')


def percentile(samples: list[float], p: int) -> float:
    ordered = sorted(samples)
    rank = math.ceil(p / 100 * len(ordered))
    return ordered[max(0, rank - 1)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--runs', type=int, default=10, help='측정 횟수 (기본 10)')
    ap.add_argument('--operation-type', default='DC', choices=['DC', 'IRP', 'DB'])
    args = ap.parse_args()

    # app을 import하는 순간 QwenGateway/RAG/워크플로가 서버와 동일하게 구성된다.
    import app as server

    if not server.qwen.enabled:
        print('중단: Qwen이 비활성 상태입니다 (demo 모드).')
        print()
        print('  .env의 DASHSCOPE_API_KEY가 비어 있으면 워크플로가 fallback으로 돌아')
        print('  0.1초 미만에 끝납니다. 그 숫자는 Qwen 모드 예상시간이 아닙니다.')
        print('  키를 넣은 뒤 다시 실행해주세요.')
        return 1

    payload = dict(BASE_USER)
    payload['operation_type'] = args.operation_type
    if args.operation_type == 'DB':
        payload.update({'current_tenure_years': 3, 'company_size': '중견기업'})
        for k in ('current_savings', 'annual_contribution', 'investment_type'):
            payload.pop(k, None)
    else:
        payload.update(pick_product(server.rag, args.operation_type))
    user = UserPensionInput.model_validate(payload)

    print(f'모델 {server.qwen.model} · {args.operation_type} · {args.runs}회 측정')
    print(f'상품 {user.product_name or "(DB형 해당 없음)"}')
    print()

    samples: list[float] = []
    failures = 0
    for i in range(1, args.runs + 1):
        started = time.perf_counter()
        try:
            server.analyze(user)          # 서버 핸들러와 같은 경로. record()도 여기서 일어난다.
        except Exception as exc:          # noqa: BLE001 - 회차 실패가 전체 측정을 끝내지 않게 한다
            failures += 1
            print(f'  {i:>2}회  실패: {type(exc).__name__}: {exc}')
            continue
        took = time.perf_counter() - started
        samples.append(took)
        print(f'  {i:>2}회  {took:>7.2f}초')

    print()
    if not samples:
        print('성공한 측정이 없습니다. 저장하지 않습니다.')
        return 1
    if failures:
        print(f'주의: {failures}회 실패했습니다. 성공한 {len(samples)}회만 집계합니다.')

    mean = statistics.fmean(samples)
    median = statistics.median(samples)
    p80 = percentile(samples, PERCENTILE)
    print(f'  표본 {len(samples)}회 · 최소 {min(samples):.2f}초 · 최대 {max(samples):.2f}초')
    print(f'  평균 {mean:.2f}초 · 중앙값 {median:.2f}초 · {PERCENTILE}분위수 {p80:.2f}초')

    if FIXED_ESTIMATE_SECONDS:
        over = sum(1 for x in samples if x > FIXED_ESTIMATE_SECONDS)
        print()
        print(f'  화면에 쓰는 고정 예상시간: {FIXED_ESTIMATE_SECONDS:.0f}초')
        print(f'  이 값을 넘긴 회차: {over}/{len(samples)}회')
        print('  차이가 크면 backend/eta_store.py의 FIXED_ESTIMATE_SECONDS를 조정하세요.')
    else:
        print()
        print('  FIXED_ESTIMATE_SECONDS가 None이라 화면은 실측 백분위수를 씁니다.')
        print('  실측 표본을 저장소에 남기려면: scripts/save_timing_baseline.py')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
