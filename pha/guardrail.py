# -*- coding: utf-8 -*-
"""GUARDRAIL + AUDIT LOG cho agent CSKH.

Mục đích: agent trả lời khách 24/7 nhưng KHÔNG được tự ý cam kết thay chủ shop.
Kiểm bằng LUẬT (regex/rule), không dùng AI kiểm AI — luật thì đọc được, sửa được,
và không tự đổi ý.

Vi phạm -> KHÔNG gửi câu của agent, thay bằng CÂU GIỮ NHỊP (khách vẫn được phản hồi
trong vài giây) + ghi alert để người xử lý.

AUDIT LOG: mọi câu agent gửi/bị chặn đều ghi vào media/inbox/_audit.jsonl. Khi khách
nói "shop hứa vậy mà", đây là chỗ duy nhất tra được agent đã nói gì lúc mấy giờ.
"""
import json
import os
import re
from datetime import datetime, timedelta

from django.conf import settings

_SUB = 'inbox'

# Câu dùng khi bị chặn: vẫn phản hồi ngay, nhưng không hứa gì.
CAU_GIU_NHIP = ('Dạ em đã ghi nhận thông tin của anh/chị ạ. Việc này em xin phép kiểm tra '
                'lại cho chính xác rồi phản hồi anh/chị ngay ạ.')

# 1) Từ khoá CAM KẾT — agent không được tự quyết
_CAM_KET = re.compile(
    r'ho[àa]n\s*ti[ềe]n|ho[àa]n\s*l[ạa]i\s*ti[ềe]n|b[ồo]i\s*th[ưu][ờo]ng|\b[đd][ềe]n\s*b[ùu]\b'
    r'|[đd][ổo]i\s*tr[ảa]\s*mi[ễe]n\s*ph[íi]|b[ảa]o\s*[đd][ảa]m\s*100|cam\s*k[ếe]t'
    r'|ch[ắa]c\s*ch[ắa]n\s*(?:s[ẽe]\s*)?(?:giao|nh[ậa]n|c[óo])|h[ứu]a\s*(?:s[ẽe]\s*)?ho[àa]n',
    re.I)

# 2) Mã giảm giá: chuỗi kiểu MÃ/CODE viết hoa-số
_MA_KM = re.compile(r'\b(?:m[ãa]|code)\s*[:\-]?\s*([A-Z0-9]{4,15})\b')

# 3) Số tiền (vnđ) trong câu trả lời
_TIEN = re.compile(r'(\d{1,3}(?:[.,]\d{3}){1,3}|\d{2,4})\s*(?:k\b|ngh[ìi]n|ng[àa]n|[đd]\b|vn[đd]|VND)', re.I)

# 4) Ngày giao cụ thể
_NGAY = re.compile(r'(?:giao|nh[ậa]n)\s*(?:h[àa]ng\s*)?(?:v[àa]o\s*)?'
                   r'(?:ng[àa]y\s*)?(\d{1,2}[/\-]\d{1,2})', re.I)

MAX_DAI = 600            # ký tự
MAX_AUTO_10P = 5         # tin tự động / 10 phút / 1 khách
MAX_KHACH_LIEN_TIEP = 3  # khách nhắn liên tiếp mà chưa được giải quyết


def _dir():
    d = os.path.join(settings.MEDIA_ROOT, _SUB)
    os.makedirs(d, exist_ok=True)
    return d


def _cfg_path():
    return os.path.join(_dir(), '_guardrail.json')


def cau_hinh():
    """Bảng giá & mã KM ĐÃ DUYỆT — agent chỉ được nhắc những con số/mã trong đây."""
    try:
        with open(_cfg_path(), encoding='utf-8') as f:
            c = json.load(f)
    except Exception:
        c = {}
    c.setdefault('bat', True)               # bật guardrail
    c.setdefault('gia_duyet', [])           # ['299000', '399000', ...]
    c.setdefault('ma_km_duyet', [])         # ['DALI10', ...]
    c.setdefault('cau_giu_nhip', CAU_GIU_NHIP)
    return c


def luu_cau_hinh(c):
    with open(_cfg_path(), 'w', encoding='utf-8') as f:
        json.dump(c, f, ensure_ascii=False, indent=1)


def _chuan_so(s):
    return re.sub(r'[^\d]', '', s or '')


# ============================== AUDIT LOG ==============================
def _audit_path():
    return os.path.join(_dir(), '_audit.jsonl')


def ghi_audit(hoi_thoai_id, actor, hanh_dong, noi_dung, ly_do=''):
    """actor: 'agent' | 'nguoi'. hanh_dong: 'gui' | 'chan' | 'goi_y'."""
    rec = {'luc': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
           'hoi_thoai': hoi_thoai_id, 'actor': actor, 'hanh_dong': hanh_dong,
           'noi_dung': (noi_dung or '')[:1000], 'ly_do': ly_do}
    try:
        with open(_audit_path(), 'a', encoding='utf-8') as f:
            f.write(json.dumps(rec, ensure_ascii=False) + '\n')
    except Exception:
        pass
    return rec


def doc_audit(n=200, hoi_thoai_id=None):
    out = []
    try:
        with open(_audit_path(), encoding='utf-8') as f:
            for line in f:
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if hoi_thoai_id and r.get('hoi_thoai') != hoi_thoai_id:
                    continue
                out.append(r)
    except Exception:
        return []
    return out[-n:][::-1]


# ============================== GUARDRAIL ==============================
def _dem_auto_gan_day(hoi_thoai, phut=10):
    """Đếm tin TỰ ĐỘNG shop đã gửi trong 'phut' phút gần nhất (chống loop)."""
    moc = datetime.now() - timedelta(minutes=phut)
    n = 0
    for t in (hoi_thoai or {}).get('tin') or []:
        if t.get('ai') != 'shop' or not t.get('tu_dong'):
            continue
        try:
            if datetime.strptime(t.get('luc', ''), '%Y-%m-%d %H:%M') >= moc:
                n += 1
        except Exception:
            continue
    return n


def _khach_lien_tiep(hoi_thoai):
    """Số tin KHÁCH liên tiếp ở cuối hội thoại (chưa được shop trả lời xen giữa)."""
    n = 0
    for t in reversed((hoi_thoai or {}).get('tin') or []):
        if t.get('ai') == 'khach':
            n += 1
        else:
            break
    return n


def kiem(noi_dung, hoi_thoai=None, tu_dong=True):
    """Kiểm câu trả lời TRƯỚC KHI GỬI.

    Trả dict:
      cho_gui   : True/False  — có được gửi câu này không
      noi_dung  : câu sẽ gửi (đã rút gọn, hoặc câu giữ nhịp nếu bị chặn)
      ly_do     : vì sao chặn ('' nếu không chặn)
      canh_bao  : True nếu cần báo người xử lý
    tu_dong=False (người bấm gửi): KHÔNG chặn — người có quyền quyết — chỉ ghi nhận.
    """
    c = cau_hinh()
    txt = (noi_dung or '').strip()
    kq = {'cho_gui': True, 'noi_dung': txt, 'ly_do': '', 'canh_bao': False}
    if not txt:
        return {'cho_gui': False, 'noi_dung': '', 'ly_do': 'Nội dung trống', 'canh_bao': False}
    if not c.get('bat') or not tu_dong:
        return kq

    def chan(ly_do):
        return {'cho_gui': False, 'noi_dung': c.get('cau_giu_nhip') or CAU_GIU_NHIP,
                'ly_do': ly_do, 'canh_bao': True}

    # (a) CHỐNG LOOP: 1 khách nhận quá nhiều tin tự động trong 10 phút
    if _dem_auto_gan_day(hoi_thoai) >= MAX_AUTO_10P:
        return {'cho_gui': False, 'noi_dung': '', 'canh_bao': True,
                'ly_do': 'Đã gửi %d tin tự động trong 10 phút — dừng, chuyển người.' % MAX_AUTO_10P}

    # (b) Khách nhắn liên tiếp nhiều lần mà agent chưa giải quyết -> chuyển người
    if _khach_lien_tiep(hoi_thoai) >= MAX_KHACH_LIEN_TIEP:
        return {'cho_gui': False, 'noi_dung': '', 'canh_bao': True,
                'ly_do': 'Khách nhắn %d lần liên tiếp — chuyển người.' % MAX_KHACH_LIEN_TIEP}

    # (c) Từ khoá CAM KẾT (hoàn tiền, đền bù, cam kết, chắc chắn ngày giao...)
    m = _CAM_KET.search(txt)
    if m:
        return chan('Câu có cam kết "%s" — chỉ người được quyết.' % m.group(0))

    # (d) Mã giảm giá không nằm trong danh sách đã duyệt
    for ma in _MA_KM.findall(txt):
        if ma.upper() not in [x.upper() for x in (c.get('ma_km_duyet') or [])]:
            return chan('Mã giảm giá "%s" chưa được duyệt.' % ma)

    # (e) Số tiền không khớp bảng giá đã duyệt (chỉ kiểm khi đã khai bảng giá)
    gia_ok = [_chuan_so(g) for g in (c.get('gia_duyet') or []) if _chuan_so(g)]
    if gia_ok:
        for so in _TIEN.findall(txt):
            s = _chuan_so(so)
            if len(s) <= 3:                       # '299k' -> 299 -> quy ra nghìn
                s = s + '000'
            if s and s not in gia_ok:
                return chan('Số tiền %s không khớp bảng giá đã duyệt.' % so)

    # (f) Hứa ngày giao cụ thể
    m = _NGAY.search(txt)
    if m:
        return chan('Câu hẹn ngày giao cụ thể (%s) — phải theo dữ liệu vận đơn.' % m.group(1))

    # (g) Quá dài -> tự rút gọn (không chặn)
    if len(txt) > MAX_DAI:
        cut = txt[:MAX_DAI]
        cut = cut[:cut.rfind('.') + 1] if '.' in cut[-120:] else cut
        kq['noi_dung'] = cut.strip()
        kq['ly_do'] = 'Câu dài %d ký tự — đã rút gọn.' % len(txt)
    return kq


# ============================== MÀN HÌNH ==============================
from django.http import JsonResponse                                    # noqa: E402
from django.shortcuts import render                                     # noqa: E402
from django.views.decorators.csrf import csrf_exempt                    # noqa: E402


@csrf_exempt
def man_hinh(request):
    from pha.views import staff_required as _sr                         # tránh vòng import
    return _sr(_man_hinh)(request)


def _man_hinh(request):
    msg = ''
    if request.method == 'POST':
        c = cau_hinh()
        c['bat'] = request.POST.get('bat') == '1'
        c['gia_duyet'] = [x.strip() for x in (request.POST.get('gia_duyet') or '').split(',') if x.strip()]
        c['ma_km_duyet'] = [x.strip() for x in (request.POST.get('ma_km_duyet') or '').split(',') if x.strip()]
        c['cau_giu_nhip'] = (request.POST.get('cau_giu_nhip') or '').strip() or CAU_GIU_NHIP
        luu_cau_hinh(c)
        msg = 'Đã lưu.'
    c = cau_hinh()
    return render(request, 'guardrail.html', {
        'c': c, 'msg': msg,
        'gia_txt': ', '.join(c.get('gia_duyet') or []),
        'ma_txt': ', '.join(c.get('ma_km_duyet') or []),
        'nhat_ky': doc_audit(150),
    })


@csrf_exempt
def api_thu(request):
    """Thử 1 câu xem có bị chặn không (không gửi cho ai)."""
    from pha.views import staff_required as _sr
    return _sr(lambda r: JsonResponse(kiem((r.POST.get('noi_dung') or ''), None, True)))(request)
