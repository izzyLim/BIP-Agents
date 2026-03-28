# BIP-Agents 아키텍처 설계

> 최종 업데이트: 2026-03-28
> 관련 repo: [BIP-Agents](https://github.com/izzyLim/BIP-Agents) / [BIP-Pipeline](https://github.com/izzyLim/BIP-Pipeline)

---

## 1. 현황 및 문제점

### 현재 구조 (BIP-Pipeline Morning Pulse)
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
| 미국 애프터장 | 없음 | - | 우선 제외, 추후 검토 |

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

## 3. MCP 서버 설계

### MCP란?
Model Context Protocol — LLM이 외부 도구를 **온디맨드**로 호출하는 표준 프로토콜.
모든 데이터를 미리 프롬프트에 주입하는 대신, 에이전트가 **필요한 것만** 요청해서 가져옴.

### 토큰 효율성
- **기존**: 모든 수집 데이터를 프롬프트에 전부 주입 (안 쓰는 것도 토큰 소비)
- **MCP**: 에이전트가 필요한 것만 tool 호출 → 선택적 소비
- **스마트 문서 처리**: 대용량 공시는 TOC만 먼저 받고, 필요 섹션만 추가 요청 → 토큰 낭비 방지

### bip-stock-mcp 제공 Tools (9개)

| Tool 함수명 | 모듈 | 설명 |
|------------|------|------|
| `dart_disclosure_list` | dart.py | DART 공시 목록 (날짜/기업/유형 필터) |
| `dart_disclosure` | dart.py | 공시 원문 문서 목록 조회 |
| `dart_financial_statement` | dart.py | XBRL 재무제표 (연결/별도, 분기별) |
| `dart_corp_search` | dart.py | 기업명으로 DART 고유번호 검색 |
| `krx_stock_trade_info` | krx.py | 종목 일별 시세 (네이버금융) |
| `krx_stock_base_info` | krx.py | 종목 기본정보 (시가총액, PER 등) |
| `krx_market_index` | krx.py | KOSPI/KOSDAQ/KPI200 지수 |
| `news_search_naver` | news.py | 네이버 뉴스 API 검색 |
| `news_search_web` | news.py | SerpAPI 웹 검색 (해외 이슈) |

> **참고**: [korea-stock-mcp](https://github.com/jjlabsio/korea-stock-mcp) 는 서브모듈로 사용하지 않고,
> DART/KRX API 호출 로직 **참고용**으로만 활용. Node.js 런타임 의존성 없음.

---

## 4. 전체 아키텍처

```
┌──────────────────────────────────────────────────────────────────┐
│                        LangGraph Graph                           │
│                                                                  │
│  입력: market_data (DB) + news_data (Naver API)                  │
│       ↓                                                          │
│  [parallel_analysis]                                             │
│  ┌──────────┬──────────┬──────────┬──────────┐                  │
│  ↓          ↓          ↓          ↓          │                  │
│ [global]  [korea]   [semi]    [flow]          │                  │
│ Sonnet    Sonnet    Sonnet    Haiku           │                  │
│           +MCP      +MCP                     │                  │
│  └──────────┴──────────┴──────────┴──────────┘                  │
│       ↓                                                          │
│  [aggregator] ← Sonnet                                           │
│       ↓                                                          │
│  [quality_checker] ← Haiku                                       │
│     ↓ PASS    ↓ FAIL (최대 2회 재시도)                           │
│  [finalize]   └→ [aggregator] (재집계)                           │
│     ↓                                                            │
│  final_report → 기존 HTML 렌더링 → 이메일 발송                    │
└──────────────────────────────────────────────────────────────────┘

외부 연결 (MCP via SSE):
  korea_agent  ──→ bip-stock-mcp:8000 (DART 공시, KRX, 뉴스)
  semi_agent   ──→ bip-stock-mcp:8000 (DART 공시, 반도체 뉴스)
  global_agent ──→ bip-stock-mcp:8000 (뉴스 검색만)
```

> **Supervisor 제거 결정**: 4개 에이전트의 역할이 고정적이므로 동적 라우팅 불필요.
> 직접 병렬 실행이 단순하고 효율적.

---

## 5. 에이전트 상세 설계

### 5-1. 글로벌 매크로 에이전트 (`global_agent`)
- **모델**: Claude Sonnet
- **담당**: 미국 시장, 글로벌 정세, 매크로 환경
- **입력 데이터** (DB):
  - `indices`: S&P500, NASDAQ, VIX
  - `exchange_rates`: 달러/원, 엔 등
  - `macro`: VIX, DXY, WTI, 금
- **MCP Tools**: `news_search_naver`, `news_search_web` (필요 시)
- **출력**: 글로벌 섹션 분석 + 신호등 `🟢/🟡/🔴`

---

### 5-2. 한국 시장 에이전트 (`korea_agent`)
- **모델**: Claude Sonnet
- **담당**: KOSPI/KOSDAQ, 섹터, 한국 시장 전망
- **입력 데이터** (DB):
  - `kospi`, `kosdaq` 지수
  - `investor_flow`: 외국인/기관/개인
  - `sectors`: 섹터별 등락률
- **MCP Tools**:
  - `dart_disclosure_list` / `dart_disclosure`: 전날 마감 후 주요 공시
  - `krx_stock_trade_info` / `krx_stock_base_info`: 종목 온디맨드 조회
  - `news_search_naver`: 국내 시장 뉴스
- **출력**: 한국 전일/전망 섹션 + 신호등

---

### 5-3. 반도체 에이전트 (`semi_agent`)
- **모델**: Claude Sonnet (Haiku → 업그레이드: 외부 맥락 해석 필요)
- **담당**: DRAM/NAND 현물가, AI/HBM 수요, 반도체 수급
- **입력 데이터** (DB):
  - `semiconductor_prices`: DRAM/NAND 현물가
  - 반도체 종목 수급 데이터
- **MCP Tools**:
  - `dart_disclosure_list` / `dart_disclosure`: 삼성/SK하이닉스 주요 공시
  - `news_search_naver`: 국내 반도체 뉴스
  - `news_search_web`: TrendForce·TSMC·ASML 동향 (SerpAPI)
- **분석 포인트**:
  - 현물가 변동 원인 (공급 감산? 수요 회복?)
  - 빅테크 CapEx → AI 서버/HBM 수요 연결
  - PC/스마트폰 출하 → 소비자용 메모리 수요
- **출력**: 반도체 섹션 분석 + 신호등

---

### 5-4. 수급 에이전트 (`flow_agent`)
- **모델**: Claude Haiku (비용 효율)
- **담당**: 외국인/기관/개인 투자자 동향
- **입력 데이터** (DB):
  - `investor_flow`, `program_trading`
- **MCP Tools**: 없음 (DB 데이터로 충분)
- **출력**: 수급 섹션 분석 + 신호등

---

### 5-5. Aggregator 에이전트
- **모델**: Claude Sonnet
- **역할**: 4개 에이전트 결과를 하나의 일관된 리포트로 통합
- **출력 포맷**:
  ```
  ### 📌 오늘의 핵심
  ### 🌐 글로벌 시장 전망
  ### 🇰🇷 한국 시장 전망
  ### ✅ 오늘의 체크리스트
  ### ⚡ 대응 시나리오
  ### 💾 반도체/테크
  ### 💰 수급 동향
  ```

---

### 5-6. Quality Checker 에이전트
- **모델**: Claude Haiku
- **역할**: 수치 오류·할루시네이션 감지, 품질 평가
- **체크 항목**:
  - 본문 수치와 DB 데이터 일치 여부
  - 신호등 판단이 데이터와 맞는지
  - 필수 섹션(핵심/전망/체크리스트/시나리오) 존재 여부
  - 체크리스트 구체성
- **재시도**: FAIL 시 aggregator로 피드백 전달, 최대 2회

---

## 6. State 설계

```python
class ReportState(TypedDict):
    # 입력
    market_data: Dict[str, Any]      # DB 적재 데이터 (지수, 수급, 반도체, 매크로)
    news_data: str                    # Naver 뉴스 RAG 결과

    # 에이전트별 분석 결과
    global_analysis: Optional[str]
    korea_analysis: Optional[str]
    semi_analysis: Optional[str]
    flow_analysis: Optional[str]

    # 집계/품질
    aggregated_report: Optional[str]
    quality_passed: Optional[bool]
    quality_feedback: Optional[str]
    retry_count: int                  # 재시도 횟수 (최대 2)

    # 최종 출력
    final_report: Optional[str]
    token_usage: Dict[str, int]       # 에이전트별 토큰 사용량

    # 메타
    report_date: str
    errors: List[str]
```

---

## 7. Tool 레이어 아키텍처

### 설계 원칙
Tool 구현을 LangGraph 에이전트 코드와 **완전히 분리**한다.

- **MCP 서버** = Tool 관리 레이어 (구현, 배포, 버전 관리)
- **LangGraph** = 에이전트 오케스트레이션 레이어 (분석 흐름, 모델 선택)

이렇게 분리하면:
- Tool 추가/수정 시 LangGraph 코드 변경 불필요
- Morning Pulse 외 다른 서비스(NL2SQL, 포트폴리오 분석 등)에서 같은 MCP 서버 재사용
- MCP Inspector로 tool 독립 테스트 가능

### 전체 레이어 구조
```
┌─────────────────────────────────────┐
│       LangGraph (Python)             │  ← 에이전트 오케스트레이션
│  parallel_analysis / aggregator     │
└───────────────┬─────────────────────┘
                │ langchain-mcp-adapters (SSE)
┌───────────────▼─────────────────────┐
│  bip-stock-mcp (Python + FastMCP)   │  ← Tool 관리 레이어
│  9개 Tool (DART / KRX / 뉴스)       │  ← :8000/sse
└───────────────┬─────────────────────┘
                │
┌───────────────▼─────────────────────┐
│         외부 API / DB               │  ← 데이터 소스
│  DART API / 네이버금융 / Naver / DB  │
└─────────────────────────────────────┘
```

### LangGraph ↔ MCP 연결 방식 (SSE)

```python
from langchain_mcp_adapters.client import MultiServerMCPClient

async with MultiServerMCPClient({
    "bip-stock": {
        "url": "http://bip-stock-mcp:8000/sse",   # Docker 환경
        "transport": "sse",
    }
}) as client:
    tools = client.get_tools()
    # 에이전트별 필요한 tool만 필터링
    korea_tools  = [t for t in tools if t.name in [
        "dart_disclosure_list", "dart_disclosure",
        "krx_stock_trade_info", "news_search_naver"
    ]]
    semi_tools   = [t for t in tools if t.name in [
        "dart_disclosure_list", "dart_disclosure",
        "news_search_naver", "news_search_web"
    ]]
    global_tools = [t for t in tools if t.name in [
        "news_search_naver", "news_search_web"
    ]]
```

---

## 8. 기술 스택

### 프로젝트 구조
```
BIP-Agents/
├── .env.example
├── .gitignore
├── docker-compose.yml
├── docs/
│   ├── bip_agents_architecture.md   ← 이 파일
│   └── dev_progress.md              ← 개발 진행 현황
├── mcp-servers/
│   └── bip-stock-mcp/
│       ├── Dockerfile
│       ├── pyproject.toml           (uv, Python 3.10)
│       ├── server.py                FastMCP 엔트리포인트
│       ├── dart.py                  DART API tools
│       ├── krx.py                   KRX/네이버금융 tools
│       └── news.py                  Naver/SerpAPI tools
└── langgraph/
    ├── Dockerfile
    ├── pyproject.toml               (uv, Python 3.10)
    ├── state.py                     ReportState TypedDict
    ├── agents.py                    에이전트 함수 정의
    ├── graph.py                     StateGraph + MCP 연결
    └── main.py                      run_morning_pulse() 진입점
```

### 기술 스택 상세

| 레이어 | 구성요소 | 기술 | 버전 |
|--------|---------|------|------|
| **에이전트 오케스트레이션** | LangGraph | Python | 1.1.3+ |
| **MCP ↔ LangGraph 연결** | langchain-mcp-adapters | Python | 0.2.2+ |
| **LLM 클라이언트** | langchain-anthropic | Python | 1.4.0+ |
| **MCP 서버** | FastMCP | Python | 3.1.1+ |
| **HTTP 클라이언트** | httpx | Python | 0.28.1+ |
| **패키지 관리** | uv | - | 0.11.2+ |
| **LLM** | Claude Sonnet 4.6 / Haiku 4.5 | Anthropic | - |
| **컨테이너** | Docker + Compose | - | - |

### FastMCP 전송 방식

| 환경 | transport | 연결 방법 |
|------|-----------|---------|
| 로컬 / Claude Desktop | `stdio` | 프로세스 직접 실행 |
| Docker | `sse` | `http://bip-stock-mcp:8000/sse` |

환경변수 `MCP_TRANSPORT`로 전환: `stdio` (기본) / `sse`

---

## 9. Docker 실행 구성

### docker-compose.yml 서비스

| 서비스 | 이미지 | 포트 | 실행 방식 |
|--------|--------|------|---------|
| `bip-stock-mcp` | 로컬 빌드 | 8000 | 상시 실행 (`restart: unless-stopped`) |
| `langgraph` | 로컬 빌드 | - | 필요 시 (`profiles: run`) |

### 네트워크 구성
```
stock-network (external)  ← Airflow와 공유
    ├── airflow-worker
    ├── airflow-scheduler
    └── bip-stock-mcp       ← Airflow에서 직접 접근 가능

bip-agents-network (internal)
    ├── bip-stock-mcp
    └── langgraph
```

### 실행 명령어
```bash
# MCP 서버 시작 (상시)
docker compose up -d bip-stock-mcp

# LangGraph 단독 테스트
docker compose run --rm langgraph python main.py

# 로그 확인
docker compose logs -f bip-stock-mcp
```

---

## 10. 비용 추정

| 에이전트 | 모델 | 예상 토큰 (in/out) | 비고 |
|---------|------|-------------------|------|
| `global_agent` | Sonnet | ~5K / ~2K | |
| `korea_agent` | Sonnet | ~5K / ~2K | MCP tool 호출 포함 |
| `semi_agent` | Sonnet | ~5K / ~2K | MCP+웹검색 포함 |
| `flow_agent` | Haiku | ~2K / ~1K | |
| `aggregator` | Sonnet | ~8K / ~4K | 4개 결과 통합 |
| `quality_checker` | Haiku | ~4K / ~0.5K | |
| **합계** | | ~29K in / ~11.5K out | |

**기존 대비**: ~2.5배 토큰 증가
**MCP 효과**: 필요 데이터만 온디맨드 → 불필요한 컨텍스트 제거
**비용 절감 포인트**: 수급(flow), 품질검증(quality)은 Haiku 유지

---

## 11. 기존 시스템과의 통합

```
[기존 BIP-Pipeline Airflow DAG]

collect_all_macro_data()          ← 그대로 유지
    ↓
run_morning_pulse()               ← analyze_market_v2() 대체
  (langgraph/main.py)
    ↓ (stock-network 통해 MCP 접근)
bip-stock-mcp (DART/KRX/뉴스)
    ↓
final_report (마크다운)
    ↓
기존 HTML 렌더링 / 이메일 발송   ← 그대로 유지
```

**변경 범위 최소화**: `analyze_market_v2()` 호출 1곳만 교체

---

## 12. 구현 단계

| 단계 | 내용 | 상태 |
|------|------|------|
| 1 | BIP-Agents 폴더 구조 + uv 환경 | ✅ 완료 |
| 2 | `bip-stock-mcp` 구현 (DART/KRX/뉴스 9개 tool) | ✅ 완료 |
| 3 | LangGraph State/Graph/Agents 기본 구조 | ✅ 완료 |
| 4 | Docker 실행 환경 (Dockerfile + docker-compose) | ✅ 완료 |
| 5 | MCP ↔ LangGraph SSE 연결 실제 동작 검증 | 🔲 예정 |
| 6 | 에이전트별 프롬프트 고도화 | 🔲 예정 |
| 7 | Airflow DAG 통합 (`analyze_market_v2` 교체) | 🔲 예정 |
| 8 | Quality Checker 재시도 루프 테스트 | 🔲 예정 |
| 9 | 토큰/비용 모니터링 | 🔲 예정 |

---

## 13. 미결 사항

| 항목 | 내용 | 결정 |
|------|------|------|
| 미국 애프터장 | 실적 발표 등 애프터장 급등락 반영 여부 | 우선 제외, 추후 검토 |
| DART API 키 | bip-stock-mcp 연결용 | ✅ 보유 중 |
| SerpAPI 키 | 해외 웹 검색용 | ⚠️ 미확인 (없으면 뉴스 검색만) |
| MCP Inspector | tool 독립 테스트 도구 활용 | Phase 2에서 검증 예정 |

---

## 14. 예상 효과

- **분석 깊이**: 섹션별 전담 에이전트로 현재 대비 질적 향상
- **공시 반영**: MCP로 전날 마감 후 주요 공시 즉시 반영 (주단위 → 당일)
- **토큰 효율**: MCP 온디맨드 조회로 불필요한 컨텍스트 제거
- **안정성**: Quality Checker로 오류 리포트 발송 방지
- **확장성**: 새 섹션/데이터 소스 추가 시 에이전트 하나만 추가, MCP tool 하나만 추가
- **재사용성**: `bip-stock-mcp`는 Morning Pulse 외 NL2SQL, 포트폴리오 분석 등에도 활용 가능
