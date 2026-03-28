"""
Morning Pulse LangGraph 상태 정의
"""

from typing import TypedDict, Optional, Dict, Any, List
from langgraph.graph import MessagesState


class ReportState(TypedDict):
    """Morning Pulse 리포트 생성 전체 상태"""

    # ── 입력 데이터 ──────────────────────────────────────────────
    market_data: Dict[str, Any]           # DB 적재 데이터 (지수, 수급, 반도체, 매크로)
    news_data: str                         # 네이버 뉴스 RAG 결과 (기존 파이프라인)

    # ── 에이전트별 분석 결과 ──────────────────────────────────────
    global_analysis: Optional[str]         # 글로벌 시장 에이전트 (Sonnet)
    korea_analysis: Optional[str]          # 한국 시장 에이전트 (Sonnet + MCP)
    semi_analysis: Optional[str]           # 반도체 에이전트 (Sonnet + DB)
    flow_analysis: Optional[str]           # 수급 에이전트 (Haiku)

    # ── 집계/품질 ────────────────────────────────────────────────
    aggregated_report: Optional[str]       # 집계 에이전트 (Sonnet)
    quality_passed: Optional[bool]         # 품질 검증 통과 여부
    quality_feedback: Optional[str]        # 품질 검증 피드백 (재분석 시 사용)
    retry_count: int                       # 재시도 횟수

    # ── 최종 출력 ────────────────────────────────────────────────
    final_report: Optional[str]            # 최종 리포트 (HTML 렌더링용)
    token_usage: Dict[str, int]            # 에이전트별 토큰 사용량 추적

    # ── 메타 ─────────────────────────────────────────────────────
    report_date: str                       # 리포트 날짜 YYYY-MM-DD
    errors: List[str]                      # 에러 로그
