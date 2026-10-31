FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
RUN useradd -r -u 10001 modelharbor && mkdir -p /data && chown modelharbor:modelharbor /data
USER modelharbor
EXPOSE 8096
CMD ["uvicorn","app.main:app","--host","0.0.0.0","--port","8096"]
