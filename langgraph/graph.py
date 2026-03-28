"""
Morning Pulse LangGraph 그래프 정의
병렬 멀티 에이전트 → 집계 → 품질 검증 → 최종 출력
"""

import asyncio
import logging
from typing import Any
from langgraph.graph import StateGraph, END

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


# ── 병렬 분석 노드 (4개 에이전트 동시 실행) ────────────────────────────────────

async def parallel_analysis_node(state: ReportState) -> dict:
    """글로벌/한국/반도체/수급 에이전트를 병렬 실행"""
    logger.info("병렬 분석 시작...")

    results = await asyncio.gather(
        global_agent_node(state),
        korea_agent_node(state),       # TODO: MCP 도구 주입
        semi_agent_node(state),        # TODO: MCP 도구 주입
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
    """품질 검증 결과에 따라 재분석 또는 종료"""
    if state.get("quality_passed"):
        return "finalize"
    if state.get("retry_count", 0) >= 2:
        logger.warning("최대 재시도 도달 — 강제 종료")
        return "finalize"
    logger.info(f"품질 검증 실패 — 재분석 (시도 {state.get('retry_count', 0) + 1})")
    return "retry"


# ── 최종 출력 노드 ────────────────────────────────────────────────────────────

async def finalize_node(state: ReportState) -> dict:
    """최종 리포트 확정"""
    report = state.get("aggregated_report", "")
    quality_feedback = state.get("quality_feedback", "")

    if quality_feedback and quality_feedback not in ("없음", ""):
        logger.info(f"품질 피드백 (참고): {quality_feedback[:100]}")

    return {"final_report": report}


# ── 그래프 빌드 ───────────────────────────────────────────────────────────────

def build_graph() -> StateGraph:
    """Morning Pulse LangGraph 그래프 빌드"""

    graph = StateGraph(ReportState)

    # 노드 등록
    graph.add_node("parallel_analysis", parallel_analysis_node)
    graph.add_node("aggregator", aggregator_node)
    graph.add_node("quality_checker", quality_checker_node)
    graph.add_node("finalize", finalize_node)

    # 엣지 정의
    graph.set_entry_point("parallel_analysis")
    graph.add_edge("parallel_analysis", "aggregator")
    graph.add_edge("aggregator", "quality_checker")

    # 조건부 엣지: 품질 통과 → 종료, 실패 → 재집계
    graph.add_conditional_edges(
        "quality_checker",
        quality_router,
        {
            "finalize": "finalize",
            "retry": "aggregator",  # 집계부터 재시도
        },
    )
    graph.add_edge("finalize", END)

    return graph.compile()


# 컴파일된 그래프 (싱글턴)
morning_pulse_graph = build_graph()
