"""
BIP-Agents API 서버
- Airflow 등 외부에서 HTTP로 에이전트 호출
"""

import logging
import os
import time
import uuid
from datetime import datetime
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

load_dotenv()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("BIP-Agents API 서버 시작")
    yield
    logger.info("BIP-Agents API 서버 종료")


app = FastAPI(
    title="BIP-Agents API",
    description="체크리스트 모니터링 등 에이전트 호출 API",
    lifespan=lifespan,
)


# ── 요청/응답 모델 ──────────────────────────────

class ChecklistRequest(BaseModel):
    checklist_text: str
    time_phase: str = "intraday"  # pre_market / intraday / close
    analysis_date: str = None


class AuditMeta(BaseModel):
    """감사 기록용 메타데이터 (Airflow agent_audit_log 기록에 사용)"""
    run_id: str
    agent_name: str = "market_monitor_checklist"
    agent_type: str = "monitor"
    llm_provider: str = "anthropic"
    llm_model: str = "claude-haiku-4-5-20251001"
    prompt_class: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    tools_used: list = []
    data_sources: list = []
    referenced_tables: list = []
    execution_ms: int = 0
    status: str = "success"
    error_message: str = ""


class ChecklistResponse(BaseModel):
    analysis_result: str
    token_usage: dict = {}
    errors: list = []
    audit: AuditMeta


# ── 엔드포인트 ──────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "service": "bip-agents"}


@app.post("/api/checklist/analyze", response_model=ChecklistResponse)
async def analyze_checklist(req: ChecklistRequest):
    """체크리스트 모니터링 에이전트 호출"""
    from checklist_main import run_checklist_monitor

    if not req.checklist_text.strip():
        raise HTTPException(status_code=400, detail="checklist_text is empty")

    date = req.analysis_date or datetime.now().strftime("%Y-%m-%d")
    run_id = f"checklist_{req.time_phase}_{uuid.uuid4().hex[:8]}"
    start = time.time()

    try:
        result = await run_checklist_monitor(
            checklist_text=req.checklist_text,
            time_phase=req.time_phase,
            analysis_date=date,
        )
        elapsed_ms = int((time.time() - start) * 1000)

        # 토큰 사용량 집계 (explainer, checklist_agent 등 여러 노드 합산)
        token_usage = result.get("token_usage", {}) or {}
        total_in = sum(
            (v.get("input", 0) if isinstance(v, dict) else 0)
            for v in token_usage.values()
        )
        total_out = sum(
            (v.get("output", 0) if isinstance(v, dict) else 0)
            for v in token_usage.values()
        )

        audit = AuditMeta(
            run_id=run_id,
            prompt_class=f"analyze_checklist_{req.time_phase}",
            prompt_tokens=total_in,
            completion_tokens=total_out,
            tools_used=[
                "realtime_fx_rate", "realtime_index", "realtime_stock_price",
                "get_indicator_context", "news_search_naver",
                "night_futures_ewy", "crypto_price", "global_index",
            ],
            data_sources=["kis_api", "upbit", "yfinance", "macro_indicators", "naver_news_api"],
            referenced_tables=["macro_indicators", "monitor_checklist"],
            execution_ms=elapsed_ms,
            status="success" if not result.get("errors") else "partial_error",
            error_message="; ".join(result.get("errors", []))[:500] if result.get("errors") else "",
        )

        return ChecklistResponse(audit=audit, **result)
    except Exception as e:
        logger.error(f"체크리스트 분석 실패: {e}")
        # 실패도 감사 메타로 응답 (Airflow가 기록할 수 있게)
        elapsed_ms = int((time.time() - start) * 1000)
        audit = AuditMeta(
            run_id=run_id,
            prompt_class=f"analyze_checklist_{req.time_phase}",
            tools_used=["realtime_*", "get_indicator_context"],
            data_sources=["macro_indicators"],
            referenced_tables=["macro_indicators", "monitor_checklist"],
            execution_ms=elapsed_ms,
            status="llm_error",
            error_message=str(e)[:500],
        )
        raise HTTPException(
            status_code=500,
            detail={"error": str(e), "audit": audit.model_dump()},
        )


class PreopenResponse(BaseModel):
    analysis_result: str
    token_usage: dict = {}
    data: dict = {}
    errors: list = []
    audit: AuditMeta


@app.post("/api/preopen/analyze", response_model=PreopenResponse)
async def analyze_preopen():
    """장 시작 전 예상 체결가 + 맥락 분석"""
    from preopen_analyzer import run_preopen_analysis

    run_id = f"preopen_{uuid.uuid4().hex[:8]}"
    start = time.time()

    try:
        result = await run_preopen_analysis()
        elapsed_ms = int((time.time() - start) * 1000)

        token_usage = result.get("token_usage", {}) or {}
        total_in = sum(
            (v.get("input", 0) if isinstance(v, dict) else 0)
            for v in token_usage.values()
        )
        total_out = sum(
            (v.get("output", 0) if isinstance(v, dict) else 0)
            for v in token_usage.values()
        )

        audit = AuditMeta(
            run_id=run_id,
            agent_name="market_monitor_preopen",
            agent_type="monitor",
            prompt_class="preopen_analysis",
            prompt_tokens=total_in,
            completion_tokens=total_out,
            tools_used=[
                "preopen_stock_price", "preopen_index",
                "realtime_fx_rate", "night_futures_ewy",
                "global_index", "get_indicator_context",
            ],
            data_sources=["kis_api", "yfinance", "macro_indicators"],
            referenced_tables=["macro_indicators"],
            execution_ms=elapsed_ms,
            status="success" if not result.get("errors") else "partial_error",
            error_message="; ".join(result.get("errors", []))[:500] if result.get("errors") else "",
        )

        return PreopenResponse(audit=audit, **result)
    except Exception as e:
        logger.error(f"preopen 분석 실패: {e}")
        elapsed_ms = int((time.time() - start) * 1000)
        audit = AuditMeta(
            run_id=run_id,
            agent_name="market_monitor_preopen",
            agent_type="monitor",
            prompt_class="preopen_analysis",
            tools_used=["preopen_*"],
            data_sources=["kis_api", "yfinance"],
            referenced_tables=["macro_indicators"],
            execution_ms=elapsed_ms,
            status="llm_error",
            error_message=str(e)[:500],
        )
        raise HTTPException(
            status_code=500,
            detail={"error": str(e), "audit": audit.model_dump()},
        )


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("API_PORT", "8100"))
    uvicorn.run(app, host="0.0.0.0", port=port)
