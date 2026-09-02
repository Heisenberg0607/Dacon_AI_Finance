from __future__ import annotations

"""라이브 실측 이력을 저장소에 커밋되는 baseline으로 승격시킨다.

data/run_timings.json은 .gitignore 대상이라 이 PC 밖으로 나가지 않는다.
그래서 새로 클론한 환경은 항상 이력 0에서 시작하고, 첫 분석이 '예상 시간 산출 전'으로 뜬다.

.env에 API 키를 넣고 실제 분석을 몇 번 돌린 뒤 이 스크립트를 실행하면
그 실측값이 data/run_timings_baseline.json에 반영된다. 이 파일을 커밋하면
다른 PC에서도 첫 분석부터 남은 시간이 표시된다.

들어가는 숫자는 서버가 perf_counter로 실제 측정한 값뿐이다.
이 스크립트는 값을 만들어내지 않는다.

사용법:
    .venv/Scripts/python.exe scripts/save_timing_baseline.py
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.eta_store import BASELINE_PATH, TIMINGS_PATH

# baseline은 저장소에 커밋되므로 라이브 이력보다 적게 유지한다.
MAX_SAMPLES = 20


def read_json(path: Path) -> dict[str, list[float]]:
    try:
        raw = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, list[float]] = {}
    for key, values in raw.items():
        if not isinstance(values, list):
            continue
        nums = [float(v) for v in values if isinstance(v, (int, float)) and float(v) > 0]
        if nums:
            out[str(key)] = nums
    return out


def main() -> int:
    live = read_json(TIMINGS_PATH)
    if not live:
        print(f'라이브 이력이 비어 있습니다: {TIMINGS_PATH}')
        print('분석을 한 번 이상 실행한 뒤 다시 실행해주세요.')
        return 1

    baseline = read_json(BASELINE_PATH)
    print(f'{TIMINGS_PATH.relative_to(ROOT)} -> {BASELINE_PATH.relative_to(ROOT)}\n')

    for key in sorted(live):
        before = len(baseline.get(key, []))
        # 버킷 단위로 교체한다. 여러 번 실행해도 같은 측정값이 중복 누적되지 않는다.
        baseline[key] = live[key][-MAX_SAMPLES:]
        was = f'baseline {before}건' if before else 'baseline 없음'
        print(f'  {key:12} {len(baseline[key]):>3}건  ({was} -> {len(baseline[key])}건)')

    untouched = sorted(set(baseline) - set(live))
    for key in untouched:
        print(f'  {key:12} {len(baseline[key]):>3}건  (라이브 이력 없음, 유지)')

    BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
    BASELINE_PATH.write_text(
        json.dumps(baseline, ensure_ascii=False, indent=2, sort_keys=True) + '\n',
        encoding='utf-8',
    )
    print(f'\n저장 완료: {BASELINE_PATH.relative_to(ROOT)}')
    print('이 파일을 커밋하면 새로 클론한 환경에서도 첫 분석부터 예상 시간이 표시됩니다.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
