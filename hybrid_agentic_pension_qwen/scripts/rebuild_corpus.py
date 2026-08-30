from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path

import fitz

ROOT = Path(__file__).resolve().parents[1]
ZIP_PATH = ROOT / 'data' / 'source_documents.zip'
CATALOG_PATH = ROOT / 'data' / 'catalog.json'
CHUNKS_PATH = ROOT / 'data' / 'chunks.jsonl'

HASH_U = re.compile(r'#U([0-9a-fA-F]{4})')


def decode_hash_u(value: str) -> str:
    return HASH_U.sub(lambda m: chr(int(m.group(1), 16)), value)


def clean_text(text: str) -> str:
    text = text.replace('\x00', ' ')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def chunk_text(text: str, chunk_size: int = 1400, overlap: int = 180) -> list[str]:
    text = clean_text(text)
    if not text:
        return []
    if len(text) <= chunk_size:
        return [text]
    chunks = []
    start = 0
    while start < len(text):
        end = min(len(text), start + chunk_size)
        if end < len(text):
            candidates = [
                text.rfind('\n', start + 850, end),
                text.rfind('. ', start + 850, end),
                text.rfind('。', start + 850, end),
            ]
            cut = max(candidates)
            if cut > start + 700:
                end = cut + 1
        part = text[start:end].strip()
        if part:
            chunks.append(part)
        if end >= len(text):
            break
        start = max(start + 1, end - overlap)
    return chunks


def main():
    if not ZIP_PATH.exists():
        raise FileNotFoundError(f'Missing source zip: {ZIP_PATH}')

    catalog = []
    chunks = []

    with zipfile.ZipFile(ZIP_PATH) as z:
        for raw_name in z.namelist():
            if not raw_name.lower().endswith('.pdf'):
                continue
            decoded = decode_hash_u(raw_name)
            parts = decoded.split('/')
            provider = parts[-2]
            filename = parts[-1]
            pdf_bytes = z.read(raw_name)
            doc = fitz.open(stream=pdf_bytes, filetype='pdf')
            first_page = clean_text(doc[0].get_text('text')) if len(doc) else ''
            match = re.search(r'『([^』]{3,180})』', first_page)
            title = match.group(1).strip() if match else Path(filename).stem
            risk_type = next((x for x in ['안정형','안정투자형','중립투자형','적극투자형'] if x in filename), None)
            subtype = 'TDF' if '_TDF_' in filename else 'BF' if '_BF_' in filename else None
            file_id = f'{provider}/{filename}'

            catalog.append({
                'file_id': file_id,
                'provider': provider,
                'filename': filename,
                'title': title,
                'risk_type': risk_type,
                'subtype': subtype,
                'pages': len(doc),
            })

            for page_no, page in enumerate(doc, start=1):
                page_text = clean_text(page.get_text('text'))
                for chunk_index, part in enumerate(chunk_text(page_text)):
                    chunks.append({
                        'chunk_id': f'{len(chunks):05d}',
                        'file_id': file_id,
                        'provider': provider,
                        'filename': filename,
                        'title': title,
                        'risk_type': risk_type,
                        'subtype': subtype,
                        'page': page_no,
                        'chunk_index': chunk_index,
                        'text': part,
                    })

    CATALOG_PATH.write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding='utf-8')
    with CHUNKS_PATH.open('w', encoding='utf-8') as f:
        for chunk in chunks:
            f.write(json.dumps(chunk, ensure_ascii=False) + '\n')

    print(f'PDFs: {len(catalog)}')
    print(f'Chunks: {len(chunks)}')
    print(f'Wrote: {CATALOG_PATH}')
    print(f'Wrote: {CHUNKS_PATH}')


if __name__ == '__main__':
    main()
