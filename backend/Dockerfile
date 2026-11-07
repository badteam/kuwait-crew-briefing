FROM python:3.12-alpine
WORKDIR /app
COPY server.py aerodatabox.py ./
RUN adduser -D -u 10001 crew && mkdir /data && chown crew /data
USER crew
ENV DATA_DIR=/data PORT=8080 PYTHONUNBUFFERED=1
EXPOSE 8080
HEALTHCHECK --interval=60s --timeout=5s CMD wget -qO- http://127.0.0.1:8080/health || exit 1
CMD ["python", "server.py"]
