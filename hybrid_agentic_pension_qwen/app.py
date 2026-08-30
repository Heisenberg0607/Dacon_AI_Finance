from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend.agents import HybridAgenticWorkflow
from backend.models import UserPensionInput
from backend.qwen_client import QwenGateway
from backend.rag import PensionRAG
from backend.config import ROOT
from backend.tools import estimate_wage_growth

FRONTEND = ROOT / 'frontend'

qwen = QwenGateway()
rag = PensionRAG(qwen)
workflow = HybridAgenticWorkflow(qwen, rag)

app = FastAPI(title='깨움 KKAEUM - Hybrid Agentic AI Workflow', version='1.5.0')
app.mount('/static', StaticFiles(directory=FRONTEND), name='static')


@app.get('/')
def index():
    return FileResponse(FRONTEND / 'index.html')


@app.get('/api/health')
def health():
    return {
        'ok': True,
        'qwen_enabled': qwen.enabled,
        'qwen_model': qwen.model,
        'rag_mode': rag.mode,
        'documents': len(rag.catalog),
        'chunks': len(rag.chunks),
    }


@app.get('/api/catalog')
def catalog():
    return {'providers': rag.providers(), 'products': rag.catalog}


@app.post('/api/estimate-wage-growth')
def wage_growth_estimate(user: UserPensionInput):
    # DB 입력폼에서 '깨움이 추정' 버튼이 호출하는 deterministic estimate.
    # 임금상승률 자체를 LLM이 임의 생성하지 않도록 숫자 계산은 코드에서 수행한다.
    return estimate_wage_growth(user)


@app.post('/api/product-extraction')
def product_extraction(user: UserPensionInput):
    """DC/IRP 선택 상품이 어떤 PDF와 매칭되고 무엇이 추출되는지 확인하는 디버그 API."""
    if user.operation_type == 'DB':
        return {'applicable': False, 'note': 'DB형은 개인 선택 상품 PDF 추출 비적용'}
    return workflow._extract_selected_product(user)


@app.post('/api/analyze')
def analyze(user: UserPensionInput):
    return workflow.run(user)
