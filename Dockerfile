# Match the repo's local Python version (`.python-version`) to avoid runtime drift.
FROM python:3.10.16-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install dependencies using the project metadata (avoids Poetry/lock mismatch issues).
COPY pyproject.toml ./
COPY poetry.lock* ./
COPY README.md ./
COPY src ./src
COPY scripts ./scripts
COPY alembic ./alembic
COPY alembic.ini ./

RUN pip install --upgrade pip && pip install .

CMD ["python", "-m", "fateforger.slack_bot.bot"]
