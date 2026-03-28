"""
Morning Pulse 에이전트 정의
- global_agent: 글로벌 시장 분석 (Sonnet)
- korea_agent: 한국 시장 분석 (Sonnet + MCP)
- semi_agent: 반도체 분석 (Sonnet)
- flow_agent: 수급 분석 (Haiku)
- aggregator: 결과 통합 (Sonnet)
- quality_checker: 품질 검증 (Haiku)
"""

import os
import logging
from typing import Any
from langchain_anthropic import ChatAnthropic
from langgraph.prebuilt import create_react_agent

from state import ReportState

logger = logging.getLogger(__name__)

# ── 모델 설정 ──────────────────────────────────────────────────────────────────
SONNET = "claude-sonnet-4-6"
HAIKU = "claude-haiku-4-5-20251001"


def _llm(model: str, max_tokens: int = 4096) -> ChatAnthropic:
    return ChatAnthropic(
        model=model,
        max_tokens=max_tokens,
        api_key=os.getenv("ANTHROPIC_API_KEY"),
    )


# ── 글로벌 시장 에이전트 ───────────────────────────────────────────────────────
GLOBAL_PROMPT = """당신은 글로벌 시장 분석 전문가입니다.
제공된 시장 데이터(지수, 환율, 금리, VIX, 매크로)를 분석하여 다음을 작성하세요:

1. **글로벌 시장 요약** - 미국/유럽/아시아 주요 지수 동향
2. **매크로 환경** - 금리, 환율, VIX, 원자재 시사점
3. **오늘의 글로벌 리스크 & 기회** - 주목할 이슈 2~3개

신호등: 🟢(긍정) / 🟡(중립) / 🔴(부정) 으로 시작하세요.
간결하고 투자자 관점에서 실용적으로 작성하세요.
"""

async def global_agent_node(state: ReportState) -> dict:
    """글로벌 시장 분석 에이전트"""
    llm = _llm(SONNET)
    market_data = state["market_data"]

    # 글로벌 데이터만 추출
    global_data = {
        "indices": market_data.get("indices", {}),
        "exchange_rates": market_data.get("exchange_rates", {}),
        "interest_rates": market_data.get("interest_rates", {}),
        "macro": {k: v for k, v in market_data.get("macro", {}).items()
                  if k in ["vix", "dxy", "wti", "gold"]},
        "news": state.get("news_data", "")[:2000],  # 글로벌 관련 뉴스만
    }

    import json
    prompt = f"{GLOBAL_PROMPT}\n\n=== 시장 데이터 ===\n{json.dumps(global_data, ensure_ascii=False, indent=2)}"

    try:
        response = await llm.ainvoke(prompt)
        return {
            "global_analysis": response.content,
            "token_usage": {
                **state.get("token_usage", {}),
                "global": getattr(response, "usage_metadata", {}).get("total_tokens", 0),
            },
        }
    except Exception as e:
        logger.error(f"global_agent 실패: {e}")
        return {"global_analysis": f"[ERROR] {e}", "errors": state.get("errors", []) + [str(e)]}


# ── 한국 시장 에이전트 ─────────────────────────────────────────────────────────
KOREA_PROMPT = """당신은 한국 주식시장 분석 전문가입니다.
KOSPI/KOSDAQ 데이터와 수급 데이터를 분석하여 다음을 작성하세요:

1. **한국 시장 요약** - 주요 지수 동향 및 특징
2. **외국인/기관 수급** - 순매수/순매도 포인트
3. **주목 섹터/종목** - 강세/약세 섹터 2~3개
4. **오늘의 전망** - 한국 시장 전망 및 전략

신호등: 🟢(긍정) / 🟡(중립) / 🔴(부정) 으로 시작하세요.
필요하면 MCP 도구로 추가 데이터(공시, 종목 정보)를 직접 조회하세요.
"""

async def korea_agent_node(state: ReportState, mcp_tools: list = None) -> dict:
    """한국 시장 분석 에이전트 (MCP 도구 포함)"""
    tools = mcp_tools or []
    llm = _llm(SONNET)
    market_data = state["market_data"]

    korea_data = {
        "kospi": market_data.get("indices", {}).get("kospi", {}),
        "kosdaq": market_data.get("indices", {}).get("kosdaq", {}),
        "investor_flow": market_data.get("investor_flow", {}),
        "sectors": market_data.get("sectors", []),
        "news": state.get("news_data", "")[:2000],
    }

    import json
    prompt = f"{KOREA_PROMPT}\n\n=== 시장 데이터 ===\n{json.dumps(korea_data, ensure_ascii=False, indent=2)}"

    if tools:
        agent = create_react_agent(llm, tools)
        try:
            result = await agent.ainvoke({"messages": [{"role": "user", "content": prompt}]})
            content = result["messages"][-1].content
        except Exception as e:
            logger.error(f"korea_agent (ReAct) 실패: {e}")
            content = f"[ERROR] {e}"
    else:
        try:
            response = await llm.ainvoke(prompt)
            content = response.content
        except Exception as e:
            logger.error(f"korea_agent 실패: {e}")
            content = f"[ERROR] {e}"

    return {
        "korea_analysis": content,
        "token_usage": {**state.get("token_usage", {})},
    }


# ── 반도체 에이전트 ────────────────────────────────────────────────────────────
SEMI_PROMPT = """당신은 반도체/테크 섹터 분석 전문가입니다.
반도체 가격 데이터와 관련 뉴스를 분석하여 다음을 작성하세요:

1. **반도체 가격 동향** - DRAM/NAND 현물가 변화
2. **주요 반도체 종목** - 삼성전자/SK하이닉스/TSMC 등 동향
3. **AI/HBM 수요 동향** - 글로벌 AI 투자와 수요 전망
4. **리스크 요인** - 재고, 경쟁, 지정학 등

신호등: 🟢(긍정) / 🟡(중립) / 🔴(부정) 으로 시작하세요.
최신 뉴스에서 관련 정보를 적극 활용하세요.
"""

async def semi_agent_node(state: ReportState, mcp_tools: list = None) -> dict:
    """반도체 섹터 분석 에이전트"""
    tools = mcp_tools or []
    llm = _llm(SONNET)
    market_data = state["market_data"]

    semi_data = {
        "semiconductor_prices": market_data.get("semiconductor_prices", {}),
        "macro": {k: v for k, v in market_data.get("macro", {}).items()
                  if "semi" in k.lower() or "memory" in k.lower()},
        "news": state.get("news_data", "")[:1500],
    }

    import json
    prompt = f"{SEMI_PROMPT}\n\n=== 데이터 ===\n{json.dumps(semi_data, ensure_ascii=False, indent=2)}"

    if tools:
        agent = create_react_agent(llm, tools)
        try:
            result = await agent.ainvoke({"messages": [{"role": "user", "content": prompt}]})
            content = result["messages"][-1].content
        except Exception as e:
            content = f"[ERROR] {e}"
    else:
        try:
            response = await llm.ainvoke(prompt)
            content = response.content
        except Exception as e:
            content = f"[ERROR] {e}"

    return {"semi_analysis": content, "token_usage": {**state.get("token_usage", {})}}


# ── 수급 에이전트 ──────────────────────────────────────────────────────────────
FLOW_PROMPT = """당신은 투자자 수급 분석 전문가입니다.
외국인/기관/개인 수급 데이터를 분석하여 다음을 작성하세요:

1. **오늘의 수급 핵심** - 주요 매매 주체별 포인트
2. **수급 신호** - 단기 방향성 시사점
3. **주목 종목** - 수급 이상 종목 (있다면)

간결하게 작성하세요. 신호등: 🟢 / 🟡 / 🔴
"""

async def flow_agent_node(state: ReportState) -> dict:
    """수급 분석 에이전트 (Haiku — 비용 효율)"""
    llm = _llm(HAIKU, max_tokens=2048)
    market_data = state["market_data"]

    flow_data = {
        "investor_flow": market_data.get("investor_flow", {}),
        "program_trading": market_data.get("program_trading", {}),
    }

    import json
    prompt = f"{FLOW_PROMPT}\n\n=== 수급 데이터 ===\n{json.dumps(flow_data, ensure_ascii=False, indent=2)}"

    try:
        response = await llm.ainvoke(prompt)
        return {"flow_analysis": response.content}
    except Exception as e:
        return {"flow_analysis": f"[ERROR] {e}"}


# ── 집계 에이전트 ──────────────────────────────────────────────────────────────
AGGREGATOR_PROMPT = """당신은 Morning Pulse 리포트 편집장입니다.
각 에이전트의 분석 결과를 통합하여 일관성 있는 모닝 브리핑을 작성하세요.

출력 형식 (마크다운):

### 📌 오늘의 핵심
[3줄 이내 핵심 요약]

### 🌐 글로벌 시장 전망
[글로벌 분석 정리]

### 🇰🇷 한국 시장 전망
[한국 분석 정리]

### ✅ 오늘의 체크리스트
- [ ] 체크 항목 1
- [ ] 체크 항목 2
- [ ] 체크 항목 3

### ⚡ 대응 시나리오
**상승 시나리오**: ...
**하락 시나리오**: ...

### 💾 반도체/테크
[반도체 분석 정리]

### 💰 수급 동향
[수급 분석 정리]

중복 제거, 상충하는 분석은 더 구체적인 데이터 기준으로 통합하세요.
"""

async def aggregator_node(state: ReportState) -> dict:
    """분석 결과 통합 에이전트"""
    llm = _llm(SONNET, max_tokens=6000)

    sections = {
        "글로벌 분석": state.get("global_analysis", ""),
        "한국 시장 분석": state.get("korea_analysis", ""),
        "반도체 분석": state.get("semi_analysis", ""),
        "수급 분석": state.get("flow_analysis", ""),
    }

    import json
    analyses = "\n\n".join([f"=== {k} ===\n{v}" for k, v in sections.items() if v])
    prompt = f"{AGGREGATOR_PROMPT}\n\n{analyses}"

    try:
        response = await llm.ainvoke(prompt)
        return {"aggregated_report": response.content}
    except Exception as e:
        return {"aggregated_report": f"[ERROR] {e}"}


# ── 품질 검증 에이전트 ─────────────────────────────────────────────────────────
QUALITY_PROMPT = """당신은 금융 리포트 품질 검증자입니다.
다음 Morning Pulse 리포트를 검토하고 품질을 평가하세요.

검증 항목:
1. 수치 일관성 — 본문의 수치가 데이터와 일치하는가
2. 할루시네이션 — 데이터에 없는 정보를 지어낸 부분이 있는가
3. 신호등 일관성 — 🟢/🟡/🔴 판단이 데이터와 맞는가
4. 체크리스트 완성도 — 실질적이고 구체적인 항목인가
5. 형식 준수 — 필수 섹션(핵심/전망/체크리스트/시나리오)이 모두 있는가

응답 형식:
PASS 또는 FAIL
피드백: [구체적인 수정 필요 사항, 없으면 "없음"]
"""

async def quality_checker_node(state: ReportState) -> dict:
    """품질 검증 에이전트"""
    llm = _llm(HAIKU, max_tokens=1024)
    report = state.get("aggregated_report", "")

    if not report or report.startswith("[ERROR]"):
        return {"quality_passed": False, "quality_feedback": "리포트 생성 실패"}

    prompt = f"{QUALITY_PROMPT}\n\n=== 리포트 ===\n{report}"

    try:
        response = await llm.ainvoke(prompt)
        content = response.content
        passed = content.strip().upper().startswith("PASS")
        feedback = content.split("피드백:")[-1].strip() if "피드백:" in content else content

        if state.get("retry_count", 0) >= 2:
            # 최대 재시도 후 강제 통과
            passed = True
            feedback = "최대 재시도 도달 — 강제 통과"

        return {
            "quality_passed": passed,
            "quality_feedback": feedback,
            "retry_count": state.get("retry_count", 0) + (0 if passed else 1),
        }
    except Exception as e:
        return {"quality_passed": True, "quality_feedback": f"검증 오류 (통과 처리): {e}"}
