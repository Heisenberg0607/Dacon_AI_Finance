from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.qwen_client import QwenGateway
from backend.config import CHUNKS_PATH, EMBEDDINGS_PATH, DATA_DIR


def main():
    qwen = QwenGateway()
    if not qwen.enabled:
        raise RuntimeError('DASHSCOPE_API_KEY가 없습니다. .env를 먼저 설정하세요.')

    chunks = []
    with CHUNKS_PATH.open('r', encoding='utf-8') as f:
        chunks = [json.loads(line) for line in f if line.strip()]

    vectors = []
    batch_size = 10
    for i in range(0, len(chunks), batch_size):
        batch = chunks[i:i+batch_size]
        texts = [
            f"사업자: {x['provider']}\n상품: {x['title']}\n투자유형: {x.get('risk_type')}\n페이지: {x['page']}\n본문: {x['text']}"
            for x in batch
        ]
        vectors.extend(qwen.embeddings(texts))
        print(f'Embedded {min(i+batch_size, len(chunks))}/{len(chunks)}')

    arr = np.asarray(vectors, dtype=np.float32)
    np.save(EMBEDDINGS_PATH, arr)
    meta = {
        'model': qwen.embedding_model,
        'dimensions': int(arr.shape[1]),
        'chunks': int(arr.shape[0]),
    }
    (DATA_DIR / 'embeddings_meta.json').write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'Saved {EMBEDDINGS_PATH} shape={arr.shape}')


if __name__ == '__main__':
    main()
