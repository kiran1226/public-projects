# Tender radar: the command-line tool and everything the daily run needs.
#
#   docker build -t tender-radar .
#   docker run --rm tender-radar tenderradar eval --out /tmp/eval-report.md
#
# compose.yaml runs the daily loop with this image.

FROM python:3.12-slim

# uv, copied from its own image. The version is the one that wrote uv.lock.
COPY --from=ghcr.io/astral-sh/uv:0.11.24 /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

# The embedding model (about 220 MB) is downloaded on first use. It is kept under var/,
# so a volume on var/ keeps it between runs, whichever user the container runs as.
ENV FASTEMBED_CACHE_PATH=/app/var/fastembed \
    HF_HOME=/app/var/huggingface

WORKDIR /app

# Dependencies first, so a change to the code does not install them again.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# The project. .dockerignore keeps .venv, var/ and any .env file out of the image.
# The install stays editable: config.py finds data/ and var/ relative to src/.
COPY . .
RUN uv sync --frozen --no-dev

# The run writes to var/, data/log and data/cassettes only. It does not run as root.
RUN useradd --uid 1000 --create-home radar \
    && mkdir -p var data/log data/cassettes \
    && chown -R radar:radar var data/log data/cassettes
USER radar

CMD ["tenderradar", "--help"]
