# -*- coding: utf-8 -*-
"""QUẢN LÝ XƯỞNG IN 3D (Bambu Lab A1) — v1: Máy in + Thư viện file in.

Lưu JSON dưới MEDIA_ROOT (media/print/), file in để trong media/print/files/ — KHÔNG
model/migration (đúng quy ước dự án; deploy = git pull + restart). File in tải qua
/media/... như ảnh kết quả. Đặt module riêng để khỏi đụng views.py.

v2 (sau): lệnh in / hàng đợi + lịch sử + nối kho /soan-tkb.
"""
import json
import os
import re
import time

from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import render

from pha.views import staff_required

_SUB = 'print'


def _dir():
    d = os.path.join(settings.MEDIA_ROOT, _SUB)
    os.makedirs(d, exist_ok=True)
    return d


def _files_dir():
    d = os.path.join(_dir(), 'files')
    os.makedirs(d, exist_ok=True)
    return d


def _path(name):
    return os.path.join(_dir(), name)


def _load(name):
    try:
        with open(_path(name), encoding='utf-8') as f:
            d = json.load(f)
            return d if isinstance(d, list) else []
    except Exception:
        return []


def _save(name, items):
    with open(_path(name), 'w', encoding='utf-8') as f:
        json.dump(items, f, ensure_ascii=False)


def _body(request):
    try:
        return json.loads(request.body.decode('utf-8') or '{}')
    except Exception:
        return {}


def _nid(pre):
    return '%s%d' % (pre, int(time.time() * 1000))


def _safe(name):
    name = re.sub(r'[^0-9A-Za-zÀ-ỹ._-]+', '_', (name or '').strip())
    return (name or 'file')[:80]


# ============================ TRANG ============================
@staff_required
def quan_ly_in(request):
    return render(request, 'quan_ly_in.html')


# ============================ MÁY IN ============================
TRANG_THAI = ('ranh', 'dang_in', 'bao_tri')


@staff_required
def may_ds(request):
    return JsonResponse({'ok': True, 'items': _load('may.json')})


@staff_required
def may_luu(request):
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST'}, status=405)
    d = _body(request)
    ten = (d.get('ten') or '').strip()
    if not ten:
        return JsonResponse({'ok': False, 'error': 'Nhập tên máy.'})
    rec = {
        'id': (d.get('id') or '').strip() or _nid('M'),
        'ten': ten[:60], 'model': (d.get('model') or 'A1')[:30],
        'trang_thai': d.get('trang_thai') if d.get('trang_thai') in TRANG_THAI else 'ranh',
        'dang_in': (d.get('dang_in') or '')[:120], 'ghi_chu': (d.get('ghi_chu') or '')[:200],
    }
    items = _load('may.json')
    for i, it in enumerate(items):
        if it.get('id') == rec['id']:
            items[i] = rec
            break
    else:
        items.append(rec)
    _save('may.json', items)
    return JsonResponse({'ok': True, 'item': rec})


@staff_required
def may_xoa(request):
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST'}, status=405)
    rid = (_body(request).get('id') or '').strip()
    _save('may.json', [it for it in _load('may.json') if it.get('id') != rid])
    return JsonResponse({'ok': True})


# ============================ THƯ VIỆN FILE IN ============================
@staff_required
def file_ds(request):
    return JsonResponse({'ok': True, 'items': _load('file.json')})


@staff_required
def file_them(request):
    """Tải file in + metadata (multipart form). Fields: nhom, ten, phut, nhua, con, mau, ghi_chu."""
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST'}, status=405)
    up = request.FILES.get('file')
    ten = (request.POST.get('ten') or '').strip()
    if not up:
        return JsonResponse({'ok': False, 'error': 'Chưa chọn file.'})
    if not ten:
        ten = os.path.splitext(up.name)[0]
    fid = _nid('F')
    fname = '%s_%s' % (fid, _safe(up.name))
    with open(os.path.join(_files_dir(), fname), 'wb') as f:
        for chunk in up.chunks():
            f.write(chunk)
    rec = {
        'id': fid, 'ten': ten[:120],
        'nhom': (request.POST.get('nhom') or 'Khác').strip()[:60],
        'file': 'print/files/' + fname, 'ten_file': up.name[:120],
        'kieu': (os.path.splitext(up.name)[1] or '').lower()[:10],
        'kb': round(up.size / 1024.0),
        'phut': _toint(request.POST.get('phut')), 'nhua': _toint(request.POST.get('nhua')),
        'con': _toint(request.POST.get('con')), 'mau': (request.POST.get('mau') or '').strip()[:40],
        'ghi_chu': (request.POST.get('ghi_chu') or '').strip()[:300],
        'luc_them': time.strftime('%Y-%m-%d %H:%M'),
    }
    items = _load('file.json')
    items.append(rec)
    _save('file.json', items)
    return JsonResponse({'ok': True, 'item': rec})


@staff_required
def file_sua(request):
    """Sửa metadata (không đổi file). JSON: {id, ten, nhom, phut, nhua, con, mau, ghi_chu}."""
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST'}, status=405)
    d = _body(request)
    rid = (d.get('id') or '').strip()
    items = _load('file.json')
    for it in items:
        if it.get('id') == rid:
            if d.get('ten'):
                it['ten'] = d['ten'].strip()[:120]
            it['nhom'] = (d.get('nhom') or it.get('nhom') or 'Khác').strip()[:60]
            it['mau'] = (d.get('mau') or '').strip()[:40]
            it['ghi_chu'] = (d.get('ghi_chu') or '').strip()[:300]
            for k in ('phut', 'nhua', 'con'):
                if k in d:
                    it[k] = _toint(d.get(k))
            _save('file.json', items)
            return JsonResponse({'ok': True, 'item': it})
    return JsonResponse({'ok': False, 'error': 'Không thấy file.'})


@staff_required
def file_xoa(request):
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST'}, status=405)
    rid = (_body(request).get('id') or '').strip()
    items = _load('file.json')
    keep = []
    for it in items:
        if it.get('id') == rid:
            try:
                os.remove(os.path.join(settings.MEDIA_ROOT, it.get('file', '')))
            except Exception:
                pass
        else:
            keep.append(it)
    _save('file.json', keep)
    return JsonResponse({'ok': True})


def _toint(v):
    try:
        return max(0, int(float(v)))
    except Exception:
        return 0
