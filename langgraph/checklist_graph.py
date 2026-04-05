"""
체크리스트 모니터링 LangGraph 그래프 (Phase 2)
4단계 파이프라인: 파서 → 수집 → 신호 → 설명
"""

import logging
from langgraph.graph import StateGraph, END

from checklist_state import ChecklistState
from checklist_parser import parser_node
from checklist_collector import collector_node
from checklist_signal import signal_node
from checklist_explainer import explainer_node

logger = logging.getLogger(__name__)


def build_checklist_graph() -> StateGraph:
    graph = StateGraph(ChecklistState)

    # 4단계 파이프라인
    graph.add_node("parser", parser_node)
    graph.add_node("collector", collector_node)
    graph.add_node("signal", signal_node)
    graph.add_node("explainer", explainer_node)

    graph.set_entry_point("parser")
    graph.add_edge("parser", "collector")
    graph.add_edge("collector", "signal")
    graph.add_edge("signal", "explainer")
    graph.add_edge("explainer", END)

    return graph.compile()


checklist_monitor_graph = build_checklist_graph()
