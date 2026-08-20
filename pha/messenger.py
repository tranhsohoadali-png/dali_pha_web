# -*- coding: utf-8 -*-
"""NỐI MESSENGER THẬT (Facebook Page) — dùng API CHÍNH THỨC của Meta.

Luồng: khách nhắn Page -> Meta gọi WEBHOOK của mình -> lưu vào Hộp thư ->
(tuỳ chọn) agent tự soạn & GỬI trả lời qua Send API.

KHOÁ/TOKEN lưu trong AppSetting (nhập qua UI) hoặc biến môi trường — KHÔNG hardcode:
  MESSENGER_VERIFY_TOKEN : chuỗi TỰ ĐẶT, khai giống hệt bên Meta khi tạo webhook
  MESSENGER_PAGE_TOKEN   : Page Access Token (Meta cấp cho Fanpage)
  MESSENGER_APP_SECRET   : App Secret — để KIỂM CHỮ KÝ webhook (chống giả mạo)
  MESSENGER_AUTO         : '1' = agent tự trả lời ngay; rỗng = chỉ lưu, người bấm gửi
"""
import hashlib
import hmac
import json
import os
import urllib.error
import urllib.parse
import urllib.request

from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt

from pha import guardrail, inbox
from pha.views import staff_required

GRAPH = 'https://graph.facebook.com/v21.0'
_KEYS = ('MESSENGER_VERIFY_TOKEN', 'MESSENGER_PAGE_TOKEN',
         'MESSENGER_APP_SECRET', 'MESSENGER_AUTO')


def _cfg(key, default=''):
    """Ưu tiên AppSetting (nhập qua UI), sau đó biến môi trường."""
    try:
        from pha.models import AppSetting
        v = (AppSetting.get(key) or '').strip()
        if v:
            return v
    except Exception:
        pass
    return (os.environ.get(key) or default).strip()


def _api(path, payload=None, params=None, method=None):
    """Gọi Graph API bằng thư viện chuẩn (môi trường không có 'requests')."""
    tok = _cfg('MESSENGER_PAGE_TOKEN')
    if not tok:
        raise RuntimeError('Chưa có Page Access Token — vào /inbox/messenger để nhập.')
    q = dict(params or {})
    q['access_token'] = tok
    url = '%s/%s?%s' % (GRAPH, path.lstrip('/'), urllib.parse.urlencode(q))
    data = json.dumps(payload).encode('utf-8') if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method or ('POST' if data else 'GET'),
                                 headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode('utf-8') or '{}')
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8', 'ignore')[:400]
        raise RuntimeError('Meta báo lỗi %s: %s' % (e.code, body))


def gui_tin(psid, text):
    """GỬI tin thật tới khách qua Send API. Trả dict kết quả của Meta."""
    return _api('me/messages', {'recipient': {'id': str(psid)},
                                'messaging_type': 'RESPONSE',
                                'message': {'text': text[:1900]}})


def _ten_khach(psid):
    try:
        r = _api(str(psid), params={'fields': 'name'}, method='GET')
        return (r.get('name') or '').strip()
    except Exception:
        return ''


# ===================== WEBHOOK (Meta gọi vào — KHÔNG đăng nhập) =====================
@csrf_exempt
def webhook(request):
    # 1) Meta xác minh webhook 1 lần khi khai báo
    if request.method == 'GET':
        if (request.GET.get('hub.mode') == 'subscribe'
                and request.GET.get('hub.verify_token') == _cfg('MESSENGER_VERIFY_TOKEN')
                and _cfg('MESSENGER_VERIFY_TOKEN')):
            return HttpResponse(request.GET.get('hub.challenge', ''))
        return HttpResponse('verify token khong khop', status=403)

    # 2) KIỂM CHỮ KÝ: chỉ nhận request thật từ Meta (bắt buộc khi đã có App Secret)
    raw = request.body
    sec = _cfg('MESSENGER_APP_SECRET')
    if sec:
        sig = request.headers.get('X-Hub-Signature-256', '')
        exp = 'sha256=' + hmac.new(sec.encode('utf-8'), raw, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, exp):
            return HttpResponse('chu ky khong hop le', status=403)

    try:
        body = json.loads(raw.decode('utf-8') or '{}')
    except Exception:
        return HttpResponse('EVENT_RECEIVED')

    auto = _cfg('MESSENGER_AUTO') == '1'
    for entry in body.get('entry') or []:
        for ev in entry.get('messaging') or []:
            psid = ((ev.get('sender') or {}).get('id') or '').strip()
            msg = ev.get('message') or {}
            # Bỏ qua tin do CHÍNH PAGE gửi (echo) -> không tự nói chuyện với mình
            if not psid or msg.get('is_echo'):
                continue
            text = (msg.get('text') or '').strip()
            if not text:
                continue
            c = inbox.ghi_tin('messenger', psid, _ten_khach(psid), 'khach', text)
            if auto:
                tra_loi, _nguon = inbox.soan_tra_loi(text, c.get('tin'), c)
                if tra_loi:
                    # GUARDRAIL: chặn cam kết/giá sai/mã KM tự chế/hẹn ngày + chống loop.
                    g = guardrail.kiem(tra_loi, c, tu_dong=True)
                    if not g['cho_gui']:
                        guardrail.ghi_audit(c['id'], 'agent', 'chan', tra_loi, g['ly_do'])
                    if g['noi_dung']:
                        try:
                            gui_tin(psid, g['noi_dung'])
                            inbox.ghi_tin('messenger', psid, '', 'shop', g['noi_dung'],
                                          tu_dong=True)
                            guardrail.ghi_audit(c['id'], 'agent', 'gui', g['noi_dung'],
                                                g['ly_do'])
                        except Exception as e:
                            guardrail.ghi_audit(c['id'], 'agent', 'loi', tra_loi, str(e)[:200])
    return HttpResponse('EVENT_RECEIVED')


# ===================== GỬI TỪ HỘP THƯ (nhân viên bấm) =====================
@csrf_exempt
@staff_required
def api_gui(request):
    """Gửi câu trả lời tới khách Messenger từ màn hình Hộp thư."""
    cid = (request.POST.get('id') or '').strip()
    nd = (request.POST.get('noi_dung') or '').strip()
    c = inbox._load(cid)
    if not c or not nd:
        return JsonResponse({'ok': False, 'error': 'Thiếu hội thoại hoặc nội dung.'})
    if c.get('kenh') != 'messenger' or not c.get('ngoai_id'):
        return JsonResponse({'ok': False, 'error': 'Hội thoại này không phải Messenger.'})
    try:
        gui_tin(c['ngoai_id'], nd)
    except Exception as e:
        return JsonResponse({'ok': False, 'error': str(e)[:300]})
    c = inbox.ghi_tin('messenger', c['ngoai_id'], '', 'shop', nd)
    guardrail.ghi_audit(c['id'], 'nguoi', 'gui', nd)
    return JsonResponse({'ok': True, 'hoi_thoai': c})


# ===================== MÀN HÌNH CÀI ĐẶT =====================
@csrf_exempt
@staff_required
def cai_dat(request):
    from pha.models import AppSetting
    msg = ''
    if request.method == 'POST':
        for k in _KEYS:
            if k in request.POST:
                AppSetting.set(k, (request.POST.get(k) or '').strip())
        msg = 'Đã lưu.'
    cfg = {k: _cfg(k) for k in _KEYS}
    # Che bớt token khi hiển thị (không lộ toàn bộ trên màn hình)
    hien = {k: (v[:6] + '…' + v[-4:] if len(v) > 14 else v) for k, v in cfg.items()}
    trang_thai = 'Chưa nối'
    if cfg['MESSENGER_PAGE_TOKEN']:
        try:
            r = _api('me', params={'fields': 'name,id'}, method='GET')
            trang_thai = 'Đã nối Page: %s (id %s)' % (r.get('name', '?'), r.get('id', '?'))
        except Exception as e:
            trang_thai = 'Lỗi token: ' + str(e)[:200]
    return render(request, 'messenger.html',
                  {'cfg': cfg, 'hien': hien, 'msg': msg, 'trang_thai': trang_thai,
                   'webhook_url': request.build_absolute_uri('/inbox/webhook/messenger')})
