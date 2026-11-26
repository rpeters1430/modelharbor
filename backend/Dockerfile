FROM python:3.15-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
RUN useradd -r -u 10001 modelharbor && mkdir -p /data && chown modelharbor:modelharbor /data
USER modelharbor
EXPOSE 8096
# The slim image has no curl; Python's standard library is enough to probe the unauthenticated health route.
HEALTHCHECK --interval=60s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8096/api/health', timeout=4).status == 200 else 1)"
CMD ["uvicorn","app.main:app","--host","0.0.0.0","--port","8096"]
