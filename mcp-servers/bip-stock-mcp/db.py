"""
DB 조회 모듈 - 투자자 수급, 반도체 가격, 시장 지수
PostgreSQL 비동기 조회 (asyncpg)
"""
import os
import logging
from typing import Optional

logger = logging.getLogger(__name__)

_pool = None


async def get_pool():
    global _pool
    if _pool is None:
        import asyncpg
        _pool = await asyncpg.create_pool(
            host=os.getenv("PG_HOST", "bip-postgres"),
            port=int(os.getenv("PG_PORT", "5432")),
            user=os.getenv("PG_USER", "user"),
            password=os.getenv("PG_PASSWORD", "pw1234"),
            database=os.getenv("PG_DB", "stockdb"),
            min_size=1,
            max_size=5,
        )
    return _pool


async def fetch_investor_flow(days: int = 5) -> list:
    """외국인/기관/개인 순매수 집계 (일별)"""
    pool = await get_pool()
    query = """
        SELECT
            timestamp::date AS date,
            ROUND(SUM(foreign_buy_volume * close) / 100000000) AS foreign_amount_억,
            ROUND(SUM(institution_buy_volume * close) / 100000000) AS institution_amount_억,
            ROUND(SUM(individual_buy_volume * close) / 100000000) AS individual_amount_억
        FROM stock_price_1d
        WHERE ticker LIKE '%.KS'
          AND timestamp::date >= CURRENT_DATE - INTERVAL '14 days'
        GROUP BY timestamp::date
        ORDER BY date DESC
        LIMIT $1
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(query, days)
    return [dict(r) for r in rows]


async def fetch_investor_trading() -> dict:
    """종목별 수급 상세 (최근 거래일 기준)"""
    pool = await get_pool()

    # 최근 거래일 조회
    async with pool.acquire() as conn:
        latest = await conn.fetchval("""
            SELECT MAX(timestamp::date)
            FROM stock_price_1d
            WHERE ticker LIKE '%.KS' OR ticker LIKE '%.KQ'
        """)
    if not latest:
        return {}

    etf_keywords = [
        'ETF', 'ETN', 'KODEX', 'TIGER', 'KINDEX', 'KOSEF', 'ARIRANG',
        'HANARO', 'TIMEFOLIO', 'FOCUS', 'SOL ', 'WOORI', 'MASTER',
        'SMART', 'ACE ', 'KBSTAR', 'TREX', 'PLUS ', '레버리지', '인버스'
    ]
    etf_condition = " OR ".join([f"si.stock_name LIKE '%{kw}%'" for kw in etf_keywords])

    query = f"""
        WITH daily AS (
            SELECT
                p.ticker,
                si.stock_name,
                si.market_type,
                p.close,
                ROUND((p.foreign_buy_volume * p.close) / 100000000)      AS foreign_net_억,
                ROUND((p.institution_buy_volume * p.close) / 100000000)  AS institution_net_억,
                ROUND((p.individual_buy_volume * p.close) / 100000000)   AS individual_net_억,
                CASE WHEN ({etf_condition}) THEN 'ETF' ELSE 'STOCK' END  AS asset_type
            FROM stock_price_1d p
            JOIN stock_info si ON p.ticker = si.ticker
            WHERE p.timestamp::date = $1
              AND (si.market_type = 'KOSPI' OR si.market_type = 'KOSDAQ')
              AND p.close > 0
              AND p.volume > 0
        )
        SELECT * FROM daily
        WHERE asset_type = 'STOCK'
        ORDER BY ABS(foreign_net_억) DESC
        LIMIT 30
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(query, latest)

    stocks = [dict(r) for r in rows]

    # 외국인/기관 순매수 TOP5, 순매도 TOP5 정리
    foreign_buy  = sorted(stocks, key=lambda x: x["foreign_net_억"] or 0, reverse=True)[:5]
    foreign_sell = sorted(stocks, key=lambda x: x["foreign_net_억"] or 0)[:5]
    inst_buy     = sorted(stocks, key=lambda x: x["institution_net_억"] or 0, reverse=True)[:5]
    inst_sell    = sorted(stocks, key=lambda x: x["institution_net_억"] or 0)[:5]

    return {
        "trade_date": str(latest),
        "foreign_net_buy_top5":  foreign_buy,
        "foreign_net_sell_top5": foreign_sell,
        "institution_net_buy_top5":  inst_buy,
        "institution_net_sell_top5": inst_sell,
    }


async def fetch_semiconductor_prices() -> list:
    """DRAM/NAND 현물가 (최신 + 전일비)"""
    pool = await get_pool()
    query = """
        WITH with_prev AS (
            SELECT
                indicator_type,
                indicator_date,
                value,
                LAG(value) OVER (PARTITION BY indicator_type ORDER BY indicator_date) AS prev_value
            FROM macro_indicators
            WHERE indicator_type LIKE 'dram%' OR indicator_type LIKE 'nand%'
        ),
        latest AS (
            SELECT indicator_type, MAX(indicator_date) AS max_date
            FROM macro_indicators
            WHERE indicator_type LIKE 'dram%' OR indicator_type LIKE 'nand%'
            GROUP BY indicator_type
        )
        SELECT
            w.indicator_type AS product_type,
            w.indicator_date AS date,
            w.value          AS price,
            CASE WHEN w.prev_value > 0
                 THEN ROUND(((w.value - w.prev_value) / w.prev_value * 100)::numeric, 2)
                 ELSE 0
            END AS change_pct
        FROM with_prev w
        JOIN latest l ON w.indicator_type = l.indicator_type
                      AND w.indicator_date = l.max_date
        ORDER BY w.indicator_type
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(query)
    return [dict(r) for r in rows]


async def fetch_market_indices() -> dict:
    """KOSPI/KOSDAQ/S&P500/NASDAQ 주요 지수 (전일 종가 + 등락)"""
    pool = await get_pool()
    query = """
        WITH latest_dates AS (
            SELECT ticker, MAX(timestamp::date) AS max_date
            FROM stock_price_1d
            WHERE ticker IN ('^KS11', '^KQ11', '^GSPC', '^IXIC', '^DJI', '^VIX')
            GROUP BY ticker
        ),
        with_prev AS (
            SELECT
                p.ticker,
                p.timestamp::date AS date,
                p.close,
                LAG(p.close) OVER (PARTITION BY p.ticker ORDER BY p.timestamp::date) AS prev_close
            FROM stock_price_1d p
            JOIN latest_dates l ON p.ticker = l.ticker
                AND p.timestamp::date >= l.max_date - INTERVAL '5 days'
        )
        SELECT
            ticker,
            date,
            close,
            prev_close,
            CASE WHEN prev_close > 0
                 THEN ROUND(((close - prev_close) / prev_close * 100)::numeric, 2)
                 ELSE 0
            END AS change_pct
        FROM with_prev
        WHERE date = (SELECT max_date FROM latest_dates WHERE ticker = with_prev.ticker)
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(query)

    ticker_map = {
        "^KS11": "kospi", "^KQ11": "kosdaq",
        "^GSPC": "sp500", "^IXIC": "nasdaq",
        "^DJI": "dow",   "^VIX": "vix",
    }
    result = {}
    for r in rows:
        key = ticker_map.get(r["ticker"], r["ticker"])
        result[key] = {
            "value": float(r["close"]) if r["close"] else None,
            "prev_close": float(r["prev_close"]) if r["prev_close"] else None,
            "change_pct": float(r["change_pct"]) if r["change_pct"] else 0,
            "date": str(r["date"]),
        }
    return result
