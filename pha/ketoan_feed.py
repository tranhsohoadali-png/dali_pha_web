# -*- coding: utf-8 -*-
"""ĐẨY NĂNG SUẤT sang phần mềm kế toán (ketoan.tranhdali.vn) — mau là nơi NHẬP,
ketoan là nơi TRÌNH BÀY/dùng (thống kê, lãi/lỗ).

mau (Django + SQLite) không chung CSDL với ketoan (PHP + MySQL) -> dùng PHƯƠNG ÁN C:
HTTP push tới endpoint của ketoan mỗi khi có log mới (gộp theo NGÀY + NHÂN VIÊN),
ketoan tự UPSERT theo (entry_date, employee_code). Có cron đẩy lại vài ngày để chống sót.

Hợp đồng dữ liệu (1 nhân viên + 1 ngày = 1 dòng):
    {date, employee_code, employee_name, pha, tranh_rot, mau_rot, sx, note}
- employee_code = CHÍNH `user` mà mau dùng ở /api/luong (khớp mã chấm công bên ketoan).
- 'Tổng việc' = pha + tranh_rot + sx do ketoan tự tính (mau KHÔNG gửi).
"""
import json
import threading
import urllib.parse
import urllib.request

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt


def _cfg():
    from pha.models import AppSetting
    return {
        'url': (AppSetting.get('KETOAN_PROD_URL', '') or '').strip(),
        'key': (AppSetting.get('KETOAN_PROD_KEY', '') or '').strip(),
    }


def _aggregate(days):
    """Gộp năng suất theo (ngày, nhân viên) cho danh sách ngày 'YYYY-MM-DD'.
    Trả list entry đúng hợp đồng dữ liệu của ketoan."""
    from pha.models import ProductionLog, PourLog, PaintingProduction
    from django.contrib.auth.models import User
    acc = {}

    def row(d, u):
        return acc.setdefault((d, u or ''), {'pha': 0, 'tranh_rot': 0, 'mau_rot': 0, 'sx': 0})

    for log in ProductionLog.objects.filter(day__in=days):
        row(log.day, log.user)['pha'] += 1
    for log in PourLog.objects.filter(day__in=days):
        q = max(1, int(log.qty or 1))
        r = row(log.day, log.user)
        r['tranh_rot'] += q
        r['mau_rot'] += int(log.color_count or 0) * q
    for p in PaintingProduction.objects.filter(day__in=days):
        row(p.day, p.user)['sx'] += max(1, int(p.qty or 1))

    names = {u.username: ((u.get_full_name() or '').strip() or u.username)
             for u in User.objects.all()}
    out = []
    for (d, u), v in sorted(acc.items()):
        out.append({'date': d, 'employee_code': u, 'employee_name': names.get(u, u),
                    'pha': v['pha'], 'tranh_rot': v['tranh_rot'],
                    'mau_rot': v['mau_rot'], 'sx': v['sx'], 'note': ''})
    return out


def _post(entries, cfg, timeout=6):
    """POST entries sang ketoan. Trả dict phản hồi (raise nếu lỗi mạng)."""
    body = json.dumps({'entries': entries}).encode('utf-8')
    sep = '&' if ('?' in cfg['url']) else '?'
    url = cfg['url'] + sep + 'action=push'
    if cfg['key']:
        url += '&key=' + urllib.parse.quote(cfg['key'])
    req = urllib.request.Request(url, data=body, method='POST',
                                 headers={'Content-Type': 'application/json',
                                          'User-Agent': 'dali-mau/1.0'})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode('utf-8', 'replace')
    try:
        return json.loads(raw)
    except Exception:
        return {'raw': raw[:200]}


def push_async(day):
    """Đẩy năng suất 1 ngày sang ketoan trong luồng nền (không chặn thao tác lưu)."""
    cfg = _cfg()
    if not cfg['url']:
        return   # chưa cấu hình -> bỏ qua êm, không tốn gì

    def work():
        try:
            entries = _aggregate([day])
            if entries:
                _post(entries, cfg)
        except Exception:
            pass   # best-effort: không để lỗi đẩy ảnh hưởng app
    try:
        threading.Thread(target=work, daemon=True).start()
    except Exception:
        pass


def connect_signals():
    """Gắn post_save cho 3 model năng suất -> tự đẩy ngày tương ứng sau khi commit."""
    from django.db.models.signals import post_save
    from django.db import transaction
    from pha.models import ProductionLog, PourLog, PaintingProduction

    def handler(sender, instance, raw=False, **kw):
        if raw:
            return
        day = getattr(instance, 'day', None)
        if not day:
            return
        try:
            transaction.on_commit(lambda: push_async(day))
        except Exception:
            push_async(day)

    # weak=False: giữ tham chiếu mạnh để handler (hàm cục bộ) không bị thu gom -> signal luôn chạy
    for M in (ProductionLog, PourLog, PaintingProduction):
        post_save.connect(handler, sender=M, weak=False, dispatch_uid='ketoan_feed_' + M.__name__)


@csrf_exempt
def feed(request):
    """Đẩy lại năng suất N ngày gần nhất (cron chống sót / quản lý bấm 'Đẩy lại').
    Cron: */5 * * * * curl -s "https://mau.tranhdali.vn/nang-suat-day-ketoan?key=<KEY>&days=3"
    Quản lý đăng nhập gọi được không cần key."""
    cfg = _cfg()
    is_staff = bool(getattr(request.user, 'is_staff', False))
    if not is_staff and (not cfg['key'] or request.GET.get('key', '') != cfg['key']):
        return JsonResponse({'ok': False, 'error': 'Sai khoá'}, status=401)
    if not cfg['url']:
        return JsonResponse({'ok': False, 'error': 'Chưa cấu hình URL kế toán (KETOAN_PROD_URL)'})
    try:
        n = max(1, min(60, int(request.GET.get('days') or 3)))
    except (TypeError, ValueError):
        n = 3
    from pha.views import _now
    from datetime import timedelta
    today = _now().date()
    days = [(today - timedelta(days=i)).strftime('%Y-%m-%d') for i in range(n)]
    entries = _aggregate(days)
    if not entries:
        return JsonResponse({'ok': True, 'pushed': 0, 'days': days, 'note': 'không có dữ liệu'})
    try:
        resp = _post(entries, cfg)
    except Exception as e:
        return JsonResponse({'ok': False, 'error': str(e)[:160]})
    return JsonResponse({'ok': True, 'pushed': len(entries), 'days': days, 'ketoan': resp})
