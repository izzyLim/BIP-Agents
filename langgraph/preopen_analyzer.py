"""
장 시작 전 예상 체결가 분석 (08:40 실행용)
- 한투 API로 KOSPI/KOSDAQ/주요 종목 예상 체결가 조회
- 전일 미국 마감 + EWY 야간선물 + 환율 맥락과 결합
- Haiku로 갭 방향 한줄 분석
"""

import logging
import os
import time
import json

from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_anthropic import ChatAnthropic

logger = logging.getLogger(__name__)

MCP_SERVER_URL = os.getenv("MCP_SERVER_URL", "http://bip-stock-mcp:8000/sse")
HAIKU = "claude-haiku-4-5-20251001"

# 장 시작 전 관찰 대상 (KOSPI 시총/업종 대표 10선)
PREOPEN_STOCKS = [
    ("005930", "삼성전자"),         # 반도체 1
    ("000660", "SK하이닉스"),       # 반도체 2
    ("373220", "LG에너지솔루션"),   # 2차전지 1
    ("207940", "삼성바이오로직스"),  # 바이오 1
    ("005380", "현대차"),           # 자동차 1
    ("000270", "기아"),             # 자동차 2
    ("005490", "POSCO홀딩스"),      # 소재
    ("035420", "NAVER"),            # IT 1
    ("035720", "카카오"),           # IT 2
    ("006400", "삼성SDI"),          # 2차전지 2
]


def _unwrap(result):
    """MCP 응답 정규화"""
    if isinstance(result, list) and result:
        first = result[0]
        if isinstance(first, dict) and "text" in first:
            try:
                parsed = json.loads(first["text"])
                return parsed if isinstance(parsed, dict) else {"items": parsed}
            except Exception:
                return {"raw": first.get("text", "")}
    if isinstance(result, str):
        try:
            return json.loads(result)
        except Exception:
            return {"raw": result}
    return result if isinstance(result, dict) else {"raw": str(result)}


async def _call(tools_map, name, args):
    tool = tools_map.get(name)
    if not tool:
        return {"error": f"도구 없음: {name}"}
    try:
        return _unwrap(await tool.ainvoke(args))
    except Exception as e:
        logger.error(f"{name} 실패: {e}")
        return {"error": str(e)}


async def run_preopen_analysis() -> dict:
    """
    장 시작 전 예상 체결가 + 맥락 수집 + Haiku 분석
    Returns: {analysis_result, token_usage, data, errors}
    """
    start = time.time()

    try:
        client = MultiServerMCPClient({
            "bip-stock": {"url": MCP_SERVER_URL, "transport": "sse"}
        })
        tools = await client.get_tools()
        tools_map = {t.name: t for t in tools}
    except Exception as e:
        return {
            "analysis_result": f"MCP 연결 실패: {e}",
            "errors": [str(e)],
            "token_usage": {},
        }

    data = {}

    # 1. 지수 예상가
    data["kospi"] = await _call(tools_map, "preopen_index", {"index_name": "KOSPI"})
    data["kosdaq"] = await _call(tools_map, "preopen_index", {"index_name": "KOSDAQ"})

    # 2. 주요 종목 예상가
    data["stocks"] = {}
    for code, name in PREOPEN_STOCKS:
        stock = await _call(tools_map, "preopen_stock_price", {"code": code})
        if isinstance(stock, dict):
            stock["name"] = name
            data["stocks"][code] = stock

    # 3. 맥락 데이터
    data["fx"] = await _call(tools_map, "realtime_fx_rate", {})
    data["night_futures"] = await _call(tools_map, "night_futures_ewy", {})
    data["sp500"] = await _call(tools_map, "global_index", {"symbol": "SP500"})
    data["nasdaq"] = await _call(tools_map, "global_index", {"symbol": "NASDAQ"})

    # 4. 환율 맥락 (역대 고점권 여부)
    data["fx_context"] = await _call(tools_map, "get_indicator_context", {
        "indicator_type": "exchange_rate",
        "region": "South Korea",
    })

    # Haiku 분석
    llm = ChatAnthropic(
        model=HAIKU,
        max_tokens=1500,
        api_key=os.getenv("ANTHROPIC_API_KEY"),
    )

    context_text = f"""
## 장 시작 전 데이터 (08:30~09:00)

### 지수 예상 시가
- KOSPI: {json.dumps(data['kospi'], ensure_ascii=False)}
- KOSDAQ: {json.dumps(data['kosdaq'], ensure_ascii=False)}

### 주요 종목 예상가
"""
    for code, stock in data["stocks"].items():
        context_text += f"- {stock.get('name')} ({code}): {json.dumps(stock, ensure_ascii=False)}\n"

    context_text += f"""

### 해외/환율 맥락
- 원/달러: {json.dumps(data['fx'], ensure_ascii=False)}
- 환율 맥락: {json.dumps(data['fx_context'], ensure_ascii=False)[:300]}
- 야간선물 EWY: {json.dumps(data['night_futures'], ensure_ascii=False)[:300]}
- S&P500: {json.dumps(data['sp500'], ensure_ascii=False)[:200]}
- NASDAQ: {json.dumps(data['nasdaq'], ensure_ascii=False)[:200]}
"""

    from datetime import datetime as _dt
    today_str = _dt.now().strftime("%Y-%m-%d")

    system_prompt = f"""당신은 한국 증시 장 시작 직전(08:40) 시장 분석 전문가입니다.

아래 데이터를 기반으로 텔레그램 메시지(6~8줄)를 작성하세요.
오늘 날짜는 {today_str}입니다.

## 출력 형식
🔔 *장 시작 임박 — {today_str} 08:40*

📊 *예상 시가*
*KOSPI* 예상값 (예상 등락률)
*KOSDAQ* 예상값 (예상 등락률)

📈 *주요 종목 예상*
- 삼성전자, SK하이닉스, LG에너지솔루션, 삼성바이오로직스, 현대차, 기아, POSCO홀딩스, NAVER, 카카오, 삼성SDI (간결하게)

🌍 *해외 & 환율*
- 미국 마감 + EWY 야간선물 + 환율 맥락

💡 *갭 방향*: 한 줄 요약 (약세/강세/혼조, 근거)

## 규칙
- 숫자는 제공된 데이터에서만 사용 (추측 금지)
- has_expected=false면 "동시호가 미개시"로 표시
- 텔레그램 마크다운: *볼드*만 사용, ##헤딩 금지
- 간결하게 (8줄 이내)"""

    try:
        resp = await llm.ainvoke([
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": context_text},
        ])
        analysis = resp.content if isinstance(resp.content, str) else str(resp.content)
        usage = getattr(resp, "usage_metadata", None) or {}

        return {
            "analysis_result": analysis,
            "token_usage": {
                "preopen_analyzer": {
                    "input": usage.get("input_tokens", 0),
                    "output": usage.get("output_tokens", 0),
                    "total": usage.get("total_tokens", 0),
                }
            },
            "data": data,
            "elapsed_ms": int((time.time() - start) * 1000),
            "errors": [],
        }
    except Exception as e:
        logger.error(f"preopen 분석 실패: {e}")
        return {
            "analysis_result": f"분석 실패: {e}",
            "token_usage": {},
            "data": data,
            "elapsed_ms": int((time.time() - start) * 1000),
            "errors": [str(e)],
        }
