# ══════════════════════════════════════════════════════════════════════
#  TestGen v2.4 — образ веб-интерфейса генератора тестов
#  Включает конвертер Markdown → PDF (weasyprint + шрифты DejaVu).
# ══════════════════════════════════════════════════════════════════════

FROM python:3.12-slim

LABEL org.opencontainers.image.title="TestGen" \
      org.opencontainers.image.description="Генератор итоговых тестов по ИБ — веб-интерфейс + PDF" \
      org.opencontainers.image.version="2.4"

# Системные зависимости WeasyPrint и шрифты с кириллицей
RUN apt-get update && apt-get install -y --no-install-recommends \
      libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 fonts-dejavu \
    && rm -rf /var/lib/apt/lists/*

# Непривилегированный пользователь
RUN useradd --create-home --shell /usr/sbin/nologin testgen

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY test_gen.py web_app.py md2pdf.py index.html ./
USER testgen

# Контейнер запускается read-only: кэши шрифтов и временные файлы — в /tmp
ENV PYTHONUNBUFFERED=1 \
    XDG_CACHE_HOME=/tmp/cache \
    HOME=/tmp

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request,sys; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/').status==200 else 1)"

CMD ["python", "web_app.py", "--host", "0.0.0.0", "--port", "8000", "--no-browser"]
