# -*- coding: utf-8 -*-
"""GHÉP KHỔ IN (imposition / nesting) — xếp các tranh (đã có viền in) lên khổ vải cố
định sao cho TỐN ÍT VẢI nhất, rồi xuất PDF đúng kích thước thật để đưa vào Flexi (RIP).

- Khổ vải: chiều RỘNG cố định (vd 151.5cm), chiều DÀI tự do (cuộn) -> bài toán strip
  packing: nhét hình chữ nhật vào dải rộng W, tối thiểu chiều dài L.
- Thuật toán: SKYLINE bottom-left (xếp dày, tự lấp khoảng trống lệch chiều cao,
  trộn nhiều cỡ trong 1 hàng), có tuỳ chọn xoay 90°.
- Đơn vị nội bộ: mm (số nguyên) cho chắc; xuất PDF quy ra point (1cm = 28.3465pt).

Mỗi tranh tự cắt rời theo viền nên không cần cắt kiểu guillotine -> xếp dày tối đa.
"""
import os

from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt

MM_PER_CM = 10.0
PT_PER_MM = 72.0 / 25.4   # 1 inch = 25.4mm = 72pt

# Khi up file PDF (xuất từ Illustrator), rasterize trang 1 sang PNG ở độ phân giải này.
RASTER_DPI = 200
_MAX_RASTER_PX = 6000          # chặn ảnh quá lớn nếu khổ artboard bị đặt sai
_IMG_EXTS = ('.png', '.jpg', '.jpeg', '.webp', '.tif', '.tiff')


def _looks_pdf(name, data=None):
    if (os.path.splitext(name or '')[1] or '').lower() == '.pdf':
        return True
    if data and data[:5] == b'%PDF-':
        return True
    return False


def _save_print_image(f, abs_dir, stem):
    """Lưu file tải lên thành ẢNH dùng được. Ảnh thường -> ghi nguyên. PDF (xuất từ
    Illustrator) -> rasterize trang 1 sang PNG ở RASTER_DPI (theo khổ thật của trang).
    Trả (filename, error_msg); một trong hai là None."""
    os.makedirs(abs_dir, exist_ok=True)
    data = f.read()
    name = getattr(f, 'name', '') or ''
    if _looks_pdf(name, data):
        try:
            try:
                import fitz  # PyMuPDF
            except ImportError:
                import pymupdf as fitz
        except Exception:
            return None, 'Server chưa cài thư viện đọc PDF (pymupdf) — chạy update.sh, hoặc up ảnh PNG/JPG.'
        try:
            doc = fitz.open(stream=data, filetype='pdf')
            if doc.page_count < 1:
                doc.close()
                return None, 'PDF rỗng (không có trang).'
            page = doc.load_page(0)
            zoom = RASTER_DPI / 72.0
            longest = max(page.rect.width, page.rect.height) * zoom
            if longest > _MAX_RASTER_PX:
                zoom *= _MAX_RASTER_PX / longest
            pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
            fn = stem + '.png'
            pix.save(os.path.join(abs_dir, fn))
            doc.close()
            return fn, None
        except Exception as e:
            return None, 'Không đọc được PDF: ' + str(e)[:120]
    # Ảnh bitmap thường
    ext = (os.path.splitext(name)[1] or '.png').lower()
    if ext not in _IMG_EXTS:
        ext = '.png'
    fn = stem + ext
    with open(os.path.join(abs_dir, fn), 'wb') as out:
        out.write(data)
    return fn, None


def _skyline_pack(items, width_mm, gap_mm=0, allow_rotate=False):
    """Xếp các hình chữ nhật vào dải rộng width_mm (mm). items: list dict
    {id, w, h, ...} (mm). Trả (placements, length_mm, used_area_mm2).
    placements: [{...item, x, y, w, h, rot}] với (x,y) góc dưới-trái, y tính từ đáy lên.
    """
    W = float(width_mm)
    # Skyline = danh sách đoạn [x, width, y(đỉnh)] theo trục X, phủ kín [0, W]
    sky = [[0.0, W, 0.0]]
    placements = []
    used_area = 0.0

    def _can_place(idx, w, h):
        """Thử đặt vật rộng w vào bắt đầu từ đoạn idx; trả y đặt được hoặc None."""
        x = sky[idx][0]
        if x + w > W + 1e-6:
            return None
        remain = w
        y = 0.0
        i = idx
        while remain > 1e-6:
            if i >= len(sky):
                return None
            y = max(y, sky[i][2])
            remain -= sky[i][1]
            i += 1
        return y

    def _place(x, w, top_y):
        """Cập nhật skyline sau khi đặt vật [x, x+w] cao tới top_y."""
        new = []
        placed = False
        i = 0
        while i < len(sky):
            seg = sky[i]
            sx, sw, sy = seg
            ex = sx + sw
            if ex <= x + 1e-6 or sx >= x + w - 1e-6:
                new.append(seg)          # đoạn ngoài vùng đặt -> giữ nguyên
                i += 1
                continue
            # đoạn giao vùng đặt -> tách phần trái / phần đặt / phần phải
            if sx < x - 1e-6:
                new.append([sx, x - sx, sy])
            if not placed:
                new.append([x, w, top_y])
                placed = True
            if ex > x + w + 1e-6:
                new.append([x + w, ex - (x + w), sy])
            i += 1
        # gộp các đoạn cùng độ cao liền kề
        merged = []
        for seg in sorted(new, key=lambda s: s[0]):
            if merged and abs(merged[-1][2] - seg[2]) < 1e-6 and \
               abs(merged[-1][0] + merged[-1][1] - seg[0]) < 1e-6:
                merged[-1][1] += seg[1]
            else:
                merged.append(list(seg))
        sky[:] = merged

    # Xếp vật cao->thấp, rộng->hẹp cho gọn
    order = sorted(items, key=lambda it: (-it['h'], -it['w']))
    for it in order:
        cands = [(it['w'], it['h'], 0)]
        if allow_rotate and it['w'] != it['h']:
            cands.append((it['h'], it['w'], 1))
        best = None   # (y, x, w, h, rot, seg_idx)
        for (w, h, rot) in cands:
            ww = w + gap_mm
            for idx in range(len(sky)):
                y = _can_place(idx, ww, h)
                if y is None:
                    continue
                cand = (y, sky[idx][0], w, h, rot)
                if best is None or (cand[0], cand[1]) < (best[0], best[1]):
                    best = cand
        if best is None:
            continue   # không vừa (không xảy ra nếu vật <= W)
        y, x, w, h, rot = best
        _place(x, w + gap_mm, y + h + gap_mm)
        placements.append({**it, 'x': x, 'y': y, 'w': w, 'h': h, 'rot': rot})
        used_area += w * h

    length_mm = max((p['y'] + p['h'] for p in placements), default=0.0)
    return placements, length_mm, used_area


def plan(items, width_cm=151.5, gap_cm=0.0, allow_rotate=False):
    """items: [{id, image_path, w_cm, h_cm, qty, label}]. Trả dict kế hoạch xếp."""
    width_mm = round(width_cm * MM_PER_CM)
    gap_mm = round(gap_cm * MM_PER_CM)
    rects = []
    for it in items:
        w = round(it['w_cm'] * MM_PER_CM)
        h = round(it['h_cm'] * MM_PER_CM)
        for k in range(max(1, int(it['qty']))):
            rects.append({'id': it['id'], 'image_path': it.get('image_path'),
                          'label': it.get('label', ''), 'w': w, 'h': h,
                          'w_cm': it['w_cm'], 'h_cm': it['h_cm']})
    placements, length_mm, used_area = _skyline_pack(rects, width_mm, gap_mm, allow_rotate)
    sheet_area = width_mm * length_mm if length_mm else 1
    return {
        'placements': placements,
        'width_mm': width_mm, 'length_mm': length_mm,
        'width_cm': width_cm, 'length_cm': length_mm / MM_PER_CM,
        'count': len(placements),
        'utilization': round(used_area / sheet_area * 100, 1) if sheet_area else 0.0,
        'meters': round(length_mm / 1000.0, 2),
    }


def render_pdf(planned, out_path, title=''):
    """Xuất PDF đúng kích thước thật: trang = khổ rộng × dài đã dùng, đặt từng ảnh
    đúng vị trí (cm). reportlab nhúng mỗi ảnh 1 lần ở độ phân giải gốc -> file nhẹ, nét."""
    from reportlab.pdfgen import canvas
    from reportlab.lib.utils import ImageReader
    W_pt = planned['width_mm'] * PT_PER_MM
    L_pt = max(planned['length_mm'], 1) * PT_PER_MM
    c = canvas.Canvas(out_path, pagesize=(W_pt, L_pt))
    if title:
        c.setTitle(title)
    for p in planned['placements']:
        x_pt = p['x'] * PT_PER_MM
        w_pt = p['w'] * PT_PER_MM
        h_pt = p['h'] * PT_PER_MM
        # PDF gốc ở góc dưới-trái; y nội bộ tính từ đáy -> trùng luôn
        y_pt = p['y'] * PT_PER_MM
        img = p.get('image_path')
        if img and os.path.exists(img):
            try:
                ir = ImageReader(img)
                if p.get('rot'):
                    c.saveState()
                    c.translate(x_pt, y_pt)
                    c.rotate(90)
                    # sau khi xoay: vẽ vào hộp (h_pt rộng theo trục mới)
                    c.drawImage(ir, 0, -w_pt, width=h_pt, height=w_pt,
                                preserveAspectRatio=False, mask='auto')
                    c.restoreState()
                else:
                    c.drawImage(ir, x_pt, y_pt, width=w_pt, height=h_pt,
                                preserveAspectRatio=False, mask='auto')
            except Exception:
                c.rect(x_pt, y_pt, w_pt, h_pt, stroke=1, fill=0)
        else:
            c.rect(x_pt, y_pt, w_pt, h_pt, stroke=1, fill=0)
    c.showPage()
    c.save()
    return out_path


def render_preview(planned, out_path, scale=2.0, image_paths=None):
    """Ảnh PNG xem trước bố cục (thu nhỏ) bằng Pillow — để hiển thị trên web."""
    from PIL import Image, ImageDraw
    W = max(1, int(planned['width_mm'] * scale / 10))      # ~scale px / cm
    H = max(1, int(max(planned['length_mm'], 10) * scale / 10))
    img = Image.new('RGB', (W, H), '#e9ecef')
    d = ImageDraw.Draw(img)
    for p in planned['placements']:
        x0 = int(p['x'] * scale / 10)
        y0 = int(p['y'] * scale / 10)
        w = int(p['w'] * scale / 10)
        h = int(p['h'] * scale / 10)
        # y từ đáy -> đảo trục cho ảnh (đỉnh ở trên)
        top = H - y0 - h
        thumb = None
        ip = p.get('image_path')
        if ip and os.path.exists(ip):
            try:
                thumb = Image.open(ip).convert('RGB').resize((max(1, w), max(1, h)))
            except Exception:
                thumb = None
        if thumb is not None:
            img.paste(thumb, (x0, top))
        else:
            d.rectangle([x0, top, x0 + w, top + h], fill='#cfe2d8')
        d.rectangle([x0, top, x0 + w - 1, top + h - 1], outline='#198754', width=1)
    img.save(out_path)
    return out_path


# ===================== TRANG WEB GHÉP KHỔ IN =====================
PRESETS = [
    {'label': '20x20', 'w': 28, 'h': 28},
    {'label': '30x30', 'w': 38, 'h': 38},
    {'label': '30x37.5', 'w': 38, 'h': 45.5},
]


@csrf_exempt
def ghep_in(request):
    """Trang GHÉP KHỔ IN: up ảnh in (đã có viền) + chọn cỡ/SL -> xếp kín khổ vải ->
    xuất PDF cho Flexi. Chỉ quản lý."""
    from django.conf import settings
    from pha.views import _now
    if not getattr(request.user, 'is_staff', False):
        from django.shortcuts import redirect
        return redirect('/login')

    from pha.models import PrintArt

    def _f(v, d):
        try:
            return float(str(v).replace(',', '.'))
        except (TypeError, ValueError):
            return d

    # ----- Quản lý KHO ẢNH IN (lưu sẵn theo mã, dùng lại) -----
    if request.method == 'POST' and request.POST.get('action') == 'save_art':
        import secrets
        from django.shortcuts import redirect
        f = request.FILES.get('art_img')
        code = (request.POST.get('art_code') or '').strip().upper()
        w_cm = _f(request.POST.get('art_w'), 0)
        h_cm = _f(request.POST.get('art_h'), 0)
        if f and code and w_cm > 0 and h_cm > 0:
            outdir = os.path.join(settings.MEDIA_ROOT, 'print_art')
            stem = '%s_%s' % (_now().strftime('%Y%m%d%H%M%S'), secrets.token_hex(3))
            fn, err = _save_print_image(f, outdir, stem)
            if err:
                messages.error(request, err)
                return redirect('/ghep-in')
            PrintArt.objects.create(code=code, image='print_art/' + fn, w_cm=w_cm, h_cm=h_cm,
                                    note=(request.POST.get('art_note') or '').strip())
            messages.info(request, f'Đã lưu vào kho: {code} ({w_cm:g}×{h_cm:g})')
        else:
            messages.error(request, 'Cần đủ: ảnh + mã tranh + rộng + cao.')
        return redirect('/ghep-in')
    if request.method == 'POST' and request.POST.get('action') == 'del_art':
        from django.shortcuts import redirect
        a = PrintArt.objects.filter(id=request.POST.get('id')).first()
        if a:
            try:
                p = os.path.join(settings.MEDIA_ROOT, a.image)
                if a.image and os.path.exists(p):
                    os.remove(p)
            except OSError:
                pass
            a.delete()
            messages.info(request, 'Đã xoá khỏi kho.')
        return redirect('/ghep-in')
    if request.method == 'POST' and request.POST.get('action') == 'edit_art':
        from django.shortcuts import redirect
        a = PrintArt.objects.filter(id=request.POST.get('id')).first()
        if a:
            w = _f(request.POST.get('w'), a.w_cm)
            h = _f(request.POST.get('h'), a.h_cm)
            if w > 0 and h > 0:
                a.w_cm = w
                a.h_cm = h
                a.save()
                messages.info(request, f'Đã cập nhật cỡ {a.code}: {w:g}×{h:g}cm')
            else:
                messages.error(request, 'Cỡ không hợp lệ.')
        return redirect('/ghep-in')

    if request.method == 'POST':
        import secrets
        from PIL import Image
        imgs = request.FILES.getlist('img')
        ws = request.POST.getlist('w')
        hs = request.POST.getlist('h')
        qtys = request.POST.getlist('qty')
        labels = request.POST.getlist('label')
        width_cm = _f(request.POST.get('width_cm'), 151.5)
        gap_cm = _f(request.POST.get('gap_cm'), 0.0)
        rotate = request.POST.get('rotate') == '1'
        target_dpi = int(_f(request.POST.get('dpi'), 150))

        outdir = os.path.join(settings.MEDIA_ROOT, 'ghep')
        os.makedirs(outdir, exist_ok=True)
        stamp = _now().strftime('%Y%m%d_%H%M%S') + '_' + secrets.token_hex(3)
        items, warnings = [], []
        for i, f in enumerate(imgs):
            w_cm = _f(ws[i] if i < len(ws) else None, 0)
            h_cm = _f(hs[i] if i < len(hs) else None, 0)
            try:
                qty = max(1, int(qtys[i])) if i < len(qtys) else 1
            except (ValueError, TypeError):
                qty = 1
            if w_cm <= 0 or h_cm <= 0:
                continue
            fn, err = _save_print_image(f, outdir, 'src_%s_%d' % (stamp, i))
            if err:
                warnings.append('%s: %s' % (f.name, err))
                continue
            path = os.path.join(outdir, fn)
            try:
                im = Image.open(path)
                dpi = im.size[0] / (w_cm / 2.54)
                if dpi < target_dpi - 5:
                    warnings.append('%s: chỉ ~%d DPI (nên ≥ %d) — in có thể mờ'
                                    % (f.name, int(dpi), target_dpi))
            except Exception:
                pass
            items.append({'id': f.name, 'image_path': path, 'w_cm': w_cm, 'h_cm': h_cm,
                          'qty': qty, 'label': labels[i] if i < len(labels) else ''})

        # Tranh chọn TỪ KHO (lưu sẵn) — không cần tải lại
        sel_ids = request.POST.getlist('sel_art')
        sel_qtys = request.POST.getlist('sel_qty')
        if sel_ids:
            arts = {str(a.id): a for a in PrintArt.objects.filter(id__in=[s for s in sel_ids if s.isdigit()])}
            for j, aid in enumerate(sel_ids):
                a = arts.get(str(aid))
                if not a:
                    continue
                try:
                    qty = max(1, int(sel_qtys[j])) if j < len(sel_qtys) else 1
                except (ValueError, TypeError):
                    qty = 1
                p = os.path.join(settings.MEDIA_ROOT, a.image)
                if not os.path.exists(p):
                    warnings.append('Kho: %s thiếu file ảnh — bỏ qua.' % a.code)
                    continue
                try:
                    im = Image.open(p)
                    dpi = im.size[0] / (a.w_cm / 2.54)
                    if dpi < target_dpi - 5:
                        warnings.append('%s: chỉ ~%d DPI (nên ≥ %d) — in có thể mờ'
                                        % (a.code, int(dpi), target_dpi))
                except Exception:
                    pass
                items.append({'id': a.code, 'image_path': p, 'w_cm': a.w_cm, 'h_cm': a.h_cm,
                              'qty': qty, 'label': a.code})

        if not items:
            return JsonResponse({'ok': False, 'msg': 'Chưa chọn/thêm tranh nào (chọn từ kho hoặc tải ảnh + kích thước).'})

        planned = plan(items, width_cm=width_cm, gap_cm=gap_cm, allow_rotate=rotate)
        pdf_rel = 'ghep/ghep_%s.pdf' % stamp
        prev_rel = 'ghep/ghep_%s.png' % stamp
        try:
            render_pdf(planned, os.path.join(settings.MEDIA_ROOT, pdf_rel), title='Ghep in DALI')
            render_preview(planned, os.path.join(settings.MEDIA_ROOT, prev_rel))
        except Exception as e:
            return JsonResponse({'ok': False, 'msg': 'Lỗi tạo file: ' + str(e)[:160]})
        return JsonResponse({
            'ok': True, 'pdf': '/media/' + pdf_rel, 'preview': '/media/' + prev_rel,
            'count': planned['count'], 'meters': planned['meters'],
            'util': planned['utilization'], 'length_cm': round(planned['length_cm'], 1),
            'width_cm': round(planned['width_cm'], 1), 'warnings': warnings,
        })

    arts = [{'id': a.id, 'code': a.code, 'url': '/media/' + a.image,
             'w': a.w_cm, 'h': a.h_cm, 'note': a.note}
            for a in PrintArt.objects.all()]
    return render(request, 'ghep_in.html', {'presets': PRESETS, 'arts': arts})
