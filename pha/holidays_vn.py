# -*- coding: utf-8 -*-
"""Lịch nghỉ lễ Việt Nam — tính TỰ ĐỘNG cho mọi năm (không cần mạng, không cần cập nhật tay).

Ngày lễ dương lịch cố định + ngày lễ âm lịch (Tết Nguyên Đán, Giỗ Tổ Hùng Vương)
quy đổi bằng thuật toán âm lịch của Hồ Ngọc Đức (múi giờ VN = +7).
"""
import math
import datetime

_TZ = 7


def _jdn(dd, mm, yy):
    a = (14 - mm) // 12
    y = yy + 4800 - a
    m = mm + 12 * a - 3
    return (dd + (153 * m + 2) // 5 + 365 * y + y // 4 - y // 100 + y // 400 - 32045)


def _jd_to_date(jd):
    a = jd + 32044
    b = (4 * a + 3) // 146097
    c = a - (b * 146097) // 4
    d = (4 * c + 3) // 1461
    e = c - (1461 * d) // 4
    m = (5 * e + 2) // 153
    day = e - (153 * m + 2) // 5 + 1
    month = m + 3 - 12 * (m // 10)
    year = b * 100 + d - 4800 + m // 10
    return day, month, year


def _new_moon(k):
    T = k / 1236.85
    T2 = T * T
    T3 = T2 * T
    dr = math.pi / 180
    Jd1 = 2415020.75933 + 29.53058868 * k + 0.0001178 * T2 - 0.000000155 * T3
    Jd1 += 0.00033 * math.sin((166.56 + 132.87 * T - 0.009173 * T2) * dr)
    M = 359.2242 + 29.10535608 * k - 0.0000333 * T2 - 0.00000347 * T3
    Mpr = 306.0253 + 385.81691806 * k + 0.0107306 * T2 + 0.00001236 * T3
    F = 21.2964 + 390.67050646 * k - 0.0016528 * T2 - 0.00000239 * T3
    C1 = (0.1734 - 0.000393 * T) * math.sin(M * dr) + 0.0021 * math.sin(2 * dr * M)
    C1 += -0.4068 * math.sin(Mpr * dr) + 0.0161 * math.sin(dr * 2 * Mpr)
    C1 += -0.0004 * math.sin(dr * 3 * Mpr)
    C1 += 0.0104 * math.sin(dr * 2 * F) - 0.0051 * math.sin(dr * (M + Mpr))
    C1 += -0.0074 * math.sin(dr * (M - Mpr)) + 0.0004 * math.sin(dr * (2 * F + M))
    C1 += -0.0004 * math.sin(dr * (2 * F - M)) - 0.0006 * math.sin(dr * (2 * F + Mpr))
    C1 += 0.0010 * math.sin(dr * (2 * F - Mpr)) + 0.0005 * math.sin(dr * (2 * Mpr + M))
    if T < -11:
        deltat = 0.001 + 0.000839 * T + 0.0002261 * T2 - 0.00000845 * T3 - 0.000000081 * T * T3
    else:
        deltat = -0.000278 + 0.000265 * T + 0.000262 * T2
    return Jd1 + C1 - deltat


def _sun_longitude(jdn):
    T = (jdn - 2451545.0) / 36525
    T2 = T * T
    dr = math.pi / 180
    M = 357.52910 + 35999.05030 * T - 0.0001559 * T2 - 0.00000048 * T * T2
    L0 = 280.46645 + 36000.76983 * T + 0.0003032 * T2
    DL = (1.914600 - 0.004817 * T - 0.000014 * T2) * math.sin(dr * M)
    DL += (0.019993 - 0.000101 * T) * math.sin(dr * 2 * M) + 0.000290 * math.sin(dr * 3 * M)
    L = (L0 + DL) * dr
    L = L - math.pi * 2 * int(L / (math.pi * 2))
    return L


def _get_sun_longitude(day_number, tz):
    return int(_sun_longitude(day_number - 0.5 - tz / 24.0) / math.pi * 6)


def _get_new_moon_day(k, tz):
    return int(_new_moon(k) + 0.5 + tz / 24.0)


def _get_lunar_month_11(yy, tz):
    off = _jdn(31, 12, yy) - 2415021
    k = int(off / 29.530588853)
    nm = _get_new_moon_day(k, tz)
    if _get_sun_longitude(nm, tz) >= 9:
        nm = _get_new_moon_day(k - 1, tz)
    return nm


def _get_leap_month_offset(a11, tz):
    k = int((a11 - 2415021.076998695) / 29.530588853 + 0.5)
    i = 1
    arc = _get_sun_longitude(_get_new_moon_day(k + i, tz), tz)
    while True:
        last = arc
        i += 1
        arc = _get_sun_longitude(_get_new_moon_day(k + i, tz), tz)
        if not (arc != last and i < 14):
            break
    return i - 1


def lunar_to_solar(lunar_day, lunar_month, lunar_year, leap=0, tz=_TZ):
    """Quy đổi ngày ÂM -> (dd, mm, yyyy) DƯƠNG. (0,0,0) nếu tháng nhuận không hợp lệ."""
    if lunar_month < 11:
        a11 = _get_lunar_month_11(lunar_year - 1, tz)
        b11 = _get_lunar_month_11(lunar_year, tz)
    else:
        a11 = _get_lunar_month_11(lunar_year, tz)
        b11 = _get_lunar_month_11(lunar_year + 1, tz)
    k = int(0.5 + (a11 - 2415021.076998695) / 29.530588853)
    off = lunar_month - 11
    if off < 0:
        off += 12
    if b11 - a11 > 365:
        leap_off = _get_leap_month_offset(a11, tz)
        leap_month = leap_off - 2
        if leap_month < 0:
            leap_month += 12
        if leap != 0 and lunar_month != leap_month:
            return (0, 0, 0)
        elif leap != 0 or off >= leap_off:
            off += 1
    month_start = _get_new_moon_day(k + off, tz)
    return _jd_to_date(month_start + lunar_day - 1)


_CACHE = {}


def holidays(year):
    """Trả dict {'YYYY-MM-DD': 'Tên lễ'} các ngày nghỉ lễ chính thức của VN trong năm."""
    if year in _CACHE:
        return _CACHE[year]
    h = {}

    def add(dd, mm, yy, name):
        if yy:
            h['%04d-%02d-%02d' % (yy, mm, dd)] = name

    add(1, 1, year, 'Tết Dương lịch')
    add(30, 4, year, 'Giải phóng miền Nam')
    add(1, 5, year, 'Quốc tế Lao động')
    add(2, 9, year, 'Quốc khánh')
    add(1, 9, year, 'Quốc khánh (nghỉ kèm)')

    # Tết Nguyên Đán: giao thừa + mùng 1..4 (5 ngày)
    d, m, y = lunar_to_solar(1, 1, year, 0)
    if y:
        t1 = datetime.date(y, m, d)
        for off in range(-1, 4):
            dd = t1 + datetime.timedelta(days=off)
            label = 'Giao thừa Tết' if off == -1 else ('Mùng %d Tết' % (off + 1))
            h[dd.strftime('%Y-%m-%d')] = label

    # Giỗ Tổ Hùng Vương: 10/3 âm lịch
    d, m, y = lunar_to_solar(10, 3, year, 0)
    add(d, m, y, 'Giỗ Tổ Hùng Vương')

    _CACHE[year] = h
    return h


def name_of(date_str):
    """Tên ngày lễ của 'YYYY-MM-DD' (chuẩn quốc gia), '' nếu là ngày thường."""
    try:
        yy = int(date_str[:4])
    except (ValueError, TypeError):
        return ''
    return holidays(yy).get(date_str, '')


def upcoming(year, from_date_str=None, limit=20):
    """Danh sách [{'date','name'}] sắp tới trong năm (để hiển thị cho quản lý)."""
    items = sorted(holidays(year).items())
    if from_date_str:
        items = [it for it in items if it[0] >= from_date_str]
    return [{'date': k, 'name': v} for k, v in items[:limit]]
