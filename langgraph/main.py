"""
Morning Pulse LangGraph 실행 진입점
독립 실행 또는 Airflow DAG에서 호출
"""

import asyncio
import json
import logging
import os
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


async def run_morning_pulse(market_data: dict, news_data: str, report_date: str = None, reference_signals: str = None) -> dict:
    """
    Morning Pulse 리포트 생성

    Args:
        market_data: DB 적재 데이터 (지수, 수급, 반도체, 매크로)
        news_data: 네이버 뉴스 RAG 결과 문자열
        report_date: 리포트 날짜 YYYY-MM-DD (없으면 오늘)

    Returns:
        {
            "final_report": str,      # 최종 리포트 마크다운
            "token_usage": dict,      # 에이전트별 토큰 사용량
            "quality_passed": bool,
            "errors": list,
        }
    """
    from graph import morning_pulse_graph

    if not report_date:
        report_date = datetime.now().strftime("%Y-%m-%d")

    initial_state = {
        "market_data": market_data,
        "news_data": news_data,
        "reference_signals": reference_signals or "",
        "global_analysis": None,
        "korea_analysis": None,
        "semi_analysis": None,
        "flow_analysis": None,
        "aggregated_report": None,
        "quality_passed": None,
        "quality_feedback": None,
        "retry_count": 0,
        "final_report": None,
        "token_usage": {},
        "report_date": report_date,
        "errors": [],
    }

    logger.info(f"Morning Pulse 시작: {report_date}")
    result = await morning_pulse_graph.ainvoke(initial_state)
    logger.info(f"Morning Pulse 완료. 토큰: {result.get('token_usage', {})}")

    return {
        "final_report": result.get("final_report", ""),
        "token_usage": result.get("token_usage", {}),
        "quality_passed": result.get("quality_passed", False),
        "errors": result.get("errors", []),
    }


if __name__ == "__main__":
    # 로컬 테스트용 샘플 데이터
    sample_market_data = {
        "indices": {
            "kospi": {"value": 2650.5, "change": 15.2, "change_pct": 0.58},
            "kosdaq": {"value": 870.1, "change": -3.4, "change_pct": -0.39},
            "sp500": {"value": 5200.0, "change": 30.0, "change_pct": 0.58},
            "nasdaq": {"value": 16300.0, "change": 120.0, "change_pct": 0.74},
        },
        "exchange_rates": {
            "usd_krw": {"value": 1340.0, "change_pct": -0.2},
        },
        "macro": {
            "vix": {"value": 18.5, "change": -1.2},
            "us_10y": {"value": 4.25, "change": 0.03},
        },
        "investor_flow": {
            "foreign": {"net": 1250, "unit": "억"},
            "institution": {"net": -430, "unit": "억"},
            "individual": {"net": -820, "unit": "억"},
        },
        "semiconductor_prices": {
            "dram_ddr5": {"value": 2.85, "change": 0.05, "unit": "USD"},
        },
    }

    sample_news = """
1. [09:15] 외국인, 코스피서 이틀 연속 순매수...반도체 중심 매집
2. [08:30] 미 연준 인사, 연내 금리인하 가능성 시사
3. [07:45] TSMC, HBM4 양산 일정 앞당긴다...삼성·하이닉스 수혜 기대
""".strip()

    result = asyncio.run(run_morning_pulse(sample_market_data, sample_news))
    print("\n=== 최종 리포트 ===")
    print(result["final_report"])

    print("\n=== 토큰 사용량 ===")
    usage = result["token_usage"]
    total_in = total_out = 0
    for agent, t in usage.items():
        if isinstance(t, dict):
            print(f"  {agent:12s}: input={t.get('input',0):>6,} / output={t.get('output',0):>5,}")
            total_in  += t.get("input", 0)
            total_out += t.get("output", 0)
    print(f"  {'합계':12s}: input={total_in:>6,} / output={total_out:>5,} / total={total_in+total_out:,}")

    print(f"\n품질 검증: {'✅ PASS' if result['quality_passed'] else '❌ FAIL'}")
    if result.get("errors"):
        print(f"오류: {result['errors']}")
