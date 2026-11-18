# Use the sanitized context produced by build.py, never the repository.
ARG RUNTIME_BASE=docker.io/library/python@sha256:83f339c1be6340ae1096010fdccf6552ac932d8f410d45d206014916bdf37e48
FROM ${RUNTIME_BASE} AS seekdb-dependencies
COPY wheel/ /wheels/
RUN python -m pip install --disable-pip-version-check --no-cache-dir --no-compile \
        --no-deps --no-index --target /opt/aio-db-deps /wheels/*.whl

FROM ${RUNTIME_BASE}
RUN apt-get update \
    && apt-get install -y --no-install-recommends nginx tini \
    && rm -rf /var/lib/apt/lists/* /etc/nginx/sites-enabled/default

COPY --from=seekdb-dependencies /opt/aio-db-deps/ /usr/local/lib/python3.11/site-packages/
COPY rootfs/ /
RUN install -d /run/aivision /data/seekdb /data/signoz /var/log/aivision /var/log/agentlens \
    && ln -s /opt/aivision/backend/manage /app/manage \
    && ln -s /opt/aivision/signoz/templates /root/templates \
    && ln -s /data/signoz /var/lib/signoz

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /
EXPOSE 5174 4317 4318
HEALTHCHECK --interval=5s --timeout=15s --start-period=180s --retries=3 \
    CMD ["/usr/local/bin/aivision-all-in-one", "healthcheck"]
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/aivision-all-in-one"]
CMD []
