from __future__ import annotations

import time

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend.agents import HybridAgenticWorkflow
from backend.chat_agent import ReportChatAgent
from backend.models import ChatRequest, OperationType, UserPensionInput
from backend.qwen_client import QwenGateway
from backend.rag import PensionRAG
from backend.config import ROOT
from backend.eta_store import RunTimeHistory
from backend.session_store import AnalysisStore
from backend.tools import estimate_wage_growth

FRONTEND = ROOT / 'frontend'

qwen = QwenGateway()
rag = PensionRAG(qwen)
workflow = HybridAgenticWorkflow(qwen, rag)
chat_agent = ReportChatAgent(qwen, rag)
analysis_store = AnalysisStore()
run_times = RunTimeHistory()

app = FastAPI(title='깨움 KKAEUM - Hybrid Agentic AI Workflow', version='1.9.0')
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


@app.get('/api/eta')
def eta(operation_type: OperationType = 'DC'):
    """2단계 대기 화면이 쓰는 예상 소요시간.

    실제로 측정된 total_seconds만 근거로 삼는다. 응답의 source가 그 근거를 밝힌다.

      measured - 이 서버의 라이브 이력
      baseline - 저장소에 커밋된 실측 baseline (새로 클론한 환경의 첫 분석)
      related  - 같은 실행모드의 다른 운영유형 (basis_operation_type에 어떤 유형인지 담긴다)

    어디에도 실측값이 없으면 available=False로 응답하고,
    화면은 임의의 숫자 대신 비확정 상태를 표시한다.
    """
    estimate = run_times.estimate(operation_type, qwen.enabled)
    if estimate is None:
        return {'available': False, 'operation_type': operation_type}
    return {'available': True, **estimate}


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
    started = time.perf_counter()
    result = workflow.run(user)
    # 보고서 화면 챗봇이 같은 분석 context를 이어서 쓰도록 서버에 잠시 보관한다.
    result['analysis_id'] = analysis_store.put(user, result)
    # 다음 사용자의 '예상 남은 시간'은 이 실측값들만 근거로 계산된다.
    #
    # 여기서 재는 값과 result['timing']['total_seconds']는 일부러 다르다.
    # total_seconds는 workflow.run()만 감싼 '보고서 생성 소요시간'이라 보고서 표지에 쓰고,
    # 대기 게이지는 화면에서 요청을 보내고 응답을 받을 때까지를 재므로 핸들러 전체를 담는다.
    # 둘을 같게 맞추면 게이지가 항상 예상 시간을 초과한다. 불일치로 보고 되돌리지 말 것.
    run_times.record(user.operation_type, time.perf_counter() - started, qwen.enabled)
    return result


@app.post('/api/chat')
def chat(request: ChatRequest):
    """보고서 화면의 Report Q&A Agent.

    분석 결과 전체는 서버 세션에 있으므로 요청에는 analysis_id와 질문만 담긴다.
    """
    session = analysis_store.get(request.analysis_id)
    if session is None:
        raise HTTPException(status_code=404, detail='분석 결과가 만료되었습니다. 다시 분석해주세요.')
    history = [turn.model_dump() for turn in request.history]
    return chat_agent.answer(session, request.message, history)
