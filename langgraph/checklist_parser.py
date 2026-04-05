"""
체크리스트 파서 + 플래너 (deterministic)
- 원문을 항목 단위로 분리
- 각 항목 유형 분류 (level/event/news/general)
- 레벨형 항목은 임계값/방향 추출
- 필수 도구 매핑
"""

import re
import logging
from typing import List

from checklist_state import ChecklistItem

logger = logging.getLogger(__name__)


# 종목명 → 코드 매핑
STOCK_MAP = {
    "삼성전자": "005930",
    "SK하이닉스": "000660",
    "LG에너지솔루션": "373220",
    "현대차": "005380",
    "삼성바이오": "207940",
    "NAVER": "035420",
    "카카오": "035720",
    "삼성SDI": "006400",
    "기아": "000270",
    "POSCO홀딩스": "005490",
}


def _parse_level_value(text: str) -> float:
    """텍스트에서 숫자 레벨 추출 (1,520원 → 1520)"""
    # 콤마 제거 후 숫자 추출
    clean = text.replace(",", "")
    match = re.search(r'([\d.]+)', clean)
    if match:
        return float(match.group(1))
    return 0.0


def _classify_item(text: str) -> dict:
    """
    체크리스트 항목 텍스트 분류
    Returns: {item_type, indicator_key, threshold, direction, required_tools, must_call_context}
    """
    lowered = text.lower()

    # 기본값
    result = {
        "item_type": "general",
        "indicator_key": None,
        "threshold": None,
        "direction": None,
        "required_tools": [],
        "must_call_context": False,
    }

    # 1. 환율 관련
    if any(k in text for k in ["원/달러", "환율", "USD/KRW", "원달러"]):
        result["item_type"] = "level"
        result["indicator_key"] = "fx_krw"
        result["required_tools"] = ["realtime_fx_rate", "get_indicator_context"]
        result["must_call_context"] = True
        # 임계값 추출 (예: "1,520원")
        match = re.search(r'(\d,\d{3}|\d{4})\s*원', text)
        if match:
            result["threshold"] = float(match.group(1).replace(",", ""))
        # 방향
        if any(k in text for k in ["돌파", "상회", "상향"]):
            result["direction"] = "above"
        elif any(k in text for k in ["이탈", "하회", "이하"]):
            result["direction"] = "below"
        return result

    # 2. KOSPI/KOSDAQ 지수
    if "코스피" in text or "kospi" in lowered:
        result["item_type"] = "level"
        result["indicator_key"] = "kospi"
        result["required_tools"] = ["realtime_index", "get_indicator_context"]
        result["must_call_context"] = True
        match = re.search(r'(\d,\d{3}|\d{4,5})', text)
        if match:
            result["threshold"] = float(match.group(1).replace(",", ""))
        if any(k in text for k in ["돌파", "상회", "회복"]):
            result["direction"] = "above"
        elif any(k in text for k in ["이탈", "붕괴", "하회"]):
            result["direction"] = "below"
        elif "지지" in text:
            result["direction"] = "above"  # 지지는 위에서 유지
        return result

    if "코스닥" in text or "kosdaq" in lowered:
        result["item_type"] = "level"
        result["indicator_key"] = "kosdaq"
        result["required_tools"] = ["realtime_index", "get_indicator_context"]
        result["must_call_context"] = True
        return result

    # 3. 개별 종목
    for stock_name, code in STOCK_MAP.items():
        if stock_name in text:
            result["item_type"] = "level"
            result["indicator_key"] = f"stock:{code}"
            result["required_tools"] = ["realtime_stock_price"]
            # 숫자 임계값 추출 (예: "190,000원")
            match = re.search(r'(\d{1,3}(?:,\d{3})+|\d{4,})\s*원', text)
            if match:
                result["threshold"] = float(match.group(1).replace(",", ""))
            if any(k in text for k in ["돌파", "회복", "상회"]):
                result["direction"] = "above"
            elif any(k in text for k in ["이탈", "하회"]):
                result["direction"] = "below"
            elif "지지" in text:
                result["direction"] = "above"
            return result

    # 4. 수급/외국인
    if any(k in text for k in ["외국인", "기관", "수급", "순매수", "순매도"]):
        result["item_type"] = "level"
        result["indicator_key"] = "investor_flow"
        result["required_tools"] = ["realtime_investor"]
        return result

    # 5. 야간선물/갭
    if any(k in text for k in ["야간선물", "CME", "갭", "EWY"]):
        result["item_type"] = "level"
        result["indicator_key"] = "night_futures"
        result["required_tools"] = ["night_futures_ewy"]
        return result

    # 6. 닛케이/S&P/나스닥
    if "닛케이" in text or "nikkei" in lowered:
        result["item_type"] = "level"
        result["indicator_key"] = "nikkei"
        result["required_tools"] = ["global_index", "get_indicator_context"]
        result["must_call_context"] = True
        return result
    if "s&p" in lowered or "sp500" in lowered or "스앤피" in text:
        result["item_type"] = "level"
        result["indicator_key"] = "sp500"
        result["required_tools"] = ["global_index", "get_indicator_context"]
        result["must_call_context"] = True
        return result
    if "나스닥" in text or "nasdaq" in lowered:
        result["item_type"] = "level"
        result["indicator_key"] = "nasdaq"
        result["required_tools"] = ["global_index", "get_indicator_context"]
        result["must_call_context"] = True
        return result

    # 7. 크립토
    if "비트코인" in text or "btc" in lowered:
        result["item_type"] = "level"
        result["indicator_key"] = "btc"
        result["required_tools"] = ["crypto_price", "get_indicator_context"]
        result["must_call_context"] = True
        return result
    if "이더리움" in text or "eth" in lowered:
        result["item_type"] = "level"
        result["indicator_key"] = "eth"
        result["required_tools"] = ["crypto_price", "get_indicator_context"]
        result["must_call_context"] = True
        return result

    # 7-1. 원자재 & VIX
    if "wti" in lowered or "유가" in text or "원유" in text:
        result["item_type"] = "level"
        result["indicator_key"] = "oil"
        result["required_tools"] = ["get_indicator_context"]
        result["must_call_context"] = True
        return result
    if "금값" in text or "금 가격" in text or text.strip().startswith("금"):
        result["item_type"] = "level"
        result["indicator_key"] = "gold"
        result["required_tools"] = ["get_indicator_context"]
        result["must_call_context"] = True
        return result
    if "vix" in lowered or "변동성지수" in text:
        result["item_type"] = "level"
        result["indicator_key"] = "vix"
        result["required_tools"] = ["get_indicator_context"]
        result["must_call_context"] = True
        return result

    # 8. 뉴스/이벤트
    if any(k in text for k in ["뉴스", "이벤트", "발표", "협약", "회의", "FOMC"]):
        result["item_type"] = "news"
        result["required_tools"] = ["news_search_naver"]
        return result

    # 9. 섹터
    if any(k in text for k in ["섹터", "업종"]):
        result["item_type"] = "general"
        result["required_tools"] = ["sector_performance"]
        return result

    # 10. 기본
    return result


def parse_checklist(checklist_text: str) -> List[ChecklistItem]:
    """
    체크리스트 원문을 항목 단위로 파싱
    - 섹션 헤더 감지 (장 시작 전 / 장 중 / 가격 레벨)
    - □ 또는 - □ 로 시작하는 항목 추출
    """
    items: List[ChecklistItem] = []
    current_section = "unknown"
    idx = 0

    for raw_line in checklist_text.split("\n"):
        line = raw_line.strip()
        if not line:
            continue

        # 섹션 헤더 감지
        if not ("□" in line or "☐" in line):
            if "장 시작 전" in line or "장시작전" in line:
                current_section = "pre_market"
            elif "장 중" in line or "장중" in line or "모니터링" in line:
                current_section = "intraday"
            elif any(k in line for k in ["가격 레벨", "주의", "레벨"]):
                current_section = "price_level"
            continue

        # 항목 추출
        # "- □ ..." 또는 "□ ..." 형식
        item_text = re.sub(r'^[-•]\s*[□☐]\s*', '', line)
        item_text = re.sub(r'^[□☐]\s*', '', item_text)
        item_text = item_text.strip()
        if not item_text:
            continue

        classified = _classify_item(item_text)
        items.append({
            "index": idx,
            "original": item_text,
            "section": current_section,
            **classified,
        })
        idx += 1

    logger.info(f"파싱 완료: {len(items)}개 항목")
    for item in items:
        logger.info(f"  [{item['section']}] [{item['item_type']}] "
                    f"{item.get('indicator_key')} threshold={item.get('threshold')} "
                    f"dir={item.get('direction')} tools={item.get('required_tools')}")

    return items


def filter_items_by_phase(items: list, time_phase: str) -> list:
    """시간대별 체크리스트 항목 필터링"""
    if time_phase == "pre_market":
        # 장 시작 전 항목만
        return [i for i in items if i.get("section") == "pre_market"]
    elif time_phase == "intraday":
        # 장 중 + 주의 가격 레벨 항목
        return [i for i in items if i.get("section") in ("intraday", "price_level")]
    elif time_phase == "close":
        # 장 마감은 전체 항목을 수집하되 explainer에서 요약 출력
        return items
    return items


async def parser_node(state: dict) -> dict:
    """LangGraph 노드 — 체크리스트 파싱 + 시간대별 필터링"""
    checklist_text = state.get("checklist_text", "")
    time_phase = state.get("time_phase", "intraday")

    if not checklist_text:
        return {"parsed_items": [], "errors": ["체크리스트 없음"]}

    items = parse_checklist(checklist_text)
    filtered = filter_items_by_phase(items, time_phase)

    logger.info(f"파싱 {len(items)}개 → 시간대 '{time_phase}' 필터 후 {len(filtered)}개")
    return {"parsed_items": filtered}
