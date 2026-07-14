# ══════════════════════════════════════════════════════════════════════
#  TestGen v2.2 — образ веб-интерфейса генератора тестов
#  Зависимостей нет: только стандартная библиотека Python.
# ══════════════════════════════════════════════════════════════════════

FROM python:3.12-slim

LABEL org.opencontainers.image.title="TestGen" \
      org.opencontainers.image.description="Генератор итоговых тестов по ИБ — веб-интерфейс" \
      org.opencontainers.image.version="2.2"

# Непривилегированный пользователь
RUN useradd --create-home --shell /usr/sbin/nologin testgen

WORKDIR /app
COPY test_gen.py web_app.py index.html ./
USER testgen

ENV PYTHONUNBUFFERED=1
EXPOSE 8000

# Проверка живости: главная страница должна отвечать 200
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request,sys; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/').status==200 else 1)"

CMD ["python", "web_app.py", "--host", "0.0.0.0", "--port", "8000", "--no-browser"]
