"""
KRX / 네이버 금융 도구
종목 거래 정보, 시장 데이터 조회
"""

import logging
from datetime import datetime
from typing import Optional
import httpx

logger = logging.getLogger(__name__)


async def get_stock_trade_info(ticker: str, date: Optional[str] = None) -> dict:
    """
    KRX 종목 거래 정보 조회 (네이버 금융 활용)

    Args:
        ticker: 종목 코드 (ex: 005930)
        date: 조회일 YYYYMMDD (없으면 최근 거래일)
    """

    if not date:
        # 최근 거래일 (오늘 또는 최근 영업일)
        date = datetime.now().strftime("%Y%m%d")

    url = f"https://finance.naver.com/item/sise_day.nhn"
    params = {"code": ticker, "page": 1}

    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.naver.com"}

    async with httpx.AsyncClient(timeout=10) as client:
        try:
            resp = await client.get(url, params=params, headers=headers)
            resp.raise_for_status()
            # 간단한 파싱 (HTML 테이블)
            return {"ticker": ticker, "raw_html": resp.text[:2000], "date": date}
        except Exception as e:
            logger.error(f"KRX 거래 정보 조회 실패 ({ticker}): {e}")
            return {"ticker": ticker, "error": str(e)}


async def get_stock_base_info(ticker: str) -> dict:
    """
    종목 기본 정보 조회 (네이버 금융)

    Args:
        ticker: 종목 코드 (ex: 005930)
    """
    url = f"https://finance.naver.com/item/main.nhn"
    params = {"code": ticker}
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.naver.com"}

    async with httpx.AsyncClient(timeout=10) as client:
        try:
            resp = await client.get(url, params=params, headers=headers)
            resp.raise_for_status()
            return {"ticker": ticker, "raw_html": resp.text[:3000]}
        except Exception as e:
            logger.error(f"종목 기본 정보 조회 실패 ({ticker}): {e}")
            return {"ticker": ticker, "error": str(e)}


async def get_market_index(index: str = "KOSPI") -> dict:
    """
    주요 지수 현재가 조회

    Args:
        index: 지수명 (KOSPI, KOSDAQ, KPI200)
    """
    index_map = {"KOSPI": "KOSPI", "KOSDAQ": "KOSDAQ", "KPI200": "KPI200"}
    index_code = index_map.get(index.upper(), "KOSPI")

    url = "https://finance.naver.com/sise/sise_index.nhn"
    params = {"code": index_code}
    headers = {"User-Agent": "Mozilla/5.0"}

    async with httpx.AsyncClient(timeout=10) as client:
        try:
            resp = await client.get(url, params=params, headers=headers)
            resp.raise_for_status()
            return {"index": index, "raw_html": resp.text[:1000]}
        except Exception as e:
            logger.error(f"지수 조회 실패 ({index}): {e}")
            return {"index": index, "error": str(e)}
