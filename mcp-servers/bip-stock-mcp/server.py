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
    return await search_web(query, num_results=num_results)


if __name__ == "__main__":
    mcp.run()
