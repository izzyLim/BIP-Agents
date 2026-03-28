# Morning Pulse LangGraph 고도화 설계

> 최종 업데이트: 2026-03-28
> 관련 repo: [BIP-Agents](https://github.com/izzyLim/BIP-Agents)

---

## 1. 현황 및 문제점

### 현재 구조
```
DB 적재 데이터 로드 (수치 데이터)
    +
Naver 뉴스 API (실시간 — 유일한 실시간 데이터)
    ↓
단일 LLM 호출 (analyze_market_v2) — 모든 섹션을 한 번에 분석
    ↓
Haiku 요약 (generate_insight_summary)
    ↓
HTML 렌더링 → 이메일 발송
```

### 현재 데이터 현황
| 데이터 | 출처 | 방식 | 비고 |
|--------|------|------|------|
| 주가/지수 (KOSPI, S&P500 등) | DB (`stock_price_1d`) | 전날 적재 | |
| 투자자 수급 | DB (`investor_flow`) | 전날 적재 | |
| 반도체 현물가 | DB (`macro_indicators`) | 주기적 적재 | |
| 매크로 지표 (금리, 환율, VIX 등) | DB (`macro_indicators`) | 주기적 적재 | |
| DART 공시 | DB | **주 단위** 적재 | ⚠️ 당일 공시 반영 불가 |
| 뉴스 | Naver 뉴스 API | **실시간** | 유일한 실시간 소스 |
| 미국 애프터장 | 없음 | - | 필요시 추가 검토 |

### 한계점
| 문제 | 내용 |
|------|------|
| 단일 컨텍스트 과부하 | 모든 데이터를 하나의 프롬프트에 넣어 분석 품질 저하 |
| 고정 데이터 위주 | DB 적재 데이터 외 실시간 맥락 반영 제한적 |
| DART 공시 지연 | 주 단위 적재로 전날 마감 후 공시 반영 불가 |
| 품질 검증 없음 | 할루시네이션, 수치 오류 자동 감지 안됨 |
| 섹션별 깊이 불균일 | 중요도와 관계없이 동일한 분석 깊이 |

---

## 2. LangGraph 도입 목표

- **병렬 멀티 에이전트**: 섹션별 전담 에이전트가 동시에 분석
- **MCP 기반 온디맨드 데이터**: 필요한 데이터만 그때그때 조회 → 토큰 효율 향상
- **ReAct 자율 검색**: DB/MCP에 없는 정보는 에이전트가 직접 뉴스 검색
- **비용 최적화**: 섹션 복잡도에 따라 모델 차별화 (Haiku / Sonnet)
- **품질 검증 루프**: Critic 에이전트가 수치 오류·할루시네이션 감지 후 재분석

---

## 3. Korea Stock MCP 서버 도입

### MCP란?
Model Context Protocol — LLM이 외부 도구를 **온디맨드**로 호출하는 표준 프로토콜.
모든 데이터를 미리 프롬프트에 주입하는 대신, 에이전트가 **필요한 것만** 요청해서 가져옴.

### 활용할 MCP: [korea-stock-mcp](https://github.com/jjlabsio/korea-stock-mcp)

| MCP Tool | 활용 목적 |
|----------|-----------|
| `get_disclosure_list` | 전날 마감 후 공시 실시간 조회 (DART 주단위 파이프라인 대체) |
| `get_disclosure` | 주요 공시 원문 내용 파악 (실적, 유상증자 등) |
| `get_financial_statement` | 실적 발표 시즌 재무제표 즉시 확인 |
| `get_stock_trade_info` | 특정 종목 당일 거래 데이터 온디맨드 조회 |
| `get_stock_base_info` | 종목 기본 정보 (시장 구분 등) |

### 토큰 효율성
- **기존**: 모든 수집 데이터를 프롬프트에 전부 주입 (안 쓰는 것도 토큰 소비)
- **MCP**: 에이전트가 필요한 것만 tool 호출 → 선택적 소비
- **스마트 문서 처리**: 대용량 공시는 TOC만 먼저 받고, 필요 섹션만 추가 요청 → 토큰 낭비 방지

---

## 4. 전체 아키텍처

```
┌──────────────────────────────────────────────────────────────────┐
│                        LangGraph Graph                            │
│                                                                  │
│  [data_loader]                                                   │
│  - DB 적재 데이터 로드 (지수, 수급, 반도체, 매크로)               │
│  - Naver 뉴스 RAG (기존)                                         │
│       ↓                                                          │
│  [supervisor] ← Haiku (라우팅/지시)                              │
│       │                                                          │
│  ┌────┴────────────────────────┐                                 │
│  ↓          ↓          ↓       ↓                                 │
│ [global]  [korea]   [semi]  [flow]                               │
│ Sonnet    Sonnet    Haiku   Haiku                                 │
│ +검색     +MCP      +DB     +DB                                  │
│  └────┬────────────────────────┘                                 │
│       ↓                                                          │
│  [aggregator] ← Sonnet                                           │
│       ↓                                                          │
│  [quality_checker] ← Haiku                                       │
│     ↓         ↓                                                  │
│   (OK)    (재분석, 최대 2회)                                      │
│     ↓         ↓                                                  │
│  [report_builder] → 이메일/PDF 발송                              │
└──────────────────────────────────────────────────────────────────┘

외부 연결:
  global_agent  ──→ Naver 뉴스 API / SerpAPI (해외 이슈)
  korea_agent   ──→ Naver 뉴스 API + korea-stock-mcp (DART 공시, KRX)
  semi_agent    ──→ DB만 (충분)
  flow_agent    ──→ DB만 (충분)
```

---

## 5. 에이전트 상세 설계

### 5-1. Supervisor 에이전트
- **모델**: Claude Haiku
- **역할**: 전체 흐름 조율, 각 에이전트에 태스크 분배
- **입력**: DB 적재 데이터 요약, 날짜/시장 상황
- **출력**: 각 에이전트별 작업 지시

```python
{
  "global_agent": {"needs_search": True,  "query": "FOMC 최신 발언 금리"},
  "korea_agent":  {"needs_search": True,  "query": "외국인 매도 원인",
                   "needs_dart": True,    "dart_query": "전날 주요 공시"},
  "semi_agent":   {"needs_search": False},
  "flow_agent":   {"needs_search": False},
}
```

---

### 5-2. 글로벌 매크로 에이전트 (global_agent)
- **모델**: Claude Sonnet / GPT-4o
- **담당**: 미국 시장, 글로벌 정세, 매크로 환경
- **DB 데이터**: S&P500, NASDAQ, VIX, 금리, 환율, 원자재
- **Tools**:
  - `search_news(query)`: Naver 뉴스 API — 국내 보도 기반 글로벌 이슈
  - `search_web(query)`: SerpAPI — 해외 매크로 이슈 (FOMC, 관세 등)
- **출력**: 글로벌 섹션 분석 텍스트 + 신호등 `[🟢/🟡/🔴]`

---

### 5-3. 한국 시장 에이전트 (korea_agent)
- **모델**: Claude Sonnet / GPT-4o
- **담당**: KOSPI/KOSDAQ, 섹터, 한국 시장 전망
- **DB 데이터**: 한국 지수, 섹터별 수익률, 야간선물, 투자자 수급 요약
- **Tools**:
  - `search_news(query)`: Naver 뉴스 — 국내 시장 이슈
  - `get_disclosure_list(date)`: MCP — 전날 마감 후 주요 공시 조회
  - `get_disclosure(id)`: MCP — 공시 원문 확인 (실적, 유상증자 등)
  - `get_stock_trade_info(ticker)`: MCP — 특정 종목 거래 데이터
- **출력**: 한국 전일/전망 섹션 + 신호등

---

### 5-4. 반도체 에이전트 (semi_agent)
- **모델**: Claude Sonnet / GPT-4o (Haiku → 업그레이드, 외부 맥락 해석 필요)
- **담당**: DRAM/NAND 현물가, 반도체 수급/공급 맥락, 섹터 분석
- **DB 데이터**: `macro_indicators` (dram_*, nand_*), 반도체 종목 수급
- **Tools**:
  - `get_disclosure_list(date)`: MCP — 삼성전자·SK하이닉스 전날 공시 (감산/증산, 실적 등)
  - `get_disclosure(id)`: MCP — 공시 원문 확인
  - `search_web(query)`: TrendForce·IC Insights 최신 리포트, TSMC 월매출, ASML 수주 동향
  - `search_news(query)`: 국내 반도체 산업 뉴스
- **분석 포인트**:
  - 현물가 변동 원인 (공급 감산? 수요 회복?)
  - 빅테크 CapEx 동향 → AI 서버 수요 연결
  - PC/스마트폰 출하량 전망 → 소비자용 DRAM/NAND 수요
  - 한국 반도체 수출 동향
- **출력**: 반도체 섹션 분석 텍스트 + 신호등

---

### 5-5. 수급 에이전트 (flow_agent)
- **모델**: Claude Haiku
- **담당**: 외국인/기관/개인 투자자 동향, ETF 수급
- **DB 데이터**: `investor_flow`, `investor_trading`
- **Tools**: 없음
- **출력**: 수급 섹션 분석 텍스트

---

### 5-6. Aggregator 에이전트
- **모델**: Claude Sonnet
- **역할**: 4개 에이전트 결과를 하나의 일관된 리포트로 통합
- **입력**: 각 에이전트 출력 텍스트
- **출력**: 통합 분석 + 오늘의 핵심 + 체크리스트

---

### 5-7. Quality Checker 에이전트
- **모델**: Claude Haiku
- **역할**: 수치 오류·할루시네이션 감지, 품질 점수 평가
- **체크 항목**:
  - 제공된 수치와 분석 내 수치 일치 여부
  - 신호등 5개 모두 존재 여부
  - 필수 섹션 누락 여부
- **출력**: `{"ok": true}` 또는 `{"ok": false, "issues": [...]}`
- **재분석**: 최대 2회까지 supervisor로 피드백 전달

---

## 6. 그래프 State 설계

```python
from typing import TypedDict
from langgraph.graph import StateGraph

class ReportState(TypedDict):
    # 입력
    macro_data: dict          # DB 적재 데이터
    news_context: str         # Naver 뉴스 RAG 결과

    # Supervisor 지시
    agent_tasks: dict

    # 에이전트 출력
    global_analysis: str
    korea_analysis: str
    semi_analysis: str
    flow_analysis: str

    # 통합 결과
    final_analysis: str
    signals: dict             # {"글로벌": "🟢", "한국(전일)": "🔴", ...}
    key_summary: str
    checklist: str
    insight_outlook: str
    insight_scenario: str

    # 품질 검증
    quality_ok: bool
    quality_issues: list
    iteration: int            # 재분석 횟수 (최대 2)
```

---

## 7. Tool 레이어 아키텍처 — MCP 서버 분리

### 설계 원칙
Tool 구현을 LangGraph 에이전트 코드와 **완전히 분리**한다.

- **MCP 서버** = Tool 관리 레이어 (구현, 배포, 버전 관리)
- **LangGraph** = 에이전트 오케스트레이션 레이어 (분석 흐름, 모델 선택)

이렇게 분리하면:
- Tool 추가/수정 시 LangGraph 코드 변경 불필요
- 모닝 리포트 외 다른 서비스(NL2SQL, 포트폴리오 분석 등)에서 같은 MCP 서버 재사용
- MCP Inspector로 tool 독립 테스트 가능
- Node.js(korea-stock-mcp) ↔ Python(LangGraph) 언어 무관 연결

### 전체 레이어 구조
```
┌─────────────────────────────────────┐
│       LangGraph (Python)             │  ← 에이전트 오케스트레이션
│  supervisor / agents / aggregator   │
└───────────────┬─────────────────────┘
                │ langchain-mcp-adapters
┌───────────────▼─────────────────────┐
│         MCP 서버 레이어              │  ← Tool 관리
│  ┌─────────────────────────────┐    │
│  │  korea-stock-mcp (Node.js)  │    │  DART / KRX
│  ├─────────────────────────────┤    │
│  │  news-search-mcp (Python)   │    │  Naver 뉴스 / SerpAPI
│  └─────────────────────────────┘    │
└───────────────┬─────────────────────┘
                │
┌───────────────▼─────────────────────┐
│         외부 API / DB                │  ← 데이터 소스
│  DART API / KRX API / Naver / DB    │
└─────────────────────────────────────┘
```

### MCP 서버 구성

| MCP 서버 | 언어 | 제공 Tools | 비고 |
|---------|------|-----------|------|
| `bip-stock-mcp` | Python + FastMCP | DART / KRX / Naver / SerpAPI 전체 | 단일 서버로 통합 구현 |

> Node.js 런타임 의존성 없음. 전체 Python 단일 스택.

### LangGraph ↔ MCP 연결 방식

```python
from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.prebuilt import create_react_agent

async with MultiServerMCPClient({
    "korea-stock": {
        "command": "node",
        "args": ["./mcp-servers/korea-stock-mcp/index.js"],
        "env": {
            "DART_API_KEY": os.getenv("DART_API_KEY"),
            "KRX_API_KEY": os.getenv("KRX_API_KEY"),
        }
    },
    "news-search": {
        "command": "python",
        "args": ["./mcp-servers/news-search-mcp/server.py"],
        "env": {
            "NAVER_CLIENT_ID": os.getenv("NAVER_CLIENT_ID"),
            "NAVER_CLIENT_SECRET": os.getenv("NAVER_CLIENT_SECRET"),
            "SERP_API_KEY": os.getenv("SERP_API_KEY"),
        }
    },
}) as client:
    tools = await client.get_tools()
    # 에이전트별로 필요한 tool만 필터링해서 주입
    korea_tools = [t for t in tools if t.name in ["get_disclosure_list", "get_disclosure", "search_news"]]
    semi_tools   = [t for t in tools if t.name in ["get_disclosure_list", "get_disclosure", "search_web", "search_news"]]
    global_tools = [t for t in tools if t.name in ["search_news", "search_web"]]
```

---

## 8. 비용 최적화 전략

| 에이전트 | 모델 | 예상 토큰 | 비고 |
|---------|------|-----------|------|
| Supervisor | Haiku | ~1K | 라우팅만 |
| global_agent | Sonnet | ~5K in / ~3K out | 검색 포함 |
| korea_agent | Sonnet | ~5K in / ~3K out | MCP+검색 포함 |
| semi_agent | Sonnet | ~5K in / ~3K out | MCP+검색 포함 (Haiku→업그레이드) |
| flow_agent | Haiku | ~2K in / ~1K out | DB만 |
| aggregator | Sonnet | ~8K in / ~4K out | 통합 |
| quality_checker | Haiku | ~4K in / ~0.5K out | |

**현재 대비 토큰**: 약 2.5~3배 증가 (반도체 에이전트 업그레이드로 소폭 증가)
**MCP 효과**: 필요한 데이터만 온디맨드 조회 → 불필요한 컨텍스트 제거
**비용 절감 포인트**: 수급(flow)은 Haiku 유지, Sonnet은 맥락 해석이 필요한 3개 에이전트 집중

---

## 9. 기존 시스템과의 관계

- **기존 DAG 파이프라인**: 그대로 유지 (수치 데이터 적재 담당)
- **LangGraph**: `analyze_market_v2()` 자리만 교체
- **MCP 서버**: korea-stock-mcp를 별도 프로세스로 실행, LangGraph tool로 wrapping
- **이메일/PDF/템플릿**: 기존 코드 그대로 사용

```
기존: collect_all_macro_data() → analyze_market_v2()    → 발송
신규: collect_all_macro_data() → langgraph_analyze()    → 발송
                                      ↕ (온디맨드)
                               korea-stock-mcp (DART/KRX)
```

---

## 10. 기술 스택 정리

### BIP-Agents 프로젝트 구조
```
BIP-Agents/                          ← 별도 repo (BIP-Pipeline과 분리)
  ├── mcp-servers/
  │   └── bip-stock-mcp/             ← Python FastMCP 통합 서버 (신규 구현)
  │       ├── dart.py                  DART API (korea-stock-mcp 로직 참고)
  │       ├── krx.py                   KRX API  (korea-stock-mcp 로직 참고)
  │       ├── news.py                  Naver / SerpAPI (realtime_news.py 재활용)
  │       ├── server.py                FastMCP 서버 엔트리포인트
  │       └── pyproject.toml
  ├── langgraph/                     ← LangGraph 에이전트
  └── .env
```

> **참고**: [korea-stock-mcp](https://github.com/jjlabsio/korea-stock-mcp) 는 서브모듈로 사용하지 않고,
> DART/KRX API 호출 로직 참고용으로만 활용. Node.js 런타임 의존성 제거.

### 기술 스택

| 레이어 | 구성요소 | 기술 | 오픈소스 |
|--------|---------|------|---------|
| **에이전트 오케스트레이션** | LangGraph 에이전트 | Python | [LangGraph](https://github.com/langchain-ai/langgraph) |
| **MCP ↔ LangGraph 연결** | Tool 어댑터 | Python | [langchain-mcp-adapters](https://github.com/langchain-ai/langchain-mcp-adapters) |
| **MCP 서버** | DART / KRX / 뉴스 Tool 통합 | Python | [FastMCP](https://github.com/jlowin/fastmcp) |
| **패키지 관리** | 의존성 관리 | Python | [uv](https://github.com/astral-sh/uv) |
| **LLM** | 에이전트 모델 | - | Claude Sonnet / Haiku |

### FastMCP vs FastAPI
| | FastAPI | FastMCP |
|---|---------|---------|
| 목적 | REST API 서버 (사람/서비스용) | MCP 서버 (LLM 에이전트 전용) |
| 호출 방식 | HTTP | MCP 프로토콜 (stdio / SSE) |
| 사용 이유 | - | LangGraph tool 노출에 최적화, 코드 단순 |

### bip-stock-mcp 제공 Tools

| Tool | 출처 참고 | 설명 |
|------|-----------|------|
| `get_disclosure_list` | korea-stock-mcp | 특정일 DART 공시 목록 조회 |
| `get_disclosure` | korea-stock-mcp | 공시 원문 조회 (대용량 섹션별 요청) |
| `get_financial_statement` | korea-stock-mcp | XBRL 재무제표 조회 |
| `get_stock_trade_info` | korea-stock-mcp | KRX 종목 거래 데이터 |
| `search_news` | realtime_news.py | Naver 뉴스 API 검색 |
| `search_web` | 신규 | SerpAPI 해외 이슈 검색 |

### 구현 예시
```python
# mcp-servers/bip-stock-mcp/server.py
from fastmcp import FastMCP
from dart import get_disclosure_list, get_disclosure, get_financial_statement
from krx import get_stock_trade_info
from news import search_news, search_web

mcp = FastMCP("bip-stock")

mcp.tool()(get_disclosure_list)
mcp.tool()(get_disclosure)
mcp.tool()(get_financial_statement)
mcp.tool()(get_stock_trade_info)
mcp.tool()(search_news)
mcp.tool()(search_web)

if __name__ == "__main__":
    mcp.run()
```

### LangGraph ↔ MCP 연결
```python
from langchain_mcp_adapters.client import MultiServerMCPClient

async with MultiServerMCPClient({
    "bip-stock": {
        "command": "python",
        "args": ["./mcp-servers/bip-stock-mcp/server.py"],
        "env": {
            "DART_API_KEY":         os.getenv("DART_API_KEY"),
            "KRX_API_KEY":          os.getenv("KRX_API_KEY"),
            "NAVER_CLIENT_ID":      os.getenv("NAVER_CLIENT_ID"),
            "NAVER_CLIENT_SECRET":  os.getenv("NAVER_CLIENT_SECRET"),
            "SERP_API_KEY":         os.getenv("SERP_API_KEY"),
        }
    },
}) as client:
    tools = await client.get_tools()
    korea_tools  = [t for t in tools if t.name in ["get_disclosure_list", "get_disclosure", "search_news"]]
    semi_tools   = [t for t in tools if t.name in ["get_disclosure_list", "get_disclosure", "search_web", "search_news"]]
    global_tools = [t for t in tools if t.name in ["search_news", "search_web"]]
```

---

## 11. 미결 사항

| 항목 | 내용 | 결정 |
|------|------|------|
| 미국 애프터장 | 실적 발표 등 애프터장 급등락 반영 여부 | 우선 제외, 추후 검토 |
| MCP 배포 방식 | Docker 컨테이너 vs 독립 프로세스 | 추후 결정 |
| DART API 키 | korea-stock-mcp 연결용 | ✅ 보유 중 |
| KRX API 키 | korea-stock-mcp 연결용 | ✅ 보유 중 |

---

## 12. 구현 단계

| 단계 | 내용 | 우선순위 |
|------|------|---------|
| 1 | BIP-Agents 기본 폴더 구조 생성 + uv 환경 세팅 | 필수 |
| 2 | `bip-stock-mcp` 구현 — DART / KRX (korea-stock-mcp 참고) | 필수 |
| 3 | `bip-stock-mcp` 구현 — Naver 뉴스 (realtime_news.py 재활용) | 필수 |
| 4 | `bip-stock-mcp` 구현 — SerpAPI 해외 검색 | 필수 |
| 5 | MCP 서버 동작 확인 (FastMCP Inspector) | 필수 |
| 6 | LangGraph State/Graph 기본 구조 세팅 | 필수 |
| 7 | 에이전트별 프롬프트 분리 (기존 프롬프트 분할) | 필수 |
| 8 | `langchain-mcp-adapters`로 MCP ↔ LangGraph 연결 | 필수 |
| 9 | Supervisor → 병렬 에이전트 → Aggregator 연결 | 필수 |
| 10 | Quality Checker + 재분석 루프 | 중간 |
| 11 | 비용/토큰 모니터링 | 낮음 |

---

## 13. 예상 효과

- **분석 깊이**: 섹션별 전담 분석으로 현재 대비 질적 향상
- **공시 반영**: MCP로 전날 마감 후 주요 공시 즉시 반영 (주단위 → 실시간)
- **토큰 효율**: MCP 온디맨드 조회로 불필요한 컨텍스트 제거
- **안정성**: Quality Checker로 오류 리포트 발송 방지
- **확장성**: 새 섹션/데이터 소스 추가 시 에이전트 하나만 추가
