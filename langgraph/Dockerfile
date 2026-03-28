FROM python:3.10-slim

WORKDIR /app

# uv 설치
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# 의존성 설치
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# 소스 복사
COPY . .

# MCP 서버 URL (docker-compose 서비스명으로 연결)
ENV MCP_SERVER_URL=http://bip-stock-mcp:8000/sse
ENV USE_MCP=true

ENTRYPOINT ["uv", "run"]
CMD ["python", "main.py"]
