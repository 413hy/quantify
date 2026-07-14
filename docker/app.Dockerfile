FROM docker.io/library/python@sha256:a5d9a95a366e9cb09c32e2623ae98320433f169b2974b451969459ca585e009a

ARG UV_VERSION=0.11.28
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH=/app/.venv/bin:$PATH

RUN python -m pip install --no-cache-dir "uv==${UV_VERSION}" \
    && groupadd --system --gid 65532 aiq \
    && useradd --system --uid 65532 --gid 65532 --home-dir /nonexistent --shell /usr/sbin/nologin aiq

WORKDIR /app
COPY --chown=65532:65532 pyproject.toml uv.lock README.md ./
COPY --chown=65532:65532 src ./src
RUN uv sync --frozen --no-dev --no-editable
COPY --chown=65532:65532 contracts ./contracts
COPY --chown=65532:65532 config ./config
COPY --chown=65532:65532 migrations ./migrations

USER 65532:65532
ENTRYPOINT []
CMD ["python", "-m", "ai_quant.services.locked_process"]
