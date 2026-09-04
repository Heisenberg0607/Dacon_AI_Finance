from __future__ import annotations

import os
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / '.env')

APP_MODE = os.getenv('APP_MODE', 'auto').lower()
DASHSCOPE_API_KEY = os.getenv('DASHSCOPE_API_KEY', '').strip()
QWEN_BASE_URL = os.getenv('QWEN_BASE_URL', 'https://dashscope.aliyuncs.com/compatible-mode/v1').strip()
QWEN_MODEL = os.getenv('QWEN_MODEL', 'qwen3.6-plus').strip()
QWEN_EMBEDDING_MODEL = os.getenv('QWEN_EMBEDDING_MODEL', 'text-embedding-v4').strip()
QWEN_EMBEDDING_DIM = int(os.getenv('QWEN_EMBEDDING_DIM', '1024'))

DATA_DIR = ROOT / 'data'
CATALOG_PATH = DATA_DIR / 'catalog.json'
CHUNKS_PATH = DATA_DIR / 'chunks.jsonl'
EMBEDDINGS_PATH = DATA_DIR / 'embeddings.npy'
SOURCE_ZIP_PATH = DATA_DIR / 'source_documents.zip'
# catalog의 file_id를 source_documents.zip 안의 원본 PDF 항목명에 이어준다.
# scripts/build_source_pdf_map.py가 만든다.
SOURCE_PDF_MAP_PATH = DATA_DIR / 'source_pdf_map.json'

WITH_QWEN = APP_MODE != 'demo' and bool(DASHSCOPE_API_KEY)
