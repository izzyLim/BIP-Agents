"""
BIP Stock MCP Server
DART / KRX / 뉴스 검색 도구를 FastMCP로 노출
"""

import os
import logging
from typing import Optional
from dotenv import load_dotenv
from fastmcp import FastMCP

from dart import get_disclosure_list, get_disclosure, get_financial_statement, get_corp_code
from krx import get_stock_trade_info, get_stock_base_info, get_market_index
from news import search_naver_news, search_web
from db import fetch_investor_flow, fetch_investor_trading, fetch_semiconductor_prices, fetch_market_indices
from realtime import (
    get_realtime_stock_price, get_realtime_index, get_realtime_investor,
    get_realtime_program_trade, get_realtime_fx,
    get_night_futures, get_crypto_price, get_sector_performance,
    get_global_index,
)

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

mcp = FastMCP(
    name="bip-stock-mcp",
    instructions=(
        "BIP 주식/금융 데이터 MCP 서버. "
        "DART 공시, KRX 종목 정보, 네이버 뉴스, 웹 검색 도구를 제공합니다. "
        "한국 주식시장 분석에 필요한 실시간 데이터를 온디맨드로 조회합니다."
    ),
)


# ─── DART 공시 도구 ────────────────────────────────────────────────────────────

@mcp.tool()
async def dart_disclosure_list(
    corp_code: Optional[str] = None,
    bgn_de: Optional[str] = None,
    end_de: Optional[str] = None,
    pblntf_ty: str = "A",
    page_count: int = 20,
) -> dict:
    """
    DART 공시 목록 조회.

    Args:
        corp_code: 기업 고유번호 (없으면 전체 공시)
        bgn_de: 시작일 YYYYMMDD (ex: 20260327)
        end_de: 종료일 YYYYMMDD (ex: 20260328)
        pblntf_ty: 공시유형 A=정기, B=주요사항보고, C=발행공시, D=지분공시, E=기타
        page_count: 결과 수 (최대 100)

    Returns:
        공시 목록 (접수번호, 기업명, 보고서명, 접수일시 등)
    """
    logger.info(f"[TOOL] dart_disclosure_list | corp_code={corp_code} bgn_de={bgn_de} end_de={end_de} page_count={page_count}")
    return await get_disclosure_list(
        corp_code=corp_code,
        bgn_de=bgn_de,
        end_de=end_de,
        pblntf_ty=pblntf_ty,
        page_count=page_count,
    )


@mcp.tool()
async def dart_disclosure(rcept_no: str) -> dict:
    """
    DART 공시 원문 문서 목록 조회.

    Args:
        rcept_no: 접수번호 (dart_disclosure_list 결과의 rcept_no)

    Returns:
        공시 문서 목록 및 URL
    """
    logger.info(f"[TOOL] dart_disclosure | rcept_no={rcept_no}")
    return await get_disclosure(rcept_no)


@mcp.tool()
async def dart_financial_statement(
    corp_code: str,
    bsns_year: str,
    reprt_code: str = "11011",
    fs_div: str = "CFS",
) -> dict:
    """
    DART XBRL 재무제표 조회.

    Args:
        corp_code: 기업 고유번호 (dart_corp_search로 확인)
        bsns_year: 사업연도 (ex: 2024)
        reprt_code: 11011=사업보고서, 11012=반기, 11013=1분기, 11014=3분기
        fs_div: CFS=연결재무제표, OFS=별도재무제표

    Returns:
        재무제표 항목별 금액
    """
    logger.info(f"[TOOL] dart_financial_statement | corp_code={corp_code} bsns_year={bsns_year} reprt_code={reprt_code}")
    return await get_financial_statement(corp_code, bsns_year, reprt_code, fs_div)


@mcp.tool()
async def dart_corp_search(corp_name: str) -> list:
    """
    기업명으로 DART 기업 고유번호 검색.

    Args:
        corp_name: 기업명 (예: 삼성전자, SK하이닉스)

    Returns:
        기업 목록 (corp_code, corp_name, stock_code 등)
    """
    logger.info(f"[TOOL] dart_corp_search | corp_name={corp_name}")
    return await get_corp_code(corp_name)


# ─── KRX / 네이버 금융 도구 ───────────────────────────────────────────────────

@mcp.tool()
async def krx_stock_trade_info(ticker: str, date: Optional[str] = None) -> dict:
    """
    종목 거래 정보 조회 (일별 시세).

    Args:
        ticker: 종목 코드 (ex: 005930 = 삼성전자)
        date: 조회일 YYYYMMDD (없으면 최근 거래일)

    Returns:
        종목 일별 시세 정보
    """
    logger.info(f"[TOOL] krx_stock_trade_info | ticker={ticker} date={date}")
    return await get_stock_trade_info(ticker, date)


@mcp.tool()
async def krx_stock_base_info(ticker: str) -> dict:
    """
    종목 기본 정보 조회 (시가총액, 업종, PER 등).

    Args:
        ticker: 종목 코드 (ex: 005930)

    Returns:
        종목 기본 정보
    """
    logger.info(f"[TOOL] krx_stock_base_info | ticker={ticker}")
    return await get_stock_base_info(ticker)


@mcp.tool()
async def krx_market_index(index: str = "KOSPI") -> dict:
    """
    주요 지수 정보 조회.

    Args:
        index: 지수명 (KOSPI, KOSDAQ, KPI200)

    Returns:
        지수 현재가 및 등락 정보
    """
    logger.info(f"[TOOL] krx_market_index | index={index}")
    return await get_market_index(index)


# ─── 뉴스 검색 도구 ──────────────────────────────────────────────────────────

@mcp.tool()
async def news_search_naver(
    query: str,
    display: int = 10,
    sort: str = "date",
) -> list:
    """
    네이버 뉴스 검색 (한국 시장 뉴스).

    Args:
        query: 검색어 (ex: "삼성전자 실적", "코스피 시황", "반도체 HBM")
        display: 결과 수 (최대 100, 기본 10)
        sort: 정렬 date=최신순, sim=관련도순

    Returns:
        뉴스 목록 (제목, 요약, 링크, 게시일)
    """
    logger.info(f"[TOOL] news_search_naver | query={query!r} display={display}")
    return await search_naver_news(query, display=display, sort=sort)


@mcp.tool()
async def news_search_web(query: str, num_results: int = 5) -> list:
    """
    웹 검색 (SerpAPI — 해외 뉴스/영문 이슈 검색용).
    SERP_API_KEY 환경변수 필요.

    Args:
        query: 검색어 (영문 권장, ex: "Fed rate decision", "NVIDIA earnings")
        num_results: 결과 수 (기본 5)

    Returns:
        검색 결과 (제목, 요약, 링크, 출처)
    """
    logger.info(f"[TOOL] news_search_web | query={query!r} num_results={num_results}")
    return await search_web(query, num_results=num_results)


# ─── DB 데이터 도구 ───────────────────────────────────────────────────────────

@mcp.tool()
async def db_investor_flow(days: int = 5) -> list:
    """
    투자자별 순매수 집계 (외국인/기관/개인) - DB 조회.

    Args:
        days: 조회 일수 (기본 5일)

    Returns:
        날짜별 외국인/기관/개인 순매수 금액 (억원)
    """
    logger.info(f"[TOOL] db_investor_flow | days={days}")
    return await fetch_investor_flow(days)


@mcp.tool()
async def db_investor_trading() -> dict:
    """
    종목별 수급 상세 (최근 거래일) - DB 조회.
    외국인/기관 순매수 TOP5, 순매도 TOP5 제공.

    Returns:
        trade_date, foreign_net_buy_top5, foreign_net_sell_top5,
        institution_net_buy_top5, institution_net_sell_top5
    """
    logger.info("[TOOL] db_investor_trading")
    return await fetch_investor_trading()


@mcp.tool()
async def db_semiconductor_prices() -> list:
    """
    DRAM/NAND 반도체 현물가 - DB 조회.
    최신 가격 및 전일 대비 등락률 포함.

    Returns:
        제품별 가격(USD), 전일비 등락률
    """
    logger.info("[TOOL] db_semiconductor_prices")
    return await fetch_semiconductor_prices()


@mcp.tool()
async def db_market_indices() -> dict:
    """
    주요 시장 지수 - DB 조회.
    KOSPI, KOSDAQ, S&P500, NASDAQ, DOW, VIX 포함.

    Returns:
        지수별 종가, 전일 종가, 등락률
    """
    logger.info("[TOOL] db_market_indices")
    return await fetch_market_indices()


# ─── 실시간 데이터 도구 (한투 API / Upbit / yfinance) ─────────────────────────

@mcp.tool()
async def realtime_stock_price(code: str) -> dict:
    """
    종목 실시간 현재가 조회 (한투 API).

    Args:
        code: 6자리 종목코드 (005930=삼성전자, 000660=SK하이닉스)

    Returns:
        현재가, 등락, 등락률, 거래량, 거래대금
    """
    logger.info(f"[TOOL] realtime_stock_price | code={code}")
    return await get_realtime_stock_price(code)


@mcp.tool()
async def realtime_index(index_name: str = "KOSPI") -> dict:
    """
    KOSPI/KOSDAQ/KOSPI200 실시간 지수 (한투 API).

    Args:
        index_name: KOSPI, KOSDAQ, KOSPI200

    Returns:
        지수값, 변동, 등락률
    """
    logger.info(f"[TOOL] realtime_index | index={index_name}")
    return await get_realtime_index(index_name)


@mcp.tool()
async def realtime_investor(code: str = "0001") -> dict:
    """
    투자자별 매매동향 실시간 (한투 API). 장중 외국인/기관/개인 순매수 현황.

    Args:
        code: 종목코드 (0001=KOSPI 전체)

    Returns:
        외국인/기관/개인 순매수 수량
    """
    logger.info(f"[TOOL] realtime_investor | code={code}")
    return await get_realtime_investor(code)


@mcp.tool()
async def realtime_program_trade() -> dict:
    """
    프로그램 매매 동향 실시간 (한투 API). 프로그램 매수/매도 현황.

    Returns:
        프로그램 매수량, 매도량
    """
    logger.info("[TOOL] realtime_program_trade")
    return await get_realtime_program_trade()


@mcp.tool()
async def realtime_fx_rate() -> dict:
    """
    원/달러 환율 조회 (한투 API).

    Returns:
        USD/KRW 환율
    """
    logger.info("[TOOL] realtime_fx_rate")
    return await get_realtime_fx()


@mcp.tool()
async def night_futures_ewy() -> dict:
    """
    야간선물 데이터 (EWY 한국 ETF, 미국 시장).
    전일 미국장 종가 + 애프터마켓/프리마켓. 한국 장 시작 전 갭 예측에 활용.

    Returns:
        EWY 종가, 등락률, 애프터마켓/프리마켓 데이터
    """
    logger.info("[TOOL] night_futures_ewy")
    return await get_night_futures()


@mcp.tool()
async def crypto_price(symbol: str = "BTC") -> dict:
    """
    크립토 실시간 시세 (Upbit API).

    Args:
        symbol: BTC, ETH 등

    Returns:
        원화 가격, 24시간 등락률
    """
    logger.info(f"[TOOL] crypto_price | symbol={symbol}")
    return await get_crypto_price(symbol)


@mcp.tool()
async def global_index(symbol: str) -> dict:
    """
    글로벌 지수 조회 (yfinance). 닛케이, S&P500, 나스닥, 다우, 항셍, DAX 등.

    Args:
        symbol: 지수명 (NIKKEI, 닛케이, SP500, NASDAQ, DOW, HSI, DAX)

    Returns:
        지수값, 등락률
    """
    logger.info(f"[TOOL] global_index | symbol={symbol}")
    return await get_global_index(symbol)


@mcp.tool()
async def sector_performance(sector_name: str) -> dict:
    """
    업종별 등락률 조회 (KRX 데이터). 섹터명으로 검색.

    Args:
        sector_name: 업종명 (반도체, 자동차, 통신장비 등)

    Returns:
        섹터명, 등락률
    """
    logger.info(f"[TOOL] sector_performance | sector={sector_name}")
    return await get_sector_performance(sector_name)


if __name__ == "__main__":
    import sys

    # Docker 환경에서는 SSE 모드로 실행 (HTTP 서버)
    # 로컬/Claude Desktop에서는 stdio 모드
    transport = os.getenv("MCP_TRANSPORT", "stdio")
    host = os.getenv("MCP_HOST", "0.0.0.0")
    port = int(os.getenv("MCP_PORT", "8000"))

    if transport == "sse":
        logger.info(f"FastMCP SSE 서버 시작: http://{host}:{port}/sse")
        mcp.run(transport="sse", host=host, port=port)
    else:
        mcp.run(transport="stdio")
