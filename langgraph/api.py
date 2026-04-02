"""
BIP-Agents API 서버
- Airflow 등 외부에서 HTTP로 에이전트 호출
"""

import logging
import os
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


class ChecklistResponse(BaseModel):
    analysis_result: str
    token_usage: dict = {}
    errors: list = []


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

    try:
        result = await run_checklist_monitor(
            checklist_text=req.checklist_text,
            time_phase=req.time_phase,
            analysis_date=date,
        )
        return ChecklistResponse(**result)
    except Exception as e:
        logger.error(f"체크리스트 분석 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("API_PORT", "8100"))
    uvicorn.run(app, host="0.0.0.0", port=port)
