# -*- coding: utf-8 -*-
"""KHO MÃ TRANH — lưu bản đã số hoá để MỞ LẠI SỬA MÀU sau này (khỏi chạy lại AI).

Mỗi mục lưu: bản THIẾT KẾ (bản màu, ẢNH HIỆN TẠI trên canvas — đã áp mọi sửa HEX),
bảng màu (mã DALI + %), bản đồ số + ảnh gốc (chép từ kết quả), Mã + Khổ + thông số.

MỞ LẠI = chép các file đã lưu ra bản làm việc MỚI rồi tạo ImageResult tạm trỏ vào
bản chép đó -> trang /xu-ly-anh nạp như một kết quả bình thường (mọi nút Excel/CSV/
chú giải/in/đóng khung/lưu-lại chạy nguyên). Bản chép bị prune sau 24h/quá 10 KHÔNG
đụng file gốc trong kho -> kho luôn còn.

Lưu FILE dưới MEDIA_ROOT/kho_ma/ (không model/migration, ngoài git):
  <id>_design.png, <id>_output.png, <id>_origin<ext>, <id>.json (meta + colors).
"""
import base64
import json
import os
import re
import shutil
import uuid
from datetime import datetime

from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt

from pha.views import staff_required
from pha.models import ImageResult

_SUB = 'kho_ma'
_ID_RE = re.compile(r'^m-[0-9a-f]{8,16}$')


def _dir():
    p = os.path.join(settings.MEDIA_ROOT, _SUB)
    os.makedirs(p, exist_ok=True)
    return p


def _read_json(path):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None


def _write_json(path, obj):
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False)
    os.replace(tmp, path)


def _register_painting(ma, mid, colors):
    """Tự khai báo/cập nhật MÃ vào danh mục mã tranh (/ma-tranh) — nối kho mã với
    khai báo. ẢNH MẪU = ẢNH + CHÚ GIẢI (tranh + bảng mã DALI) DỰNG TỰ ĐỘNG từ bản
    thiết kế + bảng màu -> khỏi làm "Ảnh + chú giải" tay rồi upload. Lỗi dựng legend
    thì lùi về bản thiết kế trơn. Ảnh khai-báo-tay cũ (painting_*) được dọn."""
    ma = (ma or '').strip()
    if not ma:
        return
    from pha.models import Painting
    d = _dir()
    design = os.path.join(d, mid + '_design.png')
    # ẢNH + CHÚ GIẢI: tranh + bảng [số · ô màu · mã DALI] (đúng kiểu ảnh mẫu C098/C105)
    image_rel = f'{_SUB}/{mid}_legend.png'
    try:
        from pha.exports import build_legend_image
        rows = [[r[0], r[1], (r[2] if len(r) > 2 else ''),
                 (r[3] if len(r) > 3 and r[3] is not None else '')] for r in colors]
        build_legend_image(design, rows, os.path.join(settings.MEDIA_ROOT, image_rel), title=ma)
    except Exception:
        image_rel = f'{_SUB}/{mid}_design.png'          # lỗi -> dùng bản thiết kế trơn
    p = Painting.objects.filter(code__iexact=ma).first()
    if p:
        old = p.image
        if old and old != image_rel and old.startswith('painting_'):
            try:
                os.remove(os.path.join(settings.MEDIA_ROOT, old))
            except OSError:
                pass
        p.code, p.image, p.color_count = ma, image_rel, len(colors)
        p.save()
    else:
        Painting.objects.create(code=ma, image=image_rel, color_count=len(colors))


def _unlink_painting(mid):
    """Xoá mã khỏi kho -> gỡ ảnh khỏi thẻ danh mục (khỏi trỏ file đã mất)."""
    from pha.models import Painting
    for p in Painting.objects.filter(image__startswith=f'{_SUB}/{mid}_'):
        p.image = ''
        p.save()


def _list():
    d = _dir()
    items = []
    for fn in os.listdir(d):
        if not fn.endswith('.json'):
            continue
        j = _read_json(os.path.join(d, fn))
        if not j:
            continue
        mid = fn[:-5]
        j['id'] = mid
        j['design'] = f'/media/{_SUB}/{mid}_design.png'
        items.append(j)
    items.sort(key=lambda x: x.get('created', ''), reverse=True)
    return items


def _find_by_ma(ma):
    ma = (ma or '').strip()
    if not ma:
        return None
    for it in _list():
        if (it.get('ma') or '').strip() == ma:
            return it['id']
    return None


@staff_required
def kho_ma(request):
    """Trang danh sách mã đã lưu."""
    return render(request, 'kho_ma.html', {'items_json': json.dumps(_list(), ensure_ascii=False)})


@csrf_exempt
@staff_required
def kho_ma_luu(request):
    """Lưu 1 bản. Nhận:
      file_url  : định danh kết quả hiện tại (để chép bản đồ số + ảnh gốc)
      ma        : mã tranh (khoá — cùng mã thì GHI ĐÈ, coi như bản mới nhất)
      design_png: dataURL PNG của canvas thiết kế HIỆN TẠI (đã áp sửa màu)
      colors    : JSON bảng màu hiện tại [[stt,hex,dali,pct],...]
    """
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST only'})
    ma = (request.POST.get('ma') or '').strip()[:60]
    du = request.POST.get('design_png') or ''
    if not du.startswith('data:image'):
        return JsonResponse({'ok': False, 'error': 'Chưa có bản thiết kế để lưu.'})
    try:
        raw = base64.b64decode(du.split(',', 1)[1])
    except Exception:
        return JsonResponse({'ok': False, 'error': 'Ảnh thiết kế hỏng.'})
    try:
        colors = json.loads(request.POST.get('colors') or '[]')
        if not isinstance(colors, list):
            colors = []
    except (ValueError, TypeError):
        colors = []

    d = _dir()
    # cùng mã -> ghi đè bản cũ (dọn file cũ trước)
    mid = _find_by_ma(ma) if ma else None
    if mid:
        for f in os.listdir(d):
            if f.startswith(mid + '_') or f == mid + '.json':
                try:
                    os.remove(os.path.join(d, f))
                except OSError:
                    pass
    else:
        mid = 'm-' + uuid.uuid4().hex[:12]

    with open(os.path.join(d, mid + '_design.png'), 'wb') as f:
        f.write(raw)

    # chép bản đồ số + ảnh gốc + lấy thông số từ kết quả (best-effort)
    params, kt, has_output, origin_ext = {}, '', False, ''
    fu = (request.POST.get('file_url') or '').replace('/media/', '')
    res = ImageResult.objects.filter(name=fu).order_by('-created_time').first() if fu else None
    if res:
        params = res.params or {}
        kt = params.get('print_size') or ''
        if not colors:
            colors = [list(r) for r in (res.colors or [])]
        if res.name_output:
            src = os.path.join(settings.MEDIA_ROOT, res.name_output)
            if os.path.isfile(src):
                shutil.copyfile(src, os.path.join(d, mid + '_output.png'))
                has_output = True
        if res.name:
            src = os.path.join(settings.MEDIA_ROOT, res.name)
            if os.path.isfile(src):
                origin_ext = os.path.splitext(res.name)[1] or '.png'
                shutil.copyfile(src, os.path.join(d, mid + '_origin' + origin_ext))

    meta = {'ma': ma, 'kt': kt, 'n_colors': len(colors), 'colors': colors,
            'params': params, 'has_output': has_output, 'origin_ext': origin_ext,
            'created': datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
    _write_json(os.path.join(d, mid + '.json'), meta)
    in_catalog = False
    if ma:                                   # tự khai báo vào danh mục (ảnh + chú giải)
        _register_painting(ma, mid, colors)
        in_catalog = True
    return JsonResponse({'ok': True, 'id': mid, 'ma': ma, 'in_catalog': in_catalog,
                         'design': f'/media/{_SUB}/{mid}_design.png'})


@csrf_exempt
@staff_required
def kho_ma_mo(request):
    """Mở 1 mã: chép ra bản làm việc MỚI + tạo ImageResult tạm -> trả file_url để
    /xu-ly-anh nạp như kết quả thường. KHÔNG đụng file gốc trong kho (prune an toàn)."""
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST only'})
    mid = (request.POST.get('id') or '').strip()
    if not _ID_RE.match(mid):
        return JsonResponse({'ok': False, 'error': 'id sai'})
    d = _dir()
    meta = _read_json(os.path.join(d, mid + '.json'))
    dsrc = os.path.join(d, mid + '_design.png')
    if not meta or not os.path.isfile(dsrc):
        return JsonResponse({'ok': False, 'error': 'Không tìm thấy mã đã lưu.'})

    ts = f'{datetime.now():%Y-%m-%d_%H-%M-%S}_{uuid.uuid4().hex[:8]}'
    root = settings.MEDIA_ROOT
    design_name = f'{ts}_design.png'
    shutil.copyfile(dsrc, os.path.join(root, design_name))

    ext = meta.get('origin_ext') or '.png'
    osrc = os.path.join(d, mid + '_origin' + ext)
    origin_name = f'{ts}_origin{ext}'
    if os.path.isfile(osrc):
        shutil.copyfile(osrc, os.path.join(root, origin_name))
    else:                                    # thiếu ảnh gốc -> dùng bản thiết kế
        origin_name = design_name

    out_src = os.path.join(d, mid + '_output.png')
    output_name = f'{ts}_output.png'
    if meta.get('has_output') and os.path.isfile(out_src):
        shutil.copyfile(out_src, os.path.join(root, output_name))
    else:                                    # thiếu bản đồ số -> hiện bản thiết kế
        output_name = design_name

    rec = ImageResult.objects.create(
        name=origin_name, name_output=output_name, design_name=design_name,
        colors=[list(r) for r in (meta.get('colors') or [])],
        params=meta.get('params') or {}, status=ImageResult.STATUS_DONE,
        user=getattr(request.user, 'username', ''))
    return JsonResponse({'ok': True, 'file_url': rec.name,
                         'ma': meta.get('ma') or '', 'kt': meta.get('kt') or ''})


@csrf_exempt
@staff_required
def kho_ma_xoa(request):
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST only'})
    mid = (request.POST.get('id') or '').strip()
    if not _ID_RE.match(mid):
        return JsonResponse({'ok': False, 'error': 'id sai'})
    d = _dir()
    for f in os.listdir(d):
        if f.startswith(mid + '_') or f == mid + '.json':
            try:
                os.remove(os.path.join(d, f))
            except OSError:
                pass
    _unlink_painting(mid)                    # gỡ ảnh khỏi thẻ danh mục
    return JsonResponse({'ok': True})
