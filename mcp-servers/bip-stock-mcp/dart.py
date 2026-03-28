"""
DART API 도구
공시 목록, 공시 원문, 재무제표 조회
"""

import os
import logging
from typing import Optional
import httpx

logger = logging.getLogger(__name__)
DART_BASE = "https://opendart.fss.or.kr/api"


def _api_key() -> str:
    key = os.getenv("DART_API_KEY", "")
    if not key:
        raise ValueError("DART_API_KEY 환경변수가 설정되지 않았습니다.")
    return key


async def get_disclosure_list(
    corp_code: Optional[str] = None,
    bgn_de: Optional[str] = None,
    end_de: Optional[str] = None,
    pblntf_ty: str = "A",
    page_no: int = 1,
    page_count: int = 20,
) -> dict:
    """
    DART 공시 목록 조회

    Args:
        corp_code: 기업 고유번호 (없으면 전체)
        bgn_de: 시작일 YYYYMMDD
        end_de: 종료일 YYYYMMDD
        pblntf_ty: 공시유형 A=정기, B=주요사항, C=발행, D=지분, E=기타
        page_no: 페이지 번호
        page_count: 페이지당 결과 수 (최대 100)
    """
    params = {
        "crtfc_key": _api_key(),
        "pblntf_ty": pblntf_ty,
        "page_no": page_no,
        "page_count": min(page_count, 100),
    }
    if corp_code:
        params["corp_code"] = corp_code
    if bgn_de:
        params["bgn_de"] = bgn_de
    if end_de:
        params["end_de"] = end_de

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(f"{DART_BASE}/list.json", params=params)
        resp.raise_for_status()
        return resp.json()


async def get_disclosure(rcept_no: str) -> dict:
    """
    공시 원문 문서 목록 조회

    Args:
        rcept_no: 접수번호
    """
    params = {"crtfc_key": _api_key(), "rcept_no": rcept_no}

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(f"{DART_BASE}/document.json", params=params)
        resp.raise_for_status()
        return resp.json()


async def get_financial_statement(
    corp_code: str,
    bsns_year: str,
    reprt_code: str = "11011",
    fs_div: str = "CFS",
) -> dict:
    """
    XBRL 재무제표 조회

    Args:
        corp_code: 기업 고유번호
        bsns_year: 사업연도 (ex: 2024)
        reprt_code: 보고서 유형 11011=사업보고서, 11012=반기, 11013=1분기, 11014=3분기
        fs_div: CFS=연결재무제표, OFS=별도재무제표
    """
    params = {
        "crtfc_key": _api_key(),
        "corp_code": corp_code,
        "bsns_year": bsns_year,
        "reprt_code": reprt_code,
        "fs_div": fs_div,
    }

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(f"{DART_BASE}/fnlttXbrlDs011.json", params=params)
        resp.raise_for_status()
        return resp.json()


async def get_corp_code(corp_name: str) -> list:
    """
    기업명으로 DART 기업 고유번호 검색

    Args:
        corp_name: 기업명 (부분 일치)
    """
    params = {"crtfc_key": _api_key(), "corp_name": corp_name, "page_no": 1, "page_count": 10}

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(f"{DART_BASE}/company.json", params=params)
        resp.raise_for_status()
        data = resp.json()
        return data.get("list", [])
