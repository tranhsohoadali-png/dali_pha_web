import os
from datetime import datetime, timedelta

try:
    from zoneinfo import ZoneInfo
    _VN = ZoneInfo('Asia/Ho_Chi_Minh')
except Exception:
    _VN = None

from django.conf import settings
from django.contrib import messages
from django.http import JsonResponse, HttpResponse, HttpResponseNotFound, FileResponse, Http404
from django.shortcuts import render, redirect
from django.views.decorators.csrf import csrf_exempt

from pha import mixing
from pha import recipes
from pha.models import ProductionLog


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
    })


@csrf_exempt
def thong_ke(request):
    label, rows = _stats(request.GET.get('range', 'today'), request.GET.get('month'))
    return JsonResponse({'label': label, 'rows': rows})


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
    now = _now()
    ProductionLog.objects.create(
        day=now.strftime('%Y-%m-%d'), month=now.strftime('%Y-%m'),
        dali=rec['dali'], hex=rec['hex'], multiplier=mult, components=comps, total=total,
    )
    return JsonResponse({'ok': True, 'msg': f'Đã ghi nhận pha {rec["dali"]} ×{("%g" % mult)}'})


@csrf_exempt
def mobile(request):
    bases = {b['name']: b['rgb'] for b in mixing.get_bases()}
    rec_list = []
    for r in recipes.get_all():
        comps = [{'name': c['name'], 'grams': c['grams'], 'rgb': bases.get(c['name'])}
                 for c in r['components']]
        rec_list.append({'dali': r['dali'], 'hex': r['hex'], 'components': comps,
                         'total': recipes.total_grams(r)})
    return render(request, 'mobile.html', {'recipes': rec_list})


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
