# syntax=docker/dockerfile:1
FROM python:3.12.13-slim-bookworm@sha256:4766d8b510c428e595d74b9cc5bbb2fae8e26316fffb4adc89908d79aacd58a2
LABEL org.opencontainers.image.source="https://github.com/Bdysj/SE-k8s-reuse-playbooks"
LABEL org.opencontainers.image.description="Recompute the study results from checksum-verified archived evidence"
LABEL org.opencontainers.image.licenses="MIT AND Apache-2.0"
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DDN_PYTHON=/usr/local/bin/python3.12
WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN python -m pip install --no-cache-dir -r requirements.txt
COPY reproduction /app/reproduction
COPY tools /app/tools
COPY offline_work /app/offline_work
COPY candidates /app/candidates
COPY manifests /app/manifests
COPY sources /app/sources
COPY seed /app/seed
COPY arch-eval /app/arch-eval
COPY reproduce.sh README.md LICENSE THIRD_PARTY_NOTICES.md /app/
RUN --mount=type=bind,source=.build-assets,target=/assets \
    python -B reproduction/restore.py --from /assets && \
    python -B reproduction/verify.py && \
    chmod -R a+rX /app/results /app/runtime-logs /app/archive-supplemental && \
    rm -rf /app/.build-assets /app/outputs && \
    mkdir /app/outputs && \
    chown 10001:10001 /app/outputs
USER 10001:10001
ENTRYPOINT ["python", "-B", "/app/reproduction/result.py"]
