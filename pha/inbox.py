# -*- coding: utf-8 -*-
"""HỘP THƯ HỢP NHẤT (Messenger / Zalo / TikTok / Shopee) + AGENT TỰ TRẢ LỜI.

Theo quy ước dự án: KHÔNG model/migration — lưu JSON dưới MEDIA_ROOT/inbox
(media/ đã .gitignore nên git pull không bao giờ đè dữ liệu chạy thật).

CÁCH NỐI KÊNH:
  - Messenger / Zalo OA / TikTok Shop: đăng nhập CHÍNH THỨC (OAuth) -> có token ->
    agent ĐỌC và GỬI tin thật. Token đọc từ biến môi trường / AppSetting, KHÔNG hardcode.
  - Shopee: chưa mở Open API cho shop này -> chế độ DÁN: nhân viên dán tin khách,
    agent soạn câu trả lời, bấm copy dán lại. KHÔNG dùng bot đăng nhập giả lập
    (vi phạm điều khoản nền tảng, rủi ro khoá shop).
"""
import json
import os
import re
import uuid
from datetime import datetime

from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt

from pha.views import staff_required

_SUB = 'inbox'
KENH = {'messenger': 'Messenger', 'zalo': 'Zalo', 'tiktok': 'TikTok', 'shopee': 'Shopee'}


def _dir():
    d = os.path.join(settings.MEDIA_ROOT, _SUB)
    os.makedirs(d, exist_ok=True)
    return d


def _path(cid):
    return os.path.join(_dir(), re.sub(r'[^0-9a-zA-Z_-]', '', cid or '')[:64] + '.json')


def _load(cid):
    try:
        with open(_path(cid), encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None


def _save(c):
    with open(_path(c['id']), 'w', encoding='utf-8') as f:
        json.dump(c, f, ensure_ascii=False, indent=1)


def _all():
    out = []
    for fn in sorted(os.listdir(_dir())):
        if not fn.endswith('.json') or fn.startswith('_'):
            continue
        try:
            with open(os.path.join(_dir(), fn), encoding='utf-8') as f:
                c = json.load(f)
            last = (c.get('tin') or [{}])[-1]
            out.append({'id': c['id'], 'kenh': c.get('kenh', ''), 'ten': c.get('ten', ''),
                        'cuoi': (last.get('noi_dung') or '')[:60],
                        'luc': last.get('luc', ''),
                        'chua_tra_loi': bool(c.get('chua_tra_loi'))})
        except Exception:
            continue
    out.sort(key=lambda x: x.get('luc') or '', reverse=True)
    return out


# ================= BỘ NÃO AGENT: kịch bản chủ shop + mẫu sẵn =================
def _kb_path():
    return os.path.join(_dir(), '_kich_ban.json')


def _kich_ban():
    try:
        with open(_kb_path(), encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {'quy_tac': [], 'chu_ky': ''}


def soan_tra_loi(cau_hoi, lich_su=None, hoi_thoai=None):
    """Soạn câu trả lời cho tin của khách.

    1) AGENT AI (đọc hiểu cả hội thoại + kho tri thức) — nếu đã cấu hình khoá AI.
    2) Không có khoá / AI lỗi -> lùi về KỊCH BẢN từ khoá của chủ shop.
    3) Cuối cùng là mẫu sẵn (giá / khổ / thời gian / chào).
    Trả (nội_dung, nguồn). '' = agent không trả lời được -> để người xử lý.
    """
    try:
        from pha import agent_ai
        if agent_ai.co_ai():
            nd, nguon, chuyen = agent_ai.tra_loi(cau_hoi, hoi_thoai or {'tin': lich_su or []})
            if nd and not chuyen:
                return nd, nguon
            if chuyen:
                return '', 'agent AI: cần người xử lý'
    except Exception:
        pass
    q = (cau_hoi or '').lower().strip()
    kb = _kich_ban()
    for r in kb.get('quy_tac') or []:
        keys = [k.strip().lower() for k in (r.get('tu_khoa') or '').split(',') if k.strip()]
        if keys and any(k in q for k in keys):
            return (r.get('tra_loi') or '').strip(), 'kịch bản: ' + (r.get('ten') or '')
    chu_ky = (kb.get('chu_ky') or '').strip()

    def _r(txt, nguon):
        return (txt + ('\n' + chu_ky if chu_ky else '')), nguon

    if re.search(r'gi[áa]\b|bao nhi[êe]u|nhi[êe]u ti[êe]n|m[áa]y ti[êe]n', q):
        return _r('Dạ bên em báo giá theo khổ tranh ạ. Anh/chị cho em xin khổ muốn làm '
                  '(vd 40x50, 50x70) em báo giá và thời gian làm ngay ạ.', 'mẫu: hỏi giá')
    if re.search(r'kh[ổo]\b|k[íi]ch th[uư][ớơ]c', q):
        return _r('Dạ bên em làm các khổ phổ biến: 30x40, 40x50, 50x70, 60x80 và khổ lớn '
                  'theo yêu cầu ạ. Anh/chị muốn khổ nào để em báo giá ạ?', 'mẫu: hỏi khổ')
    if re.search(r'bao l[âa]u|m[áa]y ng[àa]y|giao h[àa]ng|ship|nh[ậa]n h[àa]ng', q):
        return _r('Dạ tranh làm xong trong 2-3 ngày, giao toàn quốc 2-4 ngày tuỳ khu vực ạ.',
                  'mẫu: thời gian')
    if re.search(r'^(hi|hello|alo|ch[àa]o|em [ơo]i)', q):
        return _r('Dạ em chào anh/chị ạ, em có thể hỗ trợ gì cho mình ạ?', 'mẫu: chào')
    return '', ''


def ghi_tin(kenh, ngoai_id, ten, ai, noi_dung, tu_dong=False):
    """Ghi 1 tin từ KÊNH NGOÀI (Messenger/Zalo/TikTok) vào hộp thư. 'ngoai_id' là ID
    người dùng trên nền tảng (vd PSID của Messenger) -> mỗi người 1 hội thoại cố định.
    Trả hội thoại đã cập nhật."""
    cid = ('%s_%s' % (kenh, re.sub(r'[^0-9a-zA-Z]', '', str(ngoai_id))))[:60]
    c = _load(cid)
    if not c:
        c = {'id': cid, 'kenh': kenh if kenh in KENH else 'messenger',
             'ten': (ten or 'Khách')[:60], 'ngoai_id': str(ngoai_id), 'tin': [],
             'tao_luc': datetime.now().strftime('%Y-%m-%d %H:%M'), 'chua_tra_loi': False}
    elif ten and c.get('ten') in ('', 'Khách'):
        c['ten'] = ten[:60]
    c.setdefault('tin', []).append({'ai': ('khach' if ai == 'khach' else 'shop'),
                                    'noi_dung': noi_dung, 'tu_dong': bool(tu_dong),
                                    'luc': datetime.now().strftime('%Y-%m-%d %H:%M')})
    c['chua_tra_loi'] = (ai == 'khach')
    _save(c)
    return c


# ================= MÀN HÌNH + API =================
@csrf_exempt
@staff_required
def inbox(request):
    return render(request, 'inbox.html', {'kenh_json': json.dumps(KENH, ensure_ascii=False)})


@csrf_exempt
@staff_required
def api_list(request):
    return JsonResponse({'ok': True, 'items': _all(), 'kenh': KENH})


@csrf_exempt
@staff_required
def api_mo(request):
    c = _load(request.GET.get('id') or '')
    if not c:
        return JsonResponse({'ok': False, 'error': 'Không tìm thấy hội thoại.'})
    return JsonResponse({'ok': True, 'hoi_thoai': c})


@csrf_exempt
@staff_required
def api_tao(request):
    kenh = (request.POST.get('kenh') or 'shopee').strip()
    ten = (request.POST.get('ten') or 'Khách').strip()[:60]
    c = {'id': uuid.uuid4().hex[:12], 'kenh': kenh if kenh in KENH else 'shopee',
         'ten': ten, 'tin': [],
         'tao_luc': datetime.now().strftime('%Y-%m-%d %H:%M'), 'chua_tra_loi': False}
    _save(c)
    return JsonResponse({'ok': True, 'id': c['id'], 'hoi_thoai': c})


@csrf_exempt
@staff_required
def api_them_tin(request):
    """Thêm tin nhắn. ai='khach' (dán tin khách) | 'shop' (câu trả lời đã gửi)."""
    c = _load(request.POST.get('id') or '')
    if not c:
        return JsonResponse({'ok': False, 'error': 'Không tìm thấy hội thoại.'})
    ai = 'khach' if (request.POST.get('ai') or 'khach') == 'khach' else 'shop'
    nd = (request.POST.get('noi_dung') or '').strip()
    if not nd:
        return JsonResponse({'ok': False, 'error': 'Nội dung trống.'})
    c.setdefault('tin', []).append({'ai': ai, 'noi_dung': nd,
                                    'luc': datetime.now().strftime('%Y-%m-%d %H:%M')})
    c['chua_tra_loi'] = (ai == 'khach')
    _save(c)
    goi_y, nguon = soan_tra_loi(nd, c.get('tin'), c) if ai == 'khach' else ('', '')
    return JsonResponse({'ok': True, 'hoi_thoai': c, 'goi_y': goi_y, 'nguon': nguon})


@csrf_exempt
@staff_required
def api_goi_y(request):
    c = _load(request.POST.get('id') or request.GET.get('id') or '')
    if not c:
        return JsonResponse({'ok': False, 'error': 'Không tìm thấy hội thoại.'})
    cuoi = ''
    for t in reversed(c.get('tin') or []):
        if t.get('ai') == 'khach':
            cuoi = t.get('noi_dung') or ''
            break
    nd, nguon = soan_tra_loi(cuoi, c.get('tin'), c)
    if not nd:
        return JsonResponse({'ok': False,
                             'error': 'Chưa có mẫu phù hợp — thêm kịch bản để agent trả lời câu này.'})
    return JsonResponse({'ok': True, 'goi_y': nd, 'nguon': nguon})


@csrf_exempt
@staff_required
def api_kich_ban(request):
    if request.method == 'POST':
        try:
            kb = json.loads(request.POST.get('data') or '{}')
        except Exception:
            return JsonResponse({'ok': False, 'error': 'Dữ liệu không hợp lệ.'})
        with open(_kb_path(), 'w', encoding='utf-8') as f:
            json.dump(kb, f, ensure_ascii=False, indent=1)
        return JsonResponse({'ok': True})
    return JsonResponse({'ok': True, 'data': _kich_ban()})
