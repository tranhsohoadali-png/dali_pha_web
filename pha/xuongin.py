# -*- coding: utf-8 -*-
"""QUẢN LÝ XƯỞNG IN 3D (Bambu Lab A1) — v1: Máy in + Thư viện file in.

Lưu JSON dưới MEDIA_ROOT (media/print/), file in để trong media/print/files/ — KHÔNG
model/migration (đúng quy ước dự án; deploy = git pull + restart). File in tải qua
/media/... như ảnh kết quả. Đặt module riêng để khỏi đụng views.py.

v2 (sau): lệnh in / hàng đợi + lịch sử + nối kho /soan-tkb.
"""
import hmac
import io
import json
import os
import re
import time
import zipfile
from datetime import datetime

from django.conf import settings
from django.http import JsonResponse, HttpResponseForbidden
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt

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


def manifest_xuong(request):
    """Manifest PWA app 'Xưởng in' — cài màn hình chính, mở thẳng /quan-ly-in."""
    data = {
        "name": "DALI Xưởng in 3D", "short_name": "Xưởng in",
        "start_url": "/quan-ly-in", "scope": "/", "display": "standalone", "orientation": "portrait",
        "background_color": "#EEF2F5", "theme_color": "#0F9C9C",
        "icons": [
            {"src": "/media/icon-xuong-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any maskable"},
            {"src": "/media/icon-xuong-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"},
        ],
        "shortcuts": [
            {"name": "Realtime máy", "short_name": "Realtime", "url": "/quan-ly-in?tab=live"},
            {"name": "Xuất file thẻ", "short_name": "Xuất file", "url": "/quan-ly-in?tab=file"},
            {"name": "Bảo trì máy", "short_name": "Bảo trì", "url": "/quan-ly-in?tab=bt"},
        ],
    }
    return JsonResponse(data, content_type='application/manifest+json')


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
        # v3: map heartbeat realtime -> máy (Serial hiện trên màn hình máy / Access Code KHÔNG lưu ở VPS)
        'serial': (d.get('serial') or '').strip()[:40], 'ip': (d.get('ip') or '').strip()[:40],
        'dau_phun': (d.get('dau_phun') or '').strip()[:6],   # đầu phun đang lắp (0.2/0.3/0.4) để cảnh báo khi cắt thẻ
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
    """Tải file in + metadata (multipart). Fields: nhom, ten, dau_phun, phut, nhua, con, mau, ghi_chu.
    Nếu là .3mf: TỰ ĐỌC cấu hình (nhiệt vòi/bàn, đầu phun, loại nhựa, lớp) khỏi phải nhập tay."""
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
    conf = _read_3mf_config(os.path.join(_files_dir(), fname))   # tự đọc cấu hình trong .3mf
    dp = (request.POST.get('dau_phun') or conf.get('nozzle') or '').strip()
    rec = {
        'id': fid, 'ten': ten[:120],
        'nhom': (request.POST.get('nhom') or 'Khác').strip()[:60],
        'file': 'print/files/' + fname, 'ten_file': up.name[:120],
        'kieu': (os.path.splitext(up.name)[1] or '').lower()[:10],
        'kb': round(up.size / 1024.0),
        'dau_phun': dp if dp in DAU_PHUN else dp[:6],
        'phut': _toint(request.POST.get('phut')), 'nhua': _tofloat(request.POST.get('nhua')),
        'con': _toint(request.POST.get('con')), 'mau': (request.POST.get('mau') or '').strip()[:40],
        'nhiet': conf.get('nhiet', ''), 'bed': conf.get('bed', ''),
        'lop': conf.get('lop', ''), 'nhua_loai': conf.get('nhua_loai', ''),
        'so_the': _toint(request.POST.get('so_the')), 'max_khay': _toint(request.POST.get('max_khay')),
        'ghi_chu': (request.POST.get('ghi_chu') or '').strip()[:300],
        'luc_them': time.strftime('%Y-%m-%d %H:%M'),
    }
    items = _load('file.json')
    items.append(rec)
    _save('file.json', items)
    return JsonResponse({'ok': True, 'item': rec})


@staff_required
def file_sua(request):
    """Sửa metadata (không đổi file)."""
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
            if d.get('dau_phun'):
                dp = str(d['dau_phun']).strip()
                it['dau_phun'] = dp if dp in DAU_PHUN else dp[:6]
            it['ghi_chu'] = (d.get('ghi_chu') or '').strip()[:300]
            for k in ('phut', 'con', 'nhiet', 'bed', 'so_the', 'max_khay'):
                if k in d:
                    it[k] = _toint(d.get(k))
            if 'nhua' in d:
                it['nhua'] = _tofloat(d.get('nhua'))
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


# ============================ LỆNH IN / HÀNG ĐỢI ============================
_JOB_TT = ('cho', 'dang_in', 'xong', 'loi')


def _set_may(may_id, trang_thai, dang_in):
    """Đồng bộ trạng thái 1 máy khi lệnh in đổi trạng thái. Bỏ qua nếu không có máy."""
    if not may_id:
        return
    mays = _load('may.json')
    ch = False
    for m in mays:
        if m.get('id') == may_id:
            m['trang_thai'] = trang_thai
            m['dang_in'] = dang_in
            ch = True
            break
    if ch:
        _save('may.json', mays)


@staff_required
def job_ds(request):
    return JsonResponse({'ok': True, 'items': _load('job.json')})


@staff_required
def job_luu(request):
    """Tạo/sửa lệnh in. JSON: {id?, ten, nhom, file_id, so_luong, so_khay, uu_tien, ghi_chu}."""
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST'}, status=405)
    d = _body(request)
    ten = (d.get('ten') or '').strip()
    if not ten:
        return JsonResponse({'ok': False, 'error': 'Nhập tên sản phẩm / lệnh in.'})
    items = _load('job.json')
    old = None
    rid = (d.get('id') or '').strip()
    for it in items:
        if it.get('id') == rid:
            old = it
            break
    ut = d.get('uu_tien')
    rec = {
        'id': rid or _nid('J'), 'ten': ten[:120], 'nhom': (d.get('nhom') or '').strip()[:60],
        'file_id': (d.get('file_id') or '').strip()[:40],
        'so_luong': _toint(d.get('so_luong')), 'so_khay': _toint(d.get('so_khay')),
        'uu_tien': ut if ut in (1, 2, 3) else 2,
        'may_id': (old or {}).get('may_id', ''),
        'trang_thai': (old or {}).get('trang_thai', 'cho'),
        'ghi_chu': (d.get('ghi_chu') or '').strip()[:300],
        'luc_tao': (old or {}).get('luc_tao') or time.strftime('%Y-%m-%d %H:%M'),
        'luc_xong': (old or {}).get('luc_xong', ''),
        'nguoi': getattr(request.user, 'username', '') or '',
    }
    if old is not None:
        items[items.index(old)] = rec
    else:
        items.append(rec)
    _save('job.json', items)
    return JsonResponse({'ok': True, 'item': rec})


@staff_required
def job_trangthai(request):
    """Đổi trạng thái lệnh in + đồng bộ máy. JSON: {id, trang_thai, may_id?}."""
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST'}, status=405)
    d = _body(request)
    rid = (d.get('id') or '').strip()
    tt = d.get('trang_thai')
    if tt not in _JOB_TT:
        return JsonResponse({'ok': False, 'error': 'Trạng thái sai'})
    items = _load('job.json')
    for it in items:
        if it.get('id') == rid:
            if tt == 'dang_in':
                if d.get('may_id'):
                    it['may_id'] = (d.get('may_id') or '').strip()[:40]
                it['trang_thai'] = 'dang_in'
                it['luc_xong'] = ''
                _set_may(it.get('may_id'), 'dang_in',
                         (it['ten'] + (' ×%d' % it['so_luong'] if it['so_luong'] else ''))[:120])
            elif tt in ('xong', 'loi'):
                it['trang_thai'] = tt
                it['luc_xong'] = time.strftime('%Y-%m-%d %H:%M')
                _set_may(it.get('may_id'), 'ranh', '')   # in xong -> máy rảnh
            else:  # 'cho'
                it['trang_thai'] = 'cho'
                _set_may(it.get('may_id'), 'ranh', '')
                it['may_id'] = ''
            _save('job.json', items)
            return JsonResponse({'ok': True, 'item': it})
    return JsonResponse({'ok': False, 'error': 'Không thấy lệnh'})


@staff_required
def job_xoa(request):
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST'}, status=405)
    rid = (_body(request).get('id') or '').strip()
    items = _load('job.json')
    for it in items:
        if it.get('id') == rid and it.get('trang_thai') == 'dang_in':
            _set_may(it.get('may_id'), 'ranh', '')
    _save('job.json', [it for it in items if it.get('id') != rid])
    return JsonResponse({'ok': True})


@staff_required
def kho_goi_y(request):
    """Đọc kho nút /soan-tkb -> gợi ý môn HẾT/SẮP HẾT để in bù."""
    p = os.path.join(settings.MEDIA_ROOT, 'tkb', 'kho.json')
    try:
        with open(p, encoding='utf-8') as f:
            k = json.load(f)
        stock = k.get('stock') or {}
        ng = int(k.get('nguong') or 5)
    except Exception:
        stock, ng = {}, 5
    out = []
    for ten, sl in stock.items():
        try:
            sl = int(sl)
        except Exception:
            continue
        if sl <= ng:
            out.append({'ten': ten, 'con_lai': sl, 'trang_thai': 'het' if sl <= 0 else 'sap_het'})
    out.sort(key=lambda x: x['con_lai'])
    return JsonResponse({'ok': True, 'items': out, 'nguong': ng})


# ============================ V3: NỐI MÁY A1 REALTIME ============================
# Kiến trúc: VPS ở XA không với tới LAN xưởng -> 1 "agent" chạy trên PC xưởng (cùng
# mạng máy in) đọc trạng thái A1 qua MQTT rồi POST về đây (heartbeat), đồng thời POLL
# lệnh (pull) để thực thi (in/tạm dừng/tiếp tục/dừng) rồi báo lại (ack). Access Code của
# máy CHỈ nằm trong config agent, KHÔNG bao giờ lưu trên VPS. Xác thực agent bằng 1 khoá
# tự sinh trong AppSetting (giống RIP agent) — endpoint agent @csrf_exempt + kiểm khoá.
_AGENT_KEY = 'BAMBU_AGENT_KEY'
_LIVE, _CMD = 'live.json', 'cmd.json'
STALE_S = 30                       # máy/agent coi là OFFLINE nếu không báo trong 30s
CMD_ACTIONS = ('print', 'pause', 'resume', 'stop')
_CMD_KEEP = 200


def _agent_key():
    from pha.models import AppSetting
    k = AppSetting.get(_AGENT_KEY, '')
    if not k:
        k = 'ba-' + os.urandom(8).hex()
        AppSetting.set(_AGENT_KEY, k)
    return k


def _check_agent(request):
    given = request.headers.get('X-API-Key') or request.GET.get('key') or ''
    if not given:
        given = (_body(request).get('key') or '')
    return hmac.compare_digest(str(given), str(_agent_key()))


def _tofloat(v):
    try:
        return round(float(v), 1)
    except Exception:
        return 0.0


def _live_load():
    try:
        with open(_path(_LIVE), encoding='utf-8') as f:
            d = json.load(f)
            return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _live_save(d):
    with open(_path(_LIVE), 'w', encoding='utf-8') as f:
        json.dump(d, f, ensure_ascii=False)


# ---------------- Endpoint cho AGENT (kiểm khoá, không cần đăng nhập) ----------------
@csrf_exempt
def agent_heartbeat(request):
    """Agent POST trạng thái tất cả máy: {key, machines:[{serial,ip,online,gcode_state,
    percent,layer,total_layer,nozzle,nozzle_t,bed,bed_t,remain_min,stage,error,file,ams}]}."""
    if request.method != 'POST' or not _check_agent(request):
        return HttpResponseForbidden('bad key')
    try:
        from pha.wifi_ip import remember as _rw     # tiện cập nhật IP xưởng nếu chỉ chạy agent này
        _rw(request)
    except Exception:
        pass
    d = _body(request)
    now = time.time()
    live = _live_load()
    macs = live.get('machines') or {}
    for m in (d.get('machines') or []):
        sn = str(m.get('serial') or m.get('ip') or '').strip()
        if not sn:
            continue
        rec = macs.get(sn, {})
        rec.update({
            'serial': sn, 'ip': (m.get('ip') or rec.get('ip') or '')[:40],
            'online': bool(m.get('online', True)),
            'gcode_state': (str(m.get('gcode_state') or ''))[:20],
            'percent': _toint(m.get('percent')),
            'layer': _toint(m.get('layer')), 'total_layer': _toint(m.get('total_layer')),
            'nozzle': _tofloat(m.get('nozzle')), 'nozzle_t': _tofloat(m.get('nozzle_t')),
            'bed': _tofloat(m.get('bed')), 'bed_t': _tofloat(m.get('bed_t')),
            'remain_min': _toint(m.get('remain_min')),
            'stage': (str(m.get('stage') or ''))[:80],
            'error': (str(m.get('error') or ''))[:200],
            'file': (str(m.get('file') or ''))[:160],
            'ams': m.get('ams') if isinstance(m.get('ams'), list) else [],
            'ts': now,
        })
        macs[sn] = rec
    live['machines'] = macs
    live['ts'] = now
    _live_save(live)
    return JsonResponse({'ok': True, 'server_time': now})


@csrf_exempt
def agent_pull(request):
    """Agent GET (kèm khoá) -> các lệnh ĐANG CHỜ; đánh dấu 'sent' để khỏi lấy lại."""
    if not _check_agent(request):
        return HttpResponseForbidden('bad key')
    cmds = _load(_CMD)
    out, ch = [], False
    for c in cmds:
        if c.get('status') == 'cho':
            c['status'] = 'sent'
            c['luc_gui'] = time.strftime('%Y-%m-%d %H:%M:%S')
            ch = True
            # Trả nguyên bản ghi (không có gì nhạy cảm) — agent tự đọc theo 'action'.
            out.append({k: v for k, v in c.items() if k not in ('nguoi',)})
    if ch:
        _save(_CMD, cmds)
    return JsonResponse({'ok': True, 'commands': out})


@csrf_exempt
def agent_ack(request):
    """Agent POST kết quả lệnh: {key, id, status:xong|loi, message}."""
    if request.method != 'POST' or not _check_agent(request):
        return HttpResponseForbidden('bad key')
    d = _body(request)
    rid = str(d.get('id') or '')
    st = d.get('status') if d.get('status') in ('xong', 'loi') else 'xong'
    cmds = _load(_CMD)
    for c in cmds:
        if c.get('id') == rid:
            c['status'] = st
            c['message'] = (str(d.get('message') or ''))[:200]
            c['luc_xong'] = time.strftime('%Y-%m-%d %H:%M:%S')
            # Kết quả cắt thực tế (nếu là lệnh slice_print)
            for k in ('so_the', 'actual_phut', 'nhua_g'):
                if k in d:
                    c[k] = _toint(d.get(k)) if k != 'nhua_g' else _tofloat(d.get(k))
            break
    _save(_CMD, cmds)
    return JsonResponse({'ok': True})


# ---------------- Endpoint cho WEB (nhân viên) ----------------
@staff_required
def live(request):
    """Trang realtime poll: trạng thái máy (từ heartbeat) + lệnh gần đây + khoá agent."""
    now = time.time()
    lv = _live_load()
    macs = []
    for sn, m in (lv.get('machines') or {}).items():
        age = now - (m.get('ts') or 0)
        mm = dict(m)
        mm['online'] = bool(m.get('online')) and age <= STALE_S
        mm['age_s'] = int(age)
        macs.append(mm)
    macs.sort(key=lambda x: x.get('serial') or '')
    cmds = _load(_CMD)[-30:][::-1]
    return JsonResponse({'ok': True, 'machines': macs, 'cmds': cmds,
                         'agent_online': (now - (lv.get('ts') or 0)) <= STALE_S,
                         'agent_key': _agent_key(), 'stale_s': STALE_S})


@staff_required
def dieu_khien(request):
    """Nhân viên phát 1 lệnh cho agent: {action, may_id|serial, file_id?, plate_idx?, use_ams?, so_luong?}."""
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST'}, status=405)
    d = _body(request)
    action = d.get('action')
    if action not in CMD_ACTIONS:
        return JsonResponse({'ok': False, 'error': 'Lệnh không hợp lệ.'})
    serial = (d.get('serial') or '').strip()
    may_id = (d.get('may_id') or '').strip()
    if not serial and may_id:
        for m in _load('may.json'):
            if m.get('id') == may_id:
                serial = (m.get('serial') or '').strip()
                break
    if not serial:
        return JsonResponse({'ok': False, 'error': 'Máy chưa có Serial — thêm Serial cho máy ở tab Máy in.'})
    rec = {'id': _nid('C'), 'action': action, 'serial': serial, 'may_id': may_id,
           'status': 'cho', 'message': '', 'nguoi': getattr(request.user, 'username', '') or '',
           'luc_tao': time.strftime('%Y-%m-%d %H:%M:%S'), 'luc_xong': ''}
    if action == 'print':
        fid = (d.get('file_id') or '').strip()
        f = next((x for x in _load('file.json') if x.get('id') == fid), None)
        if not f:
            return JsonResponse({'ok': False, 'error': 'Chọn file in (.3mf/.gcode) đã tải lên.'})
        rec['file_id'] = fid
        rec['file_name'] = (f.get('ten_file') or f.get('ten') or 'print.3mf')[:120]
        rec['file_url'] = request.build_absolute_uri('/media/' + f.get('file', ''))
        rec['plate_idx'] = _toint(d.get('plate_idx')) or 1
        rec['use_ams'] = bool(d.get('use_ams'))
        rec['so_luong'] = _toint(d.get('so_luong'))
        rec['ten'] = rec['file_name']
    cmds = _load(_CMD)
    cmds.append(rec)
    _save(_CMD, cmds[-_CMD_KEEP:])
    return JsonResponse({'ok': True, 'item': rec})


@staff_required
def cmd_huy(request):
    """Huỷ 1 lệnh CHƯA chạy (cho/sent). Không xoá lệnh đã xong/lỗi (giữ lịch sử)."""
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST'}, status=405)
    rid = (_body(request).get('id') or '').strip()
    cmds = _load(_CMD)
    _save(_CMD, [c for c in cmds if not (c.get('id') == rid and c.get('status') in ('cho', 'sent'))])
    return JsonResponse({'ok': True})


@staff_required
def agent_key_moi(request):
    """Đổi khoá agent (khi lộ khoá). Agent phải cập nhật config theo khoá mới."""
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST'}, status=405)
    from pha.models import AppSetting
    k = 'ba-' + os.urandom(8).hex()
    AppSetting.set(_AGENT_KEY, k)
    return JsonResponse({'ok': True, 'agent_key': k})


def _toint(v):
    try:
        return max(0, int(float(v)))
    except Exception:
        return 0


# ============================ IN THẺ TỰ CẮT (model STL + cắt theo yêu cầu) ============================
# User có STL từng thẻ + Bambu Studio ở PC xưởng. Web quản MODEL + metadata; khi tạo "lệnh cắt",
# server gom yêu cầu (các thẻ + số lượng HOẶC thời gian mục tiêu, đầu phun 0.2/0.3, nhiệt độ) thành
# 1 command action='slice_print'. Agent tải STL, gọi Bambu Studio CLI cắt (profile theo đầu phun +
# override nhiệt + số bản + auto-arrange), ra .gcode.3mf, nạp FTPS + phát lệnh in, ack kèm thời gian/nhựa thực.
_TAGMODEL = 'tagmodel.json'
_MODELS_SUB = 'models'
DAU_PHUN = ('0.2', '0.4')


def _models_dir():
    d = os.path.join(_dir(), _MODELS_SUB)
    os.makedirs(d, exist_ok=True)
    return d


def _read_3mf_config(path):
    """Tự đọc cấu hình trong file .3mf (Metadata/project_settings.config): nhiệt vòi/bàn,
    đầu phun, loại nhựa, độ dày lớp. Không cần cắt. Lỗi/không phải .3mf -> {}."""
    import zipfile
    out = {}
    try:
        with zipfile.ZipFile(path) as z:
            cn = [n for n in z.namelist() if n.endswith('project_settings.config')]
            if not cn:
                return out
            cfg = json.loads(z.read(cn[0]).decode('utf-8', 'ignore'))

            def g1(k):
                v = cfg.get(k)
                return v[0] if isinstance(v, list) and v else v
            try:
                out['nhiet'] = int(float(g1('nozzle_temperature') or 0)) or ''
            except Exception:
                pass
            try:
                out['bed'] = int(float(g1('hot_plate_temp') or g1('textured_plate_temp') or 0)) or ''
            except Exception:
                pass
            lop = g1('layer_height')
            out['lop'] = str(lop) if lop else ''
            out['nhua_loai'] = (g1('filament_type') or '')[:20]
            nd = g1('nozzle_diameter')
            out['nozzle'] = str(nd) if nd else ''
    except Exception:
        pass
    return out


@staff_required
def tag_ds(request):
    return JsonResponse({'ok': True, 'items': _load(_TAGMODEL)})


@staff_required
def tag_them(request):
    """Tải model STL thẻ + metadata (multipart). Fields: ten, mon, dau_phun, phut, nhua, max_khay, ghi_chu."""
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST'}, status=405)
    up = request.FILES.get('file')
    ten = (request.POST.get('ten') or '').strip()
    if not up:
        return JsonResponse({'ok': False, 'error': 'Chưa chọn file model (.stl/.3mf/.step).'})
    ext = (os.path.splitext(up.name)[1] or '').lower()
    if ext not in ('.stl', '.3mf', '.step', '.stp', '.obj'):
        return JsonResponse({'ok': False, 'error': 'File model phải là .stl / .3mf / .step / .obj.'})
    if not ten:
        ten = os.path.splitext(up.name)[0]
    mid = _nid('T')
    fname = '%s_%s' % (mid, _safe(up.name))
    with open(os.path.join(_models_dir(), fname), 'wb') as f:
        for chunk in up.chunks():
            f.write(chunk)
    conf = _read_3mf_config(os.path.join(_models_dir(), fname))   # tự đọc cấu hình trong .3mf
    dp = (request.POST.get('dau_phun') or conf.get('nozzle') or '0.4').strip()
    rec = {
        'id': mid, 'ten': ten[:120], 'mon': (request.POST.get('mon') or '').strip()[:60],
        'dau_phun': dp if dp in DAU_PHUN else (dp[:6] or '0.4'),
        'file': 'print/%s/%s' % (_MODELS_SUB, fname), 'ten_file': up.name[:120], 'kieu': ext[:10],
        'kb': round(up.size / 1024.0),
        'phut': _toint(request.POST.get('phut')), 'nhua': _tofloat(request.POST.get('nhua')),
        'max_khay': _toint(request.POST.get('max_khay')),
        # tự phân tích từ .3mf (nhiệt/bàn/lớp/loại nhựa); thời gian/nhựa cần cắt mới có (để 0)
        'nhiet': conf.get('nhiet', ''), 'bed': conf.get('bed', ''),
        'lop': conf.get('lop', ''), 'nhua_loai': conf.get('nhua_loai', ''), 'so_the': 0,
        'ghi_chu': (request.POST.get('ghi_chu') or '').strip()[:300],
        'luc_them': time.strftime('%Y-%m-%d %H:%M'),
    }
    items = _load(_TAGMODEL)
    items.append(rec)
    _save(_TAGMODEL, items)
    return JsonResponse({'ok': True, 'item': rec})


@staff_required
def tag_sua(request):
    """Sửa metadata model (không đổi file). JSON: {id, ten, mon, dau_phun, phut, nhua, max_khay, ghi_chu}."""
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST'}, status=405)
    d = _body(request)
    rid = (d.get('id') or '').strip()
    items = _load(_TAGMODEL)
    for it in items:
        if it.get('id') == rid:
            if d.get('ten'):
                it['ten'] = d['ten'].strip()[:120]
            it['mon'] = (d.get('mon') or it.get('mon') or '').strip()[:60]
            if d.get('dau_phun'):
                dp = str(d['dau_phun']).strip()
                it['dau_phun'] = dp if dp in DAU_PHUN else dp[:6]
            for k in ('phut', 'max_khay', 'nhiet', 'bed'):
                if k in d:
                    it[k] = _toint(d.get(k))
            if 'nhua' in d:
                it['nhua'] = _tofloat(d.get('nhua'))
            it['ghi_chu'] = (d.get('ghi_chu') or '').strip()[:300]
            _save(_TAGMODEL, items)
            return JsonResponse({'ok': True, 'item': it})
    return JsonResponse({'ok': False, 'error': 'Không thấy model.'})


@staff_required
def tag_xoa(request):
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST'}, status=405)
    rid = (_body(request).get('id') or '').strip()
    items = _load(_TAGMODEL)
    keep = []
    for it in items:
        if it.get('id') == rid:
            try:
                os.remove(os.path.join(settings.MEDIA_ROOT, it.get('file', '')))
            except Exception:
                pass
        else:
            keep.append(it)
    _save(_TAGMODEL, keep)
    return JsonResponse({'ok': True})


@staff_required
def slice_in(request):
    """Tạo lệnh TỰ CẮT + in cho agent. JSON:
      {items:[{model_id, so_luong}], target_phut?, dau_phun?, nhiet_do?, bed_nhiet?, may_id|serial, ten?}
    - Có target_phut -> agent tự tính số lượng cho vừa thời gian (dựa phút/thẻ).
    - Các thẻ phải cùng ĐẦU PHUN; cảnh báo nếu máy đang lắp đầu phun khác."""
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST'}, status=405)
    d = _body(request)
    models = {m.get('id'): m for m in _load('file.json')}   # gộp: thẻ nằm trong Thư viện file in
    items, dp_set = [], set()
    for it in (d.get('items') or []):
        m = models.get((it.get('model_id') or '').strip())
        if not m:
            continue
        items.append({
            'model_id': m['id'], 'ten': m['ten'], 'so_luong': _toint(it.get('so_luong')),
            'stl_url': request.build_absolute_uri('/media/' + m.get('file', '')),
            'ten_file': m.get('ten_file') or (m['ten'] + '.stl'),
            'phut_moi_the': _toint(m.get('phut')), 'nhua_moi_the': _tofloat(m.get('nhua')),
            'max_khay': _toint(m.get('max_khay')),
        })
        if m.get('dau_phun'):
            dp_set.add(m['dau_phun'])
    if not items:
        return JsonResponse({'ok': False, 'error': 'Chọn ít nhất 1 thẻ.'})
    if len(dp_set) > 1:
        return JsonResponse({'ok': False, 'error': 'Các thẻ khác ĐẦU PHUN nhau (%s) — 1 lệnh chỉ 1 đầu phun.'
                             % '/'.join(sorted(dp_set))})
    dau_phun = (d.get('dau_phun') or (list(dp_set)[0] if dp_set else '0.4')).strip()
    target_phut = _toint(d.get('target_phut'))
    if not target_phut and not any(x['so_luong'] for x in items):
        return JsonResponse({'ok': False, 'error': 'Nhập số lượng thẻ, hoặc đặt thời gian in mục tiêu.'})
    serial = (d.get('serial') or '').strip()
    may_id = (d.get('may_id') or '').strip()
    canh_bao = ''
    for m in _load('may.json'):
        if (may_id and m.get('id') == may_id) or (serial and m.get('serial') == serial):
            serial = m.get('serial') or serial
            may_id = m.get('id') or may_id
            if m.get('dau_phun') and m.get('dau_phun') != dau_phun:
                canh_bao = 'Máy đang lắp đầu phun %s nhưng thẻ cần %s — đổi đầu phun trước khi in.' % (
                    m.get('dau_phun'), dau_phun)
            break
    if not serial:
        return JsonResponse({'ok': False, 'error': 'Chọn máy (có Serial) để gửi lệnh.'})
    ten = (d.get('ten') or ('Thẻ: ' + ', '.join('%s×%d' % (x['ten'], x['so_luong']) if x['so_luong'] else x['ten']
                                                 for x in items)))[:140]
    rec = {
        'id': _nid('C'), 'action': 'slice_print', 'serial': serial, 'may_id': may_id, 'ten': ten,
        'dau_phun': dau_phun, 'nhiet_do': _toint(d.get('nhiet_do')), 'bed_nhiet': _toint(d.get('bed_nhiet')),
        'target_phut': target_phut, 'items': items, 'status': 'cho', 'message': '',
        'nguoi': getattr(request.user, 'username', '') or '',
        'luc_tao': time.strftime('%Y-%m-%d %H:%M:%S'), 'luc_xong': '',
    }
    cmds = _load(_CMD)
    cmds.append(rec)
    _save(_CMD, cmds[-_CMD_KEEP:])
    return JsonResponse({'ok': True, 'item': rec, 'canh_bao': canh_bao})


def _trim_3mf_bytes(src_abs, M):
    """Tạo .3mf chỉ giữ M thẻ đầu (cắt bớt instance trong file khay) — THUẦN PYTHON, không
    cần slicer. Giữ nguyên mesh/2 màu/cấu hình; chỉ bỏ object/item/model_instance/assemble_item
    của các thẻ dư. Trả (bytes, số_thẻ_thực). Nếu M >= số thẻ có sẵn thì giữ tất cả."""
    zin = zipfile.ZipFile(src_abs)
    names = zin.namelist()
    mt = zin.read('3D/3dmodel.model').decode('utf-8')
    order = re.findall(r'<item\b[^>]*objectid="(\d+)"', mt)
    keep = set(order[:max(1, M)]) if order else set()

    def _oid(s):
        m = re.search(r'objectid="(\d+)"', s)
        return m.group(1) if m else None
    mt = re.sub(r'<item\b[^>]*/>', lambda m: (m.group(0) if _oid(m.group(0)) in keep else ''), mt)
    mt = re.sub(r'<object id="(\d+)"[^>]*>.*?</object>', lambda m: (m.group(0) if m.group(1) in keep else ''), mt, flags=re.S)
    out_members = {'3D/3dmodel.model': mt}
    if 'Metadata/model_settings.config' in names:
        ms = zin.read('Metadata/model_settings.config').decode('utf-8')
        ms = re.sub(r'<object id="(\d+)">.*?</object>', lambda m: (m.group(0) if m.group(1) in keep else ''), ms, flags=re.S)

        def _mi(m):
            o = re.search(r'key="object_id"\s+value="(\d+)"', m.group(0))
            return m.group(0) if (o and o.group(1) in keep) else ''
        ms = re.sub(r'<model_instance>.*?</model_instance>', _mi, ms, flags=re.S)
        ms = re.sub(r'<assemble_item object_id="(\d+)"[^>]*/>\s*', lambda m: (m.group(0) if m.group(1) in keep else ''), ms)
        out_members['Metadata/model_settings.config'] = ms
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as o:
        for n in names:
            if n in out_members:
                o.writestr(n, out_members[n])
            elif n == 'Metadata/slice_info.config':
                continue   # bỏ info cắt cũ; Bambu Studio sẽ tự cắt lại
            else:
                o.writestr(n, zin.read(n))
    zin.close()
    return buf.getvalue(), len(keep)


def _merge_3mf_bytes(specs):
    """TRỘN nhiều loại thẻ .3mf (mỗi file là 1 khay cùng template, chỉ khác mesh) thành 1 .3mf —
    THUẦN PYTHON, không cần slicer. Cách: giữ scaffold của file ĐẦU (các ô lưới sẵn vị trí), cắt
    tới N ô; với các ô thuộc thẻ khác thì TRÁO con trỏ mesh (p:path) sang mesh của file đó (thêm
    mesh file mới + relationship) và đổi tên ô theo tên thẻ.
    specs = [(src_abs, so_luong, ten), ...]  (phần tử đầu = base).
    Trả (bytes, tong_the, chi_tiet) với chi_tiet=[(ten, so_the_thuc), ...]."""
    base_abs = specs[0][0]
    zbase = zipfile.ZipFile(base_abs)
    names = zbase.namelist()
    mt = zbase.read('3D/3dmodel.model').decode('utf-8')
    order = re.findall(r'<item\b[^>]*objectid="(\d+)"', mt)   # thứ tự ô trên khay
    cap = len(order) or 1
    want = sum(max(1, q) for _, q, _ in specs)
    N = max(1, min(want, cap))
    keep_ids = order[:N]
    keep = set(keep_ids)

    def _oid(s):
        m = re.search(r'objectid="(\d+)"', s)
        return m.group(1) if m else None
    mt = re.sub(r'<item\b[^>]*/>', lambda m: (m.group(0) if _oid(m.group(0)) in keep else ''), mt)
    mt = re.sub(r'<object id="(\d+)"[^>]*>.*?</object>',
                lambda m: (m.group(0) if m.group(1) in keep else ''), mt, flags=re.S)

    # phân ô cho từng file (theo thứ tự chọn)
    assign, off = [], 0
    for _, q, _ in specs:
        q = max(1, q)
        assign.append(keep_ids[off:off + q] if off < N else [])
        off += q
    base_mesh_paths = sorted(set(re.findall(r'p:path="([^"]+)"', mt)))

    new_members, new_rels = {}, []
    for i in range(1, len(specs)):
        ids = assign[i] if i < len(assign) else []
        if not ids:
            continue
        if os.path.abspath(specs[i][0]) == os.path.abspath(base_abs):
            continue   # cùng file base -> mesh đã đúng, khỏi tráo
        zf = zipfile.ZipFile(specs[i][0])
        fpaths = sorted(set(re.findall(r'p:path="([^"]+)"', zf.read('3D/3dmodel.model').decode('utf-8'))))
        pmap = {}
        for k, bp in enumerate(base_mesh_paths):
            newpath = '/3D/Objects/object_mg%d_%s' % (i, os.path.basename(bp))
            try:
                data = zf.read(bp.lstrip('/'))
            except KeyError:
                data = zf.read(fpaths[k].lstrip('/')) if k < len(fpaths) else None
            if data is None:
                continue
            new_members[newpath.lstrip('/')] = data
            new_rels.append(newpath)
            pmap[bp] = newpath
        zf.close()

        def _swap(mo, pmap=pmap):
            block = mo.group(0)
            for old, new in pmap.items():
                block = block.replace('p:path="%s"' % old, 'p:path="%s"' % new)
            return block
        for oid in ids:
            mt = re.sub(r'<object id="%s"[^>]*>.*?</object>' % re.escape(oid), _swap, mt, flags=re.S)

    out_members = {'3D/3dmodel.model': mt}
    if 'Metadata/model_settings.config' in names:
        ms = zbase.read('Metadata/model_settings.config').decode('utf-8')
        ms = re.sub(r'<object id="(\d+)">.*?</object>',
                    lambda m: (m.group(0) if m.group(1) in keep else ''), ms, flags=re.S)

        def _mi(m):
            o = re.search(r'key="object_id"\s+value="(\d+)"', m.group(0))
            return m.group(0) if (o and o.group(1) in keep) else ''
        ms = re.sub(r'<model_instance>.*?</model_instance>', _mi, ms, flags=re.S)
        ms = re.sub(r'<assemble_item object_id="(\d+)"[^>]*/>\s*',
                    lambda m: (m.group(0) if m.group(1) in keep else ''), ms)
        for i, ids in enumerate(assign):
            ten = (specs[i][2] or '').strip()
            if not ten:
                continue
            for oid in ids:
                def _rename(mo, ten=ten, oid=oid):
                    return re.sub(r'(<object id="%s">\s*<metadata key="name" value=")[^"]*(")' % oid,
                                  lambda x: x.group(1) + ten + x.group(2), mo.group(0), count=1)
                ms = re.sub(r'<object id="%s">.*?</object>' % oid, _rename, ms, flags=re.S)
        out_members['Metadata/model_settings.config'] = ms

    rels_name = '3D/_rels/3dmodel.model.rels'
    if rels_name in names and new_rels:
        rl = zbase.read(rels_name).decode('utf-8')
        add = ''.join('<Relationship Target="%s" Id="rel-mg%d" '
                      'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>' % (t, k + 1)
                      for k, t in enumerate(new_rels))
        out_members[rels_name] = rl.replace('</Relationships>', add + '</Relationships>')

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as o:
        for n in names:
            if n in out_members:
                o.writestr(n, out_members[n])
            elif n == 'Metadata/slice_info.config':
                continue
            else:
                o.writestr(n, zbase.read(n))
        for p, data in new_members.items():
            o.writestr(p, data)
    zbase.close()
    chi_tiet = [(specs[i][2], len(assign[i])) for i in range(len(specs))]
    return buf.getvalue(), len(keep), chi_tiet


@staff_required
def xuat_file(request):
    """Xuất .3mf để TẢI VỀ (không cần máy in) với đúng số lượng — TRỘN được NHIỀU loại thẻ vào 1 file.
    GET nhiều dòng:  f=<id>&q=<sl>&f=<id>&q=<sl>...   (q = số lượng mỗi loại)
    GET 1 dòng (cũ): file_id=<id>&so_luong=<sl>   hoặc  file_id=<id>&target_phut=<phút>
    Các thẻ phải cùng ĐẦU PHUN (1 file in = 1 đầu phun)."""
    from django.http import HttpResponse
    from urllib.parse import quote
    lib = _load('file.json')
    fids = request.GET.getlist('f')
    qtys = request.GET.getlist('q')
    rows = []   # (id, qty)
    if fids:
        for i, fid in enumerate(fids):
            fid = (fid or '').strip()
            if fid:
                rows.append((fid, _toint(qtys[i]) if i < len(qtys) else 0))
    else:   # tương thích cũ: 1 dòng
        fid = (request.GET.get('file_id') or '').strip()
        tp = _toint(request.GET.get('target_phut'))
        sl = _toint(request.GET.get('so_luong'))
        f = next((x for x in lib if x.get('id') == fid), None)
        per = _toint(f.get('phut')) if f else 0
        q = max(1, int(round(tp / float(per)))) if (tp and per) else max(1, sl or 1)
        if fid:
            rows.append((fid, q))
    if not rows:
        return JsonResponse({'ok': False, 'error': 'Chọn ít nhất 1 thẻ.'}, status=400)

    specs, dp_set = [], set()
    for fid, q in rows:
        f = next((x for x in lib if x.get('id') == fid), None)
        if not f:
            return JsonResponse({'ok': False, 'error': 'Không thấy thẻ (%s).' % fid}, status=404)
        src = os.path.join(settings.MEDIA_ROOT, f.get('file', ''))
        if not (src.lower().endswith('.3mf') and os.path.exists(src)):
            return JsonResponse({'ok': False, 'error': 'Thẻ "%s" không phải .3mf nên không cắt/trộn được.'
                                 % f.get('ten', fid)}, status=400)
        if f.get('dau_phun'):
            dp_set.add(f['dau_phun'])
        specs.append((src, max(1, q or 1), (f.get('ten') or 'the').strip()))
    if len(dp_set) > 1:
        return JsonResponse({'ok': False, 'error': 'Các thẻ khác ĐẦU PHUN nhau (%s) — 1 file chỉ 1 đầu phun.'
                             % '/'.join(sorted(dp_set))}, status=400)
    try:
        data, got, chi_tiet = _merge_3mf_bytes(specs)
    except Exception as e:
        return JsonResponse({'ok': False, 'error': 'Lỗi tạo file: %s' % str(e)[:140]}, status=500)

    if len(specs) == 1:
        fn = '%s_%dthe.3mf' % (specs[0][2].replace(' ', '_'), got)
    else:
        fn = 'Tron_%dloai_%dthe.3mf' % (len(specs), got)
    resp = HttpResponse(data, content_type='application/octet-stream')
    resp['Content-Disposition'] = "attachment; filename*=UTF-8''%s" % quote(fn)
    resp['X-So-The'] = str(got)
    return resp


# ============================ BẢO TRÌ MÁY ============================
# Theo dõi bảo trì từng máy: danh sách BƯỚC (mỗi bước có chu kỳ ngày) + GHI bảo trì (tích
# bước đã làm) -> biết máy nào bảo trì bao giờ, bao lâu rồi chưa, bước nào quá hạn (tránh
# quên bảo trì & thiếu bước). Lưu JSON media/print (baotri_buoc.json, baotri_log.json).
_BT_BUOC, _BT_LOG = 'baotri_buoc.json', 'baotri_log.json'
_BT_MAC_DINH = [
    ('Vệ sinh mặt bàn (tấm PEI)', 3),
    ('Lau sạch trục trượt X/Y/Z', 7),
    ('Vệ sinh đầu phun + quạt làm mát', 14),
    ('Bôi trơn trục & vít me Z', 30),
    ('Kiểm tra / căng lại dây đai', 30),
    ('Vệ sinh bánh răng cấp nhựa (extruder)', 30),
    ('Kiểm tra AMS lite (bánh cuốn, cảm biến)', 30),
    ('Cân bàn + hiệu chỉnh flow', 30),
]


def _bt_buoc():
    items = _load(_BT_BUOC)
    if not items:
        items = [{'id': 'B%d' % i, 'ten': t, 'chu_ky': ck, 'ghi_chu': ''}
                 for i, (t, ck) in enumerate(_BT_MAC_DINH)]
        _save(_BT_BUOC, items)
    return items


def _days_since(iso):
    try:
        return (datetime.now() - datetime.strptime(iso[:16], '%Y-%m-%d %H:%M')).days
    except Exception:
        return None


@staff_required
def bt_buoc_ds(request):
    return JsonResponse({'ok': True, 'items': _bt_buoc()})


@staff_required
def bt_buoc_luu(request):
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST'}, status=405)
    d = _body(request)
    ten = (d.get('ten') or '').strip()
    if not ten:
        return JsonResponse({'ok': False, 'error': 'Nhập tên bước.'})
    items = _bt_buoc()
    rid = (d.get('id') or '').strip()
    rec = {'id': rid or _nid('B'), 'ten': ten[:120],
           'chu_ky': _toint(d.get('chu_ky')), 'ghi_chu': (d.get('ghi_chu') or '').strip()[:200]}
    for i, it in enumerate(items):
        if it.get('id') == rid:
            items[i] = rec
            break
    else:
        items.append(rec)
    _save(_BT_BUOC, items)
    return JsonResponse({'ok': True, 'item': rec})


@staff_required
def bt_buoc_xoa(request):
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST'}, status=405)
    rid = (_body(request).get('id') or '').strip()
    _save(_BT_BUOC, [it for it in _bt_buoc() if it.get('id') != rid])
    return JsonResponse({'ok': True})


@staff_required
def bt_ghi(request):
    """Ghi 1 lần bảo trì cho 1 máy. JSON: {may_id, buoc_ids:[...], ghi_chu}."""
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST'}, status=405)
    d = _body(request)
    may_id = (d.get('may_id') or '').strip()
    buoc_ids = [str(x) for x in (d.get('buoc_ids') or [])]
    if not may_id:
        return JsonResponse({'ok': False, 'error': 'Chọn máy.'})
    if not buoc_ids:
        return JsonResponse({'ok': False, 'error': 'Tích ít nhất 1 bước đã làm.'})
    rec = {'id': _nid('L'), 'may_id': may_id, 'buoc_ids': buoc_ids,
           'luc': time.strftime('%Y-%m-%d %H:%M'), 'nguoi': getattr(request.user, 'username', '') or '',
           'ghi_chu': (d.get('ghi_chu') or '').strip()[:300]}
    logs = _load(_BT_LOG)
    logs.append(rec)
    _save(_BT_LOG, logs[-2000:])
    return JsonResponse({'ok': True, 'item': rec})


@staff_required
def bt_log_ds(request):
    may_id = (request.GET.get('may_id') or '').strip()
    logs = _load(_BT_LOG)
    if may_id:
        logs = [l for l in logs if l.get('may_id') == may_id]
    logs = sorted(logs, key=lambda x: x.get('luc', ''), reverse=True)[:100]
    return JsonResponse({'ok': True, 'items': logs})


@staff_required
def bt_log_xoa(request):
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST'}, status=405)
    rid = (_body(request).get('id') or '').strip()
    _save(_BT_LOG, [l for l in _load(_BT_LOG) if l.get('id') != rid])
    return JsonResponse({'ok': True})


@staff_required
def bt_trangthai(request):
    """Trạng thái bảo trì từng máy: mỗi bước lần cuối làm khi nào, mấy ngày rồi, quá hạn chưa."""
    buoc = _bt_buoc()
    logs = _load(_BT_LOG)
    mays = _load('may.json')
    last = {}
    for l in logs:
        mid = l.get('may_id')
        for b in (l.get('buoc_ids') or []):
            k = (mid, str(b))
            if k not in last or l.get('luc', '') > last[k]:
                last[k] = l.get('luc', '')
    out = []
    for m in mays:
        rows, worst, last_any = [], -1, ''
        for b in buoc:
            ld = last.get((m['id'], b['id']), '')
            ds = _days_since(ld) if ld else None
            ck = b.get('chu_ky') or 0
            overdue = (ld == '') or bool(ck and ds is not None and ds >= ck)
            if ld and ld > last_any:
                last_any = ld
            qua = 9999 if ld == '' else ((ds - ck) if (ds is not None and ck) else 0)
            if overdue and qua > worst:
                worst = qua
            rows.append({'buoc_id': b['id'], 'ten': b['ten'], 'chu_ky': ck, 'last_done': ld,
                         'days_since': ds, 'overdue': overdue, 'never': ld == ''})
        out.append({'may_id': m['id'], 'ten': m.get('ten'), 'trang_thai': m.get('trang_thai'),
                    'last_any': last_any, 'days_since_any': _days_since(last_any) if last_any else None,
                    'so_qua_han': sum(1 for r in rows if r['overdue']), 'buoc': rows})
    out.sort(key=lambda x: (-x['so_qua_han'], x['ten'] or ''))
    return JsonResponse({'ok': True, 'items': out, 'buoc': buoc})
