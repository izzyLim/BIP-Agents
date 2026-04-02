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

OPUS   = "claude-opus-4-6"
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
아래 순서대로 도구를 호출하여 데이터를 수집한 뒤 분석하세요.

분석 순서:
1. `db_market_indices` 호출 — KOSPI/KOSDAQ 등락 확인
2. `db_investor_flow` 호출 — 외국인/기관/개인 순매수 집계 확인
3. `db_investor_trading` 호출 — 종목별 수급 상세 확인
4. `news_search_naver`로 시장 배경 뉴스 검색
   - db_market_indices/db_investor_trading 결과를 보고 당일 주요 이슈에 맞는 키워드로 검색
   - 수급 상위 종목명으로 추가 검색
5. 뉴스에서 특이 이슈 포착 시 추가 조사
   - 특정 종목 이슈 → `dart_corp_search` + `dart_disclosure_list`로 공시 확인

수집한 정보를 종합해 다음 항목 작성:
- **한국 시장 요약** - 지수 동향과 원인
- **외국인/기관 수급** - 수급 흐름과 배경 (db_investor_trading 수치 사용)
- **주목 섹터/종목** - 뉴스에서 확인된 강세/약세 배경
- **오늘의 전망** - 한국 시장 전망 및 전략

신호등: 🟢(긍정) / 🟡(중립) / 🔴(부정) 으로 시작하세요.
⚠️ 수치 규칙: 종목별 순매수/순매도 금액은 반드시 db_investor_trading 조회 값만 사용하세요.
"""

async def korea_agent_node(state: ReportState, mcp_tools: list = None) -> dict:
    """한국 시장 분석 에이전트 (MCP 도구 포함)"""
    tools = mcp_tools or []
    llm = _llm(SONNET, max_tokens=16000)
    market_data = state["market_data"]

    report_date = state.get("report_date", "")
    prompt = f"{KOREA_PROMPT}\n\n분석 기준일: {report_date}"

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
아래 순서대로 도구를 호출하여 데이터를 수집한 뒤 분석하세요.

분석 순서:
1. `db_semiconductor_prices` 호출 — DRAM/NAND 현물가 확인
2. `news_search_naver`로 최신 반도체 업계 동향 검색
   - db_semiconductor_prices 결과를 보고 주요 이슈에 맞는 키워드로 검색
   - 가격 변동이 큰 제품군, 주요 종목 이슈 등 당일 맥락에 맞게 검색어 생성
3. 뉴스에서 특이 이슈 포착 시 추가 조사
   - 주요 종목 이슈 → `dart_corp_search` + `dart_disclosure_list`로 공시 확인
   - 재무/실적 맥락 필요 시 → `dart_financial_statement` 활용

수집한 정보를 종합해 다음 항목 작성:
- **반도체 가격 동향** - DRAM/NAND 현물가 변화와 원인
- **주요 반도체 종목** - 삼성전자/SK하이닉스/TSMC 등 최신 이슈
- **AI/HBM 수요 동향** - 글로벌 AI 투자와 수요 전망
- **리스크 요인** - 재고, 경쟁, 지정학 등

신호등: 🟢(긍정) / 🟡(중립) / 🔴(부정) 으로 시작하세요.
"""

async def semi_agent_node(state: ReportState, mcp_tools: list = None) -> dict:
    """반도체 섹터 분석 에이전트"""
    tools = mcp_tools or []
    llm = _llm(SONNET, max_tokens=16000)
    market_data = state["market_data"]

    report_date = state.get("report_date", "")
    prompt = f"{SEMI_PROMPT}\n\n분석 기준일: {report_date}"

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
각 에이전트의 분석 결과를 통합하여 투자자를 위한 모닝 브리핑을 작성하세요.
총 2500자 이상, 충분히 상세하게 작성하세요.

⚠️ 규칙:
- 중복 제거, 상충하는 분석은 더 구체적인 데이터 기준으로 통합
- 수치는 에이전트가 제공한 데이터만 사용, 추정값 금지

**신호등 규칙**: 아래 5개 섹션 제목 끝에 반드시 신호등을 붙이세요.
- `[🟢]` 긍정/강세/호재: 수치가 개선되고 뉴스/정세도 우호적
- `[🟡]` 중립/혼조/불확실: 수치와 맥락이 엇갈리거나 방향이 불분명
- `[🔴]` 부정/약세/악재: 수치 악화 OR 주요 리스크 현재화

아래는 실제 수치 데이터 기반으로 사전 계산된 참고 신호입니다.
최종 신호는 뉴스, 지정학적 맥락, 시장 흐름을 종합해 당신이 직접 판단하세요.
수치 참고값과 다르게 판단했다면 그 이유를 보고서 본문에 반영하세요.

{reference_signals}

신호등이 붙는 섹션 (이 5개만):
- `## 🌍 글로벌 정세 & 매크로 환경 [🟡]` ← 이런 형식
- `## 🇰🇷 [과거] 어제 한국 시장 마감 결과 [🔴]`
- `## 🇺🇸 [최신] 오늘 새벽 미국 시장 마감 결과 [🔴]`
- `## 📈 [전망] 오늘 한국 시장 예상 [🟡]`
- `## 🔬 반도체 섹터 심층 분석 [🟢]`

출력 형식 (이 순서 그대로):

## 📌 오늘의 핵심
오늘 아침 투자자가 알아야 할 어제 시장 핵심을 불렛 포인트로 간결하게 작성하세요.
- 불렛 4~6개, 항목당 1줄 이내
- 수치와 팩트 중심, 전망·해석 없이 사실만
- 예시: `• KOSPI -0.8% 하락, 외국인 -3,200억 순매도`

---

## 🌍 글로벌 정세 & 매크로 환경
- 글로벌 지정학/경제 이슈와 시장 영향
- 달러, 금리, 원자재, 비트코인 동향
- 한국 시장에 미치는 영향

---

## 🔥 특별 이벤트/사건 심층 분석

**※ 에이전트 분석 및 뉴스에서 아래 이벤트가 언급된 경우에만 이 섹션을 작성하세요. 없으면 섹션 자체를 생략하세요.**
- 테크 컨퍼런스: NVIDIA GTC, 애플 WWDC, CES, MS Build 등
- 중앙은행: FOMC, BOJ, ECB 회의, 금리 결정
- 지정학: 전쟁, 무역 갈등, 제재, 정상회담
- 기업 이벤트: 대형 실적 발표, M&A, IPO, 파산
- 경제지표: 고용, GDP, 물가지표 발표

있다면 다음 구성으로 작성: 🎯 이벤트 개요 → 📜 배경 및 맥락 → 💰 시장 영향 → 🔮 향후 시나리오 → 📌 투자자 대응
세부 항목은 ### 으로 구분하세요.

---

## 🇰🇷 [과거] 어제 한국 시장 마감 결과
- 코스피/코스닥 등락 원인 심층 분석 (미국 시장 언급 금지)
- 외국인/기관/개인 수급 분석 — 집중 매수/매도 종목과 의미
- 강세/약세 섹터와 배경

---

## 🇺🇸 [최신] 오늘 새벽 미국 시장 마감 결과
- 주요 지수 등락과 원인
- 섹터별 차별화 움직임, 주요 종목 이슈
- 글로벌 투자 심리 변화 (리스크온/오프)

---

## 📈 [전망] 오늘 한국 시장 예상
- 새벽 미국장이 한국장에 미칠 영향
- 코스피/코스닥 예상 방향, 주목 섹터/종목
- 갭 예상 및 장 초반 대응 전략

---

## 🔬 반도체 섹터 심층 분석
- DRAM/NAND 현물가 현황과 추이
- HBM/AI 수요 동향
- 삼성전자/SK하이닉스 동향 및 투자 시사점

---

## 💡 투자 아이디어 & 전략
- 기회 섹터/종목과 이유
- 리스크 요인과 대응

---

## 📅 이번 주 주요 이벤트
| 날짜 | 이벤트 | 영향 예상 |
|------|--------|-----------|
| (날짜) | (이벤트명) | (영향) |

---

## ✅ 오늘의 체크리스트
□ (장 시작 전 확인할 것 — 구체적으로)
□ (장 중 모니터링할 것)
□ (주의해야 할 가격 레벨이나 이벤트)

---

참고: 위 분석은 투자 권유가 아닌 정보 제공 목적입니다.
"""

async def aggregator_node(state: ReportState) -> dict:
    """분석 결과 통합 에이전트"""
    llm = _llm(OPUS, max_tokens=32000)

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

    # 수치 기반 참고 신호 주입
    ref_signals = state.get("reference_signals", "") or "(참고 신호 없음)"
    prompt = AGGREGATOR_PROMPT.replace("{reference_signals}", ref_signals)
    prompt = f"{prompt}\n\n{analyses}{feedback_section}"

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

async def quality_checker_node(state: ReportState) -> dict:
    """품질 검증 — 규칙 기반 (LLM 호출 없음)"""
    report = state.get("aggregated_report", "")

    if not report or report.startswith("[ERROR]"):
        return {"quality_passed": False, "quality_feedback": "리포트 생성 실패"}

    # 필수 섹션 5개 존재 여부 확인 (이모지 키워드로 체크)
    required = {
        "오늘의 핵심": "📌",
        "글로벌":      "🌍",
        "한국 시장":   "🇰🇷",
        "미국 시장":   "🇺🇸",
        "체크리스트":  "✅",
    }
    missing = [name for name, emoji in required.items() if emoji not in report]

    # 체크리스트 항목 수 확인 (□ 기호 3개 이상)
    checklist_count = report.count("□")

    passed = len(missing) == 0 and checklist_count >= 3
    if missing:
        feedback = f"누락 섹션: {', '.join(missing)}"
    elif checklist_count < 3:
        feedback = f"체크리스트 항목 부족 ({checklist_count}개)"
    else:
        feedback = "없음"

    logger.info(f"품질 검증 {'통과' if passed else '실패'}: {feedback}")
    return {
        "quality_passed": passed,
        "quality_feedback": feedback,
        "retry_count": state.get("retry_count", 0) + (0 if passed else 1),
    }
