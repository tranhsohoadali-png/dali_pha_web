# -*- coding: utf-8 -*-
"""ĐĂNG SẢN PHẨM lên tranhdali.vn qua agent.tranhdali.vn (Social Suite).

Luồng (từ /xuong-anh, mỗi ảnh đã ghép có nút "Đăng web"):
  1. chuan-bi : đẩy ảnh vào Kho tư liệu agent (POST /api/assets, multipart) +
                duyệt (POST /api/assets/{id}/approve) — idempotent, asset_id ghi
                vào sidecar; trả danh mục + khổ (kèm giá) + khổ tick sẵn theo
                HỌ TỈ LỆ của KT gốc + cảnh báo nếu MÃ đã đăng trước đó.
  2. draft    : AI bên agent viết tên + mô tả (POST /api/web/product-draft) —
                không side-effect, retry thoải mái.
  3. publish  : đăng thật (POST /api/web/publish-product) -> admin_url; ghi sổ
                media/xuong_anh/web_pub.json theo MÃ để chống đăng trùng.

Cấu hình: AGENT_BASE / AGENT_USER / AGENT_PASS — AppSetting (dán qua /cai-dat-ai,
ưu tiên) hoặc env/systemd. Agent đứng sau nginx HTTP Basic Auth.
KHÔNG model/migration; sổ + cache đặt dưới media/ (ngoài git). urllib thuần —
không thêm thư viện, deploy vẫn chỉ git pull + restart.
"""
import base64
import json
import os
import re
import time
import uuid
import urllib.error
import urllib.request
from datetime import datetime

from django.conf import settings
from django.http import JsonResponse

from pha.views import staff_required
from pha.product_studio import _dir, _read_json, _ID_RE, _OUT_SUB

_XA_SUB = 'xuong_anh'
_CATALOG_TTL = 900          # 15 phút
_catalog_mem = {'ts': 0.0, 'data': None}


# ------------------------------------------------------------------ cấu hình
def _conf():
    from pha.models import AppSetting
    base = (AppSetting.get('AGENT_BASE') or os.environ.get('AGENT_BASE')
            or 'https://agent.tranhdali.vn').strip().rstrip('/')
    user = (AppSetting.get('AGENT_USER') or os.environ.get('AGENT_USER') or '').strip()
    pw = (AppSetting.get('AGENT_PASS') or os.environ.get('AGENT_PASS') or '').strip()
    return base, user, pw


def agent_configured():
    _b, u, p = _conf()
    return bool(u and p)


class _AgentErr(Exception):
    pass


# ------------------------------------------------------- gọi HTTP (urllib thuần)
def _request(method, path, json_body=None, multipart=None, timeout=30):
    """Gọi agent kèm Basic auth. multipart = list[(field, filename, bytes, ctype)].
    Trả dict JSON; lỗi ném _AgentErr với thông điệp phân biệt 401/mạng/API."""
    base, user, pw = _conf()
    if not (user and pw):
        raise _AgentErr('Chưa cấu hình tài khoản Agent — vào /cai-dat-ai dán AGENT_USER/PASS.')
    url = base + path
    headers = {'Authorization': 'Basic ' + base64.b64encode(
        f'{user}:{pw}'.encode()).decode()}
    data = None
    if multipart is not None:
        boundary = '----dali' + uuid.uuid4().hex
        parts = []
        for field, fname, blob, ctype in multipart:
            parts.append((f'--{boundary}\r\n'
                          f'Content-Disposition: form-data; name="{field}"; filename="{fname}"\r\n'
                          f'Content-Type: {ctype}\r\n\r\n').encode() + blob + b'\r\n')
        data = b''.join(parts) + f'--{boundary}--\r\n'.encode()
        headers['Content-Type'] = f'multipart/form-data; boundary={boundary}'
    elif json_body is not None:
        data = json.dumps(json_body).encode()
        headers['Content-Type'] = 'application/json'
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        if e.code == 401:
            raise _AgentErr('Agent từ chối (401) — sai AGENT_USER/PASS, kiểm tra /cai-dat-ai.')
        try:
            msg = json.loads(e.read().decode('utf-8')).get('error') or f'HTTP {e.code}'
        except Exception:
            msg = f'HTTP {e.code}'
        raise _AgentErr(f'Agent báo lỗi: {msg}')
    except Exception as e:
        raise _AgentErr(f'Không nối được {base}: {e}')


# -------------------------------------------------------- catalog (cache 2 lớp)
def _catalog_path():
    return os.path.join(_dir(_XA_SUB), 'catalog_meta.json')


def _catalog(force=False):
    now = time.time()
    if not force and _catalog_mem['data'] and now - _catalog_mem['ts'] < _CATALOG_TTL:
        return _catalog_mem['data']
    try:
        data = _request('GET', '/api/web/catalog-meta', timeout=20)
        if not (data.get('ok') and data.get('sizes')):
            raise _AgentErr('catalog-meta trả dữ liệu lạ — agent đổi API?')
        _catalog_mem.update(ts=now, data=data)
        tmp = _catalog_path() + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, _catalog_path())
        return data
    except _AgentErr:
        cached = _read_json(_catalog_path())      # đọc chịu lỗi: agent sập vẫn chạy
        if cached:
            _catalog_mem.update(ts=now, data=cached)
            return cached
        raise


# ------------------------------------------------- khổ: parse + họ tỉ lệ + giá
_SIZE_RE = re.compile(r'(\d+(?:[.,]\d+)?)\s*[x×]\s*(\d+(?:[.,]\d+)?)')


def _parse_wh(text):
    m = _SIZE_RE.search(str(text or ''))
    if not m:
        return None
    w = float(m.group(1).replace(',', '.'))
    h = float(m.group(2).replace(',', '.'))
    if w <= 0 or h <= 0:
        return None
    return w, h


def _ratio(wh):
    return max(wh) / min(wh)


def _ticks_for_kt(kt, sizes):
    """KT gốc ('30x37.5') -> các size CÙNG HỌ TỈ LỆ (lệch r <= 0.08) được tick.
    Trả (set_id_tick, matched). KT lạ (panorama...) -> matched=False, không tick."""
    wh = _parse_wh(kt)
    if not wh:
        return set(), False
    r0 = _ratio(wh)
    ticks = set()
    for s in sizes:
        swh = _parse_wh(s.get('name'))
        if swh and abs(_ratio(swh) - r0) <= 0.08:
            ticks.add(int(s['id']))
    return ticks, bool(ticks)


# ------------------------------------------------------------ sổ đăng theo MÃ
def _reg_path():
    return os.path.join(_dir(_XA_SUB), 'web_pub.json')


def _reg():
    return _read_json(_reg_path()) or {}


def _reg_set(ma, rec):
    reg = _reg()
    reg[ma] = rec
    tmp = _reg_path() + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(reg, f, ensure_ascii=False)
    os.replace(tmp, _reg_path())


# ------------------------------------------------------------- sidecar out-*
def _out_paths(oid):
    d = _dir(_OUT_SUB)
    return os.path.join(d, oid + '.json'), os.path.join(d, oid + '.jpg')


def _save_sidecar(oid, meta):
    p = _out_paths(oid)[0]
    tmp = p + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(meta, f, ensure_ascii=False)
    os.replace(tmp, p)


def _ensure_asset(oid, meta):
    """Upload ảnh vào kho agent + duyệt. Idempotent: asset_id ghi sidecar, bấm
    lại không upload trùng; approve hỏng lần trước thì lần này chỉ approve lại."""
    aid = meta.get('agent_asset_id')
    if not aid:
        jpg = _out_paths(oid)[1]
        if not os.path.isfile(jpg):
            # bản ghi cũ 2-ảnh: dùng bản web
            jpg = os.path.join(_dir(_OUT_SUB), oid + '_web.jpg')
        if not os.path.isfile(jpg):
            raise _AgentErr('Không tìm thấy file ảnh của bản ghi này.')
        with open(jpg, 'rb') as f:
            blob = f.read()
        fname = re.sub(r'[^A-Za-z0-9._-]', '_',
                       f"{meta.get('ma') or oid}-{meta.get('kt') or ''}.jpg")
        res = _request('POST', '/api/assets', multipart=[('files', fname, blob, 'image/jpeg')],
                       timeout=60)
        # agent THẬT trả {'ok': True, 'ids': [22]} (đo 2026-07-11); các dạng khác giữ dự phòng
        aid = None
        if isinstance(res, dict) and res.get('ids'):
            aid = int(res['ids'][0])
        else:
            items = res if isinstance(res, list) else (
                res.get('assets') or res.get('added') or res.get('items') or [])
            if isinstance(items, dict):
                items = [items]
            if items and isinstance(items[0], dict) and 'id' in items[0]:
                aid = int(items[0]['id'])
        if aid is None:
            raise _AgentErr('Upload kho agent không trả id — agent đổi API?')
        meta['agent_asset_id'] = aid
        _save_sidecar(oid, meta)
    if not meta.get('agent_asset_approved'):
        # Agent xử lý ảnh BẤT ĐỒNG BỘ sau upload (AI viết title/tags): duyệt ngay
        # trả ok nhưng KHÔNG DÍNH (status vẫn pending) — đo thật 2026-07-11.
        # -> duyệt xong phải KIỂM lại status; chưa dính thì chờ 2s duyệt tiếp.
        for _ in range(5):
            _request('POST', f'/api/assets/{aid}/approve', json_body={}, timeout=15)
            if _asset_status(aid) == 'approved':
                meta['agent_asset_approved'] = True
                _save_sidecar(oid, meta)
                break
            time.sleep(2)
        else:
            raise _AgentErr('Kho agent đang xử lý ảnh, chưa duyệt được — chờ vài giây rồi bấm lại.')
    return aid


def _asset_status(aid):
    try:
        assets = _request('GET', '/api/assets', timeout=20)
        for a in (assets if isinstance(assets, list) else []):
            if int(a.get('id', -1)) == int(aid):
                return a.get('status') or ''
    except (_AgentErr, ValueError, TypeError):
        pass
    return ''


def _load_out(request):
    oid = (request.POST.get('id') or '').strip()
    if not _ID_RE.match(oid):
        raise _AgentErr('id sai')
    meta = _read_json(_out_paths(oid)[0])
    if not meta:
        raise _AgentErr('Không đọc được bản ghi ảnh đã ghép.')
    return oid, meta


# ------------------------------------------------------------------ endpoints
@staff_required
def dang_web_chuan_bi(request):
    """Bước 1: đẩy ảnh + duyệt + trả catalog/tick/giá gợi ý + cảnh báo trùng mã."""
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST only'})
    try:
        oid, meta = _load_out(request)
        aid = _ensure_asset(oid, meta)
        cat = _catalog()
        sizes = cat.get('sizes') or []
        ticks, matched = _ticks_for_kt(meta.get('kt'), sizes)
        pub = _reg().get((meta.get('ma') or '').strip()) or meta.get('published')
        # GIÁ BÁN tự theo bảng khổ trên web — KHÔNG gửi; sale_price chỉ là
        # giá KHUYẾN MÃI tuỳ chọn (web bắt phải NHỎ HƠN giá gốc).
        return JsonResponse({'ok': True, 'asset_id': aid,
                             'categories': cat.get('categories') or [],
                             'sizes': [{'id': int(s['id']), 'name': s.get('name'),
                                        'price': s.get('price') or 0,
                                        'checked': int(s['id']) in ticks} for s in sizes],
                             'suggest': {'ma': meta.get('ma') or '',
                                         'kt': meta.get('kt') or '',
                                         'colors': meta.get('colors') or ''},
                             'size_matched': matched,
                             'published': pub or None})
    except _AgentErr as e:
        return JsonResponse({'ok': False, 'error': str(e)})


@staff_required
def dang_web_draft(request):
    """Bước 2: AI bên agent viết tên + mô tả. Không side-effect — retry vô tư."""
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST only'})
    try:
        oid, meta = _load_out(request)
        aid = meta.get('agent_asset_id')
        if not aid:
            raise _AgentErr('Chưa chuẩn bị ảnh — bấm Đăng web lại từ đầu.')
        body = {'asset_ids': [int(aid)],
                'category_name': (request.POST.get('category_name') or '').strip(),
                'sizes_text': (request.POST.get('sizes_text') or '').strip(),
                'hint': f"Tranh tô màu theo số DALI, mã {meta.get('ma') or ''}, "
                        f"khổ gốc {meta.get('kt') or ''}, {meta.get('colors') or ''} màu"}
        try:
            res = _request('POST', '/api/web/product-draft', json_body=body, timeout=75)
        except _AgentErr as e:
            # lỗi thật gặp: "Ảnh không hợp lệ — cần ảnh ĐÃ DUYỆT trong kho" (HOA)
            if any(k in str(e).lower() for k in ('duyệt', 'duyet', 'hợp lệ', 'hop le')):
                meta['agent_asset_approved'] = False   # duyệt lại (có kiểm) rồi thử lại
                _ensure_asset(oid, meta)
                res = _request('POST', '/api/web/product-draft', json_body=body, timeout=75)
            else:
                raise
        draft = res.get('draft') or res
        return JsonResponse({'ok': True,
                             'draft': {'name': draft.get('name') or '',
                                       'description': draft.get('description') or '',
                                       'colors_count': draft.get('colors_count')}})
    except _AgentErr as e:
        return JsonResponse({'ok': False, 'error': str(e)})


@staff_required
def dang_web_publish(request):
    """Bước 3: đăng thật lên tranhdali.vn -> admin_url; ghi sổ chống trùng mã."""
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST only'})
    try:
        oid, meta = _load_out(request)
        aid = meta.get('agent_asset_id')
        if not aid:
            raise _AgentErr('Chưa chuẩn bị ảnh — bấm Đăng web lại từ đầu.')
        name = (request.POST.get('name') or '').strip()
        desc = (request.POST.get('description') or '').strip()
        try:
            # GIÁ BÁN tự theo bảng khổ trên web. sale_price = giá KHUYẾN MÃI,
            # TUỲ CHỌN — chỉ gửi khi có (web bắt phải NHỎ HƠN giá gốc của khổ).
            sale = int(float(request.POST.get('sale_price') or 0))
            cat_id = int(request.POST.get('category_id') or 0)
            size_ids = [int(x) for x in request.POST.getlist('size_ids')]
            colors_count = int(request.POST.get('colors_count') or 0)
        except ValueError:
            raise _AgentErr('Giá/danh mục/khổ không hợp lệ.')
        if not name:
            raise _AgentErr('Thiếu tên sản phẩm.')
        if not cat_id or not size_ids:
            raise _AgentErr('Chọn danh mục và ít nhất 1 khổ.')
        valid_ids = {int(s['id']) for s in (_catalog().get('sizes') or [])}
        size_ids = [i for i in size_ids if i in valid_ids]
        if not size_ids:
            raise _AgentErr('Khổ đã chọn không còn trong catalog — tải lại trang.')
        body = {'name': name, 'description': desc, 'colors_count': colors_count,
                'category_id': cat_id, 'size_ids': size_ids,
                'image_asset_id': int(aid)}
        if sale > 0:
            body['sale_price'] = sale
        res = _request('POST', '/api/web/publish-product', json_body=body, timeout=45)
        admin_url = res.get('admin_url') or ''
        rec = {'admin_url': admin_url, 'at': datetime.now().strftime('%Y-%m-%d %H:%M'),
               'out_id': oid, 'name': name}
        meta['published'] = rec
        _save_sidecar(oid, meta)
        ma = (meta.get('ma') or '').strip()
        if ma:
            _reg_set(ma, rec)
        return JsonResponse({'ok': True, 'admin_url': admin_url})
    except _AgentErr as e:
        return JsonResponse({'ok': False, 'error': str(e)})


@staff_required
def dang_web_cai_dat(request):
    """Cấu hình tài khoản Agent (dán qua /cai-dat-ai — không cần SSH)."""
    from pha.models import AppSetting
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST only'})
    action = request.POST.get('action')
    if action == 'save':
        base = (request.POST.get('base') or '').strip().rstrip('/')
        user = (request.POST.get('user') or '').strip()
        pw = (request.POST.get('pass') or '').strip()
        if not (user and pw):
            return JsonResponse({'ok': False, 'msg': 'Nhập đủ user + mật khẩu.'})
        if base:
            AppSetting.set('AGENT_BASE', base)
        AppSetting.set('AGENT_USER', user)
        AppSetting.set('AGENT_PASS', pw)
        return JsonResponse({'ok': True, 'msg': 'Đã lưu tài khoản Agent.'})
    if action == 'clear':
        from pha.models import AppSetting as A
        A.objects.filter(key__in=['AGENT_BASE', 'AGENT_USER', 'AGENT_PASS']).delete()
        return JsonResponse({'ok': True, 'msg': 'Đã xoá cấu hình Agent.'})
    if action == 'test':
        try:
            st = _request('GET', '/api/web/status', timeout=20)
            return JsonResponse({'ok': True, 'msg': 'Nối OK — web %s, %s sản phẩm.'
                                 % (st.get('baseUrl') or '?', st.get('productCount', '?'))})
        except _AgentErr as e:
            return JsonResponse({'ok': False, 'msg': str(e)})
    return JsonResponse({'ok': False, 'msg': 'Hành động không hợp lệ.'})
