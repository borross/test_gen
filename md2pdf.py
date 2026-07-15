#!/usr/bin/env python3
"""
Конвертер Markdown → PDF.

Рендерит .md в HTML (с поддержкой таблиц, вложенных списков и
GitHub-style callout-ов вида `> [!INFO]`), затем печатает PDF через WeasyPrint.
Кириллица отрисовывается шрифтом DejaVu Sans.

Зависимости:
    pip install markdown weasyprint
    (для кириллицы нужны системные шрифты DejaVu, на Ubuntu: пакет fonts-dejavu)

Использование:
    python md2pdf.py input.md                  # -> input.pdf
    python md2pdf.py input.md output.pdf
    python md2pdf.py input.md -o output.pdf
"""

import argparse
import os
import re

import markdown
from weasyprint import HTML


CSS = """
@page {
  size: A4;
  margin: 2cm 1.8cm 2cm 1.8cm;
  @bottom-center {
    content: counter(page) " / " counter(pages);
    font-family: 'DejaVu Sans', sans-serif;
    font-size: 9px;
    color: #888;
  }
}
* { box-sizing: border-box; }
body {
  font-family: 'DejaVu Sans', sans-serif;
  font-size: 10.5px;
  line-height: 1.5;
  color: #1f2933;
}
h1 {
  font-size: 22px;
  color: #0b3d2e;
  border-bottom: 3px solid #1aa179;
  padding-bottom: 8px;
  margin: 0 0 6px 0;
}
h2 {
  font-size: 16px;
  color: #0b3d2e;
  margin-top: 22px;
  border-bottom: 1px solid #d4dde0;
  padding-bottom: 4px;
}
h3 {
  font-size: 13px;
  color: #14705a;
  margin-top: 16px;
  margin-bottom: 4px;
}
p { margin: 5px 0; }
strong { color: #0b3d2e; }
a { color: #157a5c; text-decoration: none; word-break: break-all; }
ul, ol { margin: 4px 0 8px 0; padding-left: 18px; }
li { margin: 2px 0; }
li > ul, li > ol { margin: 3px 0; padding-left: 22px; }
code {
  font-family: 'DejaVu Sans Mono', monospace;
  font-size: 9.5px;
  background: #eef3f1;
  padding: 1px 4px;
  border-radius: 3px;
  color: #094;
}
hr {
  border: none;
  border-top: 1px solid #d4dde0;
  margin: 18px 0;
}
table {
  border-collapse: collapse;
  width: 100%;
  margin: 8px 0;
  font-size: 10px;
}
th, td {
  border: 1px solid #cdd8da;
  padding: 6px 8px;
  text-align: left;
  vertical-align: top;
}
th { background: #e7f3ee; color: #0b3d2e; }
.callout {
  border-left: 4px solid #1aa179;
  background: #eef7f3;
  padding: 8px 12px;
  margin: 10px 0;
  border-radius: 0 4px 4px 0;
}
.callout.info { border-left-color: #2d7ff9; background: #eef4fe; }
.callout p { margin: 3px 0; }
.callout p:first-child { margin-top: 0; }
.callout p:last-child { margin-bottom: 0; }
h2, h3 { page-break-after: avoid; }
table, .callout { page-break-inside: avoid; }
.page-break { page-break-before: always; }
"""

# Заголовок блока ответов: "## ОТВЕТЫ — ...", "# ОТВЕТЫ" и т.п.
ANSWERS_HEADING_RE = re.compile(r"^(#{1,3}\s*ОТВЕТЫ\b.*)$",
                                re.IGNORECASE | re.MULTILINE)


def break_before_answers(text):
    """Вставляет разрыв страницы перед заголовком блока ответов.

    При печати теста ответы всегда начинаются с новой страницы —
    лист с ключом легко отделить от листов с вопросами.
    """
    return ANSWERS_HEADING_RE.sub(
        r'<div class="page-break"></div>\n\n\1', text)


def double_indent(s):
    """Удваивает ведущие пробелы у каждой строки.

    python-markdown требует 4 пробела для вложенного списка; многие документы
    используют 2. Удвоение сохраняет иерархию и включает корректную вложенность.
    """
    out = []
    for ln in s.split("\n"):
        m = re.match(r"^( +)", ln)
        if m:
            n = len(m.group(1))
            ln = " " * (n * 2) + ln[n:]
        out.append(ln)
    return "\n".join(out)


def convert_callouts(text):
    """Преобразует GitHub-style блоки `> [!TYPE]` в <div class="callout TYPE">."""
    lines = text.split("\n")
    out_lines = []
    i = 0
    while i < len(lines):
        line = lines[i]
        m = re.match(r"^>\s*\[!(\w+)\]\s*$", line)
        if m:
            kind = m.group(1).lower()
            body = []
            i += 1
            while i < len(lines) and lines[i].startswith(">"):
                body.append(re.sub(r"^>\s?", "", lines[i]))
                i += 1
            inner = markdown.markdown(
                "\n".join(body), extensions=["extra", "sane_lists"]
            )
            out_lines.append(f'<div class="callout {kind}">{inner}</div>')
        else:
            out_lines.append(line)
            i += 1
    return "\n".join(out_lines)


def md_to_pdf_bytes(text):
    """Конвертирует markdown-текст в PDF. Возвращает bytes."""
    text = double_indent(text)
    text = convert_callouts(text)
    text = break_before_answers(text)

    body_html = markdown.markdown(
        text,
        extensions=["extra", "tables", "sane_lists", "toc"],
    )

    full = (
        '<!DOCTYPE html><html lang="ru"><head><meta charset="utf-8">'
        f"<style>{CSS}</style></head><body>{body_html}</body></html>"
    )
    return HTML(string=full).write_pdf()


def md_to_pdf(src_path, out_path):
    """Конвертирует один .md файл в .pdf. Возвращает путь к PDF."""
    with open(src_path, encoding="utf-8") as f:
        text = f.read()

    with open(out_path, "wb") as f:
        f.write(md_to_pdf_bytes(text))
    return out_path


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Конвертер Markdown -> PDF (таблицы, вложенные списки, callout-ы, кириллица)."
    )
    parser.add_argument("input", help="путь к входному .md файлу")
    parser.add_argument(
        "output",
        nargs="?",
        default=None,
        help="путь к выходному .pdf (по умолчанию - имя входного файла с .pdf)",
    )
    parser.add_argument(
        "-o", "--output", dest="output_opt", default=None,
        help="альтернативный способ задать выходной .pdf",
    )
    args = parser.parse_args(argv)

    src = args.input
    if not os.path.isfile(src):
        parser.error(f"входной файл не найден: {src}")

    out = args.output_opt or args.output
    if out is None:
        out = os.path.splitext(src)[0] + ".pdf"

    md_to_pdf(src, out)
    print(f"PDF записан: {out}")


if __name__ == "__main__":
    main()
