"""
지표 시장 맥락 계산 (get_indicator_context)
- macro_indicators 90일 히스토리 기반 분포 통계
- indicator_family별 해석 템플릿
- TTL 메모리 캐시
"""

import logging
import time
from typing import Optional

import statistics

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# 메모리 캐시 (TTL 기반)
# ──────────────────────────────────────────────

_context_cache: dict = {}
_CACHE_TTL = 180  # 3분
_CACHE_MAX_SIZE = 500  # 메모리 누수 방지


def _cache_get(key: str):
    entry = _context_cache.get(key)
    if not entry:
        return None
    # 만료 체크
    if time.time() - entry["ts"] >= _CACHE_TTL:
        _context_cache.pop(key, None)  # 만료 시 삭제
        return None
    return entry["data"]


def _cache_set(key: str, data: dict):
    # 사이즈 제한: 초과 시 가장 오래된 엔트리 제거
    if len(_context_cache) >= _CACHE_MAX_SIZE:
        oldest_key = min(_context_cache.keys(), key=lambda k: _context_cache[k]["ts"])
        _context_cache.pop(oldest_key, None)
    _context_cache[key] = {"ts": time.time(), "data": data}


# ──────────────────────────────────────────────
# Indicator Family별 해석 규칙
# ──────────────────────────────────────────────

# indicator_type → (family, semantic_direction)
# semantic_direction:
#   higher_is_risk: 값이 높을수록 위험 (환율, VIX)
#   higher_is_positive: 값이 높을수록 긍정적 (주가 지수)
#   neutral: 방향 해석 주의 필요 (원자재, 크립토)
INDICATOR_PROFILE = {
    "exchange_rate":      {"family": "fx", "direction": "higher_is_risk"},
    "stock_index_kospi":  {"family": "index", "direction": "higher_is_positive"},
    "stock_index_kosdaq": {"family": "index", "direction": "higher_is_positive"},
    "stock_index_sp500":  {"family": "index", "direction": "higher_is_positive"},
    "stock_index_nasdaq": {"family": "index", "direction": "higher_is_positive"},
    "stock_index_nikkei": {"family": "index", "direction": "higher_is_positive"},
    "commodity_oil":      {"family": "commodity", "direction": "neutral"},
    "commodity_gold":     {"family": "commodity", "direction": "neutral"},
    "crypto_btc":         {"family": "crypto", "direction": "neutral"},
    "crypto_eth":         {"family": "crypto", "direction": "neutral"},
    "vix":                {"family": "sentiment", "direction": "higher_is_risk"},
}


def _get_profile(indicator_type: str) -> dict:
    """indicator_type → profile 매핑"""
    return INDICATOR_PROFILE.get(
        indicator_type, {"family": "unknown", "direction": "neutral"}
    )


def _percentile(value: float, sorted_values: list) -> float:
    """정렬된 리스트에서 값의 백분위(0~100)"""
    if not sorted_values:
        return 50.0
    below = sum(1 for v in sorted_values if v < value)
    equal = sum(1 for v in sorted_values if v == value)
    # 백분위: (below + 0.5*equal) / total * 100
    return round((below + 0.5 * equal) / len(sorted_values) * 100, 1)


def _zscore(value: float, values: list) -> float:
    """Z-score (평균 대비 표준편차)"""
    if len(values) < 2:
        return 0.0
    mean = statistics.mean(values)
    stdev = statistics.stdev(values)
    if stdev == 0:
        return 0.0
    return round((value - mean) / stdev, 2)


def _trend_label(current: float, avg_5d: float, avg_20d: float) -> str:
    """간단한 추세 레이블"""
    if avg_5d > avg_20d and current > avg_5d:
        return "상승"
    if avg_5d < avg_20d and current < avg_5d:
        return "하락"
    return "횡보"


def _interpret(
    family: str,
    direction: str,
    current: float,
    percentile: float,
    range_position: float,
    drawdown_from_high: float,
    sample_size: int,
) -> str:
    """Family별 해석 문자열 생성"""
    # 표본 부족 경고
    if sample_size < 20:
        return f"표본 {sample_size}건으로 신뢰도 낮음"

    # 환율 / VIX (higher_is_risk)
    if direction == "higher_is_risk":
        if percentile >= 90:
            return "역대 고점권 (경계 구간)"
        elif percentile >= 75:
            return "상위 25% 구간 (주의)"
        elif percentile >= 50:
            return "중위 이상 (관찰)"
        elif percentile >= 25:
            return "중위 이하 (안정)"
        else:
            return "하위 25% 구간 (우호)"

    # 주가 지수 (higher_is_positive)
    if direction == "higher_is_positive":
        if drawdown_from_high <= -10:
            return f"고점 대비 {drawdown_from_high:.1f}% 하락 (조정 국면)"
        elif drawdown_from_high <= -5:
            return f"고점 대비 {drawdown_from_high:.1f}% 하락 (약세 관찰)"
        elif percentile >= 90:
            return "최근 고점권 (과열 가능)"
        elif percentile >= 50:
            return "강세 유지"
        else:
            return "약세 구간"

    # 원자재 / 크립토 (neutral)
    if percentile >= 90:
        return "역대 고점권 (추세 확인)"
    elif percentile <= 10:
        return "역대 저점권 (반등 관찰)"
    else:
        pos = round(range_position * 100)
        return f"90일 범위 {pos}% 위치"


# ──────────────────────────────────────────────
# 메인 함수
# ──────────────────────────────────────────────

async def get_indicator_context(
    indicator_type: str,
    region: Optional[str] = None,
    current_value: Optional[float] = None,
    lookback_days: int = 90,
) -> dict:
    """
    지표 시장 맥락 조회
    - macro_indicators에서 최근 lookback_days 데이터 추출
    - 분포 통계 + 해석 반환
    """
    # 캐시 키: current_value를 10단위 버킷으로 반올림 (미세한 실시간 변동은 같은 캐시 사용)
    # current_value가 0 또는 None인 경우 "no_value" 키로 구분
    if current_value is None or current_value == 0:
        value_bucket = "no_value"
    else:
        value_bucket = round(current_value / 10)
    cache_key = f"{indicator_type}:{region}:{lookback_days}:{value_bucket}"
    cached = _cache_get(cache_key)
    if cached:
        return cached

    try:
        from db import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            # region 필터 조건 동적 구성
            if region:
                rows = await conn.fetch("""
                    SELECT indicator_date, value
                    FROM macro_indicators
                    WHERE indicator_type = $1
                      AND region = $2
                      AND indicator_date >= CURRENT_DATE - ($3 || ' days')::interval
                      AND value IS NOT NULL
                    ORDER BY indicator_date ASC
                """, indicator_type, region, str(lookback_days))
            else:
                rows = await conn.fetch("""
                    SELECT indicator_date, value
                    FROM macro_indicators
                    WHERE indicator_type = $1
                      AND indicator_date >= CURRENT_DATE - ($2 || ' days')::interval
                      AND value IS NOT NULL
                    ORDER BY indicator_date ASC
                """, indicator_type, str(lookback_days))

        if not rows:
            return {"error": f"{indicator_type} 데이터 없음"}

        values = [float(r["value"]) for r in rows]
        dates = [r["indicator_date"] for r in rows]
        sample_size = len(values)

        # 실시간 current_value 없으면 최신 DB 값 사용
        latest_db_value = values[-1]
        latest_db_date = dates[-1]
        current = current_value if current_value is not None else latest_db_value

        # 분포 통계
        sorted_vals = sorted(values)
        min_val = sorted_vals[0]
        max_val = sorted_vals[-1]
        avg_val = statistics.mean(values)
        median_val = statistics.median(values)

        # 백분위 & Z-score & range position
        percentile = _percentile(current, sorted_vals)
        zscore = _zscore(current, values)
        raw_range_pos = (
            (current - min_val) / (max_val - min_val)
            if max_val > min_val else 0.5
        )
        # 현재값이 90일 범위를 벗어나면 0~1로 clamp
        range_position = max(0.0, min(1.0, raw_range_pos))
        out_of_range = raw_range_pos < 0 or raw_range_pos > 1
        drawdown_from_high = round((current - max_val) / max_val * 100, 2) if max_val > 0 else 0

        # 추세
        recent_5 = values[-5:] if len(values) >= 5 else values
        recent_20 = values[-20:] if len(values) >= 20 else values
        avg_5d = statistics.mean(recent_5)
        avg_20d = statistics.mean(recent_20)
        trend = _trend_label(current, avg_5d, avg_20d)

        # 변동성 (20일 표준편차 → 변동률)
        vol_20d = 0.0
        if len(recent_20) >= 2 and avg_20d > 0:
            vol_20d = round(statistics.stdev(recent_20) / avg_20d * 100, 2)

        # Family & 해석
        profile = _get_profile(indicator_type)
        family = profile["family"]
        direction = profile["direction"]
        interpretation = _interpret(
            family, direction, current, percentile,
            range_position, drawdown_from_high, sample_size,
        )

        # 극값 근접도
        days_from_high = None
        days_from_low = None
        max_idx = values.index(max_val)
        min_idx = values.index(min_val)
        if max_idx < len(dates):
            days_from_high = (dates[-1] - dates[max_idx]).days
        if min_idx < len(dates):
            days_from_low = (dates[-1] - dates[min_idx]).days

        # DB 최신성
        staleness_hours = None
        if latest_db_date:
            from datetime import datetime, date
            today = date.today()
            staleness_hours = (today - latest_db_date).days * 24

        result = {
            "indicator_type": indicator_type,
            "region": region or "",
            "family": family,
            "direction": direction,
            "lookback_days": lookback_days,
            "sample_size": sample_size,
            "current_value": round(current, 4),
            "latest_db_value": round(latest_db_value, 4),
            "latest_db_date": str(latest_db_date),
            "staleness_hours": staleness_hours,
            # 분포
            "min_90d": round(min_val, 4),
            "max_90d": round(max_val, 4),
            "avg_90d": round(avg_val, 4),
            "median_90d": round(median_val, 4),
            # 상대 위치
            "percentile": percentile,
            "zscore": zscore,
            "range_position": round(range_position, 3),
            "out_of_90d_range": out_of_range,
            "drawdown_from_90d_high": drawdown_from_high,
            "days_from_90d_high": days_from_high,
            "days_from_90d_low": days_from_low,
            # 추세/변동성
            "trend_label": trend,
            "avg_5d": round(avg_5d, 4),
            "avg_20d": round(avg_20d, 4),
            "volatility_20d_pct": vol_20d,
            # 해석
            "interpretation": interpretation,
            "reliability": "low" if sample_size < 20 else "normal",
        }

        _cache_set(cache_key, result)
        return result

    except Exception as e:
        logger.error(f"get_indicator_context 실패 ({indicator_type}): {e}")
        return {"error": str(e)}
