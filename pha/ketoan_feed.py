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
import hmac
import json
import threading
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt


def _cfg():
    from pha.models import AppSetting
    return {
        'url': (AppSetting.get('KETOAN_PROD_URL', '') or '').strip(),
        'key': (AppSetting.get('KETOAN_PROD_KEY', '') or '').strip(),
    }


def _aggregate_productivity(days):
    """Gộp năng suất theo (ngày, nhân viên) cho danh sách ngày 'YYYY-MM-DD'.
    Trả list entry đúng hợp đồng dữ liệu của ketoan. Dùng CHUNG cho cả PUSH lẫn PULL
    -> 1 shape duy nhất. BỎ QUA bản ghi user rỗng (không map được sang mã chấm công)."""
    from pha.models import ProductionLog, PourLog, PaintingProduction
    from django.contrib.auth.models import User
    acc = {}

    def row(d, u):
        return acc.setdefault((d, u), {'pha': 0, 'tranh_rot': 0, 'mau_rot': 0, 'sx': 0})

    for log in ProductionLog.objects.filter(day__in=days):
        if not log.user:
            continue
        row(log.day, log.user)['pha'] += 1
    for log in PourLog.objects.filter(day__in=days):
        if not log.user:
            continue
        q = max(1, int(log.qty or 1))
        r = row(log.day, log.user)
        r['tranh_rot'] += q
        r['mau_rot'] += int(log.color_count or 0) * q
    for p in PaintingProduction.objects.filter(day__in=days):
        if not p.user:
            continue
        row(p.day, p.user)['sx'] += max(1, int(p.qty or 1))

    names = {u.username: ((u.get_full_name() or '').strip() or u.username)
             for u in User.objects.all()}
    out = []
    for (d, u), v in sorted(acc.items()):
        out.append({'date': d, 'employee_code': u, 'employee_name': names.get(u, u),
                    'pha': v['pha'], 'tranh_rot': v['tranh_rot'],
                    'mau_rot': v['mau_rot'], 'sx': v['sx'], 'note': '', 'source': 'mau'})
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
            entries = _aggregate_productivity([day])
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
    key = request.headers.get('X-API-Key') or request.GET.get('key', '')
    if not is_staff and not (cfg['key'] and hmac.compare_digest(str(key), str(cfg['key']))):
        return JsonResponse({'ok': False, 'error': 'Sai khoá'}, status=401)
    if not cfg['url']:
        return JsonResponse({'ok': False, 'error': 'Chưa cấu hình URL kế toán (KETOAN_PROD_URL)'})
    try:
        n = max(1, min(60, int(request.GET.get('days') or 3)))
    except (TypeError, ValueError):
        n = 3
    from pha.views import _now
    today = _now().date()
    days = [(today - timedelta(days=i)).strftime('%Y-%m-%d') for i in range(n)]
    entries = _aggregate_productivity(days)
    if not entries:
        return JsonResponse({'ok': True, 'pushed': 0, 'days': days, 'note': 'không có dữ liệu'})
    try:
        resp = _post(entries, cfg)
    except Exception as e:
        return JsonResponse({'ok': False, 'error': str(e)[:160]})
    return JsonResponse({'ok': True, 'pushed': len(entries), 'days': days, 'ketoan': resp})


def _days_param(request):
    """Suy ra danh sách ngày 'YYYY-MM-DD' từ ?day= / ?from=&to= / ?days=N (tối đa 92, mặc định 1).
    Ngày tính theo GIỜ VN (giống chấm công). Trả [] nếu tham số không hợp lệ."""
    from pha.views import _now
    d1 = (request.GET.get('day') or '').strip()
    if d1:
        try:
            datetime.strptime(d1, '%Y-%m-%d')
            return [d1]
        except ValueError:
            return []
    f = (request.GET.get('from') or '').strip()
    t = (request.GET.get('to') or '').strip()
    if f and t:
        try:
            a = datetime.strptime(f, '%Y-%m-%d').date()
            b = datetime.strptime(t, '%Y-%m-%d').date()
        except ValueError:
            return []
        if b < a:
            a, b = b, a
        span = min((b - a).days, 92)
        return [(a + timedelta(days=i)).strftime('%Y-%m-%d') for i in range(span + 1)]
    try:
        n = max(1, min(92, int(request.GET.get('days') or 1)))
    except (TypeError, ValueError):
        n = 1
    today = _now().date()
    return [(today - timedelta(days=i)).strftime('%Y-%m-%d') for i in range(n)]


@csrf_exempt
def api_nang_suat(request):
    """API PULL NĂNG SUẤT theo NGÀY cho phần mềm kế toán — DÙNG CHUNG khoá KETOAN_API_KEY
    như /api/luong & /api/ketoan (cùng hướng pull, cùng 1 khoá để khỏi cấu hình thêm).

    Khoá: header 'X-API-Key: <KETOAN_API_KEY>' (khuyên dùng) hoặc ?key= (tương thích ngược).
    Ngày: ?day=YYYY-MM-DD | ?from=YYYY-MM-DD&to=YYYY-MM-DD | ?days=N (mặc định 1, tối đa 92, GIỜ VN).
    Trả: {ok, source, generated_at, count, entries:[{date, employee_code, employee_name,
          pha, tranh_rot, mau_rot, sx, note, source}]} — 1 nhân viên/1 ngày = 1 dòng.
    'Tổng việc' = pha+tranh_rot+sx do kế toán tự tính. UPSERT theo (date, employee_code)."""
    from pha.views import _api_key, _now
    origin = getattr(settings, 'KETOAN_ALLOW_ORIGIN', '*')

    def _cors(resp):
        resp['Access-Control-Allow-Origin'] = origin
        resp['Access-Control-Allow-Methods'] = 'GET, OPTIONS'
        resp['Access-Control-Allow-Headers'] = 'Content-Type, X-API-Key'
        resp['Cache-Control'] = 'no-store'
        return resp

    if request.method == 'OPTIONS':
        return _cors(HttpResponse(status=204))

    key = request.headers.get('X-API-Key') or request.GET.get('key', '')
    real = _api_key()
    is_staff = bool(getattr(request.user, 'is_staff', False))
    if not is_staff and not (real and hmac.compare_digest(str(key), str(real))):
        return _cors(JsonResponse({'ok': False, 'error': 'Sai khoá'}, status=401))

    days = _days_param(request)
    if not days:
        return _cors(JsonResponse({'ok': False, 'error': 'Thiếu/sai tham số ngày (day | from&to | days)'},
                                  status=400))
    entries = _aggregate_productivity(days)
    return _cors(JsonResponse({'ok': True, 'source': 'mau.tranhdali.vn',
                               'generated_at': _now().strftime('%Y-%m-%d %H:%M'),
                               'count': len(entries), 'entries': entries}))
