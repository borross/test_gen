#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════════╗
║        TEST GENERATOR v2.0 — Генератор итоговых тестов по ИБ        ║
╚══════════════════════════════════════════════════════════════════════╝

Поддерживаемые форматы входных файлов:
  1. Один файл — один тест
  2. Один файл — несколько тестов (разделены # заголовками)
  3. Несколько файлов (любое сочетание форматов 1 и 2)

Типы вопросов:
  > ℹ️ Базовый   — тестовый вопрос с вариантами ответа (базовый уровень)
  (без маркера)  — тестовый вопрос с вариантами ответа (обычный уровень)
  > ℹ️ Открытый  — вопрос без вариантов ответа, с развёрнутым текстовым ответом

Формат ответов в файле:
  ## ОТВЕТЫ — Название
  1-B, 2-C, 3-A, ...

  **Ответы на открытые вопросы:**
  16. Текст развёрнутого ответа...

ПРИМЕРЫ ЗАПУСКА:
  python test_gen.py -i all_tests.md -n 25 --shuffle
  python test_gen.py -i all_tests.md -n 15 --type all --shuffle
  python test_gen.py -i all_tests.md --validate
  python test_gen.py -i all_tests.md -n 20 --shuffle --variants 4 --seed 42
  python test_gen.py -i all_tests.md extra.md -w 70 30 -n 20 --shuffle
"""

from __future__ import annotations

import argparse
import random
import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


# ─────────────────────────────────────────────────────────────────────────────
# Модель данных
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Question:
    number: int
    text: str
    options: list[tuple[str, str]]  # [(letter, text), ...]; пусто для открытых
    correct_letter: str             # 'A'..'D'; пусто для открытых
    correct_text: str               # текстовый ответ для открытых вопросов
    is_basic: bool                  # маркер > ℹ️ Базовый
    is_open: bool                   # маркер > ℹ️ Открытый
    source: str                     # название теста-источника
    source_file: str                # имя файла-источника
    source_idx: int = 0             # порядковый номер теста в общей базе


@dataclass
class Issue:
    """Проблема, найденная при разборе входных файлов."""
    severity: str    # 'error' | 'warning'
    file: str
    test: str
    message: str

    def __str__(self) -> str:
        icon = '❌' if self.severity == 'error' else '⚠'
        return f"  {icon}  [{self.file} / '{self.test}'] {self.message}"


def report(issues: list[Issue], severity: str, file: str, test: str, msg: str) -> None:
    issue = Issue(severity, file, test, msg)
    issues.append(issue)
    print(issue, file=sys.stderr)


# ─────────────────────────────────────────────────────────────────────────────
# Регулярные выражения формата
# ─────────────────────────────────────────────────────────────────────────────

# Начало блока ответов. Ловит строго:
#   "## ОТВЕТЫ — Название"    "ОТВЕТЫ:"    "**Ответы: 1-B, ..."
#   "**Ответы на открытые вопросы:**"
# и НЕ ловит слова вроде «Ответственность» или вопросы, содержащие «ответ».
ANSWERS_START = re.compile(
    r'^(?:#{1,3}\s*)?\*{0,2}\s*ответы\s*(?:[:—–-]|на\s+открыт|\*{1,2}\s*$|$)',
    re.IGNORECASE,
)

# Подзаголовок блока текстовых ответов на открытые вопросы
OPEN_ANSWERS_START = re.compile(r'ответы\s+на\s+открыт', re.IGNORECASE)

# Пара «номер-буква» в ключе ответов: 1-B, 2–C, 3—D (любой тип тире)
ANSWER_PAIR = re.compile(r'(?<![\w-])(\d+)\s*[-–—]\s*([A-Da-dАВСДавсд])(?![\w-])')

# Заголовок вопроса: ### N. Текст
QUESTION_HEADING = re.compile(r'^#{2,4}\s+(\d+)[.):\s]\s*(.+)')

# Вариант ответа: латинские A-D и кириллические двойники А, В, С, Д
OPTION_LINE = re.compile(r'^([A-DАВСДa-dавсд])[.)]\s+(.+)')

# Начало текстового ответа на открытый вопрос: "N. Текст" / "**N.** Текст"
OPEN_ANSWER_ITEM = re.compile(r'^\*{0,2}(\d+)[.)]\*{0,2}\s+(.+)')

# Нормализация: кириллические двойники латинских букв
_CYR_TO_LAT = str.maketrans('АВСДавсд', 'ABCDabcd')


def norm_letter(letter: str) -> str:
    """Кириллица → латиница, строчная → заглавная."""
    return letter.translate(_CYR_TO_LAT).upper()


# ─────────────────────────────────────────────────────────────────────────────
# Парсер: разбивка файла на блоки тестов
# ─────────────────────────────────────────────────────────────────────────────

def extract_test_name(line: str) -> str:
    """Извлекает чистое название теста из строки заголовка (без аннотации в скобках)."""
    name = re.sub(r'^#+\s*', '', line.strip()).strip('* ')
    name = re.sub(r'\s*\(.*\)\s*$', '', name)
    return name.strip()


def is_test_heading(line: str) -> bool:
    """True, если строка — заголовок теста (#/##), но не заголовок блока ответов."""
    stripped = line.strip()
    if not re.match(r'^#{1,2}\s+\S', stripped):
        return False
    return not ANSWERS_START.match(stripped)


def split_into_blocks(lines: list[str]) -> list[tuple[str, list[str]]]:
    """Разбивает список строк файла на блоки тестов: [(name, block_lines), ...]."""
    starts: list[tuple[int, str]] = []
    for i, line in enumerate(lines):
        if is_test_heading(line):
            starts.append((i, extract_test_name(line)))

    if not starts:
        return [('Тест', lines)]

    blocks = []
    for idx, (start, name) in enumerate(starts):
        end = starts[idx + 1][0] if idx + 1 < len(starts) else len(lines)
        blocks.append((name, lines[start:end]))
    return blocks


# ─────────────────────────────────────────────────────────────────────────────
# Парсер: ключ буквенных ответов
# ─────────────────────────────────────────────────────────────────────────────

def parse_letter_answers(lines: list[str], test_name: str, source_file: str,
                         issues: list[Issue]) -> dict[int, str]:
    """
    Собирает пары «номер-буква» из блока ответов.
    Сканирование начинается ТОЛЬКО после явного заголовка блока ответов
    и прекращается на подзаголовке текстовых ответов на открытые вопросы —
    это защищает от ложных совпадений вида «3-D модель» в тексте вопросов
    и в развёрнутых ответах.
    """
    answers: dict[int, str] = {}
    in_answers = False
    for line in lines:
        stripped = line.strip()
        if OPEN_ANSWERS_START.search(stripped):
            break  # дальше идут текстовые ответы — буквенные пары там не ищем
        if ANSWERS_START.match(stripped):
            in_answers = True
        if in_answers:
            for m in ANSWER_PAIR.finditer(line):
                num, letter = int(m.group(1)), norm_letter(m.group(2))
                if num in answers and answers[num] != letter:
                    report(issues, 'error', source_file, test_name,
                           f"Ключ ответов: для вопроса {num} указаны разные буквы "
                           f"({answers[num]} и {letter}) — оставляем первую")
                    continue
                answers[num] = letter
    return answers


# ─────────────────────────────────────────────────────────────────────────────
# Парсер: текстовые ответы на открытые вопросы
# ─────────────────────────────────────────────────────────────────────────────

def parse_open_answers(lines: list[str]) -> dict[int, str]:
    """
    Собирает развёрнутые ответы из блока «Ответы на открытые вопросы:».
    Накопление многострочного ответа прерывается на разделителе «---»,
    заголовках и жирных подзаголовках — мусор в ответ не попадает.
    """
    answers: dict[int, str] = {}
    in_block = False
    cur_num: Optional[int] = None
    cur_lines: list[str] = []

    def flush() -> None:
        nonlocal cur_num, cur_lines
        if cur_num is not None and cur_lines:
            answers[cur_num] = ' '.join(cur_lines).strip()
        cur_num, cur_lines = None, []

    for line in lines:
        stripped = line.strip()
        if OPEN_ANSWERS_START.search(stripped):
            in_block = True
            continue
        if not in_block:
            continue

        # Стоп-маркеры: разделитель, заголовок, жирный подзаголовок
        if stripped == '---' or stripped.startswith('#') or \
                (stripped.startswith('**') and not OPEN_ANSWER_ITEM.match(stripped)):
            flush()
            continue

        m = OPEN_ANSWER_ITEM.match(stripped)
        if m:
            flush()
            cur_num = int(m.group(1))
            cur_lines = [m.group(2).strip()]
        elif cur_num is not None and stripped:
            cur_lines.append(stripped)

    flush()
    return answers


# ─────────────────────────────────────────────────────────────────────────────
# Парсер: вопросы (конечный автомат)
# ─────────────────────────────────────────────────────────────────────────────

def parse_questions(lines: list[str], test_name: str, source_file: str,
                    letter_answers: dict[int, str], text_answers: dict[int, str],
                    issues: list[Issue]) -> list[Question]:
    """Разбирает вопросы одного теста. Все проблемы фиксируются в issues."""
    questions: list[Question] = []
    seen_numbers: set[int] = set()

    next_is_basic = next_is_open = False
    cur_num: Optional[int] = None
    cur_text = ''
    cur_options: list[tuple[str, str]] = []
    cur_basic = cur_open = False

    def reset() -> None:
        nonlocal cur_num, cur_text, cur_options, cur_basic, cur_open
        cur_num, cur_text, cur_options = None, '', []
        cur_basic = cur_open = False

    def flush() -> None:
        if cur_num is None:
            reset()
            return

        if cur_num in seen_numbers:
            report(issues, 'error', source_file, test_name,
                   f"Вопрос {cur_num}: дублирующийся номер — пропускаем повтор")
            reset()
            return

        if cur_open:
            ans_text = text_answers.get(cur_num, '')
            if not ans_text:
                report(issues, 'warning', source_file, test_name,
                       f"Открытый вопрос {cur_num}: текстовый ответ не найден — "
                       f"сохраняем без ответа")
            questions.append(Question(
                number=cur_num, text=cur_text, options=[],
                correct_letter='', correct_text=ans_text,
                is_basic=False, is_open=True,
                source=test_name, source_file=source_file,
            ))
            seen_numbers.add(cur_num)
            reset()
            return

        # Тестовый вопрос
        if not cur_options:
            report(issues, 'error', source_file, test_name,
                   f"Вопрос {cur_num}: не найдено ни одного варианта ответа — "
                   f"пропускаем (проверьте формат «A. текст»)")
            reset()
            return
        if cur_num not in letter_answers:
            report(issues, 'error', source_file, test_name,
                   f"Вопрос {cur_num}: ответ не найден в ключе — пропускаем")
            reset()
            return

        correct = letter_answers[cur_num]
        option_letters = {l for l, _ in cur_options}
        if correct not in option_letters:
            report(issues, 'error', source_file, test_name,
                   f"Вопрос {cur_num}: в ключе указан ответ «{correct}», "
                   f"но такого варианта нет (есть: {', '.join(sorted(option_letters))}) — "
                   f"пропускаем")
            reset()
            return

        questions.append(Question(
            number=cur_num, text=cur_text, options=list(cur_options),
            correct_letter=correct, correct_text='',
            is_basic=cur_basic, is_open=False,
            source=test_name, source_file=source_file,
        ))
        seen_numbers.add(cur_num)
        reset()

    for line in lines:
        stripped = line.strip()

        # Стоп: начался блок ответов
        if ANSWERS_START.match(stripped):
            flush()
            break

        # Маркеры типа вопроса (только в цитатах «> ...»)
        if stripped.startswith('>'):
            if re.search(r'базовый', stripped, re.IGNORECASE):
                next_is_basic, next_is_open = True, False
                continue
            if re.search(r'открыт', stripped, re.IGNORECASE):
                next_is_open, next_is_basic = True, False
                continue

        q_match = QUESTION_HEADING.match(stripped)
        if q_match:
            flush()
            cur_num = int(q_match.group(1))
            cur_text = q_match.group(2).strip().rstrip('*').strip()
            cur_basic, cur_open = next_is_basic, next_is_open
            next_is_basic = next_is_open = False
            continue

        if cur_num is not None and not cur_open:
            opt_match = OPTION_LINE.match(stripped)
            if opt_match:
                cur_options.append((
                    norm_letter(opt_match.group(1)),
                    opt_match.group(2).strip().rstrip('* ').strip(),
                ))

    flush()

    # Ответы, для которых не нашлось вопроса
    orphan = sorted(set(letter_answers) - seen_numbers)
    if orphan:
        report(issues, 'warning', source_file, test_name,
               f"В ключе есть ответы без вопросов: {', '.join(map(str, orphan))}")

    return questions


def parse_block(lines: list[str], test_name: str, source_file: str,
                issues: list[Issue]) -> list[Question]:
    """Полный разбор одного блока теста."""
    letter_answers = parse_letter_answers(lines, test_name, source_file, issues)
    text_answers = parse_open_answers(lines)
    return parse_questions(lines, test_name, source_file,
                           letter_answers, text_answers, issues)


def parse_file(filepath: str, issues: list[Issue]) -> list[tuple[str, list[Question]]]:
    """Читает файл, возвращает список тестов: [(name, questions), ...]."""
    path = Path(filepath)
    if not path.exists():
        raise FileNotFoundError(f"Файл не найден: {filepath}")

    # utf-8-sig: корректно обрабатывает BOM из Windows-редакторов
    content = path.read_text(encoding='utf-8-sig')
    lines = content.split('\n')

    result = []
    for name, block_lines in split_into_blocks(lines):
        questions = parse_block(block_lines, name, path.name, issues)
        if questions:
            result.append((name, questions))
        else:
            report(issues, 'warning', path.name, name,
                   "Вопросы не найдены — блок пропускаем")
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Логика выбора вопросов
# ─────────────────────────────────────────────────────────────────────────────

def filter_by_type(questions: list[Question], q_type: str) -> list[Question]:
    """
    basic  — только базовые тестовые
    normal — только обычные тестовые
    mix    — все тестовые (basic + normal), без открытых  ← по умолчанию
    open   — только открытые
    all    — абсолютно все
    """
    if q_type == 'basic':
        return [q for q in questions if not q.is_open and q.is_basic]
    if q_type == 'normal':
        return [q for q in questions if not q.is_open and not q.is_basic]
    if q_type == 'mix':
        return [q for q in questions if not q.is_open]
    if q_type == 'open':
        return [q for q in questions if q.is_open]
    return list(questions)


def distribute(total: int, weights: list[float]) -> list[int]:
    """Распределяет total единиц пропорционально weights. Сумма результата == total."""
    s = sum(weights)
    if s <= 0:
        raise ValueError("Сумма весов должна быть положительной")
    floats = [w / s * total for w in weights]
    counts = [int(f) for f in floats]
    remainders = sorted(range(len(weights)), key=lambda i: -(floats[i] - counts[i]))
    for i in remainders[:total - sum(counts)]:
        counts[i] += 1
    return counts


def redistribute_shortfall(selections: list[list], shortfall: int) -> int:
    """
    Пропорционально добирает недостающие вопросы из пулов с остаточной
    ёмкостью (вместо систематического добора из первых по порядку).
    Возвращает нераспределённый остаток (0, если добрали всё).
    """
    while shortfall > 0:
        caps = [len(sel[1]) - sel[2] for sel in selections]
        total_cap = sum(caps)
        if total_cap <= 0:
            break
        take = min(shortfall, total_cap)
        extra = distribute(take, [max(c, 0) for c in caps])
        for i, add in enumerate(extra):
            add = min(add, caps[i])
            if add > 0:
                selections[i][2] += add
                shortfall -= add
                print(f"  ℹ  Добираем {add} вопросов из '{selections[i][0]}'",
                      file=sys.stderr)
    return shortfall


def select_questions(all_sources: list[tuple[str, list[Question]]],
                     file_weights: list[float], file_test_counts: list[int],
                     total: int, q_type: str, rng: random.Random) -> list[Question]:
    """Выбирает вопросы с учётом весов файлов (вес файла делится между его тестами)."""
    test_weights: list[float] = []
    for w, n in zip(file_weights, file_test_counts):
        test_weights.extend([w / n if n > 0 else 0.0] * n)

    pools = [(name, filter_by_type(qs, q_type)) for name, qs in all_sources]
    total_available = sum(len(p) for _, p in pools)
    if total_available == 0:
        raise ValueError(f"В базе нет вопросов типа '{q_type}'")

    if total > total_available:
        print(f"\n  ⚠  Запрошено {total} вопросов, доступно только {total_available}. "
              f"Итоговый тест будет содержать {total_available}.", file=sys.stderr)
        total = total_available

    target_counts = distribute(total, test_weights)
    selections: list[list] = []  # [name, pool, cnt]
    shortfall = 0
    for (name, pool), cnt in zip(pools, target_counts):
        take = min(cnt, len(pool))
        if take < cnt:
            print(f"  ⚠  '{name}': запрошено {cnt}, доступно {len(pool)} → берём {take}",
                  file=sys.stderr)
            shortfall += cnt - take
        selections.append([name, pool, take])

    redistribute_shortfall(selections, shortfall)

    result: list[Question] = []
    for name, pool, cnt in selections:
        if cnt > 0:
            result.extend(rng.sample(pool, cnt))

    rng.shuffle(result)
    return result


def select_questions_split(all_sources: list[tuple[str, list[Question]]],
                           file_weights: list[float], file_test_counts: list[int],
                           n_mc: int, n_open: int,
                           rng: random.Random) -> list[Question]:
    """
    Ручной состав теста: ровно n_mc тестовых вопросов (базовые + обычные)
    и ровно n_open открытых. Веса файлов применяются к каждой части отдельно.
    """
    parts: list[Question] = []
    if n_mc > 0:
        parts.extend(select_questions(all_sources, file_weights, file_test_counts,
                                      n_mc, 'mix', rng))
    if n_open > 0:
        parts.extend(select_questions(all_sources, file_weights, file_test_counts,
                                      n_open, 'open', rng))
    rng.shuffle(parts)
    return parts


# ─────────────────────────────────────────────────────────────────────────────
# Перемешивание вариантов ответов
# ─────────────────────────────────────────────────────────────────────────────

def shuffle_options(q: Question, rng: random.Random) -> tuple[list[tuple[str, str]], str]:
    """Перемешивает тексты вариантов, пересчитывает букву правильного ответа."""
    correct_text = next((t for l, t in q.options if l == q.correct_letter), None)
    if correct_text is None:  # парсер такого не пропустит, но подстрахуемся
        return q.options, q.correct_letter
    texts = [t for _, t in q.options]
    rng.shuffle(texts)
    letters = 'ABCDEFGHIJ'
    new_options = [(letters[i], t) for i, t in enumerate(texts)]
    new_correct = next(l for l, t in new_options if t == correct_text)
    return new_options, new_correct


# ─────────────────────────────────────────────────────────────────────────────
# Форматирование выходного markdown
# ─────────────────────────────────────────────────────────────────────────────

def format_test(questions: list[Question], title: str, shuffle_ans: bool,
                rng: random.Random, show_source: bool = False) -> str:
    has_mc = any(not q.is_open for q in questions)
    has_open = any(q.is_open for q in questions)

    lines = [f"# {title}", ""]
    if has_mc and has_open:
        lines.append("**Формат:** тестовые вопросы — выберите один правильный вариант "
                     "ответа; открытые вопросы — напишите развёрнутый ответ.")
    elif has_open:
        lines.append("**Формат:** напишите развёрнутый ответ на каждый вопрос.")
    else:
        lines.append("**Формат:** в каждом вопросе один правильный ответ.")
    lines += ["", "---", ""]

    mc_key: list[tuple[int, str]] = []
    open_key: list[tuple[int, str]] = []

    for new_num, q in enumerate(questions, start=1):
        if q.is_open:
            lines.append(">✏️ Открытый")
        elif q.is_basic:
            lines.append(">ℹ️ Базовый")
        if show_source:
            lines.append(f"*Источник: {q.source} — вопрос {q.number}*")
        lines.append(f"### {new_num}. {q.text}")

        if q.is_open:
            lines += ["", "*Ответ:* _______________________________________________", ""]
            open_key.append((new_num, q.correct_text))
        else:
            opts, correct = (shuffle_options(q, rng) if shuffle_ans
                             else (q.options, q.correct_letter))
            mc_key.append((new_num, correct))
            lines += [f"{letter}. {text}  " for letter, text in opts]

        lines += ["", "---", ""]

    lines += [f"## ОТВЕТЫ — {title}", ""]
    if mc_key:
        lines += ["**Тестовые вопросы:**",
                  ", ".join(f"{n}-{l}" for n, l in mc_key), ""]
    if open_key:
        lines += ["**Открытые вопросы:**", ""]
        for n, text in open_key:
            lines.append(f"**{n}.** {text}" if text
                         else f"**{n}.** *(ответ не указан в источнике)*")
            lines.append("")

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="test_gen.py",
        description="Генератор итоговых тестов из markdown-файлов (v2.0)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
типы вопросов (--type):
  mix    — все тестовые вопросы с вариантами ответа (по умолчанию)
  basic  — только базовые тестовые вопросы
  normal — только обычные тестовые вопросы
  open   — только открытые вопросы (без вариантов ответа)
  all    — абсолютно все вопросы (тестовые + открытые)

примеры:
  python test_gen.py -i all_tests.md -n 25 --shuffle
  python test_gen.py -i all_tests.md --validate
  python test_gen.py -i all_tests.md -n 20 --shuffle --variants 4 --seed 42
  python test_gen.py -i all_tests.md extra.md -w 70 30 -n 20 --shuffle
  python test_gen.py -i all_tests.md -n 15 --type all --group-by-source
        """,
    )
    p.add_argument('-i', '--input', nargs='+', required=True, metavar='FILE',
                   help='Markdown-файлы с тестами')
    p.add_argument('-w', '--weights', nargs='+', type=float, metavar='W',
                   help='Веса для каждого файла (сумма не обязана = 100). '
                        'По умолчанию — равные.')
    p.add_argument('-n', '--total', type=int, metavar='N',
                   help='Количество вопросов в тесте (обязателен без --info/--validate, '
                        'если не задан --split)')
    p.add_argument('--split', nargs=2, type=int, metavar=('MC', 'OPEN'),
                   help='Ручной состав: MC тестовых вопросов + OPEN открытых '
                        '(например, --split 15 5 → тест из 20 вопросов). '
                        'Заменяет -n и --type')
    p.add_argument('--type', choices=['basic', 'normal', 'mix', 'open', 'all'],
                   default='mix', dest='q_type', metavar='TYPE',
                   help='Тип вопросов: basic, normal, mix (по умол.), open, all')
    p.add_argument('--shuffle', action='store_true',
                   help='Перемешать варианты ответов (только тестовые вопросы)')
    p.add_argument('--no-shuffle-questions', action='store_true',
                   dest='no_shuffle_questions',
                   help='Не перемешивать порядок вопросов — сохранить порядок источников')
    p.add_argument('--group-by-source', action='store_true', dest='group_by_source',
                   help='Сгруппировать вопросы по тестам-источникам')
    p.add_argument('--variants', type=int, default=1, metavar='N',
                   help='Сгенерировать N вариантов теста (файлы _v1, _v2, ...)')
    p.add_argument('-o', '--output', default='generated_test.md', metavar='FILE',
                   help='Имя выходного файла (по умолчанию: generated_test.md)')
    p.add_argument('--force', action='store_true',
                   help='Перезаписать выходной файл, если он уже существует')
    p.add_argument('--title', default='Итоговый тест по ИБ', metavar='TITLE',
                   help='Название итогового теста')
    p.add_argument('--seed', type=int, default=None, metavar='N',
                   help='Seed генератора случайных чисел (для воспроизводимости)')
    p.add_argument('--pdf', action='store_true',
                   help='Дополнительно сохранить PDF-версию каждого варианта '
                        '(требуются пакеты markdown и weasyprint, файл md2pdf.py)')
    p.add_argument('--show-source', action='store_true', dest='show_source',
                   help='Показывать источник под каждым вопросом')
    p.add_argument('--info', action='store_true',
                   help='Показать статистику по файлам; тест не генерируется')
    p.add_argument('--validate', action='store_true',
                   help='Проверить базу вопросов и вывести отчёт о проблемах; '
                        'тест не генерируется')
    return p


def dedupe_inputs(inputs: list[str]) -> list[str]:
    """Убирает дубли путей (один файл, указанный дважды, дал бы дубли вопросов)."""
    seen: set = set()
    result = []
    for f in inputs:
        key = Path(f).resolve()
        if key in seen:
            print(f"  ⚠  Файл {f} указан несколько раз — учитываем один раз",
                  file=sys.stderr)
            continue
        seen.add(key)
        result.append(f)
    return result


def output_path_for_variant(base: str, variant: int, total_variants: int) -> Path:
    if total_variants == 1:
        return Path(base)
    p = Path(base)
    return p.with_name(f"{p.stem}_v{variant}{p.suffix or '.md'}")


def order_questions(selected: list[Question], args) -> list[Question]:
    """Применяет --group-by-source / --no-shuffle-questions к выбранным вопросам."""
    if args.no_shuffle_questions:
        return sorted(selected, key=lambda q: (q.source_idx, q.number))
    if args.group_by_source:
        return sorted(selected, key=lambda q: q.source_idx)  # стабильная сортировка
    return selected


# ─────────────────────────────────────────────────────────────────────────────
# main
# ─────────────────────────────────────────────────────────────────────────────

def print_stats(all_sources, n_files: int) -> None:
    total_all = sum(len(qs) for _, qs in all_sources)
    total_b = sum(1 for _, qs in all_sources for q in qs if q.is_basic)
    total_o = sum(1 for _, qs in all_sources for q in qs if q.is_open)
    total_n = total_all - total_b - total_o
    print(f"\n📊 Итоговая статистика базы:")
    print(f"   Файлов:             {n_files}")
    print(f"   Тестов:             {len(all_sources)}")
    print(f"   Всего вопросов:     {total_all}")
    print(f"   ├─ Базовых (тест.): {total_b}")
    print(f"   ├─ Обычных (тест.): {total_n}")
    print(f"   └─ Открытых:        {total_o}")


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    utility_mode = args.info or args.validate
    if args.split is not None:
        if args.total is not None:
            parser.error("--split и -n/--total взаимоисключающие: "
                         "--split сам определяет общее количество вопросов")
        mc, op = args.split
        if mc < 0 or op < 0:
            parser.error("--split: значения не могут быть отрицательными")
        if mc + op == 0:
            parser.error("--split: сумма тестовых и открытых должна быть больше нуля")
    if not utility_mode and args.total is None and args.split is None:
        parser.error("укажите -n / --total или --split, либо --info / --validate")
    if args.total is not None and args.total <= 0:
        parser.error("--total должен быть положительным числом")
    if args.variants < 1:
        parser.error("--variants должен быть >= 1")

    args.input = dedupe_inputs(args.input)
    if args.weights is not None:
        if len(args.weights) != len(args.input):
            parser.error(f"количество весов ({len(args.weights)}) должно совпадать "
                         f"с количеством уникальных файлов ({len(args.input)})")
        if any(w < 0 for w in args.weights):
            parser.error("веса не могут быть отрицательными")
        if sum(args.weights) <= 0:
            parser.error("сумма весов должна быть положительной")

    # ── Загрузка ─────────────────────────────────────────────────────────────
    print("\n📂 Загрузка файлов...")
    issues: list[Issue] = []
    all_sources: list[tuple[str, list[Question]]] = []
    file_test_counts: list[int] = []

    for filepath in args.input:
        try:
            tests = parse_file(filepath, issues)
        except FileNotFoundError as e:
            print(f"  ❌ {e}", file=sys.stderr)
            sys.exit(1)

        file_test_counts.append(len(tests))
        if not tests:
            print(f"  ❌ {Path(filepath).name}: тесты не найдены", file=sys.stderr)
            sys.exit(1)

        total_q = sum(len(qs) for _, qs in tests)
        total_b = sum(1 for _, qs in tests for q in qs if q.is_basic)
        total_o = sum(1 for _, qs in tests for q in qs if q.is_open)
        total_n = total_q - total_b - total_o
        tag = f"({len(tests)} тестов)" if len(tests) > 1 else "(1 тест)"
        print(f"  ✅ {Path(filepath).name}  {tag}  →  {total_q} вопросов "
              f"({total_b} базовых + {total_n} обычных + {total_o} открытых)")
        if len(tests) > 1:
            for name, qs in tests:
                b = sum(1 for q in qs if q.is_basic)
                o = sum(1 for q in qs if q.is_open)
                print(f"       • '{name}': {len(qs)} вопр. "
                      f"({b} баз. + {len(qs) - b - o} обыч. + {o} откр.)")
        all_sources.extend(tests)

    for idx, (_, qs) in enumerate(all_sources):
        for q in qs:
            q.source_idx = idx

    # ── Режим --validate ─────────────────────────────────────────────────────
    if args.validate:
        print_stats(all_sources, len(args.input))
        errors = [i for i in issues if i.severity == 'error']
        warnings = [i for i in issues if i.severity == 'warning']
        print(f"\n🔍 Результат проверки: "
              f"{len(errors)} ошибок, {len(warnings)} предупреждений")
        if errors:
            print("\nОшибки (вопросы исключены из базы):")
            for i in errors:
                print(str(i))
        if warnings:
            print("\nПредупреждения:")
            for i in warnings:
                print(str(i))
        if not issues:
            print("   ✅ Проблем не найдено — база готова к генерации")
        sys.exit(1 if errors else 0)

    # ── Режим --info ─────────────────────────────────────────────────────────
    if args.info:
        print_stats(all_sources, len(args.input))
        return

    # ── Генерация ────────────────────────────────────────────────────────────
    file_weights = args.weights if args.weights else [1.0] * len(args.input)
    base_seed = args.seed if args.seed is not None else random.randrange(10**9)

    print(f"\n⚙️  Параметры генерации:")
    if args.split is not None:
        print(f"   Состав варианта     : {args.split[0]} тестовых + "
              f"{args.split[1]} открытых = {sum(args.split)} вопросов")
    else:
        print(f"   Вопросов на вариант: {args.total}")
        print(f"   Тип                 : {args.q_type}")
    print(f"   Вариантов           : {args.variants}")
    print(f"   Перемешать ответы   : {'да' if args.shuffle else 'нет'}")
    print(f"   Порядок вопросов    : "
          f"{'как в источниках' if args.no_shuffle_questions else ('по темам' if args.group_by_source else 'случайный')}")
    print(f"   Веса файлов         : "
          f"{ {Path(f).name: w for f, w in zip(args.input, file_weights)} }")
    print(f"   Seed                : {base_seed}"
          f"{'' if args.seed is not None else ' (сгенерирован автоматически)'}")

    for v in range(1, args.variants + 1):
        rng = random.Random(f"{base_seed}:{v}")
        out_path = output_path_for_variant(args.output, v, args.variants)

        if out_path.exists() and not args.force:
            print(f"\n❌ Файл {out_path} уже существует. "
                  f"Используйте --force для перезаписи или задайте другое имя через -o.",
                  file=sys.stderr)
            sys.exit(1)

        print(f"\n🎲 Вариант {v}: выбор вопросов...")
        try:
            if args.split is not None:
                selected = select_questions_split(
                    all_sources, file_weights, file_test_counts,
                    args.split[0], args.split[1], rng)
            else:
                selected = select_questions(all_sources, file_weights, file_test_counts,
                                            args.total, args.q_type, rng)
        except ValueError as e:
            print(f"\n❌ {e}", file=sys.stderr)
            sys.exit(1)
        if not selected:
            print("❌ Не удалось выбрать ни одного вопроса", file=sys.stderr)
            sys.exit(1)

        selected = order_questions(selected, args)
        title = args.title + (f" — Вариант {v}" if args.variants > 1 else "")
        out_path.write_text(format_test(selected, title, args.shuffle, rng,
                                        args.show_source), encoding='utf-8')

        if args.pdf:
            try:
                from md2pdf import md_to_pdf
                pdf_path = out_path.with_suffix('.pdf')
                md_to_pdf(str(out_path), str(pdf_path))
                print(f"   📄 PDF сохранён: {pdf_path.resolve()}")
            except ImportError as e:
                print(f"  ⚠  PDF пропущен — не хватает зависимостей: {e}.\n"
                      f"     Установите: pip install markdown weasyprint "
                      f"(и держите md2pdf.py рядом со скриптом)", file=sys.stderr)
            except Exception as e:  # noqa: BLE001
                print(f"  ⚠  PDF пропущен — ошибка конвертации: {e}", file=sys.stderr)

        cnt_b = sum(1 for q in selected if q.is_basic)
        cnt_o = sum(1 for q in selected if q.is_open)
        print(f"   ✅ Сохранён: {out_path.resolve()}")
        print(f"   Состав ({len(selected)} вопросов): "
              f"{cnt_b} баз. + {len(selected) - cnt_b - cnt_o} обыч. + {cnt_o} откр.")
        for src, cnt in sorted(Counter(q.source for q in selected).items(),
                               key=lambda x: -x[1]):
            print(f"   {cnt:3d} ({cnt / len(selected) * 100:4.1f}%)  ←  {src}")


if __name__ == '__main__':
    main()
