import hmac
import os
import logging
from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from .contracts import AgentError, RunRequest
from .engine import Orchestrator

app = FastAPI(title='HandoffOS private worker', docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware('http')
async def authenticate(request: Request, call_next):
    secret = os.getenv('HANDOFF_INTERNAL_TOKEN', '')
    supplied = request.headers.get('authorization', '')
    if not secret or not hmac.compare_digest(supplied, 'Bearer ' + secret):
        return JSONResponse({'code': 'UNAUTHORIZED', 'message': '인증이 필요합니다.'}, status_code=401)
    return await call_next(request)


@app.exception_handler(AgentError)
async def agent_error(request, exc):
    return JSONResponse({'code': exc.code, 'message': exc.message}, status_code=exc.status)


@app.exception_handler(RequestValidationError)
async def invalid_input(request, exc):
    return JSONResponse({'code': 'INVALID_REQUEST', 'message': '내부 요청 스키마가 올바르지 않습니다.'}, status_code=400)


@app.get('/internal/health')
def health():
    return {'status': 'ok', 'modelMode': os.getenv('HANDOFF_MODEL_MODE', 'offline'), 'retrievalMode': os.getenv('HANDOFF_RETRIEVAL_MODE', 'fixture')}


@app.post('/internal/run')
async def run(request: RunRequest):
    try:
        runtime = os.getenv('HANDOFF_WORKFLOW_RUNTIME', 'native')
        if runtime == 'nat':
            try:
                from .nat_workflow import run_in_nat
            except ImportError:
                raise AgentError('NAT_NOT_CONFIGURED', '지원 Python 환경에 .[nat]를 설치하세요.', 503)
            return await run_in_nat(request)
        if runtime != 'native':
            raise AgentError('WORKFLOW_NOT_CONFIGURED', 'workflow runtime 설정이 올바르지 않습니다.', 503)
        return await Orchestrator().run(request)
    except AgentError:
        raise
    except Exception:
        # Do not expose provider response bodies, source contents or credentials to the client.
        logging.error('Orchestrator unexpected failure (details withheld from API)')
        raise AgentError('INTERNAL_ERROR', '처리를 완료하지 못했습니다. 서버 설정을 확인하세요.', 500)
