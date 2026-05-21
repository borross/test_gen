
"""
╔══════════════════════════════════════════════════════════════════════╗
║              TEST GENERATOR — Генератор итоговых тестов             ║
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
  17. Текст развёрнутого ответа...

ПРИМЕРЫ ЗАПУСКА:
  python test_gen.py -i all_tests.md -n 25 --shuffle
  python test_gen.py -i all_tests.md -n 15 --type all --shuffle
  python test_gen.py -i all_tests.md -n 10 --type open
  python test_gen.py -i all_tests.md -n 10 --type basic --shuffle
  python test_gen.py -i all_tests.md --info
  python test_gen.py -i all_tests.md extra.md -w 70 30 -n 20 --shuffle
  python test_gen.py -i all_tests.md -n 20 --shuffle --seed 42
"""

import argparse
import random
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path


# ─────────────────────────────────────────────────────────────────────────────
# Модель данных
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Question:
    number: int
    text: str
    options: list        # [(letter, text), ...] — пустой список для открытых вопросов
    correct_letter: str  # 'A'/'B'/'C'/'D' — пустая строка для открытых вопросов
    correct_text: str    # развёрнутый текстовый ответ для открытых вопросов
    is_basic: bool       # True — базовый уровень (маркер > ℹ️ Базовый)
    is_open: bool        # True — открытый вопрос (маркер > ℹ️ Открытый)
    source: str          # название теста-источника
    source_file: str     # имя файла-источника


# ─────────────────────────────────────────────────────────────────────────────
# Парсер: разбивка файла на блоки тестов
# ─────────────────────────────────────────────────────────────────────────────

def extract_test_name(line):
    """
    Извлекает чистое название теста из строки заголовка.

    Примеры:
      # 15 Безопасность приложений (Всего вопросов: 21, ...)  → '15 Безопасность приложений'
      # 15 Безопасность приложений (15 вопросов)              → '15 Безопасность приложений'
      ## Тест 12. Риско-ориентированный подход                 → 'Тест 12. Риско-ориентированный подход'
    """
    name = re.sub(r'^#+\s*', '', line.strip()).strip('* ')
    # Убираем любую trailing аннотацию в скобках: (Всего вопросов: ...), (15 вопросов), (15 вопр.)
    name = re.sub(r'\s*\(.*\)\s*$', '', name)
    return name.strip()


def is_test_heading(line):
    """
    True если строка — заголовок теста (#/##),
    но НЕ заголовок блока ответов (## ОТВЕТЫ ...).
    """
    stripped = line.strip()
    if not re.match(r'^#{1,2}\s+\S', stripped):
        return False
    heading_text = re.sub(r'^#+\s*', '', stripped).strip('* ')
    return not re.search(r'ответ', heading_text, re.I)


def split_into_blocks(lines):
    """
    Разбивает список строк на блоки по тестам.
    Возвращает [(name, block_lines), ...].
    """
    starts = []
    for i, line in enumerate(lines):
        if is_test_heading(line):
            name = extract_test_name(line)
            starts.append((i, name))

    if not starts:
        return [('Тест', lines)]

    blocks = []
    for idx, (start, name) in enumerate(starts):
        end = starts[idx + 1][0] if idx + 1 < len(starts) else len(lines)
        blocks.append((name, lines[start:end]))

    return blocks


# ─────────────────────────────────────────────────────────────────────────────
# Парсер: разбор одного блока теста
# ─────────────────────────────────────────────────────────────────────────────

# Нормализация: кириллические двойники латинских букв (С→C, А→A, В→B, Д→D)
_CYR_TO_LAT = str.maketrans('АВСДавсд', 'ABCDabcd')

def _norm_letter(letter):
    """Нормализует букву ответа: кириллица → латиница, строчная → заглавная."""
    return letter.translate(_CYR_TO_LAT).upper()


def parse_block(lines, test_name, source_file):
    """
    Разбирает строки одного теста.
    Возвращает список Question (тестовые + открытые).
    """

    # ── 1. Блок буквенных ответов (1-B, 2-C, ...) ────────────────────────────
    letter_answers = {}
    in_answers = False
    for line in lines:
        if re.search(r'ответ', line, re.I):
            in_answers = True
        if in_answers:
            for m in re.finditer(r'(\d+)-([A-Da-dАВСДавсд])', line):
                letter_answers[int(m.group(1))] = _norm_letter(m.group(2))

    # ── 2. Блок текстовых ответов на открытые вопросы ─────────────────────────
    # Формат: "**Ответы на открытые вопросы:**" затем "N. Текст ответа..."
    text_answers = {}
    in_open_ans = False
    cur_open_num = None
    cur_open_lines = []

    def flush_open_answer():
        if cur_open_num is not None and cur_open_lines:
            text_answers[cur_open_num] = ' '.join(cur_open_lines).strip()

    for line in lines:
        stripped = line.strip().rstrip('*').strip()
        # Маркер начала блока открытых ответов
        if re.search(r'открыт', stripped, re.I) and re.search(r'ответ', stripped, re.I):
            in_open_ans = True
            continue
        if in_open_ans:
            m = re.match(r'^(\d+)[.)]\s+(.+)', stripped)
            if m:
                flush_open_answer()
                cur_open_num = int(m.group(1))
                cur_open_lines = [m.group(2).strip()]
            elif cur_open_num is not None and stripped:
                cur_open_lines.append(stripped)

    flush_open_answer()

    # ── 3. Парсинг вопросов: конечный автомат ─────────────────────────────────
    questions = []
    next_is_basic = False
    next_is_open  = False
    cur_num     = None
    cur_text    = None
    cur_options = []
    cur_basic   = False
    cur_open    = False

    def flush():
        nonlocal cur_num, cur_text, cur_options, cur_basic, cur_open
        if cur_num is None:
            cur_num = cur_text = None; cur_options = []; cur_basic = cur_open = False
            return

        if cur_open:
            # Открытый вопрос — варианты не нужны, ищем текстовый ответ
            ans_text = text_answers.get(cur_num, '')
            if not ans_text:
                print(f"  ⚠  [{source_file} / '{test_name}'] "
                      f"Открытый вопрос {cur_num}: текстовый ответ не найден — сохраняем без ответа",
                      file=sys.stderr)
            questions.append(Question(
                number=cur_num, text=cur_text, options=[],
                correct_letter='', correct_text=ans_text,
                is_basic=False, is_open=True,
                source=test_name, source_file=source_file,
            ))
        else:
            # Тестовый вопрос — нужны варианты и буквенный ответ
            if not cur_options:
                cur_num = cur_text = None; cur_options = []; cur_basic = cur_open = False
                return
            if cur_num not in letter_answers:
                print(f"  ⚠  [{source_file} / '{test_name}'] "
                      f"Вопрос {cur_num}: ответ не найден — пропускаем", file=sys.stderr)
            else:
                questions.append(Question(
                    number=cur_num, text=cur_text, options=list(cur_options),
                    correct_letter=letter_answers[cur_num], correct_text='',
                    is_basic=cur_basic, is_open=False,
                    source=test_name, source_file=source_file,
                ))

        cur_num = cur_text = None; cur_options = []; cur_basic = cur_open = False

    for line in lines:
        stripped = line.strip()

        # Стоп: начался блок ответов
        if re.match(r'^#{1,3}.*ответ', stripped, re.I):
            flush(); break

        # Маркер > ℹ️ Базовый
        if re.search(r'базовый', stripped, re.I) and stripped.startswith('>'):
            next_is_basic = True
            next_is_open  = False
            continue

        # Маркер > ℹ️ Открытый
        if re.search(r'открыт', stripped, re.I) and stripped.startswith('>'):
            next_is_open  = True
            next_is_basic = False
            continue

        # Заголовок вопроса: ### N. / ## N.
        q_match = re.match(r'^#{2,4}\s+(\d+)[.):\s]\s*(.+)', stripped)
        if q_match:
            flush()
            cur_num   = int(q_match.group(1))
            cur_text  = q_match.group(2).strip().rstrip('*').strip()
            cur_basic = next_is_basic
            cur_open  = next_is_open
            next_is_basic = next_is_open = False
            continue

        # Вариант ответа A. / B. / C. / D.
        if cur_num is not None and not cur_open:
            opt_match = re.match(r'^([A-D])[.)]\s+(.+)', stripped)
            if opt_match:
                cur_options.append((
                    opt_match.group(1),
                    opt_match.group(2).strip().rstrip('* ')
                ))

    flush()
    return questions


# ─────────────────────────────────────────────────────────────────────────────
# Основная точка входа парсера
# ─────────────────────────────────────────────────────────────────────────────

def parse_file(filepath):
    """
    Читает файл, возвращает список тестов: [(name, questions), ...].
    """
    path = Path(filepath)
    if not path.exists():
        raise FileNotFoundError(f"Файл не найден: {filepath}")

    content = path.read_text(encoding='utf-8')
    lines   = content.split('\n')

    blocks = split_into_blocks(lines)
    result = []
    for name, block_lines in blocks:
        questions = parse_block(block_lines, name, path.name)
        if questions:
            result.append((name, questions))
        else:
            print(f"  ⚠  [{path.name}] Блок '{name}': вопросы не найдены — пропускаем",
                  file=sys.stderr)
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Логика выбора вопросов
# ─────────────────────────────────────────────────────────────────────────────

def filter_by_type(questions, q_type):
    """
    Фильтрует вопросы по типу:
      basic  — только базовые тестовые (с вариантами ответа)
      normal — только обычные тестовые (с вариантами ответа)
      mix    — все тестовые (basic + normal), без открытых  ← по умолчанию
      open   — только открытые вопросы
      all    — абсолютно все вопросы
    """
    if q_type == 'basic':
        return [q for q in questions if not q.is_open and q.is_basic]
    if q_type == 'normal':
        return [q for q in questions if not q.is_open and not q.is_basic]
    if q_type == 'mix':
        return [q for q in questions if not q.is_open]
    if q_type == 'open':
        return [q for q in questions if q.is_open]
    # all
    return list(questions)


def distribute(total, weights):
    """Распределяет total единиц пропорционально weights. Сумма результата == total."""
    s = sum(weights)
    floats = [w / s * total for w in weights]
    counts = [int(f) for f in floats]
    remainders = sorted(range(len(weights)), key=lambda i: -(floats[i] - counts[i]))
    diff = total - sum(counts)
    for i in remainders[:diff]:
        counts[i] += 1
    return counts


def select_questions(all_sources, file_weights, file_test_counts, total, q_type, rng):
    """
    Выбирает вопросы с учётом весов файлов.
    Если файл содержит N тестов — его вес делится поровну между ними.
    """
    test_weights = []
    for w, n in zip(file_weights, file_test_counts):
        per_test = w / n if n > 0 else 0
        test_weights.extend([per_test] * n)

    pools = [(name, filter_by_type(qs, q_type)) for name, qs in all_sources]

    total_available = sum(len(p) for _, p in pools)
    if total_available == 0:
        raise ValueError(f"В базе нет вопросов типа '{q_type}'")

    if total > total_available:
        print(f"\n  ⚠  Запрошено {total} вопросов, доступно только {total_available}.",
              file=sys.stderr)
        print(f"     Итоговый тест будет содержать {total_available} вопросов.", file=sys.stderr)
        total = total_available

    target_counts  = distribute(total, test_weights)
    final_selections = []
    shortfall = 0

    for (name, pool), cnt in zip(pools, target_counts):
        available = len(pool)
        if available == 0:
            print(f"  ⚠  '{name}': нет вопросов типа '{q_type}'", file=sys.stderr)
            shortfall += cnt
            final_selections.append((name, pool, 0))
        elif cnt > available:
            print(f"  ⚠  '{name}': запрошено {cnt}, доступно {available} → берём {available}",
                  file=sys.stderr)
            shortfall += cnt - available
            final_selections.append((name, pool, available))
        else:
            final_selections.append((name, pool, cnt))

    if shortfall > 0:
        for i, (name, pool, cnt) in enumerate(final_selections):
            if shortfall == 0:
                break
            extra = min(len(pool) - cnt, shortfall)
            if extra > 0:
                final_selections[i] = (name, pool, cnt + extra)
                shortfall -= extra
                print(f"  ℹ  Добираем {extra} вопросов из '{name}'", file=sys.stderr)

    result = []
    for name, pool, cnt in final_selections:
        if cnt > 0:
            result.extend(rng.sample(pool, cnt))

    rng.shuffle(result)
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Перемешивание вариантов ответов (только для тестовых вопросов)
# ─────────────────────────────────────────────────────────────────────────────

def shuffle_options(q, rng):
    correct_text = next((t for l, t in q.options if l == q.correct_letter), None)
    if correct_text is None:
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

def format_test(questions, title, shuffle_ans, rng, show_source=False):
    has_mc   = any(not q.is_open for q in questions)
    has_open = any(q.is_open     for q in questions)

    lines = []
    lines.append(f"# {title}")
    lines.append("")

    # Подсказка по формату зависит от состава теста
    if has_mc and has_open:
        lines.append("**Формат:** тестовые вопросы — выберите один правильный вариант ответа; "
                     "открытые вопросы — напишите развёрнутый ответ.")
    elif has_open:
        lines.append("**Формат:** напишите развёрнутый ответ на каждый вопрос.")
    else:
        lines.append("**Формат:** в каждом вопросе один правильный ответ.")

    lines.append("")
    lines.append("---")
    lines.append("")

    mc_answer_key   = []   # [(new_num, letter), ...]
    open_answer_key = []   # [(new_num, text), ...]

    for new_num, q in enumerate(questions, start=1):

        # ── Маркер типа вопроса ──────────────────────────────────────────────
        if q.is_open:
            lines.append(">✏️ Открытый")
        elif q.is_basic:
            lines.append(">ℹ️ Базовый")

        # ── Источник (опционально) ───────────────────────────────────────────
        if show_source:
            lines.append(f"*Источник: {q.source} — вопрос {q.number}*")

        # ── Заголовок вопроса ────────────────────────────────────────────────
        lines.append(f"### {new_num}. {q.text}")

        if q.is_open:
            # Открытый вопрос — вариантов нет, только поле для ответа
            lines.append("")
            lines.append("*Ответ:* _______________________________________________")
            lines.append("")
            open_answer_key.append((new_num, q.correct_text))
        else:
            # Тестовый вопрос — варианты ответов
            if shuffle_ans:
                opts, correct = shuffle_options(q, rng)
            else:
                opts, correct = q.options, q.correct_letter

            mc_answer_key.append((new_num, correct))

            for letter, text in opts:
                lines.append(f"{letter}. {text}  ")

        lines.append("")
        lines.append("---")
        lines.append("")

    # ── Блок ответов ─────────────────────────────────────────────────────────
    lines.append(f"## ОТВЕТЫ — {title}")
    lines.append("")

    if mc_answer_key:
        lines.append("**Тестовые вопросы:**")
        lines.append(", ".join(f"{n}-{l}" for n, l in mc_answer_key))
        lines.append("")

    if open_answer_key:
        lines.append("**Открытые вопросы:**")
        lines.append("")
        for n, text in open_answer_key:
            if text:
                lines.append(f"**{n}.** {text}")
            else:
                lines.append(f"**{n}.** *(ответ не указан в источнике)*")
            lines.append("")

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def build_parser():
    p = argparse.ArgumentParser(
        prog="test_gen.py",
        description="Генератор итоговых тестов из markdown-файлов",
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
  python test_gen.py -i all_tests.md -n 15 --type all --shuffle
  python test_gen.py -i all_tests.md -n 10 --type open
  python test_gen.py -i all_tests.md -n 10 --type basic --shuffle
  python test_gen.py -i all_tests.md extra.md -w 70 30 -n 20 --shuffle
  python test_gen.py -i all_tests.md --info
  python test_gen.py -i all_tests.md -n 20 --shuffle --seed 42
        """,
    )
    p.add_argument('-i', '--input', nargs='+', required=True, metavar='FILE',
                   help='Markdown-файлы с тестами')
    p.add_argument('-w', '--weights', nargs='+', type=float, metavar='W',
                   help='Веса для каждого файла (сумма не обязана = 100). '
                        'По умолчанию — равные веса.')
    p.add_argument('-n', '--total', type=int, metavar='N',
                   help='Общее количество вопросов (обязателен без --info)')
    p.add_argument('--type', choices=['basic', 'normal', 'mix', 'open', 'all'],
                   default='mix', dest='q_type', metavar='TYPE',
                   help='Тип вопросов: basic, normal, mix (по умол.), open, all')
    p.add_argument('--shuffle', action='store_true',
                   help='Перемешать варианты ответов (только для тестовых вопросов)')
    p.add_argument('-o', '--output', default='generated_test.md', metavar='FILE',
                   help='Имя выходного файла (по умолчанию: generated_test.md)')
    p.add_argument('--title', default=None, metavar='TITLE',
                   help='Название итогового теста')
    p.add_argument('--seed', type=int, default=None, metavar='N',
                   help='Seed генератора случайных чисел (для воспроизводимости)')
    p.add_argument('--show-source', action='store_true', dest='show_source',
                   help='Показывать источник под каждым вопросом')
    p.add_argument('--info', action='store_true',
                   help='Показать статистику по файлам; тест не генерируется')
    return p


# ─────────────────────────────────────────────────────────────────────────────
# main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = build_parser()
    args   = parser.parse_args()

    if not args.info and args.total is None:
        parser.error("укажите -n / --total или --info")
    if args.weights is not None and len(args.weights) != len(args.input):
        parser.error(f"количество весов ({len(args.weights)}) должно совпадать "
                     f"с количеством файлов ({len(args.input)})")
    if args.total is not None and args.total <= 0:
        parser.error("--total должен быть положительным числом")

    # ── Загружаем файлы ───────────────────────────────────────────────────────
    print("\n📂 Загрузка файлов...")
    all_sources     = []
    file_test_counts = []

    for filepath in args.input:
        try:
            tests = parse_file(filepath)
            count = len(tests)
            file_test_counts.append(count)

            if count == 0:
                print(f"  ❌ {Path(filepath).name}: тесты не найдены", file=sys.stderr)
                sys.exit(1)

            total_q  = sum(len(qs) for _, qs in tests)
            total_b  = sum(sum(1 for q in qs if q.is_basic and not q.is_open) for _, qs in tests)
            total_n  = sum(sum(1 for q in qs if not q.is_basic and not q.is_open) for _, qs in tests)
            total_o  = sum(sum(1 for q in qs if q.is_open) for _, qs in tests)
            tag      = f"({count} тестов)" if count > 1 else "(1 тест)"

            print(f"  ✅ {Path(filepath).name}  {tag}  →  {total_q} вопросов "
                  f"({total_b} базовых + {total_n} обычных + {total_o} открытых)")

            if count > 1:
                for name, qs in tests:
                    b = sum(1 for q in qs if q.is_basic and not q.is_open)
                    n = sum(1 for q in qs if not q.is_basic and not q.is_open)
                    o = sum(1 for q in qs if q.is_open)
                    print(f"       • '{name}': {len(qs)} вопр. "
                          f"({b} баз. + {n} обыч. + {o} откр.)")

            all_sources.extend(tests)

        except FileNotFoundError as e:
            print(f"  ❌ {e}", file=sys.stderr); sys.exit(1)
        except Exception as e:
            print(f"  ❌ Ошибка при разборе {filepath}: {e}", file=sys.stderr); raise

    # ── Режим --info ──────────────────────────────────────────────────────────
    if args.info:
        total_all = sum(len(qs) for _, qs in all_sources)
        total_b   = sum(sum(1 for q in qs if q.is_basic and not q.is_open) for _, qs in all_sources)
        total_n   = sum(sum(1 for q in qs if not q.is_basic and not q.is_open) for _, qs in all_sources)
        total_o   = sum(sum(1 for q in qs if q.is_open) for _, qs in all_sources)
        print(f"\n📊 Итоговая статистика базы:")
        print(f"   Файлов:             {len(args.input)}")
        print(f"   Тестов:             {len(all_sources)}")
        print(f"   Всего вопросов:     {total_all}")
        print(f"   ├─ Базовых (тест.): {total_b}")
        print(f"   ├─ Обычных (тест.): {total_n}")
        print(f"   └─ Открытых:        {total_o}")
        return

    # ── Генерация ─────────────────────────────────────────────────────────────
    file_weights = args.weights if args.weights else [1.0] * len(args.input)
    rng = random.Random(args.seed)

    print(f"\n⚙️  Параметры генерации:")
    print(f"   Запрошено вопросов : {args.total}")
    print(f"   Тип                : {args.q_type}")
    print(f"   Перемешать ответы  : {'да' if args.shuffle else 'нет'}")
    print(f"   Веса файлов        : "
          f"{ {Path(f).name: w for f, w in zip(args.input, file_weights)} }")
    print(f"   Seed               : {args.seed if args.seed is not None else 'случайный'}")

    print(f"\n🎲 Выбор вопросов...")
    try:
        selected = select_questions(
            all_sources, file_weights, file_test_counts,
            args.total, args.q_type, rng
        )
    except ValueError as e:
        print(f"\n❌ {e}", file=sys.stderr); sys.exit(1)

    if not selected:
        print("❌ Не удалось выбрать ни одного вопроса", file=sys.stderr); sys.exit(1)

    title      = args.title if args.title else "Итоговый тест по ИБ"
    output_md  = format_test(selected, title, args.shuffle, rng, args.show_source)
    out_path   = Path(args.output)
    out_path.write_text(output_md, encoding='utf-8')

    # ── Итоговая статистика ───────────────────────────────────────────────────
    source_counts = Counter(q.source for q in selected)
    cnt_b = sum(1 for q in selected if q.is_basic and not q.is_open)
    cnt_n = sum(1 for q in selected if not q.is_basic and not q.is_open)
    cnt_o = sum(1 for q in selected if q.is_open)

    print(f"\n✅ Тест сохранён: {out_path.resolve()}")
    print(f"\n📊 Состав итогового теста ({len(selected)} вопросов):")
    print(f"   ├─ Базовых (тест.) : {cnt_b}")
    print(f"   ├─ Обычных (тест.) : {cnt_n}")
    print(f"   └─ Открытых        : {cnt_o}")
    print(f"\n   По тестам-источникам:")
    for src, cnt in sorted(source_counts.items(), key=lambda x: -x[1]):
        print(f"   {cnt:3d} ({cnt/len(selected)*100:4.1f}%)  ←  {src}")


if __name__ == '__main__':
    main()
