"""
실시간 시장 데이터 수집
- 한투 API (KIS): 종목 현재가, 지수, 투자자 매매동향, 프로그램 매매, 환율
- Upbit API: BTC/ETH 크립토
- yfinance: 야간선물 EWY (미국 시장)
"""

import os
import logging
from datetime import datetime
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

KIS_BASE_URL = "https://openapi.koreainvestment.com:9443"
_TOKEN_FILE = "/tmp/kis_token.json"

# 메모리 토큰 캐시
_token_cache = {"token": None, "expires": None}


async def _get_kis_token() -> str:
    """KIS API 접근 토큰 발급 (메모리 + 파일 캐시)"""
    import time
    import json as _json
    now = time.time()

    # 1. 메모리 캐시 확인
    if _token_cache["token"] and _token_cache["expires"] and now < _token_cache["expires"]:
        return _token_cache["token"]

    # 2. 파일 캐시 확인 (컨테이너 재시작 대응)
    try:
        with open(_TOKEN_FILE, "r") as f:
            saved = _json.load(f)
            if saved.get("token") and saved.get("expires", 0) > now:
                _token_cache["token"] = saved["token"]
                _token_cache["expires"] = saved["expires"]
                logger.info("KIS 토큰 파일 캐시 로드")
                return saved["token"]
    except (FileNotFoundError, _json.JSONDecodeError):
        pass

    # 3. 신규 발급
    app_key = os.getenv("KIS_APP_KEY", "")
    app_secret = os.getenv("KIS_APP_SECRET", "")
    if not app_key or not app_secret:
        raise ValueError("KIS_APP_KEY / KIS_APP_SECRET 미설정")

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{KIS_BASE_URL}/oauth2/tokenP",
            json={
                "grant_type": "client_credentials",
                "appkey": app_key,
                "appsecret": app_secret,
            },
        )
        resp.raise_for_status()
        data = resp.json()

    token = data["access_token"]
    expires = now + 80000  # ~22시간

    # 메모리 + 파일 캐시 저장
    _token_cache["token"] = token
    _token_cache["expires"] = expires
    try:
        import json as _json
        with open(_TOKEN_FILE, "w") as f:
            _json.dump({"token": token, "expires": expires}, f)
        logger.info("KIS 토큰 발급 및 파일 캐시 저장")
    except Exception as e:
        logger.warning(f"토큰 파일 저장 실패: {e}")

    return token


async def _kis_request(path: str, tr_id: str, params: dict) -> dict:
    """KIS API 공통 GET 요청"""
    token = await _get_kis_token()
    app_key = os.getenv("KIS_APP_KEY", "")
    app_secret = os.getenv("KIS_APP_SECRET", "")

    headers = {
        "authorization": f"Bearer {token}",
        "appkey": app_key,
        "appsecret": app_secret,
        "tr_id": tr_id,
        "content-type": "application/json; charset=utf-8",
    }

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(f"{KIS_BASE_URL}{path}", headers=headers, params=params)
        resp.raise_for_status()
        return resp.json()


# ──────────────────────────────────────────────
# 한투 API 기반 실시간 데이터
# ──────────────────────────────────────────────

async def get_realtime_stock_price(code: str) -> dict:
    """종목 현재가 조회 (한투 API)"""
    try:
        data = await _kis_request(
            "/uapi/domestic-stock/v1/quotations/inquire-price",
            "FHKST01010100",
            {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code},
        )
        output = data.get("output", {})
        return {
            "code": code,
            "name": output.get("hts_kor_isnm", code),
            "price": int(output.get("stck_prpr", 0)),
            "change": int(output.get("prdy_vrss", 0)),
            "change_pct": float(output.get("prdy_ctrt", 0)),
            "volume": int(output.get("acml_vol", 0)),
            "trading_value": int(output.get("acml_tr_pbmn", 0)),
        }
    except Exception as e:
        logger.error(f"종목 현재가 조회 실패 ({code}): {e}")
        return {"code": code, "error": str(e)}


async def get_preopen_price(code: str) -> dict:
    """
    장 시작 전 예상 체결가 조회 (한투 API)
    - 08:30~09:00 동시호가 시간에만 의미 있는 값
    - antc_cntg_prc: 예상 체결가
    - antc_vol: 예상 체결량
    - stck_sdpr: 기준가 (전일 종가)
    """
    try:
        data = await _kis_request(
            "/uapi/domestic-stock/v1/quotations/inquire-price",
            "FHKST01010100",
            {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code},
        )
        output = data.get("output", {})

        antc_prc = int(output.get("antc_cntg_prc", 0) or 0)
        base_prc = int(output.get("stck_sdpr", 0) or 0)  # 전일 종가
        change = antc_prc - base_prc if (antc_prc and base_prc) else 0
        change_pct = round((change / base_prc * 100), 2) if base_prc > 0 else 0

        return {
            "code": code,
            "name": output.get("hts_kor_isnm", code),
            "expected_price": antc_prc,
            "prev_close": base_prc,
            "expected_change": change,
            "expected_change_pct": change_pct,
            "has_expected": antc_prc > 0,  # 08:30~09:00 외에는 false
        }
    except Exception as e:
        logger.error(f"예상 체결가 조회 실패 ({code}): {e}")
        return {"code": code, "error": str(e)}


async def get_preopen_index(index_name: str) -> dict:
    """
    지수 예상가 조회 (한투 API)
    - 08:30~09:00 동시호가에서 결정되는 예상 시가
    """
    index_map = {
        "KOSPI": "0001",
        "KOSDAQ": "1001",
        "KOSPI200": "2001",
    }
    code = index_map.get(index_name.upper())
    if not code:
        return {"error": f"지원하지 않는 지수: {index_name}"}

    try:
        data = await _kis_request(
            "/uapi/domestic-stock/v1/quotations/inquire-index-price",
            "FHPUP02100000",
            {"FID_COND_MRKT_DIV_CODE": "U", "FID_INPUT_ISCD": code},
        )
        output = data.get("output", {})

        # 지수 예상가 필드 (bstp_nmix_oprc = 시가, bstp_nmix_prpr = 현재가)
        expected = float(output.get("bstp_nmix_oprc", 0) or 0)
        prev_close = float(output.get("bstp_nmix_prdy_clpr", 0) or 0)
        if not prev_close:
            prev_close = float(output.get("bstp_nmix_prpr", 0) or 0) - float(output.get("bstp_nmix_prdy_vrss", 0) or 0)

        change = expected - prev_close if (expected and prev_close) else 0
        change_pct = round((change / prev_close * 100), 2) if prev_close > 0 else 0

        return {
            "index": index_name.upper(),
            "expected_value": expected,
            "prev_close": prev_close,
            "expected_change": round(change, 2),
            "expected_change_pct": change_pct,
            "has_expected": expected > 0,
        }
    except Exception as e:
        logger.error(f"지수 예상가 조회 실패 ({index_name}): {e}")
        return {"index": index_name, "error": str(e)}


async def get_realtime_index(index_name: str) -> dict:
    """주요 지수 현재가 (한투 API)"""
    index_map = {
        "KOSPI": "0001",
        "KOSDAQ": "1001",
        "KOSPI200": "2001",
    }
    code = index_map.get(index_name.upper())
    if not code:
        return {"error": f"지원하지 않는 지수: {index_name}"}

    try:
        data = await _kis_request(
            "/uapi/domestic-stock/v1/quotations/inquire-index-price",
            "FHPUP02100000",
            {"FID_COND_MRKT_DIV_CODE": "U", "FID_INPUT_ISCD": code},
        )
        output = data.get("output", {})
        return {
            "index": index_name.upper(),
            "value": float(output.get("bstp_nmix_prpr", 0)),
            "change": float(output.get("bstp_nmix_prdy_vrss", 0)),
            "change_pct": float(output.get("bstp_nmix_prdy_ctrt", 0)),
        }
    except Exception as e:
        logger.error(f"지수 조회 실패 ({index_name}): {e}")
        return {"index": index_name, "error": str(e)}


async def get_realtime_investor(code: str = "0001") -> dict:
    """
    투자자별 매매동향 — 장중 실시간
    - code="0001": KOSPI 시장 전체 (네이버 금융)
    - 개별 종목: 한투 API
    """
    # 시장 전체는 네이버 금융 사용 (한투 API는 종목별만 지원)
    if code in ("0001", "1001"):
        try:
            from bs4 import BeautifulSoup
            from datetime import datetime

            today_str = datetime.now().strftime("%Y%m%d")
            headers = {"User-Agent": "Mozilla/5.0 AppleWebKit/537.36"}
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"https://finance.naver.com/sise/investorDealTrendDay.naver?bizdate={today_str}",
                    headers=headers,
                )
            soup = BeautifulSoup(resp.text, "html.parser")
            table = soup.select_one("table.type_1")
            if not table:
                return {"error": "네이버 테이블 없음"}

            for row in table.select("tr"):
                cells = row.select("td")
                if len(cells) < 4:
                    continue
                individual = int(cells[1].get_text(strip=True).replace(",", ""))
                foreign = int(cells[2].get_text(strip=True).replace(",", ""))
                institution = int(cells[3].get_text(strip=True).replace(",", ""))
                return {
                    "market": "KOSPI" if code == "0001" else "KOSDAQ",
                    "foreign": foreign,
                    "institution": institution,
                    "individual": individual,
                    "unit": "억원",
                }
            return {"error": "데이터 없음"}
        except Exception as e:
            logger.error(f"시장 투자자 매매동향 조회 실패: {e}")
            return {"error": str(e)}

    # 종목별은 한투 API
    try:
        data = await _kis_request(
            "/uapi/domestic-stock/v1/quotations/inquire-investor",
            "FHKST01010900",
            {
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": code,
            },
        )
        items = data.get("output", [])
        if items:
            latest = items[0]
            return {
                "code": code,
                "foreign_buy": int(latest.get("frgn_ntby_qty", 0)),
                "institution_buy": int(latest.get("orgn_ntby_qty", 0)),
                "individual_buy": int(latest.get("prsn_ntby_qty", 0)),
            }
        return {"error": "데이터 없음"}
    except Exception as e:
        logger.error(f"투자자 매매동향 조회 실패: {e}")
        return {"error": str(e)}


async def get_realtime_program_trade() -> dict:
    """프로그램 매매 동향 (한투 API)"""
    try:
        data = await _kis_request(
            "/uapi/domestic-stock/v1/quotations/program-trade-by-stock",
            "FHPPG04650100",
            {
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": "0001",
            },
        )
        items = data.get("output", [])
        if items:
            return {
                "program_buy": sum(int(i.get("seln_qty", 0)) for i in items[:10]),
                "program_sell": sum(int(i.get("shnu_qty", 0)) for i in items[:10]),
                "items_count": len(items),
            }
        return {"error": "데이터 없음"}
    except Exception as e:
        logger.error(f"프로그램 매매 조회 실패: {e}")
        return {"error": str(e)}


async def get_realtime_fx() -> dict:
    """환율 조회 (ExchangeRate API + DB 전일 대비 변동률)"""
    try:
        # 현재 환율
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get("https://open.er-api.com/v6/latest/USD")
        data = resp.json()
        krw = data.get("rates", {}).get("KRW")
        if not krw:
            return {"error": "KRW 환율 데이터 없음"}

        result = {"pair": "USD/KRW", "value": round(krw, 2)}

        # DB에서 전일 USD/KRW 환율 조회 → 변동률 계산
        # exchange_rate 타입에 여러 통화(KRW/JPY/CNY 등)가 섞여 있어서
        # 1000 이상인 값만 KRW로 간주 (다른 통화는 비율값이라 1000 미만)
        try:
            from db import get_pool
            pool = await get_pool()
            async with pool.acquire() as conn:
                row = await conn.fetchrow("""
                    SELECT value FROM macro_indicators
                    WHERE indicator_type = 'exchange_rate'
                      AND region = 'South Korea'
                      AND indicator_date < CURRENT_DATE
                      AND value > 1000
                    ORDER BY indicator_date DESC LIMIT 1
                """)
                if row and row["value"]:
                    prev = float(row["value"])
                    change = krw - prev
                    change_pct = (change / prev * 100) if prev > 0 else 0
                    result["prev_value"] = round(prev, 2)
                    result["change"] = round(change, 2)
                    result["change_pct"] = round(change_pct, 2)
        except Exception as db_err:
            logger.warning(f"환율 전일 데이터 조회 실패: {db_err}")

        return result
    except Exception as e:
        logger.error(f"환율 조회 실패: {e}")
        return {"error": str(e)}


# ──────────────────────────────────────────────
# 외부 API (한투 외)
# ──────────────────────────────────────────────

async def get_night_futures() -> dict:
    """야간선물 EWY (한국 ETF, 미국 시장) — 장 시작 전 갭 예측용"""
    try:
        import yfinance as yf
        ewy = yf.Ticker("EWY")
        hist = ewy.history(period="5d")
        info = ewy.info

        if hist.empty or len(hist) < 2:
            return {"error": "데이터 없음"}

        latest = hist.iloc[-1]
        prev = hist.iloc[-2]
        regular_close = latest["Close"]
        change_pct = ((regular_close - prev["Close"]) / prev["Close"] * 100) if prev["Close"] > 0 else 0

        result = {
            "name": "EWY (한국 ETF)",
            "regular_close": round(float(regular_close), 2),
            "change_pct": round(change_pct, 2),
        }

        post_price = info.get("postMarketPrice")
        if post_price and post_price > 0:
            post_chg = ((post_price - prev["Close"]) / prev["Close"] * 100)
            result["after_hours"] = round(post_price, 2)
            result["after_hours_change_pct"] = round(post_chg, 2)

        pre_price = info.get("preMarketPrice")
        if pre_price and pre_price > 0:
            pre_chg = ((pre_price - prev["Close"]) / prev["Close"] * 100)
            result["pre_market"] = round(pre_price, 2)
            result["pre_market_change_pct"] = round(pre_chg, 2)

        return result
    except Exception as e:
        logger.error(f"야간선물 조회 실패: {e}")
        return {"error": str(e)}


async def get_crypto_price(symbol: str = "BTC") -> dict:
    """크립토 실시간 시세 (Upbit API)"""
    try:
        market = f"KRW-{symbol.upper()}"
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"https://api.upbit.com/v1/ticker?markets={market}")
        data = resp.json()
        if data:
            coin = data[0]
            return {
                "symbol": symbol.upper(),
                "price_krw": coin["trade_price"],
                "change_pct": round(coin["signed_change_rate"] * 100, 2),
            }
        return {"error": "데이터 없음"}
    except Exception as e:
        logger.error(f"크립토 조회 실패 ({symbol}): {e}")
        return {"error": str(e)}


async def get_global_index(symbol: str) -> dict:
    """글로벌 지수 조회 (yfinance) — 닛케이, S&P500, 나스닥 등"""
    symbol_map = {
        "NIKKEI": "^N225",
        "NIKKEI225": "^N225",
        "N225": "^N225",
        "닛케이": "^N225",
        "SP500": "^GSPC",
        "S&P500": "^GSPC",
        "NASDAQ": "^IXIC",
        "DOW": "^DJI",
        "DAX": "^GDAXI",
        "HSI": "^HSI",
        "항셍": "^HSI",
        "SHANGHAI": "000001.SS",
        "상해": "000001.SS",
    }
    yf_symbol = symbol_map.get(symbol.upper(), symbol)

    try:
        import yfinance as yf
        ticker = yf.Ticker(yf_symbol)
        hist = ticker.history(period="2d")
        if hist.empty or len(hist) < 2:
            return {"error": f"{symbol} 데이터 없음"}

        latest = hist.iloc[-1]
        prev = hist.iloc[-2]
        close = float(latest["Close"])
        change_pct = ((close - prev["Close"]) / prev["Close"] * 100) if prev["Close"] > 0 else 0

        return {
            "symbol": symbol,
            "yf_symbol": yf_symbol,
            "value": round(close, 2),
            "change_pct": round(change_pct, 2),
        }
    except Exception as e:
        logger.error(f"글로벌 지수 조회 실패 ({symbol}): {e}")
        return {"error": str(e)}


async def get_sector_performance(sector_name: str) -> dict:
    """업종별 등락률 — DB에서 KRX 섹터 데이터 조회"""
    try:
        from db import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT indicator_type, change_pct
                FROM macro_indicators
                WHERE indicator_type LIKE 'krx_sector_%'
                  AND indicator_type LIKE $1
                  AND indicator_date = (SELECT MAX(indicator_date) FROM macro_indicators WHERE indicator_type LIKE 'krx_sector_%')
            """, f"%{sector_name}%")

            if row:
                name = row["indicator_type"].replace("krx_sector_", "")
                return {"sector": name, "change_pct": float(row["change_pct"])}
        return {"error": f"'{sector_name}' 섹터 데이터 없음"}
    except Exception as e:
        logger.error(f"섹터 조회 실패 ({sector_name}): {e}")
        return {"error": str(e)}
