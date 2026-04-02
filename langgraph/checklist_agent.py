"""
체크리스트 모니터링 에이전트
- MCP 도구를 활용하여 체크리스트 항목별 실시간 데이터 수집 및 판단
- Haiku + tool use로 비용 효율적
"""

import os
import logging
from typing import Dict, List

from langchain_anthropic import ChatAnthropic
from langchain_mcp_adapters.client import MultiServerMCPClient

from checklist_state import ChecklistState

logger = logging.getLogger(__name__)

HAIKU = "claude-haiku-4-5-20251001"

MCP_SERVER_URL = os.getenv("MCP_SERVER_URL", "http://bip-stock-mcp:8000/sse")


CHECKLIST_AGENT_PROMPT = """당신은 주식 시장 체크리스트 모니터링 에이전트입니다.

## 핵심 원칙
- 모든 항목에 반드시 도구를 호출하여 실시간 데이터를 수집하세요
- 도구 호출 없이 추측으로 답변하지 마세요
- 데이터 조회 실패 시 news_search_naver로 관련 뉴스를 검색하세요
- 체크리스트 항목과 별개로 투자자 수급(realtime_investor)은 반드시 확인하여 종합 판단에 포함하세요
- 삼성전자(005930)와 SK하이닉스(000660)는 체크리스트에 없더라도 반드시 현재가를 조회하여 포함하세요

## 도구 선택 가이드
- 종목 가격/지지/저항 → realtime_stock_price (코드: 005930=삼성전자, 000660=SK하이닉스)
- KOSPI/KOSDAQ 지수 → realtime_index
- 환율 → realtime_fx_rate
- 야간선물/갭/EWY → night_futures_ewy
- 외국인/기관 수급 → realtime_investor
- 프로그램 매매 → realtime_program_trade
- 글로벌 지수 (닛케이, S&P500) → global_index (심볼: NIKKEI, SP500, NASDAQ)
- 크립토 (BTC, ETH) → crypto_price
- 섹터 등락률 → sector_performance
- 뉴스/이벤트/정세 → news_search_naver (키워드 검색)

## 현재 시간대: {time_phase}
- pre_market: 장 시작 전. 야간선물, 환율, 글로벌 지수, 뉴스 위주
- intraday: 장중. 실시간 지수, 종목, 수급, 프로그램 매매 위주
- close: 장 마감. 체크리스트 전체 항목을 최종 점검하고 결과를 정리. 각 항목이 달성됐는지, 주요 수치가 어떻게 마감됐는지 종합 리뷰

## 출력 형식 (텔레그램용, 간결하게)
- 마크다운 헤딩(##, ###) 사용 금지
- 볼드는 *텍스트* (싱글 별표)만 사용
- 각 항목은 간결하게 (2~3줄 이내)
- 구분선(---, ━━━) 사용 금지. 빈 줄로 구분

□ *원문 항목*
→ 실제 데이터 기반 판단 (구체적 수치 포함)

📊 *주요 종목*: 삼성전자 현재가(등락률), SK하이닉스 현재가(등락률)
📊 *수급*: 외국인/기관/개인 순매수 현황
💡 *종합*: 전체 데이터를 종합한 시장 전망 한줄
"""


async def load_mcp_tools() -> list:
    """MCP 서버에서 도구 목록 로드"""
    try:
        client = MultiServerMCPClient({
            "bip-stock": {
                "url": MCP_SERVER_URL,
                "transport": "sse",
            }
        })
        tools = await client.get_tools()
        logger.info(f"MCP 도구 로드: {[t.name for t in tools]}")
        return tools
    except Exception as e:
        logger.warning(f"MCP 연결 실패: {e}")
        return []


async def checklist_analysis_node(state: ChecklistState) -> dict:
    """체크리스트 분석 노드 — MCP 도구 + Haiku"""
    from langgraph.prebuilt import create_react_agent

    checklist_text = state.get("checklist_text", "")
    time_phase = state.get("time_phase", "intraday")

    if not checklist_text:
        return {"analysis_result": "체크리스트 없음", "errors": ["체크리스트 텍스트 없음"]}

    # MCP 도구 로드
    tools = await load_mcp_tools()
    if not tools:
        return {"analysis_result": "도구 연결 실패", "errors": ["MCP 서버 연결 실패"]}

    # 실시간 모니터링에 관련된 도구만 필터
    realtime_tool_names = {
        "realtime_stock_price", "realtime_index", "realtime_investor",
        "realtime_program_trade", "realtime_fx_rate",
        "night_futures_ewy", "crypto_price", "sector_performance",
        "global_index", "news_search_naver", "db_investor_flow",
    }
    filtered_tools = [t for t in tools if t.name in realtime_tool_names]
    logger.info(f"사용 가능 도구: {[t.name for t in filtered_tools]}")

    # ReAct 에이전트 생성
    llm = ChatAnthropic(
        model=HAIKU,
        max_tokens=2000,
        api_key=os.getenv("ANTHROPIC_API_KEY"),
    )

    prompt = CHECKLIST_AGENT_PROMPT.format(time_phase=time_phase)

    agent = create_react_agent(
        llm,
        tools=filtered_tools,
        prompt=prompt,
    )

    try:
        result = await agent.ainvoke({
            "messages": [{"role": "user", "content": f"체크리스트:\n{checklist_text}"}],
        })

        # 최종 응답 추출
        messages = result.get("messages", [])
        final_text = ""
        for msg in reversed(messages):
            if hasattr(msg, "content") and isinstance(msg.content, str) and msg.content.strip():
                final_text = msg.content
                break

        # 토큰 사용량 추출
        token_usage = {}
        for msg in messages:
            usage = getattr(msg, "usage_metadata", None)
            if usage:
                token_usage = {
                    "input": usage.get("input_tokens", 0),
                    "output": usage.get("output_tokens", 0),
                    "total": usage.get("total_tokens", 0),
                }

        return {
            "analysis_result": final_text,
            "token_usage": {"checklist_agent": token_usage},
        }

    except Exception as e:
        logger.error(f"체크리스트 에이전트 실패: {e}")
        return {
            "analysis_result": f"분석 실패: {str(e)}",
            "errors": [str(e)],
        }
