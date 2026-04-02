"""
체크리스트 모니터링 LangGraph 그래프
단일 노드 (에이전트가 도구 호출 → 분석 결과 반환)
"""

import logging
from langgraph.graph import StateGraph, END

from checklist_state import ChecklistState
from checklist_agent import checklist_analysis_node

logger = logging.getLogger(__name__)


def build_checklist_graph() -> StateGraph:
    graph = StateGraph(ChecklistState)

    graph.add_node("analyze", checklist_analysis_node)

    graph.set_entry_point("analyze")
    graph.add_edge("analyze", END)

    return graph.compile()


checklist_monitor_graph = build_checklist_graph()
