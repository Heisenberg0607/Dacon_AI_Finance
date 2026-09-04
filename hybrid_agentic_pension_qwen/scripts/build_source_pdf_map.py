"""catalog.json의 각 상품을 source_documents.zip 안의 원본 PDF와 짝지어 map을 만든다.

보고서 화면의 '원문 PDF 다운로드'가 쓰는 데이터다. 왜 매핑이 따로 필요한가:

  catalog.json  file_id  '미래에셋증권/미래에셋증권_안정투자형_01_상품설명서.pdf'
  zip 안의 이름          '해커톤 데이터 모음/미래에셋증권/미래에셋증권_디폴트옵션_안정투자형_포트폴리오_1_상품설명서.pdf'

카탈로그는 정규화된 이름을, zip은 배포처의 원본 이름을 쓴다. 이름만 보고 규칙으로 맞추면
'안정투자형_01'이 '안정형'에 붙는 식으로 조용히 틀린 PDF를 내려보낼 수 있다. 사용자가 받는
파일이 달라지는 문제라 추측으로 두지 않고, 가능한 곳은 PDF 본문으로 대조해 확정한다.

두 단계로 짝짓는다.

  1) 본문 대조 - chunks.jsonl에 저장된 그 file_id의 텍스트와 zip PDF에서 새로 추출한 텍스트를
     6-gram Jaccard로 비교해 사업자 안에서 1:1로 배정한다. 대부분 0.99 이상으로 붙는다.
  2) 이름 규칙 - 1)에서 남은 것만, 같은 사업자의 아직 안 쓰인 zip 항목 중에서
     (위험유형, 서브타입, 번호, 문서종류)가 유일하게 일치하는 것으로 채운다.

     미래에셋증권 14건이 여기로 온다. 배포된 chunks.jsonl의 해당 문서 텍스트가 거의 비어 있어
     (5페이지 문서의 1페이지가 '3 등급 (다소높은위험)...' 한 줄) 본문 신호가 없다.
     이 사업자는 카탈로그 14건과 zip 14건이 (위험유형, 서브타입, 번호, 종류)로 완전한 1:1이라
     제약 안에서는 규칙이 유일해를 준다.

어느 한 건이라도 못 채우거나 zip 항목이 두 번 쓰이면 아무것도 쓰지 않고 실패한다.
결과에는 method와 score를 함께 남겨 나중에 어떤 근거로 붙은 짝인지 확인할 수 있게 한다.

실행: python scripts/build_source_pdf_map.py
"""

from __future__ import annotations

import json
import re
import sys
import zipfile
from collections import defaultdict
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.config import CATALOG_PATH, CHUNKS_PATH, SOURCE_PDF_MAP_PATH, SOURCE_ZIP_PATH

# zip 항목명이 #U한글 형태로 인코딩된 경우가 있다. rebuild_corpus.py와 같은 규칙으로 되돌린다.
HASH_U = re.compile(r'#U([0-9a-fA-F]{4})')

RISK_TYPES = ('안정투자형', '안정형', '중립투자형', '적극투자형')
# 긴 것부터 본다. '상품설명서'가 '설명서'로 먼저 잡히면 종류가 뭉개진다.
DOC_KINDS = ('상품설명서', '핵심설명서', '상품안내서', '안내서', '설명서')

CONTENT_MIN_SCORE = 0.5
GRAM = 6


def decode_name(value: str) -> str:
    return HASH_U.sub(lambda m: chr(int(m.group(1), 16)), value)


def normalize(text: str) -> str:
    return re.sub(r'[^가-힣a-z0-9]+', '', (text or '').lower())


def grams(text: str) -> set[str]:
    t = normalize(text)
    return {t[i:i + GRAM] for i in range(max(0, len(t) - GRAM + 1))}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def name_key(filename: str) -> tuple:
    """파일명에서 (위험유형, 서브타입, 번호, 문서종류)를 뽑는다.

    사업자마다 표기가 달라 전역 매칭에는 쓸 수 없다. 같은 사업자의 남은 후보끼리
    가릴 때만 쓴다.
    """
    squashed = filename.replace(' ', '')
    risk = next((r for r in RISK_TYPES if r in squashed), None)
    if 'TDF' in squashed.upper():
        subtype = 'TDF'
    elif re.search(r'(?<![A-Za-z])BF(?![A-Za-z])', squashed, re.I):
        subtype = 'BF'
    else:
        subtype = None
    kind = next((k for k in DOC_KINDS if k in squashed), None)
    stem = re.sub(r'\.pdf$', '', filename, flags=re.I)
    numbers = re.findall(r'(?<!\d)(\d{1,2})(?!\d)', stem)
    index = int(numbers[-1]) if numbers else None
    return risk, subtype, index, kind


def load_catalog_text() -> dict[str, set[str]]:
    """file_id별로 배포된 chunks.jsonl 텍스트를 모아 gram 집합으로 만든다."""
    texts: dict[str, list[str]] = defaultdict(list)
    with CHUNKS_PATH.open(encoding='utf-8') as f:
        for line in f:
            if not line.strip():
                continue
            chunk = json.loads(line)
            texts[chunk['file_id']].append(chunk.get('text') or '')
    return {file_id: grams(' '.join(parts)) for file_id, parts in texts.items()}


def load_zip_documents() -> list[dict]:
    docs = []
    with zipfile.ZipFile(SOURCE_ZIP_PATH) as archive:
        for raw_name in archive.namelist():
            if not raw_name.lower().endswith('.pdf'):
                continue
            decoded = decode_name(raw_name)
            parts = decoded.split('/')
            with pymupdf.open(stream=archive.read(raw_name), filetype='pdf') as doc:
                text = '\n'.join(page.get_text('text') for page in doc)
                pages = len(doc)
            docs.append({
                'raw_name': raw_name,
                'filename': parts[-1],
                'provider': parts[-2] if len(parts) > 1 else '',
                'pages': pages,
                'grams': grams(text),
            })
    return docs


def main() -> int:
    for path in (CATALOG_PATH, CHUNKS_PATH, SOURCE_ZIP_PATH):
        if not path.exists():
            print(f'없는 파일: {path}')
            return 1

    catalog = json.loads(CATALOG_PATH.read_text(encoding='utf-8'))
    catalog_grams = load_catalog_text()
    zip_docs = load_zip_documents()
    print(f'카탈로그 {len(catalog)}건 / zip PDF {len(zip_docs)}건')

    zip_by_provider: dict[str, list[dict]] = defaultdict(list)
    for doc in zip_docs:
        zip_by_provider[doc['provider']].append(doc)

    mapping: dict[str, dict] = {}
    claimed: set[str] = set()

    # 1) 본문 대조. 점수 높은 쌍부터 확정해 사업자 안에서 1:1이 되게 한다.
    pairs = []
    for entry in catalog:
        source = catalog_grams.get(entry['file_id'], set())
        for doc in zip_by_provider.get(entry['provider'], []):
            score = jaccard(source, doc['grams'])
            if score >= CONTENT_MIN_SCORE:
                pairs.append((score, entry['file_id'], doc))
    pairs.sort(key=lambda x: -x[0])
    for score, file_id, doc in pairs:
        if file_id in mapping or doc['raw_name'] in claimed:
            continue
        mapping[file_id] = {
            'zip_name': doc['raw_name'],
            'download_name': doc['filename'],
            'method': 'content',
            'score': round(score, 4),
        }
        claimed.add(doc['raw_name'])
    print(f'본문 대조로 확정: {len(mapping)}건')

    # 2) 남은 것만 이름 규칙으로. 같은 사업자의 미사용 항목 중 유일 일치일 때만 받는다.
    #
    # 두 번 훑는다. 먼저 (위험유형, 서브타입, 번호, 종류)가 그대로 맞는 것,
    # 그 다음 번호를 뺀 키로 좁힌다. 종류가 하나뿐이면 배포처가 번호를 안 붙이는데
    # (미래에셋증권_디폴트옵션_안정형_포트폴리오_상품설명서.pdf) 카탈로그는 _01을 붙여둔다.
    # 번호를 빼도 후보가 하나로 남을 때만 받으므로 느슨해지지 않는다.
    before_name_pass = len(mapping)
    for keyed in (lambda k: k, lambda k: (k[0], k[1], k[3])):
        for entry in [e for e in catalog if e['file_id'] not in mapping]:
            want = keyed(name_key(entry['filename']))
            candidates = [
                d for d in zip_by_provider.get(entry['provider'], [])
                if d['raw_name'] not in claimed and keyed(name_key(d['filename'])) == want
            ]
            if len(candidates) == 1:
                doc = candidates[0]
                mapping[entry['file_id']] = {
                    'zip_name': doc['raw_name'],
                    'download_name': doc['filename'],
                    'method': 'name',
                    'score': None,
                }
                claimed.add(doc['raw_name'])
    print(f'이름 규칙으로 추가: {len(mapping) - before_name_pass}건')

    # 검증. 하나라도 어긋나면 파일을 쓰지 않는다.
    unresolved = [e['file_id'] for e in catalog if e['file_id'] not in mapping]
    used = [v['zip_name'] for v in mapping.values()]
    duplicated = sorted({n for n in used if used.count(n) > 1})
    if unresolved or duplicated:
        print()
        print('중단 - map을 쓰지 않았다.')
        for file_id in unresolved:
            print(f'  짝을 못 찾음: {file_id}')
        for name in duplicated:
            print(f'  zip 항목 중복 사용: {decode_name(name)}')
        return 1

    # 페이지 수가 맞는지 한 번 더 본다. 어긋나면 다른 문서를 붙였다는 신호다.
    zip_pages = {d['raw_name']: d['pages'] for d in zip_docs}
    page_mismatch = [
        (e['file_id'], e['pages'], zip_pages[mapping[e['file_id']]['zip_name']])
        for e in catalog
        if e.get('pages') and zip_pages[mapping[e['file_id']]['zip_name']] != e['pages']
    ]

    payload = {
        'source_zip': SOURCE_ZIP_PATH.name,
        'documents': {file_id: mapping[file_id] for file_id in (e['file_id'] for e in catalog)},
    }
    SOURCE_PDF_MAP_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8'
    )

    by_method = defaultdict(int)
    for value in mapping.values():
        by_method[value['method']] += 1
    print()
    print(f'매핑 {len(mapping)}건 (본문 {by_method["content"]} / 이름 {by_method["name"]})')
    print(f'페이지 수 불일치: {len(page_mismatch)}건')
    for file_id, want, got in page_mismatch[:10]:
        print(f'  {file_id}: 카탈로그 {want}p vs zip {got}p')
    print(f'Wrote: {SOURCE_PDF_MAP_PATH}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
