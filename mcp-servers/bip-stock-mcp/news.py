"""
뉴스 검색 도구 (Naver + SerpAPI)
BIP-Pipeline realtime_news.py 재활용
"""

import os
import html
import re
import logging
from typing import List, Dict, Optional
import httpx

logger = logging.getLogger(__name__)

TRUSTED_SOURCES = [
    "연합뉴스", "한국경제", "매일경제", "조선비즈", "머니투데이",
    "이데일리", "서울경제", "파이낸셜뉴스", "뉴스1", "아시아경제",
    "한경", "매경", "헤럴드경제", "SBS", "KBS", "MBC", "YTN",
    "블룸버그", "로이터", "CNBC", "WSJ",
]

SPAM_PATTERNS = [
    r'\[광고\]', r'\[AD\]', r'\[PR\]', r'\[제휴\]',
    r'무료상담', r'이벤트', r'할인', r'쿠폰',
    r'주식리딩방', r'종목추천', r'급등주', r'테마주',
    r'대출', r'카드론', r'신용대출',
]


def _clean(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r'<[^>]+>', '', text)
    text = html.unescape(text)
    return re.sub(r'\s+', ' ', text).strip()


def _is_spam(title: str) -> bool:
    for pattern in SPAM_PATTERNS:
        if re.search(pattern, title, re.IGNORECASE):
            return True
    return False


async def search_naver_news(query: str, display: int = 10, sort: str = "date") -> List[Dict]:
    """네이버 뉴스 검색 API"""
    client_id = os.getenv("NAVER_CLIENT_ID", "")
    client_secret = os.getenv("NAVER_CLIENT_SECRET", "")

    if not client_id or not client_secret:
        logger.warning("NAVER_CLIENT_ID / NAVER_CLIENT_SECRET 미설정")
        return []

    headers = {
        "X-Naver-Client-Id": client_id,
        "X-Naver-Client-Secret": client_secret,
    }
    params = {"query": query, "display": min(display, 100), "sort": sort}

    async with httpx.AsyncClient(timeout=10) as client:
        try:
            resp = await client.get(
                "https://openapi.naver.com/v1/search/news.json",
                headers=headers, params=params
            )
            resp.raise_for_status()
            items = resp.json().get("items", [])
        except Exception as e:
            logger.error(f"Naver 뉴스 검색 실패 ({query}): {e}")
            return []

    results = []
    seen: set = set()
    for item in items:
        title = _clean(item.get("title", ""))
        if not title or _is_spam(title) or title in seen:
            continue
        seen.add(title)
        results.append({
            "title": title,
            "description": _clean(item.get("description", "")),
            "link": item.get("link", ""),
            "pub_date": item.get("pubDate", ""),
            "query": query,
        })

    return results


async def search_web(query: str, num_results: int = 5) -> List[Dict]:
    """SerpAPI 웹 검색 (영문 해외 이슈용)"""
    api_key = os.getenv("SERP_API_KEY", "")
    if not api_key:
        logger.warning("SERP_API_KEY 미설정 — 웹 검색 생략")
        return []

    params = {
        "q": query,
        "num": num_results,
        "hl": "en",
        "api_key": api_key,
    }

    async with httpx.AsyncClient(timeout=15) as client:
        try:
            resp = await client.get("https://serpapi.com/search", params=params)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.error(f"SerpAPI 검색 실패 ({query}): {e}")
            return []

    results = []
    for item in data.get("organic_results", [])[:num_results]:
        results.append({
            "title": item.get("title", ""),
            "snippet": item.get("snippet", ""),
            "link": item.get("link", ""),
            "source": item.get("source", ""),
        })

    return results
