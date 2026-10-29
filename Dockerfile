FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install . && useradd --create-home app

USER app
EXPOSE 8000

# 프리셋을 프로세스 메모리에 저장하므로 워커는 1개
CMD ["uvicorn", "departure_timing.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
