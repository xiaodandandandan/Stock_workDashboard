FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY core ./core
COPY web ./web
COPY server.py scan.py ./
RUN mkdir -p /app/cache && chown -R 10001:10001 /app
USER 10001:10001
EXPOSE 8766
CMD ["python", "-B", "server.py", "--host", "0.0.0.0", "--port", "8766", "--no-browser"]
