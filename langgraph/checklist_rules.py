"""
체크리스트 신호 판정 룰 엔진 (deterministic)
- indicator_family별 규칙 분리
- signal_node에서 import해서 사용
- 추후 테스트/튜닝 용이하도록 순수 함수로 구성
"""

from typing import Optional, Dict, Any, Tuple


# 신호등 기호
GREEN = "🟢"
YELLOW = "🟡"
RED = "🔴"
GRAY = "⚪"


def rule_fx(
    value: Optional[float],
    threshold: Optional[float],
    direction: Optional[str],
    context: Optional[Dict[str, Any]],
) -> Tuple[str, str]:
    """환율 판정 규칙 (higher_is_risk)"""
    if value is None:
        return GRAY, "데이터 없음"

    if context and "error" not in context:
        percentile = context.get("percentile", 50)
        interpretation = context.get("interpretation", "")

        if percentile >= 90:
            return RED, f"{interpretation} (p{percentile})"
        elif percentile >= 75:
            return YELLOW, f"{interpretation} (p{percentile})"

    if threshold is not None and direction:
        met = (direction == "above" and value >= threshold) or \
              (direction == "below" and value <= threshold)
        if met:
            d = "돌파" if direction == "above" else "이탈"
            return YELLOW, f"{value:,.1f}원, {threshold:,.0f}원 {d}"
        else:
            return GREEN, f"{value:,.1f}원, {threshold:,.0f}원 미도달"

    return YELLOW, f"현재 {value:,.1f}원"


def rule_index(
    value: Optional[float],
    threshold: Optional[float],
    direction: Optional[str],
    context: Optional[Dict[str, Any]],
) -> Tuple[str, str]:
    """지수 판정 규칙 (higher_is_positive)"""
    if value is None:
        return GRAY, "데이터 없음"

    if context and "error" not in context:
        drawdown = context.get("drawdown_from_90d_high", 0)
        percentile = context.get("percentile", 50)
        interpretation = context.get("interpretation", "")

        if drawdown <= -10:
            return RED, f"{interpretation}"
        elif drawdown <= -5:
            return YELLOW, f"{interpretation}"
        elif percentile >= 90:
            return YELLOW, "최근 고점권 (과열 주의)"

    if threshold is not None and direction:
        met = (direction == "above" and value >= threshold) or \
              (direction == "below" and value <= threshold)
        if met:
            rel = "상회" if direction == "above" else "하회"
            return GREEN, f"현재 {value:,.2f}, {threshold:,.0f} {rel}"
        else:
            d = "돌파" if direction == "above" else "이탈"
            diff = value - threshold
            return YELLOW, f"현재 {value:,.2f}, {threshold:,.0f} {d} 대기 ({diff:+,.0f})"

    return YELLOW, f"현재 {value:,.2f}"


def rule_stock(
    value: Optional[float],
    threshold: Optional[float],
    direction: Optional[str],
    context: Optional[Dict[str, Any]],
    raw: Optional[Dict[str, Any]] = None,
) -> Tuple[str, str]:
    """개별 종목 판정 규칙"""
    if value is None:
        return GRAY, "데이터 없음"

    change_pct = (raw or {}).get("change_pct", 0)

    if threshold is not None and direction:
        met = (direction == "above" and value >= threshold) or \
              (direction == "below" and value <= threshold)
        if met:
            return GREEN, f"{value:,.0f}원 ({change_pct:+.1f}%), {threshold:,.0f}원 {'상회' if direction == 'above' else '하회'}"
        else:
            d = "돌파" if direction == "above" else "이탈"
            return YELLOW, f"{value:,.0f}원 ({change_pct:+.1f}%), {threshold:,.0f}원 {d} 미도달"

    return YELLOW, f"{value:,.0f}원 ({change_pct:+.1f}%)"


def rule_commodity(
    value: Optional[float],
    context: Optional[Dict[str, Any]],
) -> Tuple[str, str]:
    """원자재 판정 규칙 (neutral)"""
    if value is None:
        return GRAY, "데이터 없음"

    if context and "error" not in context:
        interpretation = context.get("interpretation", "")
        zscore = context.get("zscore", 0)
        if abs(zscore) >= 2:
            return RED, f"{interpretation} (z={zscore})"
        elif abs(zscore) >= 1.5:
            return YELLOW, f"{interpretation} (z={zscore})"
        return YELLOW, interpretation or f"현재 ${value:,.1f}"

    return YELLOW, f"현재 ${value:,.1f}"


def rule_crypto(
    value: Optional[float],
    context: Optional[Dict[str, Any]],
    raw: Optional[Dict[str, Any]] = None,
) -> Tuple[str, str]:
    """크립토 판정 규칙 (변동성 큰 자산)"""
    if value is None:
        return GRAY, "데이터 없음"

    change_pct = (raw or {}).get("change_pct", 0)

    if context and "error" not in context:
        interpretation = context.get("interpretation", "")
        percentile = context.get("percentile", 50)

        if percentile >= 90 or percentile <= 10:
            return YELLOW, f"{interpretation}"

    if abs(change_pct) >= 5:
        color = RED if abs(change_pct) >= 10 else YELLOW
        return color, f"{value:,.0f}원 ({change_pct:+.1f}%)"

    return YELLOW, f"{value:,.0f}원 ({change_pct:+.1f}%)"


def rule_general(
    raw: Optional[Dict[str, Any]],
) -> Tuple[str, str]:
    """뉴스/일반 항목 (중립 기본)"""
    if not raw or "error" in (raw or {}):
        return GRAY, "데이터 없음"
    return YELLOW, "확인 완료"


def judge(
    indicator_key: Optional[str],
    value: Optional[float],
    threshold: Optional[float],
    direction: Optional[str],
    raw: Optional[Dict[str, Any]],
    context: Optional[Dict[str, Any]],
) -> Tuple[str, str]:
    """
    메인 판정 함수 — indicator_key에 따라 적절한 룰 호출
    """
    if indicator_key in ("fx_krw", "vix"):
        return rule_fx(value, threshold, direction, context)
    if indicator_key in ("kospi", "kosdaq", "sp500", "nasdaq", "nikkei"):
        return rule_index(value, threshold, direction, context)
    if indicator_key and indicator_key.startswith("stock:"):
        return rule_stock(value, threshold, direction, context, raw)
    if indicator_key in ("wti", "gold", "oil"):
        return rule_commodity(value, context)
    if indicator_key in ("btc", "eth"):
        return rule_crypto(value, context, raw)

    return rule_general(raw)
