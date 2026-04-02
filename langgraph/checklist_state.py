"""
체크리스트 모니터링 에이전트 상태 정의
"""

from typing import TypedDict, Optional, Dict, Any, List


class ChecklistState(TypedDict):
    """체크리스트 모니터링 상태"""

    # ── 입력 ──────────────────────────────────────
    checklist_text: str                    # 모닝리포트 체크리스트 원문
    time_phase: str                        # pre_market / intraday / close

    # ── 에이전트 분석 결과 ──────────────────────────
    analysis_result: Optional[str]         # 에이전트 최종 분석 (텔레그램 메시지용)
    token_usage: Dict[str, int]            # 토큰 사용량

    # ── 메타 ─────────────────────────────────────
    analysis_date: str                     # YYYY-MM-DD
    errors: List[str]
