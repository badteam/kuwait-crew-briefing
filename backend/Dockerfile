FROM python:3.12-alpine
WORKDIR /app
COPY server.py .
RUN adduser -D -u 10001 crew && mkdir /data && chown crew /data
USER crew
ENV DATA_DIR=/data PORT=8080
EXPOSE 8080
CMD ["python", "server.py"]
