"""
체크리스트 모니터링 실행 진입점
- Airflow DAG에서 HTTP 호출 또는 직접 실행
"""

import asyncio
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


async def run_checklist_monitor(
    checklist_text: str,
    time_phase: str = "intraday",
    analysis_date: str = None,
) -> dict:
    """
    체크리스트 모니터링 실행

    Args:
        checklist_text: 모닝리포트 체크리스트 원문
        time_phase: pre_market / intraday / close
        analysis_date: 날짜 YYYY-MM-DD (없으면 오늘)

    Returns:
        {
            "analysis_result": str,   # 텔레그램 메시지용 분석 결과
            "token_usage": dict,
            "errors": list,
        }
    """
    from checklist_graph import checklist_monitor_graph

    if not analysis_date:
        analysis_date = datetime.now().strftime("%Y-%m-%d")

    initial_state = {
        "checklist_text": checklist_text,
        "time_phase": time_phase,
        "parsed_items": [],
        "gathered_data": {},
        "always_stocks": {},
        "market_context": {},
        "signals": {},
        "analysis_result": None,
        "token_usage": {},
        "analysis_date": analysis_date,
        "errors": [],
    }

    logger.info(f"체크리스트 모니터링 시작: {analysis_date} / {time_phase}")
    result = await checklist_monitor_graph.ainvoke(initial_state)

    logger.info(f"체크리스트 모니터링 완료. 토큰: {result.get('token_usage', {})}")

    return {
        "analysis_result": result.get("analysis_result", ""),
        "token_usage": result.get("token_usage", {}),
        "errors": result.get("errors", []),
    }


if __name__ == "__main__":
    # 로컬 테스트
    sample_checklist = """장 시작 전 (08:30~09:00)
□ 외국인 KOSPI200 선물 야간 동향 확인
□ 원/달러 환율 1,510원 이하 안착 여부
□ 일본 닛케이 개장 동향

장 중 모니터링 (09:00~15:30)
□ 삼성전자 190,000원 지지 여부
□ 프로그램 매매 동향
□ 거래대금 15조원 유지 여부

주의해야 할 가격 레벨
□ 코스피 5,600 돌파 시 — 추가 상승 모멘텀
□ 코스피 5,300 이탈 시 — 급락 전환
□ 원/달러 1,520원 돌파 시 — 외국인 매도 가속"""

    result = asyncio.run(run_checklist_monitor(sample_checklist, time_phase="intraday"))

    print("\n=== 분석 결과 ===")
    print(result["analysis_result"])

    print(f"\n=== 토큰 ===")
    print(result["token_usage"])

    if result["errors"]:
        print(f"\n=== 오류 ===")
        print(result["errors"])
