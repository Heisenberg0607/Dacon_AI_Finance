"""보고서 화면의 '원문 PDF 다운로드'가 쓰는 원본 문서 접근.

원본 PDF는 data/source_documents.zip 안에 배포처가 준 이름 그대로 들어 있고, 카탈로그는
정규화된 이름을 쓴다. 둘을 이어주는 data/source_pdf_map.json은
scripts/build_source_pdf_map.py가 PDF 본문 대조로 미리 만들어 둔다.

보안상 중요한 점: 클라이언트에게서 경로를 받지 않는다. file_id는 이 map의 키로만 쓰고
실제로 열 zip 항목명은 map이 준다. map에 없는 값은 그대로 거절하므로 경로 조작이 성립할
여지가 없다.

zip 핸들은 요청마다 새로 연다. zipfile.ZipFile은 동시 읽기에 안전하지 않은데 SSE 분석이
별도 스레드에서 도는 서버라 공유 핸들을 두면 언젠가 깨진다. zip은 중앙 디렉터리를 갖고
있어 한 항목만 꺼내는 비용은 작다.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any

from .config import SOURCE_PDF_MAP_PATH, SOURCE_ZIP_PATH


class SourceDocumentStore:
    def __init__(self, map_path: Path = SOURCE_PDF_MAP_PATH, zip_path: Path = SOURCE_ZIP_PATH):
        self.zip_path = zip_path
        self.documents: dict[str, dict[str, Any]] = {}
        if map_path.exists() and zip_path.exists():
            try:
                payload = json.loads(map_path.read_text(encoding='utf-8'))
                self.documents = payload.get('documents') or {}
            except Exception:
                # map이 깨졌다고 서버가 못 뜨면 안 된다. 다운로드 기능만 꺼진다.
                self.documents = {}

    @property
    def enabled(self) -> bool:
        return bool(self.documents)

    def has(self, file_id: str | None) -> bool:
        return bool(file_id) and file_id in self.documents

    def read(self, file_id: str | None) -> tuple[str, bytes] | None:
        """(내려받을 파일명, PDF 바이트). 모르는 file_id면 None."""
        entry = self.documents.get(file_id or '')
        if not entry:
            return None
        try:
            with zipfile.ZipFile(self.zip_path) as archive:
                data = archive.read(entry['zip_name'])
        except (KeyError, OSError, zipfile.BadZipFile):
            # map은 있는데 zip이 바뀐 경우. 없는 문서로 취급한다.
            return None
        return entry.get('download_name') or 'document.pdf', data
