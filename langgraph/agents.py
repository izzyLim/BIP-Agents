"""
Morning Pulse 에이전트 정의
- global_agent: 글로벌 시장 분석 (Sonnet)
- korea_agent: 한국 시장 분석 (Sonnet + MCP)
- semi_agent: 반도체 분석 (Sonnet)
- flow_agent: 수급 분석 (Haiku)
- aggregator: 결과 통합 (Sonnet)
- quality_checker: 품질 검증 (Haiku)
"""

import json
import os
import logging
from typing import Any, Dict
from langchain_anthropic import ChatAnthropic
from langgraph.prebuilt import create_react_agent

from state import ReportState

logger = logging.getLogger(__name__)

SONNET = "claude-sonnet-4-6"
HAIKU  = "claude-haiku-4-5-20251001"


def _llm(model: str, max_tokens: int = 4096) -> ChatAnthropic:
    return ChatAnthropic(
        model=model,
        max_tokens=max_tokens,
        api_key=os.getenv("ANTHROPIC_API_KEY"),
    )


def _extract_tokens(response) -> Dict[str, int]:
    """LangChain ChatAnthropic 응답에서 토큰 수 추출"""
    usage = getattr(response, "usage_metadata", None) or {}
    return {
        "input":  usage.get("input_tokens", 0),
        "output": usage.get("output_tokens", 0),
        "total":  usage.get("total_tokens", 0),
    }


def _log_tokens(agent: str, tokens: Dict[str, int]):
    logger.info(f"💰 [{agent}] input={tokens['input']:,} / output={tokens['output']:,} tokens")


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

    global_data = {
        "indices": market_data.get("indices", {}),
        "exchange_rates": market_data.get("exchange_rates", {}),
        "interest_rates": market_data.get("interest_rates", {}),
        "macro": {k: v for k, v in market_data.get("macro", {}).items()
                  if k in ["vix", "dxy", "wti", "gold"]},
        "news": state.get("news_data", "")[:2000],
    }

    prompt = f"{GLOBAL_PROMPT}\n\n=== 시장 데이터 ===\n{json.dumps(global_data, ensure_ascii=False, indent=2)}"

    try:
        response = await llm.ainvoke(prompt)
        tokens = _extract_tokens(response)
        _log_tokens("global", tokens)
        return {
            "global_analysis": response.content,
            "token_usage": {"global": tokens},
        }
    except Exception as e:
        logger.error(f"global_agent 실패: {e}")
        return {
            "global_analysis": f"[ERROR] {e}",
            "token_usage": {"global": {"input": 0, "output": 0, "total": 0}},
            "errors": state.get("errors", []) + [str(e)],
        }


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

    prompt = f"{KOREA_PROMPT}\n\n=== 시장 데이터 ===\n{json.dumps(korea_data, ensure_ascii=False, indent=2)}"

    total_input = total_output = 0

    if tools:
        agent = create_react_agent(llm, tools)
        try:
            result = await agent.ainvoke({"messages": [{"role": "user", "content": prompt}]})
            content = result["messages"][-1].content
            # ReAct: 메시지별 토큰 합산
            for msg in result["messages"]:
                u = getattr(msg, "usage_metadata", None) or {}
                total_input  += u.get("input_tokens", 0)
                total_output += u.get("output_tokens", 0)
        except Exception as e:
            logger.error(f"korea_agent (ReAct) 실패: {e}")
            content = f"[ERROR] {e}"
    else:
        try:
            response = await llm.ainvoke(prompt)
            content = response.content
            tokens = _extract_tokens(response)
            total_input, total_output = tokens["input"], tokens["output"]
        except Exception as e:
            logger.error(f"korea_agent 실패: {e}")
            content = f"[ERROR] {e}"

    tokens = {"input": total_input, "output": total_output, "total": total_input + total_output}
    _log_tokens("korea", tokens)
    return {"korea_analysis": content, "token_usage": {"korea": tokens}}


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

    prompt = f"{SEMI_PROMPT}\n\n=== 데이터 ===\n{json.dumps(semi_data, ensure_ascii=False, indent=2)}"

    total_input = total_output = 0

    if tools:
        agent = create_react_agent(llm, tools)
        try:
            result = await agent.ainvoke({"messages": [{"role": "user", "content": prompt}]})
            content = result["messages"][-1].content
            for msg in result["messages"]:
                u = getattr(msg, "usage_metadata", None) or {}
                total_input  += u.get("input_tokens", 0)
                total_output += u.get("output_tokens", 0)
        except Exception as e:
            content = f"[ERROR] {e}"
    else:
        try:
            response = await llm.ainvoke(prompt)
            content = response.content
            tokens = _extract_tokens(response)
            total_input, total_output = tokens["input"], tokens["output"]
        except Exception as e:
            content = f"[ERROR] {e}"

    tokens = {"input": total_input, "output": total_output, "total": total_input + total_output}
    _log_tokens("semi", tokens)
    return {"semi_analysis": content, "token_usage": {"semi": tokens}}


# ── 수급 에이전트 ──────────────────────────────────────────────────────────────
FLOW_PROMPT = """당신은 투자자 수급 분석 전문가입니다.
외국인/기관/개인 수급 데이터를 분석하여 다음을 작성하세요:

1. **오늘의 수급 핵심** - 주요 매매 주체별 포인트
2. **수급 신호** - 단기 방향성 시사점
3. **주목 종목** - 수급 이상 종목 (있다면)

간결하게 작성하세요. 신호등: 🟢 / 🟡 / 🔴
"""

async def flow_agent_node(state: ReportState) -> dict:
    """수급 분석 에이전트 (Haiku)"""
    llm = _llm(HAIKU, max_tokens=2048)
    market_data = state["market_data"]

    flow_data = {
        "investor_flow": market_data.get("investor_flow", {}),
        "program_trading": market_data.get("program_trading", {}),
    }

    prompt = f"{FLOW_PROMPT}\n\n=== 수급 데이터 ===\n{json.dumps(flow_data, ensure_ascii=False, indent=2)}"

    try:
        response = await llm.ainvoke(prompt)
        tokens = _extract_tokens(response)
        _log_tokens("flow", tokens)
        return {"flow_analysis": response.content, "token_usage": {"flow": tokens}}
    except Exception as e:
        return {
            "flow_analysis": f"[ERROR] {e}",
            "token_usage": {"flow": {"input": 0, "output": 0, "total": 0}},
        }


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

    # 품질 피드백이 있으면 재작성 지시로 포함
    feedback = state.get("quality_feedback", "")
    feedback_section = f"\n\n=== 품질 검증 피드백 (반드시 반영) ===\n{feedback}" if feedback else ""

    analyses = "\n\n".join([f"=== {k} ===\n{v}" for k, v in sections.items() if v])
    prompt = f"{AGGREGATOR_PROMPT}\n\n{analyses}{feedback_section}"

    try:
        response = await llm.ainvoke(prompt)
        tokens = _extract_tokens(response)
        _log_tokens("aggregator", tokens)
        existing = state.get("token_usage", {})
        return {
            "aggregated_report": response.content,
            "token_usage": {**existing, "aggregator": tokens},
        }
    except Exception as e:
        return {"aggregated_report": f"[ERROR] {e}"}


# ── 품질 검증 에이전트 ─────────────────────────────────────────────────────────
QUALITY_PROMPT = """당신은 Morning Pulse 리포트 품질 검증자입니다.
아래 리포트와 원본 시장 데이터를 비교하여 품질을 평가하세요.

검증 항목 (각 항목은 독립적으로 평가):
1. **형식 준수** — 필수 섹션 5개가 모두 있는가
   (📌오늘의핵심 / 🌐글로벌 / 🇰🇷한국 / ✅체크리스트 / ⚡시나리오)
2. **수치 일관성** — 리포트 본문의 수치가 원본 시장 데이터와 크게 다르지 않은가
   (MCP로 추가 조회한 최신 정보는 원본 데이터에 없어도 PASS)
3. **체크리스트 품질** — 항목이 3개 이상이고 구체적인가 (막연한 "모니터링" 금지)
4. **시나리오 완성도** — 상승/하락 시나리오 모두 있고 구체적 조건이 명시되었는가
5. **신호등 존재** — 각 섹션에 🟢/🟡/🔴 중 하나 이상 있는가

판정 기준:
- 5개 항목 중 4개 이상 통과 → PASS
- 3개 이하 통과 → FAIL (구체적 수정 사항 명시)

⚠️ 응답 규칙 (반드시 준수):
- 첫 번째 단어는 반드시 PASS 또는 FAIL 중 하나만 출력
- 그 외 다른 텍스트, 마크다운 헤더, 설명을 첫 줄에 쓰지 말 것
- 형식: PASS\n피드백: 없음  또는  FAIL\n피드백: [수정 사항]
"""

async def quality_checker_node(state: ReportState) -> dict:
    """품질 검증 에이전트"""
    llm = _llm(HAIKU, max_tokens=1024)
    report = state.get("aggregated_report", "")

    if not report or report.startswith("[ERROR]"):
        return {"quality_passed": False, "quality_feedback": "리포트 생성 실패"}

    # 강제 통과 조건: 최대 재시도 초과
    if state.get("retry_count", 0) >= 2:
        logger.warning("최대 재시도 도달 — 강제 통과")
        return {"quality_passed": True, "quality_feedback": "최대 재시도 도달 — 강제 통과"}

    # market_data 핵심 수치만 요약해서 전달 (토큰 절약)
    market_data = state.get("market_data", {})
    data_summary = {
        "indices":   {k: v.get("value") for k, v in market_data.get("indices", {}).items()},
        "usd_krw":   market_data.get("exchange_rates", {}).get("usd_krw", {}).get("value"),
        "vix":       market_data.get("macro", {}).get("vix", {}).get("value"),
        "investor_flow": {k: v.get("net") for k, v in market_data.get("investor_flow", {}).items()},
    }

    prompt = (
        f"{QUALITY_PROMPT}\n\n"
        f"=== 원본 시장 데이터 (핵심 수치) ===\n{json.dumps(data_summary, ensure_ascii=False, indent=2)}\n\n"
        f"=== 리포트 ===\n{report}"
    )

    try:
        response = await llm.ainvoke(prompt)
        tokens = _extract_tokens(response)
        _log_tokens("quality", tokens)

        content = response.content
        # "PASS" 판정: 첫 줄 또는 "판정:" 뒤에 PASS가 있으면 통과
        first_lines = content[:300].upper()
        passed = (
            first_lines.strip().startswith("PASS")
            or "판정: PASS" in content
            or "판정:PASS" in content
            or "\nPASS" in first_lines
        )
        feedback = content.split("피드백:")[-1].strip() if "피드백:" in content else content

        existing = state.get("token_usage", {})
        return {
            "quality_passed": passed,
            "quality_feedback": feedback,
            "retry_count": state.get("retry_count", 0) + (0 if passed else 1),
            "token_usage": {**existing, "quality": tokens},
        }
    except Exception as e:
        return {"quality_passed": True, "quality_feedback": f"검증 오류 (통과 처리): {e}"}
