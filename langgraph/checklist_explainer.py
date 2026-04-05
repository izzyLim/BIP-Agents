"""
설명 노드 (Haiku LLM)
- parsed_items + gathered_data + signals 기반으로 최종 텔레그램 메시지 생성
- 신호 판정은 이미 끝났으므로 LLM은 텍스트 포맷팅/종합 판단만 담당
- MCP 도구 호출 없음 (빠르고 저렴)
"""

import os
import json
import logging
from typing import Dict

from langchain_anthropic import ChatAnthropic

logger = logging.getLogger(__name__)

HAIKU = "claude-haiku-4-5-20251001"


EXPLAIN_PROMPT_ITEMS = """당신은 주식 시장 체크리스트 결과를 텔레그램 메시지로 정리하는 편집자입니다.

이미 deterministic 파이프라인이 각 항목을 파싱하고 신호등을 판정했습니다.
당신의 역할은 **정해진 신호와 데이터를 바탕으로 읽기 좋은 텍스트로 포맷**하는 것입니다.

## 규칙
- 신호등(🟢🟡🔴⚪)은 이미 결정되어 있으니 **바꾸지 마세요**
- 수치는 제공된 데이터에서만 사용하세요
- 추측, 창작, 과장 금지
- 각 항목은 2줄 (원문 + 판단)
- 텔레그램 마크다운: *볼드*만 사용, ##헤딩 금지, ---구분선 금지

## 출력 형식
✅ *원문 항목*
[신호등 이모지] 판단 근거 (수치 포함)

———————————————
📊 *주요 종목*: 삼성전자 / SK하이닉스 현재가 (있으면)
📊 *수급*: 외국인/기관/개인 (있으면)
💡 *종합*: 체크리스트 + 맥락 기반 시장 전망 한줄
"""


EXPLAIN_PROMPT_CLOSE = """당신은 장 마감 시장 분석 전문가입니다.

오늘 아침 모닝리포트가 제시한 체크리스트와, 하루 종일 쌓인 데이터를 근거로
**"오늘 체크리스트 기반 복기"**를 수행합니다.

## 핵심 원칙
1. 오늘 아침에 작성된 체크리스트 항목을 **전체 기준**으로 삼는다
2. 각 항목의 달성/미달성/경계를 종합하여 **오늘 시장의 성격**을 규정한다
3. 체크리스트가 예측한 시나리오 중 **실제 현실화된 것**과 **무산된 것**을 구분
4. 이 복기를 바탕으로 **내일 관찰 포인트**를 제시

## 규칙
- 체크리스트 항목을 □ 형식으로 하나씩 나열하지 말 것 (그룹 단위로 묶어 평가)
- 수치는 제공된 데이터에서만 사용, 추측 금지
- 텔레그램 마크다운: *볼드*만 사용, ##헤딩 금지

## 출력 형식
📉 *장 마감 복기 — 오늘 시장*

*시장 요약*
- KOSPI/KOSDAQ 마감 + 주요 등락률
- 환율/수급/주요 종목 핵심 수치

*체크리스트 복기*
- 달성된 항목 (그룹 단위 요약)
- 미달성/경고 발생 항목 (그룹 단위 요약)
- 예측이 빗나간 부분 (있다면)

*오늘의 성격*
- 체크리스트 기준으로 오늘 시장을 한 문장으로 정의

💡 *내일 관찰 포인트*
- 오늘 결과에 기반한 내일의 핵심 관찰 항목 2~3개
"""


def _format_data_for_llm(parsed_items: list, gathered: dict, signals: dict) -> str:
    """LLM에 전달할 컨텍스트 텍스트 조립"""
    lines = ["## 체크리스트 항목 (판정 완료)"]
    for item in parsed_items:
        idx = item["index"]
        sig = signals.get(idx, {})
        data = gathered.get(idx, {})
        raw = data.get("raw") or {}
        ctx = data.get("context") or {}

        lines.append(f"\n[항목 {idx + 1}] {item['original']}")
        lines.append(f"  section: {item.get('section')}")
        lines.append(f"  신호등: {sig.get('level', '⚪')}")
        lines.append(f"  판정 근거: {sig.get('reason', '')}")
        if raw and "error" not in raw:
            lines.append(f"  실시간 데이터: {json.dumps(raw, ensure_ascii=False)[:200]}")
        if ctx and "error" not in ctx:
            lines.append(f"  시장 맥락: percentile={ctx.get('percentile')}, "
                         f"range_pos={ctx.get('range_position')}, "
                         f"trend={ctx.get('trend_label')}, "
                         f"해석={ctx.get('interpretation')}")
    return "\n".join(lines)


async def explainer_node(state: dict) -> dict:
    """LangGraph 노드 — Haiku로 최종 텍스트 생성"""
    parsed_items = state.get("parsed_items", [])
    gathered = state.get("gathered_data", {})
    signals = state.get("signals", {})
    always_stocks = state.get("always_stocks", {}) or {}
    time_phase = state.get("time_phase", "intraday")

    if not parsed_items:
        return {"analysis_result": "체크리스트 없음"}

    context_text = _format_data_for_llm(parsed_items, gathered, signals)

    # 항상 수집한 주요 종목 정보 추가
    if always_stocks:
        context_text += "\n\n## 주요 종목 현재가 (체크리스트 외)"
        for code, stock in always_stocks.items():
            name = stock.get("_name") or stock.get("name", code)
            price = stock.get("price", 0)
            change_pct = stock.get("change_pct", 0)
            context_text += f"\n- {name} ({code}): {price:,}원 ({change_pct:+.2f}%)"

    llm = ChatAnthropic(
        model=HAIKU,
        max_tokens=2000,
        api_key=os.getenv("ANTHROPIC_API_KEY"),
    )

    # 시간대별 프롬프트/요청 분기
    if time_phase == "close":
        system_prompt = EXPLAIN_PROMPT_CLOSE
        user_msg = (
            f"{context_text}\n\n"
            "오늘 장 마감입니다. 위는 오늘 아침 작성된 체크리스트 전체와 "
            "각 항목에 대한 deterministic 판정 결과입니다. "
            "\n\n"
            "요청: 오늘 체크리스트 전체를 복기하여, "
            "1) 달성/미달성을 그룹 단위로 요약하고 "
            "2) 예측이 맞은 부분과 빗나간 부분을 구분하며 "
            "3) 오늘 시장의 성격을 한 문장으로 규정한 뒤 "
            "4) 내일 관찰 포인트 2~3개를 제시하세요."
        )
    else:
        system_prompt = EXPLAIN_PROMPT_ITEMS
        user_msg = (
            f"{context_text}\n\n"
            "위 데이터를 기반으로 텔레그램 메시지를 작성해주세요. "
            "각 항목의 신호등과 판정 근거는 그대로 사용하고, "
            "하단에 보조 정보(주요 종목/수급/종합)를 추가하세요."
        )

    try:
        resp = await llm.ainvoke([
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_msg},
        ])
        analysis_text = resp.content if isinstance(resp.content, str) else str(resp.content)

        # 토큰 usage
        usage = getattr(resp, "usage_metadata", None) or {}
        token_usage = {
            "explainer": {
                "input": usage.get("input_tokens", 0),
                "output": usage.get("output_tokens", 0),
                "total": usage.get("total_tokens", 0),
            }
        }

        return {"analysis_result": analysis_text, "token_usage": token_usage}
    except Exception as e:
        logger.error(f"explainer 실패: {e}")
        # fallback: deterministic 텍스트
        lines = []
        for item in parsed_items:
            idx = item["index"]
            sig = signals.get(idx, {})
            lines.append(f"✅ *{item['original']}*")
            lines.append(f"{sig.get('level', '⚪')} {sig.get('reason', '')}")
        return {"analysis_result": "\n".join(lines), "errors": [str(e)]}
