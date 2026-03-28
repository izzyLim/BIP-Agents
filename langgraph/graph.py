"""
Morning Pulse LangGraph 그래프 정의
병렬 멀티 에이전트 → 집계 → 품질 검증 → 최종 출력
"""

import asyncio
import logging
import os
from langgraph.graph import StateGraph, END
from langchain_mcp_adapters.client import MultiServerMCPClient

from state import ReportState
from agents import (
    global_agent_node,
    korea_agent_node,
    semi_agent_node,
    flow_agent_node,
    aggregator_node,
    quality_checker_node,
)

logger = logging.getLogger(__name__)

# MCP 서버 URL (Docker: bip-stock-mcp 서비스명, 로컬: localhost)
MCP_SERVER_URL = os.getenv("MCP_SERVER_URL", "http://bip-stock-mcp:8000/sse")
USE_MCP = os.getenv("USE_MCP", "true").lower() == "true"


# ── MCP 도구 로드 ─────────────────────────────────────────────────────────────

async def load_mcp_tools() -> list:
    """bip-stock-mcp 서버에서 tool 목록을 가져옴"""
    if not USE_MCP:
        logger.info("MCP 비활성화 — DB 데이터만 사용")
        return []

    try:
        async with MultiServerMCPClient({
            "bip-stock": {
                "url": MCP_SERVER_URL,
                "transport": "sse",
            }
        }) as client:
            tools = client.get_tools()
            logger.info(f"MCP 도구 로드 완료: {[t.name for t in tools]}")
            return tools
    except Exception as e:
        logger.warning(f"MCP 서버 연결 실패 ({MCP_SERVER_URL}) — MCP 없이 실행: {e}")
        return []


# ── 병렬 분석 노드 ────────────────────────────────────────────────────────────

async def parallel_analysis_node(state: ReportState) -> dict:
    """글로벌/한국/반도체/수급 에이전트를 병렬 실행"""
    logger.info("병렬 분석 시작...")

    # MCP 도구 로드 (실패해도 계속 진행)
    mcp_tools = await load_mcp_tools()

    results = await asyncio.gather(
        global_agent_node(state),
        korea_agent_node(state, mcp_tools=mcp_tools),
        semi_agent_node(state, mcp_tools=mcp_tools),
        flow_agent_node(state),
        return_exceptions=True,
    )

    merged = {
        "token_usage": state.get("token_usage", {}),
        "errors": state.get("errors", []),
    }

    for result in results:
        if isinstance(result, Exception):
            logger.error(f"에이전트 실패: {result}")
            merged["errors"].append(str(result))
        else:
            merged.update(result)

    logger.info(f"병렬 분석 완료. 오류: {len(merged['errors'])}건")
    return merged


# ── 품질 판단 라우터 ──────────────────────────────────────────────────────────

def quality_router(state: ReportState) -> str:
    if state.get("quality_passed"):
        return "finalize"
    if state.get("retry_count", 0) >= 2:
        logger.warning("최대 재시도 도달 — 강제 종료")
        return "finalize"
    logger.info(f"품질 검증 실패 — 재분석 (시도 {state.get('retry_count', 0) + 1})")
    return "retry"


# ── 최종 출력 노드 ────────────────────────────────────────────────────────────

async def finalize_node(state: ReportState) -> dict:
    feedback = state.get("quality_feedback", "")
    if feedback and feedback not in ("없음", ""):
        logger.info(f"품질 피드백 (참고): {feedback[:100]}")
    return {"final_report": state.get("aggregated_report", "")}


# ── 그래프 빌드 ───────────────────────────────────────────────────────────────

def build_graph() -> StateGraph:
    graph = StateGraph(ReportState)

    graph.add_node("parallel_analysis", parallel_analysis_node)
    graph.add_node("aggregator", aggregator_node)
    graph.add_node("quality_checker", quality_checker_node)
    graph.add_node("finalize", finalize_node)

    graph.set_entry_point("parallel_analysis")
    graph.add_edge("parallel_analysis", "aggregator")
    graph.add_edge("aggregator", "quality_checker")
    graph.add_conditional_edges(
        "quality_checker",
        quality_router,
        {"finalize": "finalize", "retry": "aggregator"},
    )
    graph.add_edge("finalize", END)

    return graph.compile()


morning_pulse_graph = build_graph()
