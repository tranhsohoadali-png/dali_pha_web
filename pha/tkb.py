# -*- coding: utf-8 -*-
"""PHIẾU SOẠN NÚT MÔN HỌC (Thời khóa biểu) — tích khi soạn đơn + LƯU LẠI để tra sau.

- Trang checklist chạy ở trình duyệt (tự lưu localStorage cho đơn đang làm).
- Nút "Lưu đơn" -> ghi lên SERVER (JSON dưới MEDIA_ROOT) theo tên bé / mã đơn, để mọi
  máy đều tra lại được. KHÔNG dùng model/migration (đúng quy ước dự án).
Đặt ở module riêng để khỏi đụng views.py.
"""
import json
import os
import time

from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import render

from pha.views import staff_required

_SUB = 'tkb'
_MAX = 800                                     # giữ tối đa ~800 đơn gần nhất


def _path():
    d = os.path.join(settings.MEDIA_ROOT, _SUB)
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, 'donhang.json')


def _load_all():
    try:
        with open(_path(), encoding='utf-8') as f:
            d = json.load(f)
            return d if isinstance(d, list) else []
    except Exception:
        return []


def _save_all(items):
    with open(_path(), 'w', encoding='utf-8') as f:
        json.dump(items[-_MAX:], f, ensure_ascii=False)


@staff_required
def soan_tkb(request):
    return render(request, 'soan_tkb.html')


@staff_required
def soan_tkb_luu(request):
    """Lưu/cập nhật 1 đơn đã soạn. Body JSON: {id?, ten, ma, ngay, mau, cap, ticks[],
    mon_xong, mon_tong, nut_xong, nut_tong}. Có id -> cập nhật; không -> tạo mới."""
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST'}, status=405)
    try:
        d = json.loads(request.body.decode('utf-8') or '{}')
    except Exception:
        return JsonResponse({'ok': False, 'error': 'JSON hỏng'}, status=400)
    ten = (d.get('ten') or '').strip()
    ma = (d.get('ma') or '').strip()
    if not ten and not ma:
        return JsonResponse({'ok': False, 'error': 'Nhập tên bé hoặc mã đơn trước khi lưu.'})
    rec = {
        'id': (d.get('id') or '').strip() or ('D%d' % int(time.time() * 1000)),
        'ten': ten[:120], 'ma': ma[:60],
        'ngay': (d.get('ngay') or '')[:20], 'mau': (d.get('mau') or '')[:40],
        'cap': 'c2' if d.get('cap') == 'c2' else 'c1',
        'ticks': [bool(x) for x in (d.get('ticks') or [])][:40],
        'mon_xong': int(d.get('mon_xong') or 0), 'mon_tong': int(d.get('mon_tong') or 0),
        'nut_xong': int(d.get('nut_xong') or 0), 'nut_tong': int(d.get('nut_tong') or 0),
        'luc_luu': time.strftime('%Y-%m-%d %H:%M'),
        'nguoi': getattr(request.user, 'username', '') or '',
    }
    items = _load_all()
    for i, it in enumerate(items):
        if it.get('id') == rec['id']:
            items[i] = rec
            break
    else:
        items.append(rec)
    _save_all(items)
    return JsonResponse({'ok': True, 'id': rec['id'], 'item': rec})


@staff_required
def soan_tkb_ds(request):
    """Danh sách đơn đã lưu, mới nhất trước."""
    items = _load_all()
    items = list(reversed(items))[:200]
    return JsonResponse({'ok': True, 'items': items})


@staff_required
def soan_tkb_xoa(request):
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST'}, status=405)
    try:
        rid = (json.loads(request.body.decode('utf-8') or '{}').get('id') or '').strip()
    except Exception:
        rid = ''
    if not rid:
        return JsonResponse({'ok': False, 'error': 'Thiếu id'})
    items = [it for it in _load_all() if it.get('id') != rid]
    _save_all(items)
    return JsonResponse({'ok': True})


# ===================== KHO NÚT (tồn kho + cảnh báo hết/sắp hết) =====================
def _kho_path():
    d = os.path.join(settings.MEDIA_ROOT, _SUB)
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, 'kho.json')


def _kho_load():
    try:
        with open(_kho_path(), encoding='utf-8') as f:
            d = json.load(f)
            if isinstance(d, dict):
                return {'stock': d.get('stock') or {}, 'nguong': int(d.get('nguong') or 5)}
    except Exception:
        pass
    return {'stock': {}, 'nguong': 5}


@staff_required
def soan_tkb_kho(request):
    """Đọc tồn kho từng môn + ngưỡng 'sắp hết'."""
    return JsonResponse({'ok': True, **_kho_load()})


@staff_required
def soan_tkb_kho_luu(request):
    """Lưu tồn kho. Body JSON: {stock:{tên_môn: số_còn,...}, nguong:N} (thay toàn bộ)."""
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST'}, status=405)
    try:
        d = json.loads(request.body.decode('utf-8') or '{}')
    except Exception:
        return JsonResponse({'ok': False, 'error': 'JSON hỏng'}, status=400)
    stock = {}
    for k, v in (d.get('stock') or {}).items():
        try:
            stock[str(k)[:60]] = max(0, int(v))
        except Exception:
            continue
    try:
        nguong = max(0, int(d.get('nguong', 5)))
    except Exception:
        nguong = 5
    with open(_kho_path(), 'w', encoding='utf-8') as f:
        json.dump({'stock': stock, 'nguong': nguong}, f, ensure_ascii=False)
    return JsonResponse({'ok': True})
