from __future__ import annotations

import json
import math
import re
from collections import Counter
from typing import Any

import numpy as np

from .config import CATALOG_PATH, CHUNKS_PATH, EMBEDDINGS_PATH
from .qwen_client import QwenGateway

TOKEN_RE = re.compile(r'[가-힣A-Za-z0-9]+')


def tokenize(text: str) -> list[str]:
    return [t.lower() for t in TOKEN_RE.findall(text or '') if len(t) > 1]


def _norm_title(text: str | None) -> str:
    return re.sub(r'[^가-힣a-z0-9]+', '', (text or '').lower())


class PensionRAG:
    def __init__(self, qwen: QwenGateway):
        self.qwen = qwen
        self.catalog = json.loads(CATALOG_PATH.read_text(encoding='utf-8')) if CATALOG_PATH.exists() else []
        self.chunks: list[dict[str, Any]] = []
        if CHUNKS_PATH.exists():
            with CHUNKS_PATH.open('r', encoding='utf-8') as f:
                self.chunks = [json.loads(line) for line in f if line.strip()]

        self.doc_tokens: list[set[str]] = []
        df = Counter()
        for c in self.chunks:
            toks = set(tokenize(c['text'] + ' ' + c.get('title', '') + ' ' + c.get('filename', '')))
            self.doc_tokens.append(toks)
            df.update(toks)
        n = max(1, len(self.chunks))
        self.idf = {t: math.log((n + 1) / (freq + 1)) + 1 for t, freq in df.items()}

        self.embeddings = None
        if EMBEDDINGS_PATH.exists():
            arr = np.load(EMBEDDINGS_PATH)
            if len(arr) == len(self.chunks):
                norms = np.linalg.norm(arr, axis=1, keepdims=True) + 1e-12
                self.embeddings = arr / norms

    @property
    def mode(self) -> str:
        if self.embeddings is not None and self.qwen.enabled:
            return 'Qwen semantic + exact-product metadata RAG'
        return 'exact-product metadata + lexical RAG'

    def providers(self) -> list[str]:
        return sorted({x.get('provider', '') for x in self.catalog if x.get('provider')})

    def products_for_provider(self, provider: str) -> list[dict[str, Any]]:
        out = [x for x in self.catalog if x.get('provider') == provider]
        seen = set()
        dedup = []
        for x in out:
            key = (x.get('title'), x.get('risk_type'), x.get('subtype'))
            if key in seen:
                continue
            seen.add(key)
            dedup.append(x)
        return sorted(dedup, key=lambda x: (x.get('risk_type') or '', x.get('title') or ''))

    def resolve_product(self, provider: str | None, product_name: str | None) -> dict[str, Any] | None:
        if not product_name:
            return None
        candidates = [x for x in self.catalog if not provider or x.get('provider') == provider]
        # 1) 화면에서 전달되는 title exact match
        for x in candidates:
            if x.get('title') == product_name:
                return x
        # 2) 파일명 stem exact match
        for x in candidates:
            if re.sub(r'\.pdf$', '', x.get('filename', ''), flags=re.I) == product_name:
                return x
        # 3) 공백/기호 차이만 허용한 normalized exact match
        target = _norm_title(product_name)
        matches = [x for x in candidates if _norm_title(x.get('title')) == target or _norm_title(re.sub(r'\.pdf$', '', x.get('filename', ''), flags=re.I)) == target]
        return matches[0] if len(matches) == 1 else None

    def exact_product_document(self, provider: str | None, product_name: str | None, max_chars: int = 52000) -> dict[str, Any]:
        product = self.resolve_product(provider, product_name)
        if not product:
            return {'product': None, 'pages': [], 'text': '', 'matched_exactly': False}
        file_id = product['file_id']
        rows = [c for c in self.chunks if c.get('file_id') == file_id]
        rows.sort(key=lambda c: (int(c.get('page', 0)), int(c.get('chunk_index', 0))))
        pages_map: dict[int, list[str]] = {}
        for c in rows:
            pages_map.setdefault(int(c.get('page', 0)), []).append(c.get('text', ''))
        pages = []
        used = 0
        for page, texts in sorted(pages_map.items()):
            text = '\n'.join(texts).strip()
            if used + len(text) > max_chars:
                text = text[:max(0, max_chars - used)]
            if text:
                pages.append({'page': page, 'text': text})
                used += len(text)
            if used >= max_chars:
                break
        return {
            'product': product,
            'pages': pages,
            'text': '\n\n'.join(f"[PDF p.{p['page']}]\n{p['text']}" for p in pages),
            'matched_exactly': True,
            'chunk_count': len(rows),
        }

    def _semantic_scores(self, query: str) -> np.ndarray | None:
        if self.embeddings is None or not self.qwen.enabled:
            return None
        try:
            q = np.asarray(self.qwen.embeddings([query])[0], dtype=np.float32)
            q = q / (np.linalg.norm(q) + 1e-12)
            return self.embeddings @ q
        except Exception:
            return None

    def search(
        self,
        query: str,
        provider: str | None = None,
        product_name: str | None = None,
        risk_type: str | None = None,
        top_k: int = 6,
        scope: str = 'selected',
    ) -> dict[str, Any]:
        """scope='selected'는 선택 상품 PDF 안에서만, scope='all'은 전체 코퍼스에서 검색한다.

        분석 파이프라인(agents.py)은 기본값 'selected'를 그대로 사용해 기존 동작을 유지하고,
        보고서 챗봇만 'all'로 전체 상품 DB를 열어 비교 질문에 답한다.
        """
        scope = scope if scope in {'selected', 'all'} else 'selected'
        if not self.chunks:
            return {'mode': self.mode, 'query': query, 'search_scope': scope, 'results': []}

        resolved = self.resolve_product(provider, product_name) if product_name else None
        selected_file_id = resolved.get('file_id') if resolved else None
        exact_file_id = selected_file_id if scope == 'selected' else None
        q_tokens = set(tokenize(query))
        semantic = self._semantic_scores(query)
        scored = []

        for i, c in enumerate(self.chunks):
            # scope='selected'에서 상품이 특정되면 전체 DB를 뒤지지 않고 그 공식 PDF 내부에서만 검색한다.
            if exact_file_id and c.get('file_id') != exact_file_id:
                continue
            if scope == 'selected' and provider and not exact_file_id and c.get('provider') != provider:
                continue

            toks = self.doc_tokens[i]
            overlap = q_tokens & toks
            lexical = sum(self.idf.get(t, 1.0) for t in overlap) / max(2.5, math.sqrt(len(toks) + 1))
            score = lexical
            if semantic is not None:
                score = float(semantic[i]) * 1.6 + lexical * 0.55
            if risk_type and c.get('risk_type') == risk_type:
                score += 0.2
            # 전체 코퍼스를 열더라도 사용자 본인이 가입한 상품 근거가 상위에 남도록 소폭 가산한다.
            if scope == 'all' and selected_file_id and c.get('file_id') == selected_file_id:
                score += 0.15
            scored.append((score, i))

        scored.sort(reverse=True)
        results = []
        seen = set()
        for score, i in scored:
            c = self.chunks[i]
            key = (c['file_id'], c['page'], c['chunk_index'])
            if key in seen:
                continue
            seen.add(key)
            text = c['text'].strip()
            snippet = text[:1100] + ('…' if len(text) > 1100 else '')
            results.append({
                'evidence_id': f'E{len(results)+1}',
                'score': round(float(score), 4),
                'file_id': c.get('file_id'),
                'provider': c.get('provider'),
                'title': c.get('title'),
                'filename': c.get('filename'),
                'risk_type': c.get('risk_type'),
                'subtype': c.get('subtype'),
                'page': c.get('page'),
                'snippet': snippet,
            })
            if len(results) >= max(1, min(top_k, 10)):
                break

        return {
            'mode': self.mode,
            'query': query,
            'search_scope': scope,
            'resolved_product': resolved,
            'selected_file_id': selected_file_id,
            'exact_product_filter': bool(exact_file_id),
            'results': results,
        }
