# Edit moderation cascade: the command-line tool, the review page and everything the live loop needs.
#
#   docker build -t edit-moderation-cascade .
#   docker run --rm edit-moderation-cascade editwatch demo
#
# compose.yaml runs the live loop and serves the review page with this image.
# The image holds no key. It is passed in through the environment.

FROM python:3.12-slim

# uv, copied from its own image. The version is the one that wrote uv.lock.
COPY --from=ghcr.io/astral-sh/uv:0.11.24 /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Dependencies first, so a change to the code does not install them again. The `ui`
# extra is Streamlit, for the review page.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --extra ui --no-install-project

# The project. .dockerignore keeps .venv, var/, data/corrections and any .env file out
# of the image. The install stays editable: config.py finds data/ and var/ relative to src/.
COPY . .
RUN uv sync --frozen --no-dev --extra ui

# The commands write to var/ and data/ only: the store, the trained tier 1 and the
# scoreboard under var/, the logs under data/log, the reviewer's decisions under
# data/corrections, and the summaries that `train` and the evaluation write into data/.
# Nothing runs as root.
RUN useradd --uid 1000 --create-home watch \
    && mkdir -p var data/log data/corrections \
    && chown -R watch:watch var data
USER watch

CMD ["editwatch", "--help"]
