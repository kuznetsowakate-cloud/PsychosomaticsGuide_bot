import math
import re
from datetime import date

MONTHS_RU = ['янв', 'фев', 'мар', 'апр', 'май', 'июн',
             'июл', 'авг', 'сен', 'окт', 'ноя', 'дек']


def _to_ym(total_months: int) -> str:
    total_months = max(0, round(total_months))
    y = total_months // 12
    m = total_months % 12
    return f"{y} лет {m} мес"


def _add_months(d: date, months: int) -> date:
    month0 = d.month - 1 + months      # 0-based month index
    year = d.year + month0 // 12
    month = month0 % 12 + 1
    return date(year, month, 1)


def _date_label(birth: date, months: int) -> str:
    d = _add_months(birth, round(months))
    return f"{MONTHS_RU[d.month - 1]} {str(d.year)[-2:]}"


def calculate_chain(
    diag_y: int, diag_m: int,
    sep_y: int, sep_m: int,
    cur_y: int, cur_m: int,
    client_fio: str = "",
) -> str:
    diag_total = diag_y * 12 + diag_m
    sep_total = sep_y * 12 + sep_m
    cur_total = cur_y * 12 + cur_m

    half = round(diag_total / 2)
    quarter = round(diag_total / 4)
    eighth = round(diag_total / 8)
    sixteenth = round(diag_total / 16)
    reno = round(diag_total / 5)

    # Определяем глубину: первая доля < 60 мес задаёт уровень детализации
    if half < 60:
        fractions = [("1/2", half)]
        key_months = half
    elif quarter < 60:
        fractions = [("1/2", half), ("1/4", quarter)]
        key_months = quarter
    elif eighth < 60:
        fractions = [("1/2", half), ("1/4", quarter), ("1/8", eighth)]
        key_months = eighth
    else:
        fractions = [("1/2", half), ("1/4", quarter), ("1/8", eighth), ("1/16", sixteenth)]
        key_months = sixteenth

    line_count = math.ceil(cur_total / sep_total) if sep_total > 0 else 1

    # Приблизительная дата рождения: сегодня минус cur_total месяцев
    birth = _add_months(date.today(), -cur_total)

    key_y = key_months // 12

    parts: list[str] = []

    if client_fio:
        parts.append(f"Клиент: <b>{client_fio}</b>\n")

    parts.append(
        f"Возраст постановки диагноза: {diag_y} лет {diag_m} мес\n"
        f"Возраст клиента: {cur_y} лет {cur_m} мес\n"
        f"Возраст сепарации: {sep_y} лет {sep_m} мес\n"
    )
    parts.append(
        f"1-й год жизни: {key_y}–{key_y + 1} мес\n"
        f"беременность: {key_y}–{key_y + 1} мес\n"
        f"до беременности: {9 - key_y - 1}–{9 - key_y} мес\n"
    )

    for i in range(1, line_count + 1):
        shift = (i - 1) * sep_total
        block_lines = [
            f"<b>{i} параллель (от {_to_ym(shift)} до {_to_ym(shift + sep_total)}):</b>"
        ]
        for label, base in fractions:
            point = base + shift
            block_lines.append(f"{label}:   {_to_ym(point)} ({_date_label(birth, point)})")
        reno_point = reno + shift
        block_lines.append(f"Рено: {_to_ym(reno_point)} ({_date_label(birth, reno_point)})")
        parts.append("\n".join(block_lines))

    return "\n\n".join(parts)


def parse_chain_input(text: str) -> dict | None:
    """
    Парсит сообщение вида:
        ФИО: Иванова Мария Петровна
        Д: 18 л 7 мес
        С: 25 л 9 мес
        К: 37 л 0 мес

    Возвращает словарь с ключами diag_y, diag_m, sep_y, sep_m, cur_y, cur_m, client_fio
    или None если данные не распознаны.
    """
    def extract_ym(s: str) -> tuple[int, int]:
        nums = re.findall(r'\d+', s)
        y = int(nums[0]) if len(nums) > 0 else 0
        m = int(nums[1]) if len(nums) > 1 else 0
        return y, m

    lines = [ln.strip() for ln in text.strip().splitlines() if ln.strip()]

    client_fio = ""
    remaining = []
    for ln in lines:
        match = re.match(r'^(?:фио|клиент)\s*:\s*(.+)$', ln, re.IGNORECASE)
        if match:
            client_fio = match.group(1).strip()
        else:
            remaining.append(ln)

    diag_line = sep_line = cur_line = None
    for ln in remaining:
        low = ln.lower()
        if re.match(r'^[ДдDd]\s*:', ln) or 'диагноз' in low:
            diag_line = ln
        elif re.match(r'^[СсCc]\s*:', ln) or 'сепарац' in low:
            sep_line = ln
        elif re.match(r'^[КкKk]\s*:', ln) or 'текущ' in low:
            cur_line = ln

    # Позиционный fallback если метки не найдены
    diag_text = diag_line or (remaining[0] if len(remaining) > 0 else "")
    sep_text = sep_line or (remaining[1] if len(remaining) > 1 else "")
    cur_text = cur_line or (remaining[2] if len(remaining) > 2 else "")

    diag_y, diag_m = extract_ym(diag_text)
    sep_y, sep_m = extract_ym(sep_text)
    cur_y, cur_m = extract_ym(cur_text)

    # Проверяем что строка вообще содержала цифры (не путаем "не найдено" с "явный 0")
    if not re.search(r'\d', diag_text):
        return None
    if not re.search(r'\d', sep_text):
        return None
    if not re.search(r'\d', cur_text):
        return None

    return {
        "client_fio": client_fio,
        "diag_y": diag_y, "diag_m": diag_m,
        "sep_y": sep_y, "sep_m": sep_m,
        "cur_y": cur_y, "cur_m": cur_m,
    }
