FROM python:3.12-slim

# The official image already includes venv support, so installing Laya from
# the dashboard works inside the container (it goes into /data/laya).
RUN useradd --system --uid 10001 --home-dir /data --shell /usr/sbin/nologin afterword \
 && mkdir /data && chown afterword:afterword /data && chmod 700 /data

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY afterword ./afterword
RUN pip install --no-cache-dir . && rm -rf /app

USER afterword
ENV AFTERWORD_DATA=/data \
    AFTERWORD_LISTEN=0.0.0.0:8080 \
    PYTHONUNBUFFERED=1
VOLUME ["/data"]
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=3)"
CMD ["afterword", "serve"]
