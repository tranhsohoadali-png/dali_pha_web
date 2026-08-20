# -*- coding: utf-8 -*-
"""AGENT CSKH THẬT SỰ — đọc hiểu cả hội thoại rồi tự nghĩ câu trả lời (LLM).

Khác bot từ khoá: bot chỉ khớp chữ, gặp câu lạ là chịu. Agent này đọc 8 tin gần
nhất + KHO TRI THỨC của shop rồi viết câu trả lời như nhân viên thật.

Thứ tự an toàn (không bỏ bước nào):
  tin khách -> nạp ngữ cảnh -> LLM viết câu -> GUARDRAIL -> gửi
Không có khoá AI / LLM lỗi -> tự lùi về kịch bản từ khoá (không bao giờ đứng im).

Khoá AI dùng CHUNG với phần xử lý ảnh (AppSetting: OPENAI_API_KEY / GOOGLE_API_KEY).
"""
import json
import os
import re
import urllib.error
import urllib.request

from django.conf import settings

_SUB = 'inbox'
MODEL_OPENAI = 'gpt-4o-mini'
MODEL_GEMINI = 'gemini-2.0-flash'

SYSTEM = """Bạn là nhân viên chăm sóc khách hàng của DALI — thương hiệu tranh tô màu theo số.
Xưng "em", gọi khách "anh/chị". Viết ngắn, ấm, tự nhiên như người thật. Tối đa 1 emoji.

QUY TẮC TUYỆT ĐỐI (vi phạm là gây thiệt hại thật cho shop):
- CHỈ dùng dữ kiện có trong KHO TRI THỨC và LỊCH SỬ bên dưới. Không có thì nói thật là
  em cần kiểm tra lại, KHÔNG bịa.
- KHÔNG tự tạo mã giảm giá. KHÔNG hứa ngày giao cụ thể. KHÔNG xác nhận hoàn tiền / đổi
  trả / đền bù — việc đó do chủ shop quyết.
- Chỉ nói giá có trong kho tri thức. Không nhớ giá thì hỏi lại khổ tranh khách muốn.
- Khách khiếu nại (tranh rách, thiếu màu, sai hàng): xin lỗi, xin ảnh, nói sẽ báo bộ phận
  phụ trách xử lý — và đặt chuyen_nguoi=true.
- Không chắc chắn -> đặt chuyen_nguoi=true, đừng đoán bừa.

Trả về DUY NHẤT một JSON:
{"tra_loi": "câu nhắn gửi khách", "chuyen_nguoi": false, "ly_do": ""}"""


def _dir():
    d = os.path.join(settings.MEDIA_ROOT, _SUB)
    os.makedirs(d, exist_ok=True)
    return d


# ============================ KHO TRI THỨC ============================
def _kb_path():
    return os.path.join(_dir(), '_kho_tri_thuc.json')


def kho_tri_thuc():
    """[{tieu_de, noi_dung}] — chủ shop tự viết, đây là thứ quyết định chất lượng."""
    try:
        with open(_kb_path(), encoding='utf-8') as f:
            d = json.load(f)
            return d if isinstance(d, list) else (d.get('muc') or [])
    except Exception:
        return []


def luu_kho(muc):
    with open(_kb_path(), 'w', encoding='utf-8') as f:
        json.dump(muc, f, ensure_ascii=False, indent=1)


def _tach_tu(s):
    return [w for w in re.split(r'[^0-9a-zA-ZÀ-ỹ]+', (s or '').lower()) if len(w) > 1]


def tim_kb(cau_hoi, n=5):
    """Tìm mục liên quan bằng chấm điểm từ khoá (đủ dùng cho ~150 mục, không cần vector DB)."""
    muc = kho_tri_thuc()
    if not muc:
        return []
    tu = set(_tach_tu(cau_hoi))
    if not tu:
        return muc[:n]
    diem = []
    for m in muc:
        kho = set(_tach_tu((m.get('tieu_de') or '') + ' ' + (m.get('noi_dung') or '')))
        d = len(tu & kho)
        if d:
            diem.append((d, m))
    diem.sort(key=lambda x: -x[0])
    return [m for _d, m in diem[:n]]


# ============================ GỌI LLM ============================
def _key(prov):
    try:
        from pha.models import AppSetting
        v = (AppSetting.get(prov) or '').strip()
        if v:
            return v
    except Exception:
        pass
    return (os.environ.get(prov) or '').strip()


def co_ai():
    return bool(_key('OPENAI_API_KEY') or _key('GOOGLE_API_KEY'))


def _post_json(url, payload, headers, timeout=25):
    req = urllib.request.Request(url, data=json.dumps(payload).encode('utf-8'),
                                 headers=headers, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode('utf-8') or '{}')
    except urllib.error.HTTPError as e:
        raise RuntimeError('%s: %s' % (e.code, e.read().decode('utf-8', 'ignore')[:300]))


def _goi_llm(system, user):
    """Gọi OpenAI trước, không có thì Gemini. Trả chuỗi text."""
    k = _key('OPENAI_API_KEY')
    if k:
        r = _post_json('https://api.openai.com/v1/chat/completions',
                       {'model': _model('AGENT_MODEL', MODEL_OPENAI),
                        'messages': [{'role': 'system', 'content': system},
                                     {'role': 'user', 'content': user}],
                        'temperature': 0.3,
                        'response_format': {'type': 'json_object'}},
                       {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + k})
        return ((r.get('choices') or [{}])[0].get('message') or {}).get('content') or ''
    k = _key('GOOGLE_API_KEY')
    if k:
        url = ('https://generativelanguage.googleapis.com/v1beta/models/%s:generateContent?key=%s'
               % (_model('AGENT_MODEL_GEMINI', MODEL_GEMINI), k))
        r = _post_json(url, {'systemInstruction': {'parts': [{'text': system}]},
                             'contents': [{'parts': [{'text': user}]}],
                             'generationConfig': {'temperature': 0.3,
                                                  'responseMimeType': 'application/json'}},
                       {'Content-Type': 'application/json'})
        cand = (r.get('candidates') or [{}])[0]
        return ''.join(p.get('text', '') for p in ((cand.get('content') or {}).get('parts') or []))
    raise RuntimeError('Chưa cấu hình khoá AI (OPENAI_API_KEY hoặc GOOGLE_API_KEY).')


def _model(key, mac_dinh):
    try:
        from pha.models import AppSetting
        return (AppSetting.get(key) or '').strip() or mac_dinh
    except Exception:
        return mac_dinh


# ============================ SOẠN CÂU TRẢ LỜI ============================
def _ngu_canh(hoi_thoai, cau_hoi):
    """Dựng phần ngữ cảnh đưa cho LLM: lịch sử + kho tri thức + bảng giá đã duyệt."""
    tin = (hoi_thoai or {}).get('tin') or []
    lich_su = []
    for t in tin[-8:]:
        ai = 'Khách' if t.get('ai') == 'khach' else 'Shop'
        lich_su.append('%s: %s' % (ai, t.get('noi_dung') or ''))
    kb = tim_kb(cau_hoi, 5)
    kb_txt = '\n'.join('- %s: %s' % (m.get('tieu_de') or '', m.get('noi_dung') or '') for m in kb)
    try:
        from pha import guardrail
        gia = ', '.join(guardrail.cau_hinh().get('gia_duyet') or [])
    except Exception:
        gia = ''
    return ('KHO TRI THỨC (chỉ được dùng dữ kiện trong đây):\n%s\n\n'
            'BẢNG GIÁ ĐÃ DUYỆT (đồng): %s\n\n'
            'LỊCH SỬ HỘI THOẠI:\n%s\n\n'
            'TIN MỚI NHẤT CỦA KHÁCH: %s'
            % (kb_txt or '(chưa có mục nào — hãy nói cần kiểm tra lại)',
               gia or '(chưa khai)', '\n'.join(lich_su) or '(chưa có)', cau_hoi))


def tra_loi(cau_hoi, hoi_thoai=None):
    """Agent đọc ngữ cảnh và tự viết câu trả lời.

    Trả (noi_dung, nguon, chuyen_nguoi). noi_dung='' nghĩa là agent không trả lời được.
    """
    try:
        raw = _goi_llm(SYSTEM, _ngu_canh(hoi_thoai, cau_hoi))
    except Exception as e:
        return '', 'lỗi AI: ' + str(e)[:120], False
    txt = (raw or '').strip()
    m = re.search(r'\{.*\}', txt, re.S)
    if m:
        try:
            d = json.loads(m.group(0))
            return ((d.get('tra_loi') or '').strip(),
                    'agent AI', bool(d.get('chuyen_nguoi')))
        except Exception:
            pass
    return txt[:900], 'agent AI (thô)', False


# ============================ MÀN HÌNH KHO TRI THỨC ============================
from django.http import JsonResponse                                    # noqa: E402
from django.shortcuts import render                                     # noqa: E402
from django.views.decorators.csrf import csrf_exempt                    # noqa: E402


@csrf_exempt
def man_hinh_kb(request):
    from pha.views import staff_required as _sr
    return _sr(_kb_view)(request)


def _kb_view(request):
    msg = ''
    if request.method == 'POST':
        try:
            muc = json.loads(request.POST.get('data') or '[]')
            luu_kho([m for m in muc if (m.get('tieu_de') or m.get('noi_dung'))])
            msg = 'Đã lưu %d mục.' % len(kho_tri_thuc())
        except Exception as e:
            msg = 'Lỗi: %s' % str(e)[:120]
    return render(request, 'kho_tri_thuc.html', {
        'muc_json': json.dumps(kho_tri_thuc(), ensure_ascii=False),
        'so_muc': len(kho_tri_thuc()), 'msg': msg, 'co_ai': co_ai(),
        'model': _model('AGENT_MODEL', MODEL_OPENAI) if _key('OPENAI_API_KEY') else (
            _model('AGENT_MODEL_GEMINI', MODEL_GEMINI) if _key('GOOGLE_API_KEY') else ''),
    })


@csrf_exempt
def api_thu_agent(request):
    """Thử hỏi agent một câu (không gửi cho khách) — để chấm điểm trước khi bật thật."""
    from pha.views import staff_required as _sr

    def _v(r):
        q = (r.POST.get('cau_hoi') or '').strip()
        if not q:
            return JsonResponse({'ok': False, 'error': 'Chưa nhập câu hỏi.'})
        nd, nguon, chuyen = tra_loi(q, {'tin': []})
        kq = {'ok': True, 'tra_loi': nd, 'nguon': nguon, 'chuyen_nguoi': chuyen}
        try:
            from pha import guardrail
            g = guardrail.kiem(nd or '', None, tu_dong=True)
            kq['guardrail'] = {'cho_gui': g['cho_gui'], 'ly_do': g['ly_do'],
                               'se_gui': g['noi_dung']}
        except Exception:
            pass
        return JsonResponse(kq)
    return _sr(_v)(request)
