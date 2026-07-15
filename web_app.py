#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════════╗
║      TEST GENERATOR — Веб-интерфейс (локальный, без зависимостей)   ║
╚══════════════════════════════════════════════════════════════════════╝

Локальный веб-сервер поверх test_gen.py. Стандартная библиотека, ни одного
стороннего пакета. Файлы никуда не отправляются — всё работает на вашей машине.

Запуск:
  python web_app.py                 # http://127.0.0.1:8000, браузер откроется сам
  python web_app.py --port 9000     # другой порт
  python web_app.py --no-browser    # не открывать браузер автоматически

API:
  POST /api/parse     — статистика базы и отчёт о проблемах
  POST /api/generate  — генерация вариантов теста
"""

from __future__ import annotations

import argparse
import http.server
import json
import random
import threading
import webbrowser
from pathlib import Path
from typing import Optional

from test_gen import (
    Issue, Question, filter_by_type, format_test, parse_block,
    select_questions, split_into_blocks,
)

APP_DIR = Path(__file__).resolve().parent
INDEX_FILE = APP_DIR / 'index.html'

VALID_TYPES = {'mix', 'basic', 'normal', 'open', 'all'}
VALID_ORDERS = {'random', 'grouped', 'source'}
MAX_VARIANTS = 20

_PDF_STATE: dict = {}


def pdf_available() -> bool:
    """True, если установлены зависимости конвертера PDF (markdown, weasyprint)."""
    if 'ok' not in _PDF_STATE:
        try:
            import md2pdf  # noqa: F401 — проверяем и сам модуль, и его зависимости
            _PDF_STATE['ok'] = True
        except Exception as e:  # noqa: BLE001
            _PDF_STATE['ok'] = False
            _PDF_STATE['reason'] = str(e)
    return _PDF_STATE['ok']


# ─────────────────────────────────────────────────────────────────────────────
# Разбор контента, присланного из браузера (адаптер над test_gen)
# ─────────────────────────────────────────────────────────────────────────────

def parse_content(filename: str, content: str,
                  issues: list[Issue]) -> list[tuple[str, list[Question]]]:
    """Как parse_file, но для текста из запроса (без обращения к диску)."""
    lines = content.lstrip('\ufeff').split('\n')
    result = []
    for name, block_lines in split_into_blocks(lines):
        questions = parse_block(block_lines, name, filename, issues)
        if questions:
            result.append((name, questions))
        else:
            issue = Issue('warning', filename, name,
                          'Вопросы не найдены — блок пропускаем')
            issues.append(issue)
    return result


def counts(questions: list[Question]) -> dict:
    basic = sum(1 for q in questions if q.is_basic)
    open_ = sum(1 for q in questions if q.is_open)
    return {'total': len(questions), 'basic': basic, 'open': open_,
            'normal': len(questions) - basic - open_}


def load_files(files_payload: list[dict]) -> tuple[list, list[int], list[Issue]]:
    """Разбирает все присланные файлы. Возвращает (all_sources, per-file counts, issues)."""
    issues: list[Issue] = []
    all_sources: list[tuple[str, list[Question]]] = []
    file_test_counts: list[int] = []
    seen_names: set[str] = set()

    for f in files_payload:
        name = str(f.get('name', 'файл.md'))
        if name in seen_names:
            issues.append(Issue('warning', name, '—',
                                'Файл с таким именем уже загружен — учитываем один раз'))
            file_test_counts.append(0)
            continue
        seen_names.add(name)
        tests = parse_content(name, str(f.get('content', '')), issues)
        file_test_counts.append(len(tests))
        all_sources.extend(tests)

    for idx, (_, qs) in enumerate(all_sources):
        for q in qs:
            q.source_idx = idx
    return all_sources, file_test_counts, issues


def issues_json(issues: list[Issue]) -> list[dict]:
    return [{'severity': i.severity, 'file': i.file,
             'test': i.test, 'message': i.message} for i in issues]


# ─────────────────────────────────────────────────────────────────────────────
# Обработчики API
# ─────────────────────────────────────────────────────────────────────────────

def api_parse(payload: dict) -> dict:
    files_payload = payload.get('files') or []
    if not files_payload:
        raise ValueError('Загрузите хотя бы один файл с тестами')

    all_sources, _, issues = load_files(files_payload)

    per_file: list[dict] = []
    # Восстанавливаем группировку по файлам через source_file вопросов
    by_file: dict[str, list] = {}
    for name, qs in all_sources:
        by_file.setdefault(qs[0].source_file, []).append((name, qs))
    for f in files_payload:
        fname = str(f.get('name', 'файл.md'))
        tests = by_file.get(fname, [])
        per_file.append({
            'name': fname,
            'tests': [{'name': tname, **counts(qs)} for tname, qs in tests],
            **counts([q for _, qs in tests for q in qs]),
        })

    all_q = [q for _, qs in all_sources for q in qs]
    return {'files': per_file, 'totals': counts(all_q),
            'available': {t: len(filter_by_type(all_q, t)) for t in VALID_TYPES},
            'pdf': pdf_available(),
            'issues': issues_json(issues)}


def api_generate(payload: dict) -> dict:
    files_payload = payload.get('files') or []
    if not files_payload:
        raise ValueError('Загрузите хотя бы один файл с тестами')

    total = payload.get('total')
    if not isinstance(total, int) or total <= 0:
        raise ValueError('Количество вопросов должно быть положительным числом')

    q_type = payload.get('type', 'mix')
    if q_type not in VALID_TYPES:
        raise ValueError(f'Неизвестный тип вопросов: {q_type}')

    variants = payload.get('variants', 1)
    if not isinstance(variants, int) or not 1 <= variants <= MAX_VARIANTS:
        raise ValueError(f'Количество вариантов — от 1 до {MAX_VARIANTS}')

    order = payload.get('order', 'random')
    if order not in VALID_ORDERS:
        raise ValueError(f'Неизвестный порядок вопросов: {order}')

    weights = payload.get('weights')
    if weights is not None:
        if len(weights) != len(files_payload):
            raise ValueError('Количество весов должно совпадать с количеством файлов')
        weights = [float(w) for w in weights]
        if any(w < 0 for w in weights):
            raise ValueError('Веса не могут быть отрицательными')
        if sum(weights) <= 0:
            raise ValueError('Сумма весов должна быть положительной')

    seed: Optional[int] = payload.get('seed')
    base_seed = int(seed) if seed is not None else random.randrange(10 ** 9)
    shuffle = bool(payload.get('shuffle', True))
    show_source = bool(payload.get('show_source', False))
    title = str(payload.get('title') or 'Итоговый тест по ИБ').strip()

    all_sources, file_test_counts, issues = load_files(files_payload)
    if not all_sources:
        raise ValueError('Ни в одном файле не найдено вопросов — проверьте формат')

    file_weights = weights if weights else [1.0] * len(files_payload)

    out_variants = []
    for v in range(1, variants + 1):
        rng = random.Random(f"{base_seed}:{v}")
        selected = select_questions(all_sources, file_weights, file_test_counts,
                                    total, q_type, rng)
        if order == 'source':
            selected = sorted(selected, key=lambda q: (q.source_idx, q.number))
        elif order == 'grouped':
            selected = sorted(selected, key=lambda q: q.source_idx)

        v_title = title + (f' — Вариант {v}' if variants > 1 else '')
        md = format_test(selected, v_title, shuffle, rng, show_source)
        by_source = sorted(
            {q.source for q in selected},
            key=lambda s: -sum(1 for q in selected if q.source == s))
        out_variants.append({
            'variant': v,
            'title': v_title,
            'filename': (f'generated_test_v{v}.md' if variants > 1
                         else 'generated_test.md'),
            'markdown': md,
            'composition': counts(selected),
            'by_source': [
                {'source': s, 'count': sum(1 for q in selected if q.source == s)}
                for s in by_source],
        })

    return {'seed': base_seed, 'variants': out_variants,
            'issues': issues_json(issues)}


ROUTES = {'/api/parse': api_parse, '/api/generate': api_generate}


def api_pdf(payload: dict) -> tuple[bytes, str]:
    """Конвертирует присланный markdown в PDF. Возвращает (bytes, имя файла)."""
    if not pdf_available():
        raise ValueError(
            'Конвертер PDF недоступен: не установлены зависимости '
            '(pip install markdown weasyprint). '
            f'Детали: {_PDF_STATE.get("reason", "—")}')
    md_text = str(payload.get('markdown', '')).strip()
    if not md_text:
        raise ValueError('Пустой markdown — нечего конвертировать')

    from md2pdf import md_to_pdf_bytes
    filename = str(payload.get('filename') or 'generated_test.pdf')
    if not filename.lower().endswith('.pdf'):
        filename += '.pdf'
    return md_to_pdf_bytes(md_text), filename


# ─────────────────────────────────────────────────────────────────────────────
# HTTP-сервер
# ─────────────────────────────────────────────────────────────────────────────

class Handler(http.server.BaseHTTPRequestHandler):
    server_version = 'TestGenWeb/2.4'

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, code: int, obj: dict) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode('utf-8'),
                   'application/json; charset=utf-8')

    def do_GET(self) -> None:  # noqa: N802
        if self.path in ('/', '/index.html'):
            if not INDEX_FILE.exists():
                self._send(500, 'index.html не найден рядом с web_app.py'.encode(),
                           'text/plain; charset=utf-8')
                return
            self._send(200, INDEX_FILE.read_bytes(), 'text/html; charset=utf-8')
        else:
            self._send(404, b'Not found', 'text/plain; charset=utf-8')

    def do_POST(self) -> None:  # noqa: N802
        try:
            length = int(self.headers.get('Content-Length', 0))
            payload = json.loads(self.rfile.read(length).decode('utf-8') or '{}')

            if self.path == '/api/pdf':
                pdf_bytes, filename = api_pdf(payload)
                self.send_response(200)
                self.send_header('Content-Type', 'application/pdf')
                self.send_header('Content-Length', str(len(pdf_bytes)))
                self.send_header('Content-Disposition',
                                 f'attachment; filename="{filename}"')
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                self.wfile.write(pdf_bytes)
                return

            handler = ROUTES.get(self.path)
            if handler is None:
                self._send_json(404, {'error': 'Неизвестный путь API'})
                return
            self._send_json(200, handler(payload))
        except ValueError as e:
            self._send_json(400, {'error': str(e)})
        except Exception as e:  # noqa: BLE001 — отдаём ошибку в интерфейс
            self._send_json(500, {'error': f'Внутренняя ошибка: {e}'})

    def log_message(self, fmt: str, *args) -> None:
        print(f'  {self.address_string()} — {fmt % args}')


def main() -> None:
    p = argparse.ArgumentParser(description='Веб-интерфейс генератора тестов')
    p.add_argument('--host', default='127.0.0.1',
                   help='Адрес для прослушивания (по умолчанию 127.0.0.1; '
                        'в Docker используйте 0.0.0.0)')
    p.add_argument('--port', type=int, default=8000, help='Порт (по умолчанию 8000)')
    p.add_argument('--no-browser', action='store_true',
                   help='Не открывать браузер автоматически')
    args = p.parse_args()

    server = http.server.ThreadingHTTPServer((args.host, args.port), Handler)
    shown_host = '127.0.0.1' if args.host in ('0.0.0.0', '::') else args.host
    url = f'http://{shown_host}:{args.port}'
    print(f'\n🚀 TestGen Web запущен: {url}'
          f'{"  (слушает " + args.host + ")" if args.host != shown_host else ""}')
    print('   Файлы обрабатываются локально и никуда не отправляются.')
    print('   Остановить: Ctrl+C\n')

    if not args.no_browser and args.host not in ('0.0.0.0', '::'):
        threading.Timer(0.6, webbrowser.open, args=(url,)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\n👋 Сервер остановлен')


if __name__ == '__main__':
    main()
