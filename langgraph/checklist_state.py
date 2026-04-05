"""
체크리스트 모니터링 에이전트 상태 정의
- 파서/플래너/수집/신호/설명 노드 간 데이터 전달
"""

from typing import TypedDict, Optional, Dict, Any, List


class ChecklistItem(TypedDict):
    """파싱된 체크리스트 단일 항목"""
    index: int                      # 순서
    original: str                   # 원문
    section: str                    # "pre_market" / "intraday" / "price_level" / "unknown"
    item_type: str                  # "level" / "event" / "news" / "general"
    # 레벨형 항목의 메타데이터
    indicator_key: Optional[str]    # "fx_krw", "kospi", "stock:005930" 등
    threshold: Optional[float]      # 임계값 (예: 1520, 5600, 190000)
    direction: Optional[str]        # "above" (돌파) / "below" (이탈)
    # 계획 단계에서 채움
    required_tools: List[str]       # 호출할 도구 목록
    must_call_context: bool         # get_indicator_context 필수 여부


class ChecklistState(TypedDict):
    """체크리스트 모니터링 상태"""

    # ── 입력 ──────────────────────────────────────
    checklist_text: str                    # 모닝리포트 체크리스트 원문
    time_phase: str                        # pre_market / intraday / close

    # ── Phase 2 파이프라인 ─────────────────────────
    parsed_items: List[ChecklistItem]      # 파서 노드 결과
    gathered_data: Dict[str, Any]          # 수집 노드 결과 (item_index → data)
    always_stocks: Dict[str, Any]          # 항상 수집하는 주요 종목 데이터 (code → raw)
    market_context: Dict[str, Any]         # phase별 기본 시장 데이터 (체크리스트 무관)
    signals: Dict[int, Dict]               # 신호 노드 결과 (item_index → {level, reason})

    # ── 에이전트 분석 결과 ──────────────────────────
    analysis_result: Optional[str]         # 최종 텔레그램 메시지 텍스트
    token_usage: Dict[str, int]            # 토큰 사용량

    # ── 메타 ─────────────────────────────────────
    analysis_date: str                     # YYYY-MM-DD
    errors: List[str]
