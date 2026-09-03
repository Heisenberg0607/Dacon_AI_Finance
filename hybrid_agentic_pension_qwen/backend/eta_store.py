from __future__ import annotations

import json
import math
import threading
from pathlib import Path
from typing import Any

from backend.config import DATA_DIR

TIMINGS_PATH = DATA_DIR / 'run_timings.json'
# baseline은 저장소에 함께 커밋되는 읽기 전용 실측 이력이다.
# 새로 클론한 환경에서도 첫 분석부터 남은 시간을 추정할 수 있게 해준다.
BASELINE_PATH = DATA_DIR / 'run_timings_baseline.json'

# 보고서 생성 예상시간을 실측 분포 대신 정해둔 한 값으로 고정한다.
#
# None이면 아래 근거 사다리(measured -> baseline -> related)가 그대로 동작한다.
# 숫자를 넣으면 운영유형·실행모드와 무관하게 항상 그 값을 예상시간으로 쓴다.
#
# 지금 값은 실측이 아니라 제품 결정이다. 근거로 댈 표본이 없으므로 화면은 '실측 N회 중
# 80%가 이내 완료' 같은 근거 문구를 아예 띄우지 않고 경과 시간만 보여준다.
# 실제 소요시간과 얼마나 맞는지는 scripts/measure_qwen_eta.py로 확인할 수 있다.
FIXED_ESTIMATE_SECONDS: float | None = 150.0  # 2분 30초

# 남은 시간은 백분위수로 잡는다. 중앙값을 쓰면 정의상 과거 실행의 절반이 그 값을 넘어
# '예상 시간 초과' 상태가 상시로 뜬다. 80분위수는 표본의 80%가 그 안에 끝났다는 뜻이라
# 초과가 5회에 1번꼴로 줄고, 임의의 안전계수를 곱하지 않아 근거가 실측 표본 안에 그대로 남는다.
PERCENTILE = 80

# 같은 실행모드 안에서 서로 참고할 수 있는 운영유형 순서.
# DC/IRP는 상품 PDF 구조화 추출을 포함해 파이프라인이 같고,
# DB는 그 단계를 건너뛰어 더 빠르므로 뒤에 둔다.
RELATED_TYPES: dict[str, tuple[str, ...]] = {
    'DC': ('IRP', 'DB'),
    'IRP': ('DC', 'DB'),
    'DB': ('DC', 'IRP'),
}


class RunTimeHistory:
    """분석 소요시간 실측 이력 저장소.

    2단계 대기 화면의 '예상 남은 시간'은 성격상 추정치지만, 그 근거는 서버가
    perf_counter로 실측한 과거 total_seconds뿐이다. 이력이 없으면 None을 돌려주고
    화면은 숫자 대신 비확정 상태를 보여준다. 프런트에서 임의 상수를 쓰지 않는다.

    운영유형별로 파이프라인이 다르므로(DB는 상품 PDF 구조화 추출을 건너뛴다)
    DB / DC / IRP 이력을 분리해서 쌓는다. Qwen 실행과 demo fallback은 소요시간이
    자릿수 단위로 다르므로 이 둘도 서로 다른 버킷에 넣는다. 섞이면 중앙값이 무의미해진다.

    이력이 비어 있을 때를 대비해 근거를 아래 순서로 찾는다. 어느 단계에서 나온 값인지는
    결과의 source에 담아 화면이 근거를 그대로 밝힐 수 있게 한다.

      0. fixed     - FIXED_ESTIMATE_SECONDS가 설정돼 있으면 항상 이것
      1. measured  - 이 PC의 라이브 이력, 정확히 같은 버킷
      2. baseline  - 저장소에 커밋된 실측 baseline, 정확히 같은 버킷
      3. related   - 같은 실행모드의 다른 운영유형 (라이브 -> baseline 순)

    fixed는 백분위수가 아니라 '정해둔 한 값'이라 percentile을 None으로 돌려준다.
    표본에서 나온 값이 아니므로 '표본의 N%가 이내 완료'라고 적으면 거짓말이 되고,
    대신 적을 근거도 없다. 그래서 화면은 근거 문구 없이 경과 시간만 보여준다.

    실행모드 경계는 넘지 않는다. demo(0.1초대)와 qwen(수십 초)을 섞으면 추정이 무의미해진다.
    """

    def __init__(
        self,
        path: Path = TIMINGS_PATH,
        max_samples: int = 30,
        baseline_path: Path = BASELINE_PATH,
        fixed_seconds: float | None = FIXED_ESTIMATE_SECONDS,
    ) -> None:
        self.path = path
        self.baseline_path = baseline_path
        self.max_samples = max_samples
        self._lock = threading.Lock()
        self._samples: dict[str, list[float]] = self._read(self.path)
        # baseline은 서버가 절대 쓰지 않는다. 기동 시 한 번만 읽는다.
        self._baseline: dict[str, list[float]] = self._read(self.baseline_path)
        self._fixed_seconds = fixed_seconds if (fixed_seconds or 0) > 0 else None

    def _read(self, path: Path) -> dict[str, list[float]]:
        try:
            raw = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return {}
        if not isinstance(raw, dict):
            return {}
        cleaned: dict[str, list[float]] = {}
        for key, values in raw.items():
            if not isinstance(values, list):
                continue
            nums = [float(v) for v in values if isinstance(v, (int, float)) and float(v) > 0]
            if nums:
                cleaned[str(key)] = nums[-self.max_samples:]
        return cleaned

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self._samples, ensure_ascii=False), encoding='utf-8')
        except OSError:
            # 이력 저장 실패가 분석 응답을 막지 않는다. 다음 실행에서 다시 시도한다.
            pass

    @staticmethod
    def _key(operation_type: str, qwen_enabled: bool) -> str:
        return f"{operation_type}|{'qwen' if qwen_enabled else 'demo'}"

    def record(self, operation_type: str, seconds: float | None, qwen_enabled: bool) -> None:
        """방금 끝난 분석의 실측 소요시간을 이력에 추가한다."""
        if seconds is None:
            return
        try:
            value = float(seconds)
        except (TypeError, ValueError):
            return
        if value <= 0:
            return
        with self._lock:
            bucket = self._samples.setdefault(self._key(operation_type, qwen_enabled), [])
            bucket.append(round(value, 2))
            del bucket[:-self.max_samples]
            self._save()

    def estimate(self, operation_type: str, qwen_enabled: bool) -> dict[str, Any] | None:
        """예상 소요시간과 그 근거. 어디에도 실측값이 없으면 None."""
        with self._lock:
            live = dict(self._samples)
        baseline = self._baseline

        exact = self._key(operation_type, qwen_enabled)

        # 고정값이 설정돼 있으면 그것을 쓴다. 실측 이력이 쌓여도 흔들리지 않는다.
        if self._fixed_seconds is not None:
            return {
                'operation_type': operation_type,
                'source': 'fixed',
                'percentile': None,
                'expected_seconds': round(self._fixed_seconds, 2),
                'min_seconds': None,
                'max_seconds': None,
                'sample_size': None,
            }

        for store, source in ((live, 'measured'), (baseline, 'baseline')):
            samples = store.get(exact)
            if samples:
                return self._summarize(samples, operation_type, source)

        # 같은 실행모드의 다른 운영유형으로 넘어간다. 근거가 된 유형을 함께 알린다.
        for related in RELATED_TYPES.get(operation_type, ()):
            key = self._key(related, qwen_enabled)
            for store in (live, baseline):
                samples = store.get(key)
                if samples:
                    result = self._summarize(samples, operation_type, 'related')
                    result['basis_operation_type'] = related
                    return result
        return None

    @staticmethod
    def _percentile(samples: list[float], p: int) -> float:
        """nearest-rank 백분위수. 표본의 p%가 이 값 이하다.

        보간하지 않고 정렬된 표본에서 직접 고르므로 표본이 1건이어도 동작하고,
        화면에 쓰는 '최근 N회 중 80%가 이 시간 안에 완료' 설명이 문자 그대로 참이 된다.
        """
        ordered = sorted(samples)
        rank = math.ceil(p / 100 * len(ordered))
        return ordered[max(0, rank - 1)]

    @classmethod
    def _summarize(cls, samples: list[float], operation_type: str, source: str) -> dict[str, Any]:
        return {
            'operation_type': operation_type,
            'source': source,
            'percentile': PERCENTILE,
            'expected_seconds': round(cls._percentile(samples, PERCENTILE), 2),
            'min_seconds': round(min(samples), 2),
            'max_seconds': round(max(samples), 2),
            'sample_size': len(samples),
        }
