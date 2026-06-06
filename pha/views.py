import csv
import json
import os
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

try:
    from zoneinfo import ZoneInfo
    _VN = ZoneInfo('Asia/Ho_Chi_Minh')
except Exception:
    _VN = None

from functools import wraps

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.files.storage import FileSystemStorage
from django.http import JsonResponse, HttpResponse, HttpResponseNotFound, FileResponse, Http404
from django.shortcuts import render, redirect
from django.views.decorators.csrf import csrf_exempt

from django.db.models import F, Sum, Count

from pha import mixing
from pha import recipes
from pha.models import ProductionLog, ImageResult, PaintStock

_img_executor = ThreadPoolExecutor(max_workers=2)


def staff_required(view):
    """Chỉ CHỦ (is_staff) mới vào được trang quản lý; nhân viên bị đẩy về /app."""
    @wraps(view)
    @login_required(login_url='/login')
    def wrapped(request, *args, **kwargs):
        if not request.user.is_staff:
            return redirect('/app')
        return view(request, *args, **kwargs)
    return wrapped


def login_view(request):
    if request.user.is_authenticated:
        return redirect('/' if request.user.is_staff else '/app')
    if request.method == 'POST':
        u = authenticate(request, username=request.POST.get('username', '').strip(),
                         password=request.POST.get('password', ''))
        if u is not None:
            login(request, u)
            nxt = request.GET.get('next')
            return redirect(nxt or ('/' if u.is_staff else '/app'))
        messages.error(request, 'Sai tài khoản hoặc mật khẩu.')
    return render(request, 'login.html')


def logout_view(request):
    logout(request)
    return redirect('/login')


@csrf_exempt
@staff_required
def nhan_vien(request):
    """Quản lý tài khoản nhân viên / quản lý (chỉ chủ)."""
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'add_user':
            uname = request.POST.get('username', '').strip()
            pwd = request.POST.get('password', '')
            is_manager = request.POST.get('is_manager') == 'on'
            if not uname or not pwd:
                messages.error(request, 'Thiếu tên đăng nhập hoặc mật khẩu.')
            elif User.objects.filter(username__iexact=uname).exists():
                messages.error(request, f'Tài khoản "{uname}" đã tồn tại.')
            else:
                u = User.objects.create_user(username=uname, password=pwd)
                u.is_staff = is_manager
                u.save()
                messages.info(request, f'Đã tạo {"quản lý" if is_manager else "nhân viên"}: {uname}.')
        elif action == 'delete_user':
            uname = request.POST.get('username', '')
            if uname == request.user.username:
                messages.error(request, 'Không thể tự xoá tài khoản đang dùng.')
            else:
                User.objects.filter(username=uname, is_superuser=False).delete()
                messages.info(request, f'Đã xoá tài khoản {uname}.')
        elif action == 'reset_pw':
            uname = request.POST.get('username', '')
            pwd = request.POST.get('password', '')
            u = User.objects.filter(username=uname).first()
            if u and pwd:
                u.set_password(pwd)
                u.save()
                messages.info(request, f'Đã đổi mật khẩu cho {uname}.')
        return redirect('/nhan-vien')
    users = User.objects.order_by('-is_superuser', '-is_staff', 'username')
    return render(request, 'nhan_vien.html', {'users': users})


def _low_stock_names():
    return [p.name for p in PaintStock.objects.all()
            if p.low_threshold > 0 and p.stock <= p.low_threshold]


@csrf_exempt
@staff_required
def dashboard(request):
    """Bảng điều khiển: số liệu, biểu đồ, dự báo mua sơn, năng suất."""
    now = _now()
    today = now.strftime('%Y-%m-%d')
    this_month = now.strftime('%Y-%m')

    today_n = ProductionLog.objects.filter(day=today).count()
    month_n = ProductionLog.objects.filter(month=this_month).count()
    month_cost = ProductionLog.objects.filter(month=this_month).aggregate(s=Sum('cost'))['s'] or 0

    # 30 ngày qua: số mẻ mỗi ngày
    days = [now.date() - timedelta(days=i) for i in range(29, -1, -1)]
    dc = dict(ProductionLog.objects.filter(day__gte=days[0].strftime('%Y-%m-%d'))
              .values_list('day').annotate(n=Count('id')))
    day_labels = [d.strftime('%d/%m') for d in days]
    day_data = [dc.get(d.strftime('%Y-%m-%d'), 0) for d in days]

    # 12 tháng: chi phí
    months, y, m = [], now.year, now.month
    for _ in range(12):
        months.append(f'{y:04d}-{m:02d}')
        m -= 1
        if m == 0:
            m = 12; y -= 1
    months = months[::-1]
    mc = dict(ProductionLog.objects.values_list('month').annotate(c=Sum('cost')))
    month_labels = [_fmt_month(mm) for mm in months]
    month_cost_data = [round(mc.get(mm, 0) or 0) for mm in months]

    # màu dùng nhiều tháng này
    _, top_rows = _stats('month', this_month)

    # dự báo mua sơn (theo 30 ngày qua)
    since = days[0].strftime('%Y-%m-%d')
    usage30 = defaultdict(float)
    for log in ProductionLog.objects.filter(day__gte=since):
        for c in (log.components or []):
            try:
                usage30[c['name']] += float(c['grams'])
            except (KeyError, TypeError, ValueError):
                pass
    forecast = []
    for b in mixing.get_bases():
        ps, _ = PaintStock.objects.get_or_create(name=b['name'])
        used = usage30.get(b['name'], 0)
        per_day = used / 30.0
        days_left = round(ps.stock / per_day) if per_day > 0 else None
        forecast.append({
            'name': b['name'], 'rgb': b['rgb'], 'stock': round(ps.stock, 1), 'used30': round(used),
            'per_day': round(per_day, 1), 'days_left': days_left,
            'suggest': max(0, round(used - ps.stock)),   # đủ dùng ~30 ngày tới
            'low': ps.low_threshold > 0 and ps.stock <= ps.low_threshold,
        })

    # Gắn ô màu + thanh tỉ lệ cho bảng "màu dùng nhiều"
    base_rgb = {b['name']: b['rgb'] for b in mixing.get_bases()}
    max_g = max((r['grams'] for r in top_rows), default=0) or 1
    for r in top_rows:
        r['rgb'] = base_rgb.get(r['name'])
        r['pct'] = round(r['grams'] / max_g * 100)

    users = list(ProductionLog.objects.filter(month=this_month).values('user')
                 .annotate(n=Count('id'), c=Sum('cost')).order_by('-n'))

    return render(request, 'dashboard.html', {
        'today_n': today_n, 'month_n': month_n, 'month_cost': round(month_cost),
        'low_stock': _low_stock_names(),
        'day_labels': json.dumps(day_labels), 'day_data': json.dumps(day_data),
        'month_labels': json.dumps(month_labels), 'month_cost_data': json.dumps(month_cost_data),
        'top_rows': top_rows, 'forecast': forecast, 'users': users,
        'month_label': _fmt_month(this_month), 'today_label': now.strftime('%d/%m/%Y'),
    })


@csrf_exempt
@staff_required
def kho_son(request):
    """Quản lý tồn kho màu sơn gốc (chỉ chủ)."""
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        action = request.POST.get('action')
        try:
            val = float(request.POST.get('value') or 0)
        except ValueError:
            val = 0
        ps, _ = PaintStock.objects.get_or_create(name=name)
        if action == 'add_stock':
            ps.stock = round(ps.stock + val, 1); ps.save()
            messages.info(request, f'Đã nhập thêm {val:g}g vào "{name}".')
        elif action == 'set_stock':
            ps.stock = val; ps.save()
            messages.info(request, f'Đã đặt tồn kho "{name}" = {val:g}g.')
        elif action == 'set_threshold':
            ps.low_threshold = val; ps.save()
            messages.info(request, f'Đã đặt ngưỡng cảnh báo "{name}" = {val:g}g.')
        elif action == 'set_price':
            ps.price_per_kg = val; ps.save()
            messages.info(request, f'Đã đặt giá "{name}" = {val:,.0f} đ/kg.')
        return redirect('/kho-son')

    items = []
    total_value = 0
    for b in mixing.get_bases():
        ps, _ = PaintStock.objects.get_or_create(name=b['name'])
        value = ps.stock * ps.price_per_kg / 1000.0
        total_value += value
        items.append({
            'name': b['name'], 'rgb': b['rgb'], 'stock': round(ps.stock, 1),
            'threshold': ps.low_threshold, 'price': ps.price_per_kg,
            'value': round(value),
            'low': ps.low_threshold > 0 and ps.stock <= ps.low_threshold,
        })
    return render(request, 'kho_son.html', {'items': items, 'total_value': round(total_value)})


def _now():
    return datetime.now(_VN) if _VN else datetime.now()


def _aggregate(qs):
    acc = {}
    for log in qs:
        for c in (log.components or []):
            try:
                acc[c['name']] = round(acc.get(c['name'], 0) + float(c['grams']), 2)
            except (KeyError, TypeError, ValueError):
                continue
    return sorted([{'name': k, 'grams': v} for k, v in acc.items()], key=lambda x: -x['grams'])


def _fmt_month(m):
    try:
        return datetime.strptime(m, '%Y-%m').strftime('%m/%Y')
    except ValueError:
        return m


def _stats_qs(range_, month_param):
    """Trả (label, queryset ProductionLog) theo khoảng: today / week / month."""
    now = _now()
    if range_ == 'week':
        d = now.date()
        monday = d - timedelta(days=d.weekday())
        sunday = monday + timedelta(days=6)
        qs = ProductionLog.objects.filter(day__gte=monday.strftime('%Y-%m-%d'),
                                          day__lte=sunday.strftime('%Y-%m-%d'))
        label = f"Tuần này ({monday.strftime('%d/%m')} – {sunday.strftime('%d/%m/%Y')})"
    elif range_ == 'month':
        m = month_param or now.strftime('%Y-%m')
        qs = ProductionLog.objects.filter(month=m)
        label = "Tháng " + _fmt_month(m)
    else:
        qs = ProductionLog.objects.filter(day=now.strftime('%Y-%m-%d'))
        label = "Hôm nay (" + now.strftime('%d/%m/%Y') + ")"
    return label, qs


def _stats(range_, month_param):
    label, qs = _stats_qs(range_, month_param)
    return label, _aggregate(qs)


@csrf_exempt
@staff_required
def cong_thuc_mau(request):
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'add_base':
            ok, msg = mixing.add_base(request.POST.get('name', ''), request.POST.get('hex', ''))
            messages.info(request, msg)
            return redirect('cong_thuc_mau')
        if action == 'delete_base':
            n = mixing.delete_base(request.POST.get('name', ''))
            messages.info(request, "Đã xoá màu gốc." if n else "Không tìm thấy.")
            return redirect('cong_thuc_mau')
        if action == 'save_recipe':
            names = request.POST.getlist('base_name')
            weights = request.POST.getlist('base_weight')
            components = [{'name': n, 'grams': w} for n, w in zip(names, weights)]
            ok, msg = recipes.add_recipe(request.POST.get('dali', ''), request.POST.get('hex', ''), components)
            messages.info(request, msg)
            return redirect('cong_thuc_mau')
        if action == 'delete_recipe':
            recipes.delete_recipe(request.POST.get('dali', ''))
            messages.info(request, "Đã xoá công thức.")
            return redirect('cong_thuc_mau')

    rec_list = []
    for r in recipes.get_all():
        rec_list.append({
            'dali': r['dali'], 'hex': r['hex'], 'components': r['components'],
            'total': recipes.total_grams(r), 'formula': recipes.as_formula(r),
        })
    months = sorted(set(ProductionLog.objects.values_list('month', flat=True)), reverse=True)
    stat_months = [{'value': m, 'label': _fmt_month(m)} for m in months]
    label, rows = _stats('today', None)
    return render(request, 'cong_thuc_mau.html', {
        'bases': mixing.get_bases(), 'recipes': rec_list,
        'stat_months': stat_months, 'stat_label': label, 'stat_rows': rows,
        'low_stock': _low_stock_names(),
    })


@csrf_exempt
@staff_required
def thong_ke(request):
    label, rows = _stats(request.GET.get('range', 'today'), request.GET.get('month'))
    return JsonResponse({'label': label, 'rows': rows})


@csrf_exempt
@login_required(login_url='/login')
def lich_su(request):
    """Lịch sử các mẻ đã pha (mới nhất trước). ?range=today|all."""
    now = _now()
    if request.GET.get('range') == 'today':
        qs = ProductionLog.objects.filter(day=now.strftime('%Y-%m-%d'))
    else:
        qs = ProductionLog.objects.all()
    rows = []
    for log in qs.order_by('-created_time')[:100]:
        t = log.created_time
        try:
            t = t.astimezone(_VN) if _VN else t
        except Exception:
            pass
        rows.append({'dt': t.strftime('%d/%m %H:%M'), 'dali': log.dali,
                     'mult': '×' + ('%g' % log.multiplier), 'user': log.user or ''})
    return JsonResponse({'rows': rows})


@csrf_exempt
def export_thong_ke_excel(request):
    """Xuất báo cáo lượng màu đã pha ra Excel (.xlsx) theo khoảng thời gian."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment

    range_ = request.GET.get('range', 'month')
    month = request.GET.get('month')
    label, qs = _stats_qs(range_, month)
    rows = _aggregate(qs)

    wb = Workbook()
    head_fill = PatternFill('solid', fgColor='2E7D32')
    head_font = Font(bold=True, color='FFFFFF')
    center = Alignment(horizontal='center')

    # Sheet 1: tổng theo màu gốc
    ws = wb.active
    ws.title = "Tong theo mau"
    ws.append(["BÁO CÁO LƯỢNG MÀU ĐÃ PHA"])
    ws.append([label])
    ws.append(["Màu gốc", "Tổng đã dùng (g)"])
    for c in ws[3]:
        c.fill = head_fill; c.font = head_font; c.alignment = center
    for u in rows:
        ws.append([u['name'], u['grams']])
    ws.column_dimensions['A'].width = 22
    ws.column_dimensions['B'].width = 18

    # Sheet 2: chi tiết từng mẻ pha
    ws2 = wb.create_sheet("Chi tiet pha")
    ws2.append(["Ngày giờ", "Mã DALI", "Hệ số nhân", "Tổng (g)", "Chi tiết"])
    for c in ws2[1]:
        c.fill = head_fill; c.font = head_font; c.alignment = center
    for log in qs.order_by('created_time'):
        detail = " + ".join(f"{x.get('name')} {x.get('grams')}g" for x in (log.components or []))
        t = log.created_time
        try:
            t = t.astimezone(_VN) if _VN else t
        except Exception:
            pass
        ws2.append([t.strftime('%d/%m/%Y %H:%M'), log.dali, f"x{('%g' % log.multiplier)}",
                    log.total, detail])
    for col, w in zip('ABCDE', (18, 14, 12, 12, 60)):
        ws2.column_dimensions[col].width = w

    resp = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    resp['Content-Disposition'] = 'attachment; filename="bao_cao_mau.xlsx"'
    wb.save(resp)
    return resp


@csrf_exempt
@login_required(login_url='/login')
def pha(request):
    if request.method != 'POST':
        return HttpResponseNotFound("POST only")
    dali = request.POST.get('dali', '').strip()
    try:
        mult = float(request.POST.get('multiplier') or 1)
    except ValueError:
        mult = 1.0
    if mult <= 0:
        mult = 1.0
    rec = next((r for r in recipes.get_all() if r['dali'].strip().lower() == dali.lower()), None)
    if not rec:
        return JsonResponse({'ok': False, 'msg': 'Không tìm thấy công thức.'})
    comps = [{'name': c['name'], 'grams': round(c['grams'] * mult, 2)} for c in rec['components']]
    total = round(sum(c['grams'] for c in comps), 2)
    # Chi phí sơn của mẻ (theo giá hiện tại, đồng)
    prices = {p.name: p.price_per_kg for p in PaintStock.objects.all()}
    cost = round(sum(c['grams'] * prices.get(c['name'], 0) / 1000.0 for c in comps))
    now = _now()
    ProductionLog.objects.create(
        day=now.strftime('%Y-%m-%d'), month=now.strftime('%Y-%m'),
        dali=rec['dali'], hex=rec['hex'], multiplier=mult, components=comps, total=total,
        user=request.user.username, cost=cost,
    )
    # Trừ kho sơn theo lượng đã dùng (chỉ trừ màu có theo dõi tồn kho)
    for c in comps:
        PaintStock.objects.filter(name=c['name']).update(stock=F('stock') - c['grams'])
    return JsonResponse({'ok': True, 'msg': f'Đã ghi nhận pha {rec["dali"]} ×{("%g" % mult)}'})


@csrf_exempt
@login_required(login_url='/login')
def mobile(request):
    bases = {b['name']: b['rgb'] for b in mixing.get_bases()}
    rec_list = []
    for r in recipes.get_all():
        comps = [{'name': c['name'], 'grams': c['grams'], 'rgb': bases.get(c['name'])}
                 for c in r['components']]
        rec_list.append({'dali': r['dali'], 'hex': r['hex'], 'components': comps,
                         'total': recipes.total_grams(r)})
    return render(request, 'mobile.html', {'recipes': rec_list})


@csrf_exempt
@staff_required
def quan_ly(request):
    """App ĐIỆN THOẠI cho quản lý: nhập kho sơn + xem nhanh dashboard."""
    now = _now()
    today_n = ProductionLog.objects.filter(day=now.strftime('%Y-%m-%d')).count()
    month_n = ProductionLog.objects.filter(month=now.strftime('%Y-%m')).count()
    month_cost = ProductionLog.objects.filter(month=now.strftime('%Y-%m')).aggregate(s=Sum('cost'))['s'] or 0
    since = (now.date() - timedelta(days=29)).strftime('%Y-%m-%d')
    usage30 = defaultdict(float)
    for log in ProductionLog.objects.filter(day__gte=since):
        for c in (log.components or []):
            try:
                usage30[c['name']] += float(c['grams'])
            except (KeyError, TypeError, ValueError):
                pass
    items, need_buy = [], []
    total_value = 0.0
    for b in mixing.get_bases():
        ps, _ = PaintStock.objects.get_or_create(name=b['name'])
        low = ps.low_threshold > 0 and ps.stock <= ps.low_threshold
        value = ps.stock / 1000.0 * (ps.price_per_kg or 0)
        total_value += value
        # Ước tính số ngày còn dùng được theo mức dùng 30 ngày gần nhất
        avg_day = usage30.get(b['name'], 0) / 30.0
        days = int(ps.stock / avg_day) if avg_day > 0 else None
        if low or (days is not None and days < 7):
            level, bar = 'low', (min(100, round(days / 30.0 * 100)) if days is not None else 8)
        elif days is not None and days < 14:
            level, bar = 'warn', min(100, round(days / 30.0 * 100))
        else:
            level, bar = 'ok', (min(100, round(days / 30.0 * 100)) if days is not None else 100)
        items.append({
            'name': b['name'], 'rgb': b['rgb'], 'stock': round(ps.stock, 1), 'low': low,
            'value': f'{round(value):,.0f}'.replace(',', '.'), 'days': days,
            'level': level, 'bar': bar, 'price': round(ps.price_per_kg or 0),
        })
        suggest = max(0, round(usage30.get(b['name'], 0) - ps.stock))
        if suggest > 0:
            need_buy.append({'name': b['name'], 'suggest': suggest})
    return render(request, 'quan_ly.html', {
        'items': items, 'today_n': today_n, 'month_n': month_n, 'month_cost': round(month_cost),
        'low_stock': _low_stock_names(), 'need_buy': need_buy, 'today_label': now.strftime('%d/%m/%Y'),
        'total_value': f'{round(total_value):,.0f}'.replace(',', '.'),
    })


@csrf_exempt
@staff_required
def quan_ly_nhap(request):
    """AJAX: nhập thêm / đặt lại tồn kho 1 màu. Trả JSON."""
    if request.method != 'POST':
        return HttpResponseNotFound('POST only')
    name = request.POST.get('name', '').strip()
    action = request.POST.get('action')
    try:
        val = float(request.POST.get('value') or 0)
    except ValueError:
        val = 0
    ps, _ = PaintStock.objects.get_or_create(name=name)
    if action == 'add':
        ps.stock = round(ps.stock + val, 1)
    elif action == 'set':
        ps.stock = val
    else:
        return JsonResponse({'ok': False})
    ps.save()
    low = ps.low_threshold > 0 and ps.stock <= ps.low_threshold
    return JsonResponse({'ok': True, 'name': name, 'stock': round(ps.stock, 1), 'low': low})


def manifest_ql(request):
    data = {
        "name": "Quản lý kho sơn", "short_name": "Quản lý",
        "start_url": "/quan-ly", "scope": "/", "display": "standalone", "orientation": "portrait",
        "background_color": "#ffffff", "theme_color": "#0d6efd",
        "icons": [
            {"src": "/media/icon-ql-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any maskable"},
            {"src": "/media/icon-ql-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"},
        ],
    }
    return JsonResponse(data, content_type='application/manifest+json')


def manifest(request):
    data = {
        "name": "Công thức pha DALI", "short_name": "Pha màu",
        "start_url": "/app", "scope": "/", "display": "standalone", "orientation": "portrait",
        "background_color": "#ffffff", "theme_color": "#2E7D32",
        "icons": [
            {"src": "/media/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any maskable"},
            {"src": "/media/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"},
        ],
    }
    return JsonResponse(data, content_type='application/manifest+json')


def service_worker(request):
    js = (
        "const CACHE='pha-v1';\n"
        "self.addEventListener('install', function(e){ self.skipWaiting(); });\n"
        "self.addEventListener('activate', function(e){ e.waitUntil(self.clients.claim()); });\n"
        "self.addEventListener('fetch', function(e){\n"
        "  e.respondWith(fetch(e.request).then(function(r){\n"
        "    try{ var c=r.clone(); caches.open(CACHE).then(function(ch){ ch.put(e.request, c); }); }catch(_){}\n"
        "    return r;\n"
        "  }).catch(function(){ return caches.match(e.request); }));\n"
        "});\n"
    )
    return HttpResponse(js, content_type='application/javascript')


def media_icon(request, name):
    path = os.path.join(settings.MEDIA_ROOT, name)
    if not os.path.exists(path):
        raise Http404
    return FileResponse(open(path, 'rb'), content_type='image/png')


@csrf_exempt
@staff_required
def dali_colors(request):
    """Bảng màu DALI: xem / tìm / thêm-sửa / xoá / nạp lại (chỉ chủ)."""
    from pha import dali_match
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'add':
            ok, msg = dali_match.add_entry(request.POST.get('hex', ''), request.POST.get('dali', ''))
            messages.info(request, msg)
        elif action == 'delete':
            n = dali_match.delete_entry(request.POST.get('hex', ''), request.POST.get('dali') or None)
            messages.info(request, f'Đã xoá {n} mục.' if n else 'Không tìm thấy mục để xoá.')
        elif action == 'reload':
            n = dali_match.reload_reference()
            messages.info(request, f'Đã nạp lại {n} màu từ file.')
        return redirect('/dali-colors')

    query = (request.GET.get('q') or '').strip().lower()
    items = dali_match.get_all()
    if query:
        items = [it for it in items if query in it['hex'].lower() or query in it['dali'].lower()]
    total = dali_match.reference_size()
    shown = items[:500]
    return render(request, 'dali_colors.html', {
        'items': shown, 'total': total, 'query': request.GET.get('q') or '',
        'found': len(items), 'truncated': len(items) > 500,
    })


# ===================== XỬ LÝ ẢNH (tab cho chủ) =====================
def _fmt_name(filename):
    try:
        first_dot = filename.find('.')
        return filename[:first_dot] + ' ' + filename[filename.find('_') + 1:]
    except Exception:
        return filename


def _get_img(request):
    data = request.GET.get('file_url') or request.POST.get('file_url')
    if not data:
        return None
    return ImageResult.objects.filter(name=data.replace('/media/', '')) \
        .order_by('-created_time').first()


def _colors_with_edits(res, request):
    """Trả về bản sao bảng màu, áp mã DALI người dùng vừa sửa (gửi qua 'edits',
    dạng {stt: 'MÃ'}). KHÔNG ghi vào bảng tham chiếu DALI và KHÔNG lưu DB —
    chỉ dùng cho file tải về (vì mã sửa chưa qua kiểm nghiệm)."""
    colors = [list(r) for r in (res.colors or [])]
    raw = request.GET.get('edits') or request.POST.get('edits')
    if raw:
        try:
            edits = json.loads(raw)
        except (ValueError, TypeError):
            edits = {}
        if isinstance(edits, dict):
            for row in colors:
                key = str(row[0])
                val = edits.get(key)
                if val:
                    while len(row) < 3:
                        row.append('')
                    row[2] = str(val).strip()
    return colors


@csrf_exempt
@staff_required
def xu_ly_anh(request):
    from pha.imageproc import process_image
    last = ImageResult.objects.all().order_by('-created_time')[:30]
    last_query = [{'name': _fmt_name(q.name), 'url': q.name} for q in last]
    if request.method == 'POST' and request.FILES.get('image'):
        upload = request.FILES['image']
        fss = FileSystemStorage()
        name = f'{datetime.now():%Y-%m-%d_%H-%M-%S}_{upload.name}'
        fss.save(name, upload)
        rec = ImageResult.objects.create(name=name, status=ImageResult.STATUS_PROCESSING,
                                         user=request.user.username)
        _img_executor.submit(process_image, rec.id, name)
        return render(request, 'xu_ly_anh.html', {'file_url': '/media/' + name, 'last_query': last_query})
    return render(request, 'xu_ly_anh.html', {'last_query': last_query})


@csrf_exempt
@staff_required
def anh_result(request):
    from pha.imageproc import split_list
    res = _get_img(request)
    if not res:
        return JsonResponse({'status': 'processing'})
    if res.status == ImageResult.STATUS_PROCESSING:
        return JsonResponse({'status': 'processing'})
    if res.status == ImageResult.STATUS_ERROR:
        return JsonResponse({'status': 'error', 'error': res.error_message})
    return JsonResponse({'status': 'done', 'img_output': '/media/' + res.name_output,
                         'colors': split_list(10, res.colors)})


@csrf_exempt
@staff_required
def anh_save_color(request):
    """Sửa mã DALI cho 1 màu: ghi vào bảng tham chiếu DALI + cập nhật ảnh hiện tại."""
    from pha import dali_match
    if request.method != 'POST':
        return HttpResponseNotFound('POST only')
    hex_value = request.POST.get('hex', '')
    dali = request.POST.get('dali', '')
    stt = request.POST.get('stt', '')
    file_url = request.POST.get('file_url', '')
    ok, msg = dali_match.add_entry(hex_value, dali)
    if not ok:
        return JsonResponse({'ok': False, 'msg': msg})
    if file_url:
        res = ImageResult.objects.filter(name=file_url.replace('/media/', '')) \
            .order_by('-created_time').first()
        if res and res.colors:
            changed = False
            for row in res.colors:
                if str(row[0]) == str(stt) and len(row) > 2:
                    row[2] = dali; changed = True
            if changed:
                res.save()
    return JsonResponse({'ok': True, 'msg': msg})


@csrf_exempt
@staff_required
def anh_export_colors(request):
    res = _get_img(request)
    if not res:
        return HttpResponseNotFound('no result')
    resp = HttpResponse(content_type='text/csv; charset=utf-8-sig')
    resp['Content-Disposition'] = 'attachment; filename="bang_mau_dali.csv"'
    resp.write('﻿')
    w = csv.writer(resp)
    w.writerow(['STT', 'HEX', 'R', 'G', 'B', 'Ma_DALI', 'Phan_tram'])
    for row in _colors_with_edits(res, request):
        h = row[1].lstrip('#')
        try:
            r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        except (ValueError, IndexError):
            r = g = b = ''
        w.writerow([row[0], row[1], r, g, b, row[2] if len(row) > 2 else '', row[3] if len(row) > 3 else ''])
    return resp


@csrf_exempt
@staff_required
def anh_export_xlsx(request):
    from pha.exports import build_xlsx
    res = _get_img(request)
    if not res:
        return HttpResponseNotFound('no result')
    out = os.path.join(settings.MEDIA_ROOT, 'bang_mau_dali.xlsx')
    build_xlsx(_colors_with_edits(res, request), out)
    with open(out, 'rb') as f:
        data = f.read()
    resp = HttpResponse(data, content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    resp['Content-Disposition'] = 'attachment; filename="bang_mau_dali.xlsx"'
    return resp


@csrf_exempt
@staff_required
def anh_legend(request):
    from pha.exports import build_legend_image
    res = _get_img(request)
    if not res:
        return HttpResponseNotFound('no result')
    left = res.name if os.path.exists(os.path.join(settings.MEDIA_ROOT, res.name)) else res.name_output
    left_path = os.path.join(settings.MEDIA_ROOT, left)
    title = (request.GET.get('title') or '').strip()
    out = os.path.join(settings.MEDIA_ROOT, f'{res.name_output or res.name}_legend.png')
    build_legend_image(left_path, _colors_with_edits(res, request), out, title=title)
    with open(out, 'rb') as f:
        data = f.read()
    resp = HttpResponse(data, content_type='image/png')
    resp['Content-Disposition'] = 'attachment; filename="bang_mau_DALI.png"'
    return resp


@csrf_exempt
@staff_required
def anh_download_result(request):
    from pha.imageproc import get_paint_image
    result_url = request.GET.get('result_url')
    image_name = request.GET.get('image_name') or 'result'
    scale = request.GET.get('scale_option') or '20x20'
    orientation = request.GET.get('orientation_option') or 'auto'
    try:
        file_paint, file_a3 = get_paint_image(result_url, image_name, scale, orientation)
        return JsonResponse({'file_paint': file_paint, 'file_a3': file_a3, 'origin_result': result_url})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)
