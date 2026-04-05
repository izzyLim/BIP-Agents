"""
데이터 수집 노드 (deterministic, LLM 없음)
- 파싱된 항목별로 required_tools를 코드에서 직접 호출
- must_call_context=True면 get_indicator_context 강제 호출
- gathered_data에 저장 (item_index → {raw, context})
"""

import logging
from typing import Dict, Any

from langchain_mcp_adapters.client import MultiServerMCPClient
import os

logger = logging.getLogger(__name__)

MCP_SERVER_URL = os.getenv("MCP_SERVER_URL", "http://bip-stock-mcp:8000/sse")


# 항상 수집하는 주요 종목 (체크리스트 포함 여부와 무관)
# KOSPI 시총/업종 대표 10선
ALWAYS_COLLECT_STOCKS = [
    ("005930", "삼성전자"),
    ("000660", "SK하이닉스"),
    ("373220", "LG에너지솔루션"),
    ("207940", "삼성바이오로직스"),
    ("005380", "현대차"),
    ("000270", "기아"),
    ("005490", "POSCO홀딩스"),
    ("035420", "NAVER"),
    ("035720", "카카오"),
    ("006400", "삼성SDI"),
]


# indicator_key → (context_indicator_type, region)
CONTEXT_MAPPING = {
    "fx_krw": ("exchange_rate", "South Korea"),
    "kospi": ("stock_index_kospi", "South Korea"),
    "kosdaq": ("stock_index_kosdaq", "South Korea"),
    "sp500": ("stock_index_sp500", "North America"),
    "nasdaq": ("stock_index_nasdaq", "North America"),
    "nikkei": ("stock_index_nikkei", "Japan"),
    "btc": ("crypto_btc", None),
    "eth": ("crypto_eth", None),
    "oil": ("commodity_oil", "North America"),
    "gold": ("commodity_gold", "North America"),
    "vix": ("vix", "North America"),
}


async def _call_tool(tools_map: dict, tool_name: str, args: dict):
    """도구 호출 헬퍼 (dict 반환 보장)"""
    import json as _json

    tool = tools_map.get(tool_name)
    if not tool:
        return {"error": f"도구 없음: {tool_name}"}

    def _unwrap(result):
        """MCP 반환 구조 정규화 → dict"""
        # langchain_mcp_adapters는 [{"type": "text", "text": "json"}] 형태로 반환
        if isinstance(result, list) and result:
            first = result[0]
            if isinstance(first, dict) and "text" in first:
                try:
                    parsed = _json.loads(first["text"])
                    return parsed if isinstance(parsed, dict) else {"items": parsed}
                except Exception:
                    return {"raw": first.get("text", "")}
            return {"items": result}

        if isinstance(result, str):
            try:
                parsed = _json.loads(result)
                return parsed if isinstance(parsed, dict) else {"items": parsed}
            except Exception:
                return {"raw": result}

        if isinstance(result, dict):
            return result

        return {"raw": str(result)}

    try:
        result = await tool.ainvoke(args)
        return _unwrap(result)
    except Exception as e:
        logger.error(f"{tool_name} 호출 실패: {e}")
        return {"error": str(e)}


async def _extract_current_value(raw_data, indicator_key: str):
    """raw 도구 결과에서 현재값 추출"""
    if not isinstance(raw_data, dict) or "error" in raw_data:
        return None

    if indicator_key == "fx_krw":
        return raw_data.get("value")
    if indicator_key in ("kospi", "kosdaq"):
        return raw_data.get("value")
    if indicator_key in ("sp500", "nasdaq", "nikkei"):
        return raw_data.get("value")
    if indicator_key in ("btc", "eth"):
        return raw_data.get("price_krw")
    if indicator_key and indicator_key.startswith("stock:"):
        return raw_data.get("price")
    return None


async def collector_node(state: dict) -> dict:
    """LangGraph 노드 — MCP 도구 코드 레벨 호출"""
    parsed_items = state.get("parsed_items", [])
    if not parsed_items:
        return {"gathered_data": {}}

    # MCP 도구 로드
    try:
        client = MultiServerMCPClient({
            "bip-stock": {"url": MCP_SERVER_URL, "transport": "sse"}
        })
        tools = await client.get_tools()
        tools_map = {t.name: t for t in tools}
    except Exception as e:
        logger.error(f"MCP 연결 실패: {e}")
        return {"gathered_data": {}, "errors": [f"MCP 연결 실패: {e}"]}

    gathered: Dict[int, Dict[str, Any]] = {}

    for item in parsed_items:
        idx = item["index"]
        item_data = {"raw": None, "context": None}

        required = item.get("required_tools", [])
        indicator_key = item.get("indicator_key")

        # 1. 기본 실시간 도구 호출
        for tool_name in required:
            if tool_name == "get_indicator_context":
                continue  # 컨텍스트는 아래에서 처리

            # 도구별 인자 매핑
            if tool_name == "realtime_stock_price" and indicator_key and indicator_key.startswith("stock:"):
                code = indicator_key.split(":")[1]
                raw = await _call_tool(tools_map, tool_name, {"code": code})
            elif tool_name == "realtime_index":
                idx_name = "KOSPI" if indicator_key == "kospi" else "KOSDAQ"
                raw = await _call_tool(tools_map, tool_name, {"index_name": idx_name})
            elif tool_name == "global_index":
                symbol_map = {"nikkei": "NIKKEI", "sp500": "SP500", "nasdaq": "NASDAQ"}
                raw = await _call_tool(tools_map, tool_name, {"symbol": symbol_map.get(indicator_key, "NIKKEI")})
            elif tool_name == "crypto_price":
                raw = await _call_tool(tools_map, tool_name, {"symbol": "BTC"})
            elif tool_name == "news_search_naver":
                # 원문에서 키워드 2~3개 추출
                query = item["original"][:30]
                raw = await _call_tool(tools_map, tool_name, {"query": query, "display": 3})
            elif tool_name == "sector_performance":
                raw = await _call_tool(tools_map, tool_name, {"sector_name": item["original"][:10]})
            else:
                # 파라미터 없는 도구
                raw = await _call_tool(tools_map, tool_name, {})

            # 첫 성공 결과만 item_data["raw"]에 저장
            if isinstance(raw, dict) and "error" not in raw and not item_data["raw"]:
                item_data["raw"] = raw

        # 2. must_call_context면 get_indicator_context 강제 호출
        if item.get("must_call_context") and indicator_key in CONTEXT_MAPPING:
            indicator_type, region = CONTEXT_MAPPING[indicator_key]
            current = await _extract_current_value(item_data.get("raw"), indicator_key)
            ctx_args = {"indicator_type": indicator_type}
            if region:
                ctx_args["region"] = region
            if current is not None:
                ctx_args["current_value"] = current
            context = await _call_tool(tools_map, "get_indicator_context", ctx_args)
            item_data["context"] = context

        gathered[idx] = item_data
        logger.info(f"  item[{idx}] {indicator_key}: raw={bool(item_data['raw'])}, ctx={bool(item_data['context'])}")

    # 항상 수집할 주요 종목 (체크리스트 포함 여부 무관)
    always_stocks = {}
    for code, name in ALWAYS_COLLECT_STOCKS:
        # 이미 체크리스트에서 수집된 종목은 스킵 (중복 호출 방지)
        in_checklist = any(
            item.get("indicator_key") == f"stock:{code}"
            for item in parsed_items
        )
        if in_checklist:
            continue
        raw = await _call_tool(tools_map, "realtime_stock_price", {"code": code})
        if isinstance(raw, dict) and "error" not in raw:
            raw["_name"] = name
            always_stocks[code] = raw

    if always_stocks:
        logger.info(f"  추가 주요 종목 수집: {len(always_stocks)}개")

    return {
        "gathered_data": gathered,
        "always_stocks": always_stocks,
    }
