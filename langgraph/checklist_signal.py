"""
신호 생성 노드 (deterministic)
- 수집된 데이터를 룰 엔진(checklist_rules)에 넘겨 판정
"""

import logging
from typing import Dict

from checklist_rules import judge

logger = logging.getLogger(__name__)


async def signal_node(state: dict) -> dict:
    """LangGraph 노드 — 룰 엔진 기반 신호 생성"""
    parsed_items = state.get("parsed_items", [])
    gathered_data = state.get("gathered_data", {})
    signals: Dict[int, Dict] = {}

    for item in parsed_items:
        idx = item["index"]
        data = gathered_data.get(idx, {})
        raw = data.get("raw")
        context = data.get("context")

        indicator_key = item.get("indicator_key")
        threshold = item.get("threshold")
        direction = item.get("direction")

        # 현재값 추출
        current = None
        if isinstance(raw, dict) and "error" not in raw:
            if indicator_key == "fx_krw":
                current = raw.get("value")
            elif indicator_key in ("kospi", "kosdaq"):
                current = raw.get("value")
            elif indicator_key in ("sp500", "nasdaq", "nikkei"):
                current = raw.get("value")
            elif indicator_key in ("btc", "eth"):
                current = raw.get("price_krw")
            elif indicator_key and indicator_key.startswith("stock:"):
                current = raw.get("price")

        # 룰 엔진 판정
        emoji, reason = judge(
            indicator_key=indicator_key,
            value=current,
            threshold=threshold,
            direction=direction,
            raw=raw,
            context=context,
        )

        signals[idx] = {
            "level": emoji,
            "reason": reason,
            "current_value": current,
        }
        logger.info(f"  signal[{idx}] {emoji} {reason}")

    return {"signals": signals}
