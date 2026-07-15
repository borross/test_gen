#!/usr/bin/env python3
"""
Юнит-тесты для test_gen.py.
Запуск:  python test_test_gen.py   или   pytest test_test_gen.py
Покрывают все баги, исправленные в v2.0.
"""

import random
import re

from test_gen import (
    Question, distribute, filter_by_type, norm_letter, parse_block,
    shuffle_options, split_into_blocks,
)


def _parse(md: str):
    issues = []
    qs = parse_block(md.split('\n'), 'Тест', 'test.md', issues)
    return qs, issues


# ── Баг 1: слово «ответ» в заголовке вопроса/теста не должно ломать разбор ──

def test_question_with_word_otvet_in_title():
    md = """
### 1. Что такое риск?
A. Первое
B. Второе

---
### 2. Ответственность оператора ПДн наступает когда?
A. Всегда
B. По закону

---
## ОТВЕТЫ — Тест
1-B, 2-B
"""
    qs, issues = _parse(md)
    assert len(qs) == 2, f"Ожидалось 2 вопроса, получено {len(qs)}"
    assert qs[1].text.startswith('Ответственность')
    assert not [i for i in issues if i.severity == 'error']


def test_heading_otvetstvennost_is_test_heading():
    blocks = split_into_blocks([
        '# Ответственность за нарушения в ИБ (10 вопросов)',
        '### 1. Вопрос?', 'A. Да', 'B. Нет', '',
        '## ОТВЕТЫ — Ответственность', '1-A',
    ])
    assert len(blocks) == 1
    assert blocks[0][0] == 'Ответственность за нарушения в ИБ'


# ── Баг 2: мусор («---», подзаголовки) не склеивается в открытые ответы ─────

def test_open_answer_not_polluted_by_separator():
    md = """
> ℹ️ Открытый
### 1. Что такое SQL-инъекция?

---
## ОТВЕТЫ — Тест

**Ответы на открытые вопросы:**

1. SQL-инъекция — внедрение SQL-кода в пользовательский ввод.
Может привести к утечке данных.

---

Какой-то посторонний текст после разделителя.
"""
    qs, _ = _parse(md)
    assert len(qs) == 1
    ans = qs[0].correct_text
    assert '---' not in ans
    assert 'посторонний' not in ans
    assert ans.endswith('утечке данных.')


# ── Баг 3: пары «N-B» в тексте вопросов не попадают в ключ ответов ──────────

def test_answer_pairs_in_question_body_ignored():
    md = """
### 1. Что описывает модель 3-D?
A. Трёхмерную модель угроз
B. Ничего

---
### 2. Уровни сетевой модели 4-C и выше?
A. Да
B. Нет

---
## ОТВЕТЫ — Тест
1-A, 2-B
"""
    qs, issues = _parse(md)
    assert len(qs) == 2
    assert qs[0].correct_letter == 'A'   # не перезаписан ложным «3-D»
    assert qs[1].correct_letter == 'B'   # не перезаписан ложным «4-C»
    assert not [i for i in issues if i.severity == 'error']


def test_answer_pairs_in_open_answer_text_ignored():
    md = """
### 1. Вопрос?
A. Да
B. Нет

---
> ℹ️ Открытый
### 2. Открытый вопрос?

---
## ОТВЕТЫ — Тест
1-A

**Ответы на открытые вопросы:**

2. В стандарте описаны уровни 1-C и 5-D, но это не ответы.
"""
    qs, _ = _parse(md)
    mc = [q for q in qs if not q.is_open]
    assert mc[0].correct_letter == 'A'   # «1-C» из текста не перезаписал ключ


# ── Баг 4: кириллические буквы вариантов + предупреждение о пропуске ────────

def test_cyrillic_option_letters_normalized():
    md = """
### 1. Вопрос?
А. Первый
В. Второй
С. Третий
Д. Четвёртый

---
## ОТВЕТЫ — Тест
1-С
"""
    qs, issues = _parse(md)
    assert len(qs) == 1, "Кириллические варианты А/В/С/Д должны распознаваться"
    assert qs[0].correct_letter == 'C'
    assert [l for l, _ in qs[0].options] == ['A', 'B', 'C', 'D']


def test_question_without_options_reports_error():
    md = """
### 1. Вопрос без вариантов вообще?

---
## ОТВЕТЫ — Тест
1-A
"""
    qs, issues = _parse(md)
    assert len(qs) == 0
    assert any('варианта' in i.message for i in issues if i.severity == 'error')


# ── Баг 5: буква из ключа должна существовать среди вариантов ───────────────

def test_key_letter_not_in_options_rejected():
    md = """
### 1. Вопрос?
A. Первый
B. Второй
C. Третий

---
## ОТВЕТЫ — Тест
1-D
"""
    qs, issues = _parse(md)
    assert len(qs) == 0
    assert any('такого варианта нет' in i.message for i in issues)


# ── Надёжность ──────────────────────────────────────────────────────────────

def test_en_dash_in_answer_key():
    md = """
### 1. Вопрос?
A. Да
B. Нет

---
## ОТВЕТЫ — Тест
1–B
"""
    qs, _ = _parse(md)
    assert len(qs) == 1 and qs[0].correct_letter == 'B'


def test_duplicate_question_number_reported():
    md = """
### 1. Первый?
A. Да
B. Нет

---
### 1. Дубль?
A. Да
B. Нет

---
## ОТВЕТЫ — Тест
1-A
"""
    qs, issues = _parse(md)
    assert len(qs) == 1
    assert any('дублирующийся' in i.message.lower() for i in issues)


def test_orphan_answers_reported():
    md = """
### 1. Вопрос?
A. Да
B. Нет

---
## ОТВЕТЫ — Тест
1-A, 2-B, 3-C
"""
    _, issues = _parse(md)
    assert any('без вопросов' in i.message for i in issues)


def test_distribute_zero_weights_raises():
    try:
        distribute(10, [0, 0])
        assert False, "Ожидался ValueError"
    except ValueError:
        pass


def test_distribute_sum_exact():
    counts = distribute(20, [30, 40, 30])
    assert sum(counts) == 20
    assert counts == [6, 8, 6]


# ── Перемешивание вариантов ─────────────────────────────────────────────────

def test_shuffle_options_preserves_correct_answer():
    q = Question(1, 'Q?', [('A', 'один'), ('B', 'два'), ('C', 'три'), ('D', 'четыре')],
                 'C', '', False, False, 'Тест', 'f.md')
    for seed in range(30):
        opts, correct = shuffle_options(q, random.Random(seed))
        assert dict(opts)[correct] == 'три'


# ── Фильтрация по типам ─────────────────────────────────────────────────────

def test_filter_by_type():
    qs = [
        Question(1, 'b', [('A', 'x')], 'A', '', True, False, 'T', 'f'),
        Question(2, 'n', [('A', 'x')], 'A', '', False, False, 'T', 'f'),
        Question(3, 'o', [], '', 'ans', False, True, 'T', 'f'),
    ]
    assert [q.number for q in filter_by_type(qs, 'basic')] == [1]
    assert [q.number for q in filter_by_type(qs, 'normal')] == [2]
    assert [q.number for q in filter_by_type(qs, 'mix')] == [1, 2]
    assert [q.number for q in filter_by_type(qs, 'open')] == [3]
    assert [q.number for q in filter_by_type(qs, 'all')] == [1, 2, 3]


# ── Форматирование: открытые вопросы после тестовых ────────────────────────

def test_format_open_questions_last():
    from test_gen import format_test
    qs = [
        Question(1, 'откр1', [], '', 'ответ1', False, True, 'T', 'f'),
        Question(2, 'тест1', [('A', 'x'), ('B', 'y')], 'A', '', False, False, 'T', 'f'),
        Question(3, 'откр2', [], '', 'ответ2', False, True, 'T', 'f'),
        Question(4, 'тест2', [('A', 'x'), ('B', 'y')], 'B', '', True, False, 'T', 'f'),
    ]
    md = format_test(qs, 'Т', False, random.Random(0))
    order = [m for m in re.findall(r'^### \d+\. (\S+)', md, re.M)]
    assert order == ['тест1', 'тест2', 'откр1', 'откр2'], order
    # и нумерация в ключе соответствует: тестовые 1-2, открытые 3-4
    assert '1-A, 2-B' in md
    assert '**3.** ответ1' in md and '**4.** ответ2' in md


def test_norm_letter():
    assert norm_letter('С') == 'C'   # кириллица
    assert norm_letter('в') == 'B'
    assert norm_letter('d') == 'D'


if __name__ == '__main__':
    import sys
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith('test_') and callable(f)]
    failed = 0
    for name, fn in fns:
        try:
            fn()
            print(f"  ✅ {name}")
        except AssertionError as e:
            failed += 1
            print(f"  ❌ {name}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} тестов пройдено")
    sys.exit(1 if failed else 0)
