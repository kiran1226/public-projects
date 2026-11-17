# Issue triage desk: the command-line tool, the review page and everything the hourly run needs.
#
#   docker build -t issue-triage-desk .
#   docker run --rm issue-triage-desk triagedesk demo --stub-embedder
#
# compose.yaml runs the hourly loop and serves the review page with this image.
# The image holds no key and no token. Both are passed in through the environment.

FROM python:3.12-slim

# uv, copied from its own image. The version is the one that wrote uv.lock.
COPY --from=ghcr.io/astral-sh/uv:0.11.24 /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

# The embedding model (about 70 MB) is downloaded on first use. It is kept under var/,
# so a volume on var/ keeps it between runs, whichever user the container runs as.
ENV FASTEMBED_CACHE_PATH=/app/var/fastembed \
    HF_HOME=/app/var/huggingface

WORKDIR /app

# Dependencies first, so a change to the code does not install them again. The `ui`
# extra is Streamlit, for the review page.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --extra ui --no-install-project

# The project. .dockerignore keeps .venv, var/, data/corrections and any .env file out
# of the image. The install stays editable: config.py finds data/ and var/ relative to src/.
COPY . .
RUN uv sync --frozen --no-dev --extra ui

# The run writes to var/, data/log, data/taxonomy-live.json and data/corrections only.
# It does not run as root.
RUN useradd --uid 1000 --create-home desk \
    && mkdir -p var data/log data/corrections \
    && chown desk:desk data \
    && chown -R desk:desk var data/log data/corrections
USER desk

CMD ["triagedesk", "--help"]
