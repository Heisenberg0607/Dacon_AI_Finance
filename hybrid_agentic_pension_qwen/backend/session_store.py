"""분석 결과 세션 저장소.

보고서 챗봇은 분석 당시의 context 전체(finance / monte_carlo / optimizer /
product_extraction / rag)가 필요하다. 질문마다 결과 전체를 브라우저와 왕복시키지 않도록
서버 메모리에 잠시 보관한다.

제약: 단일 프로세스 메모리 저장소다. `uvicorn app:app --reload` 데모 환경을 전제로 하며,
멀티 워커/멀티 인스턴스로 배포할 경우 파일 또는 Redis 백엔드로 교체해야 한다.
"""

from __future__ import annotations

import time
import uuid
from collections import OrderedDict
from typing import Any

from .models import UserPensionInput

DEFAULT_MAX_ENTRIES = 200
DEFAULT_TTL_SECONDS = 6 * 60 * 60


class AnalysisStore:
    def __init__(self, max_entries: int = DEFAULT_MAX_ENTRIES, ttl_seconds: int = DEFAULT_TTL_SECONDS):
        self.max_entries = max_entries
        self.ttl_seconds = ttl_seconds
        self._items: OrderedDict[str, dict[str, Any]] = OrderedDict()

    def _purge(self) -> None:
        now = time.time()
        expired = [k for k, v in self._items.items() if now - v['created_at'] > self.ttl_seconds]
        for k in expired:
            self._items.pop(k, None)
        while len(self._items) > self.max_entries:
            self._items.popitem(last=False)

    def put(self, user: UserPensionInput, result: dict[str, Any]) -> str:
        """원본 UserPensionInput 객체를 결과와 함께 보관한다.

        result['user']는 years_to_retirement 등 파생키가 섞여 있어(agents.py의 run 참고)
        그대로 재검증할 수 없다. what-if 재계산에서 model_copy가 필요하므로 원본을 따로 둔다.
        """
        analysis_id = uuid.uuid4().hex
        self._items[analysis_id] = {'user': user, 'result': result, 'created_at': time.time()}
        self._items.move_to_end(analysis_id)
        self._purge()
        return analysis_id

    def get(self, analysis_id: str | None) -> dict[str, Any] | None:
        if not analysis_id:
            return None
        self._purge()
        session = self._items.get(analysis_id)
        if session is None:
            return None
        self._items.move_to_end(analysis_id)
        return session
