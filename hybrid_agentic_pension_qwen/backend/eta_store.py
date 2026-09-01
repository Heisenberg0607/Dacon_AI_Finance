from __future__ import annotations

import json
import statistics
import threading
from pathlib import Path
from typing import Any

from backend.config import DATA_DIR

TIMINGS_PATH = DATA_DIR / 'run_timings.json'


class RunTimeHistory:
    """분석 소요시간 실측 이력 저장소.

    2단계 대기 화면의 '예상 남은 시간'은 성격상 추정치지만, 그 근거는 서버가
    perf_counter로 실측한 과거 total_seconds뿐이다. 이력이 없으면 None을 돌려주고
    화면은 숫자 대신 비확정 상태를 보여준다. 프런트에서 임의 상수를 쓰지 않는다.

    운영유형별로 파이프라인이 다르므로(DB는 상품 PDF 구조화 추출을 건너뛴다)
    DB / DC / IRP 이력을 분리해서 쌓는다. Qwen 실행과 demo fallback은 소요시간이
    자릿수 단위로 다르므로 이 둘도 서로 다른 버킷에 넣는다. 섞이면 중앙값이 무의미해진다.
    """

    def __init__(self, path: Path = TIMINGS_PATH, max_samples: int = 30) -> None:
        self.path = path
        self.max_samples = max_samples
        self._lock = threading.Lock()
        self._samples: dict[str, list[float]] = self._load()

    def _load(self) -> dict[str, list[float]]:
        try:
            raw = json.loads(self.path.read_text(encoding='utf-8'))
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
        """해당 운영유형 + 실행모드의 예상 소요시간. 실측 이력이 없으면 None."""
        with self._lock:
            samples = list(self._samples.get(self._key(operation_type, qwen_enabled)) or [])
        if not samples:
            return None
        return {
            'operation_type': operation_type,
            # 중앙값은 한 번의 이상치(네트워크 지연 등)에 덜 흔들린다.
            'expected_seconds': round(statistics.median(samples), 2),
            'min_seconds': round(min(samples), 2),
            'max_seconds': round(max(samples), 2),
            'sample_size': len(samples),
        }
