"""IN A3 — tải file (ảnh/PDF) lên 'kho in A3', đặt SỐ LƯỢNG, rồi tạo MỘT PDF khổ A3 in
sẵn (mỗi file lặp đúng số lượng, canh giữa & vừa khổ A3) để in một lần. Giải quyết việc
mất thời gian TÌM FILE + in lẻ từng cái: file đã tải lưu lại ở media/in_a3 -> lần sau chọn
lại khỏi tìm.

Đặt ở MODULE RIÊNG (không nhét vào views.py) — theo thói quen dự án, vì user hay sửa
views.py song song nên tách endpoint ra cho khỏi đụng nhau.
"""
import os
import re
import tempfile
import time

from django.conf import settings
from django.http import JsonResponse, HttpResponse, HttpResponseNotFound
from django.shortcuts import render, redirect
from PIL import Image

from pha.views import staff_required
from pha.imposition import _import_fitz

_DIRNAME = 'in_a3'
# A3 dọc @72dpi (297 x 420 mm). Tự XOAY ngang nếu file ngang -> in to nhất có thể.
A3_W_PT, A3_H_PT = 841.89, 1190.55
_MARGIN_PT = 14.0                       # ~5mm lề (chừa vùng máy in không in được)
_ALLOWED = ('.png', '.jpg', '.jpeg', '.webp', '.bmp', '.pdf')


def _a3_dir():
    d = os.path.join(settings.MEDIA_ROOT, _DIRNAME)
    os.makedirs(d, exist_ok=True)
    return d


def _safe_stem(name):
    base = os.path.splitext(os.path.basename(name or 'file'))[0]
    base = re.sub(r'[^0-9A-Za-z_.\-]+', '_', base).strip('_') or 'file'
    return base[:50]


def _list_files():
    """Danh sách file trong kho IN A3 (mới nhất trước)."""
    d = _a3_dir()
    items = []
    for fn in os.listdir(d):
        ext = (os.path.splitext(fn)[1] or '').lower()
        if ext not in _ALLOWED:
            continue
        p = os.path.join(d, fn)
        try:
            mt = os.path.getmtime(p)
        except OSError:
            mt = 0
        items.append({'name': fn, 'url': f'/media/{_DIRNAME}/{fn}',
                      'is_pdf': ext == '.pdf', 'mtime': mt})
    items.sort(key=lambda x: -x['mtime'])
    return items


@staff_required
def in_a3(request):
    """Trang IN A3: kho file đã tải + đặt số lượng + nút tạo PDF in."""
    return render(request, 'in_a3.html', {'files': _list_files()})


@staff_required
def in_a3_upload(request):
    """Tải 1 hoặc nhiều file lên kho IN A3."""
    if request.method != 'POST':
        return HttpResponseNotFound('POST only')
    saved = 0
    for f in request.FILES.getlist('files'):
        ext = (os.path.splitext(f.name or '')[1] or '').lower()
        if ext not in _ALLOWED:
            continue
        # Lưu theo TÊN GỐC (đã làm sạch) cho dễ tìm; trùng tên -> ghi đè (coi như cập nhật).
        fn = f'{_safe_stem(f.name)}{ext}'
        with open(os.path.join(_a3_dir(), fn), 'wb') as out:
            for chunk in f.chunks():
                out.write(chunk)
        # xoá thumbnail cũ (nếu có) để render lại đúng nội dung mới
        tp = os.path.join(_a3_dir(), '_thumbs', fn + '.png')
        try:
            if os.path.exists(tp):
                os.remove(tp)
        except OSError:
            pass
        saved += 1
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return JsonResponse({'ok': True, 'saved': saved})
    return redirect('/in-a3')


@staff_required
def in_a3_thumb(request):
    """Ảnh thu nhỏ trang 1 của file PDF (cache) -> hiện nội dung thật trong kho thay vì
    icon chung chung. Ảnh thường thì dùng thẳng /media nên không cần endpoint này."""
    name = os.path.basename(request.GET.get('name') or '')
    path = os.path.join(_a3_dir(), name)
    ext = (os.path.splitext(name)[1] or '').lower()
    if not name or ext != '.pdf' or not os.path.exists(path):
        return HttpResponseNotFound('no pdf')
    thumbs = os.path.join(_a3_dir(), '_thumbs')
    os.makedirs(thumbs, exist_ok=True)
    tp = os.path.join(thumbs, name + '.png')
    if not os.path.exists(tp) or os.path.getmtime(tp) < os.path.getmtime(path):
        fitz = _import_fitz()
        if fitz is None:
            return HttpResponseNotFound('no fitz')
        try:
            src = fitz.open(path)
            try:
                pg = src.load_page(0)
                zoom = 320.0 / max(pg.rect.width, pg.rect.height, 1)   # ~320px cạnh dài
                pix = pg.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
                pix.save(tp)
            finally:
                src.close()
        except Exception:
            return HttpResponseNotFound('thumb err')
    resp = HttpResponse(open(tp, 'rb').read(), content_type='image/png')
    resp['Cache-Control'] = 'max-age=86400'
    return resp


@staff_required
def in_a3_xoa(request):
    """Xoá 1 file khỏi kho IN A3."""
    if request.method != 'POST':
        return HttpResponseNotFound('POST only')
    name = os.path.basename(request.POST.get('name') or '')
    if name:
        for p in (os.path.join(_a3_dir(), name),
                  os.path.join(_a3_dir(), '_thumbs', name + '.png')):
            if os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return JsonResponse({'ok': True})
    return redirect('/in-a3')


def _fit_rect(fitz, cw, ch, pr, m):
    """Hình chữ nhật canh GIỮA, vừa khít trong trang 'pr' (trừ lề m), giữ tỉ lệ cw×ch."""
    aw, ah = pr.width - 2 * m, pr.height - 2 * m
    s = min(aw / float(cw), ah / float(ch))
    w, h = cw * s, ch * s
    x0 = pr.x0 + m + (aw - w) / 2.0
    y0 = pr.y0 + m + (ah - h) / 2.0
    return fitz.Rect(x0, y0, x0 + w, y0 + h)


@staff_required
def in_a3_pdf(request):
    """Tạo MỘT PDF A3: mỗi file (đã chọn) -> lặp 'qty' trang, mỗi trang canh giữa & vừa
    khổ A3 (tự xoay ngang nếu file ngang). Trả PDF mở thẳng trong trình duyệt để in."""
    if request.method != 'POST':
        return HttpResponseNotFound('POST only')
    fitz = _import_fitz()
    if fitz is None:
        return JsonResponse({'ok': False,
                             'msg': 'Server chưa cài pymupdf — chạy update.sh.'}, status=500)
    d = _a3_dir()
    doc = fitz.open()
    total = 0
    for name in request.POST.getlist('names'):
        name = os.path.basename(name)
        path = os.path.join(d, name)
        if not os.path.exists(path):
            continue
        try:
            qty = max(1, min(500, int(request.POST.get(f'qty_{name}') or 1)))
        except ValueError:
            qty = 1
        ext = (os.path.splitext(name)[1] or '').lower()
        for _ in range(qty):
            if ext == '.pdf':
                src = fitz.open(path)
                try:
                    for sp in src:
                        r = sp.rect
                        land = r.width > r.height
                        pw, ph = (A3_H_PT, A3_W_PT) if land else (A3_W_PT, A3_H_PT)
                        page = doc.new_page(width=pw, height=ph)
                        page.show_pdf_page(
                            _fit_rect(fitz, r.width, r.height, page.rect, _MARGIN_PT),
                            src, sp.number)
                finally:
                    src.close()
            else:
                try:
                    with Image.open(path) as im:
                        iw, ih = im.size
                except Exception:
                    iw, ih = int(A3_W_PT), int(A3_H_PT)
                land = iw > ih
                pw, ph = (A3_H_PT, A3_W_PT) if land else (A3_W_PT, A3_H_PT)
                page = doc.new_page(width=pw, height=ph)
                page.insert_image(_fit_rect(fitz, iw, ih, page.rect, _MARGIN_PT), filename=path)
            total += 1
    if total == 0:
        doc.close()
        return JsonResponse({'ok': False, 'msg': 'Chưa chọn file nào để in.'}, status=400)
    fd, tmp = tempfile.mkstemp(suffix='.pdf', prefix='in_a3_')
    os.close(fd)
    doc.save(tmp, deflate=True, garbage=3)    # nén -> PDF nhẹ (như khâu Ghép in)
    doc.close()
    try:
        data = open(tmp, 'rb').read()
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    resp = HttpResponse(data, content_type='application/pdf')
    resp['Content-Disposition'] = (
        f'inline; filename="in_a3_{time.strftime("%Y%m%d_%H%M%S")}_{total}trang.pdf"')
    return resp
