# Scrollback in a container, with Piper for reading posts aloud.
#
#   docker run -d --name scrollback -p 127.0.0.1:8765:8765 \
#     -v scrollback:/data ghcr.io/ppolydoras/scrollback
#
# Then open http://localhost:8765 and pick a voice under Settings.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SCROLLBACK_DATA_DIR=/data

RUN pip install --no-cache-dir "piper-tts>=1.3,<2" \
 && useradd --create-home --uid 1000 scrollback \
 && mkdir /data \
 && chown scrollback:scrollback /data

WORKDIR /app
COPY scrollback.py LICENSE ./
COPY scrollback_core ./scrollback_core
COPY static ./static

USER scrollback
VOLUME /data
EXPOSE 8765

HEALTHCHECK --interval=1m --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8765/api/status', timeout=4)"

CMD ["python", "scrollback.py", "--host", "0.0.0.0", "--port", "8765"]
