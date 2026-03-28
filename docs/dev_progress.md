# BIP-Agents 개발 진행 현황

> 최종 업데이트: 2026-03-28
> 관련 repo: [BIP-Agents](https://github.com/izzyLim/BIP-Agents) / [BIP-Pipeline](https://github.com/izzyLim/BIP-Pipeline)

---

## 전체 진행 상태

| 단계 | 내용 | 상태 |
|------|------|------|
| **사전작업** | Morning Pulse (BIP-Pipeline) 버그 수정 및 기능 개선 | ✅ 완료 |
| **Phase 1** | BIP-Agents 기반 구조 — MCP 서버 + LangGraph 골격 + Docker | ✅ 완료 |
| **Phase 2** | MCP ↔ LangGraph 실제 연결 (SSE 통신 검증) | 🔲 미착수 |
| **Phase 3** | 에이전트 프롬프트 고도화 + 품질 검증 루프 테스트 | 🔲 미착수 |
| **Phase 4** | BIP-Pipeline Airflow DAG 통합 | 🔲 미착수 |
| **Phase 5** | 운영 안정화 (토큰 모니터링, 알림, 로그) | 🔲 미착수 |

---

## 사전작업: BIP-Pipeline Morning Pulse 개선 (2026-03-28)

Morning Pulse를 LangGraph로 전환하기 전에 기존 시스템의 버그를 수정하고
LangGraph 전환 후에도 유지할 개선 사항을 반영했습니다.

### 수정된 파일

#### `airflow/dags/reports/templates/morning_report.html`
- **신호등 위치 변경**: 페이지 상단 → AI 상세 분석 섹션 위로 이동
- **섹션 순서 변경**: 핵심 → 시장전망 → 체크리스트 → 대응시나리오 → 지수/매크로/수급 → 신호등 → AI분석
- **`insight_summary` → `insight_outlook` + `insight_scenario`**: 별도 박스로 분리 (파란색/보라색)
- **체크리스트 박스 추가**: 녹색 체크리스트 섹션 신규 추가
- **타이틀 변경**: "어제의 핵심" → "오늘의 핵심"

#### `airflow/dags/reports/report_builder.py`
- **`extract_key_summary` 버그 수정**: `'오늘의 핵심'`과 `'어제의 핵심'` 둘 다 매칭하도록 수정
  - 원인: LLM이 가끔 "어제의 핵심"으로 생성했으나 파싱이 "오늘의 핵심"만 탐지
- **체크리스트 파싱 디버그 로그 추가**

#### `airflow/dags/reports/llm_analyzer_v2.py`
- **`call_haiku` fallback 수정**: `ANTHROPIC_API_KEY` 없을 때 에러 문자열 반환 → `call_llm()` fallback
- **토큰 사용량 로깅 추가**: 각 LLM 호출마다 `input/output token` 수 출력
- **`calculate_reference_signals()` 추가**: 수치 기준으로 신호등 사전 계산
  - VIX, S&P 500, KOSPI, 수급, 반도체 가격 기반 정량 신호
  - AI 판단의 보조 기준으로 프롬프트에 주입
- **`gpt-5.1` 모델 설정 추가**
- **`ANALYSIS_PROMPT_V2`**: `{reference_signals}` 플레이스홀더 추가

---

## Phase 1: 기반 구조 구현 (2026-03-28)

### 구현된 파일 구조

```
BIP-Agents/
├── .env.example                  ← 환경변수 템플릿
├── .gitignore
├── docker-compose.yml            ← 서비스 오케스트레이션
├── docs/
│   ├── bip_agents_architecture.md   ← 전체 설계 문서
│   └── dev_progress.md              ← 이 파일
├── mcp-servers/
│   └── bip-stock-mcp/            ← FastMCP 서버
│       ├── Dockerfile
│       ├── pyproject.toml        ← uv 프로젝트 (Python 3.10)
│       ├── server.py             ← FastMCP 엔트리포인트 (9개 tool)
│       ├── dart.py               ← DART 공시 API (4개 tool)
│       ├── krx.py                ← KRX/네이버금융 (3개 tool)
│       └── news.py               ← 네이버뉴스 + SerpAPI (2개 tool)
└── langgraph/
    ├── Dockerfile
    ├── pyproject.toml            ← uv 프로젝트 (Python 3.10)
    ├── state.py                  ← ReportState TypedDict
    ├── agents.py                 ← 6개 에이전트 함수
    ├── graph.py                  ← StateGraph 정의 + MCP 연결
    └── main.py                   ← run_morning_pulse() 진입점
```

---

### bip-stock-mcp 제공 Tools (9개)

| Tool 함수명 | 파일 | 설명 |
|------------|------|------|
| `dart_disclosure_list` | dart.py | DART 공시 목록 (날짜/기업/유형 필터) |
| `dart_disclosure` | dart.py | 공시 원문 문서 목록 조회 |
| `dart_financial_statement` | dart.py | XBRL 재무제표 (연결/별도, 분기별) |
| `dart_corp_search` | dart.py | 기업명으로 고유번호 검색 |
| `krx_stock_trade_info` | krx.py | 종목 일별 시세 |
| `krx_stock_base_info` | krx.py | 종목 기본정보 (시가총액, PER 등) |
| `krx_market_index` | krx.py | KOSPI/KOSDAQ/KPI200 지수 |
| `news_search_naver` | news.py | 네이버 뉴스 API 검색 |
| `news_search_web` | news.py | SerpAPI 웹 검색 (해외 이슈) |

---

### LangGraph 에이전트 구성

| 에이전트 | 모델 | MCP 도구 | 역할 |
|---------|------|---------|------|
| `global_agent` | Sonnet | 없음 | 글로벌 지수/매크로/VIX 분석 |
| `korea_agent` | Sonnet | ✅ 전체 | KOSPI/KOSDAQ/섹터/공시 분석 |
| `semi_agent` | Sonnet | ✅ 공시+뉴스 | 반도체 가격/수요/종목 분석 |
| `flow_agent` | Haiku | 없음 | 외국인/기관/개인 수급 분석 |
| `aggregator` | Sonnet | 없음 | 4개 에이전트 결과 통합 |
| `quality_checker` | Haiku | 없음 | 수치 오류/할루시네이션 검증 |

**그래프 흐름:**
```
parallel_analysis (global+korea+semi+flow 동시)
    ↓
aggregator
    ↓
quality_checker
    ↓ FAIL (최대 2회 재시도)
    ↓ PASS
finalize → final_report
```

---

### Docker 실행 구성

**서비스 구성:**
| 서비스 | 역할 | 실행 방식 |
|--------|------|---------|
| `bip-stock-mcp` | FastMCP SSE 서버 (:8000) | 상시 실행 (`restart: unless-stopped`) |
| `langgraph` | 에이전트 실행 | 필요 시 실행 (`profiles: run`) |

**네트워크:**
- `bip-agents-network`: 내부 통신
- `stock-network` (external): Airflow와 공유 → Airflow 워커에서 MCP 서버 접근 가능

**MCP 전송 방식:**
- 로컬/Claude Desktop: `stdio` 모드 (`MCP_TRANSPORT=stdio`)
- Docker 환경: `SSE` 모드 (`MCP_TRANSPORT=sse`, `http://bip-stock-mcp:8000/sse`)

---

### 실행 명령어

```bash
# 1. 환경변수 설정
cp .env.example .env
# .env 파일에 API 키 입력

# 2. MCP 서버 빌드 & 실행 (상시)
docker compose up -d bip-stock-mcp

# 3. LangGraph 단독 테스트
docker compose run --rm langgraph python main.py

# 4. 서비스 상태 확인
docker compose ps
docker compose logs bip-stock-mcp
```

---

## Phase 2 예정 작업

### 2-1. MCP ↔ LangGraph SSE 연결 검증
- `docker compose up -d bip-stock-mcp` 후 실제 연결 테스트
- `langchain-mcp-adapters` `MultiServerMCPClient` SSE 방식 동작 확인
- 에이전트별 tool 필터링 적용 (korea_tools, semi_tools, global_tools)

### 2-2. 에이전트별 프롬프트 고도화
- 기존 `ANALYSIS_PROMPT_V2` 분할하여 각 에이전트 전문화
- Reference signals (정량 신호) 각 에이전트 컨텍스트에 주입
- ReAct 패턴 테스트: 에이전트가 MCP tool을 자율적으로 호출하는지 확인

### 2-3. Airflow 통합 DAG
- `dag_morning_report.py`의 `analyze_market_v2()` 호출 부분을
  `run_morning_pulse()` 호출로 교체
- Airflow 워커 → `bip-stock-mcp` 직접 연결 (stock-network)

---

## 주요 기술 결정 사항

| 결정 | 선택 | 이유 |
|------|------|------|
| MCP 서버 언어 | Python (FastMCP) | Node.js 이중 런타임 없이 단일 Python 스택 |
| MCP 서버 수 | 1개 (`bip-stock-mcp`) | 분리 시 관리 비용 증가, 통합이 단순 |
| MCP 전송 방식 | SSE (Docker 환경) | 컨테이너 간 HTTP 통신 필요 |
| LangGraph Supervisor | 제거 | 4개 에이전트 역할이 고정적 → 동적 라우팅 불필요 |
| semi_agent 모델 | Sonnet (Haiku → 업그레이드) | 반도체 외부 맥락(AI 수요, 공급망) 해석에 Sonnet 필요 |
| 패키지 관리 | uv | 빠른 의존성 해결, lock 파일 지원 |
| 재시도 전략 | 최대 2회, aggregator부터 재시도 | 개별 에이전트 재호출보다 통합 단계 재시도가 효율적 |
