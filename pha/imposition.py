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
_PDF_HINT = 'là PDF nhưng server chưa cài pymupdf — chạy update.sh, hoặc up ảnh PNG/JPG'


def _import_fitz():
    """Trả module PyMuPDF (fitz) hoặc None nếu chưa cài."""
    try:
        import fitz
        return fitz
    except ImportError:
        try:
            import pymupdf as fitz
            return fitz
        except Exception:
            return None
    except Exception:
        return None


def _looks_pdf(name, data=None):
    if (os.path.splitext(name or '')[1] or '').lower() == '.pdf':
        return True
    if data and data[:5] == b'%PDF-':
        return True
    return False


def _rasterize_pdf(data, out_png, fitz):
    """Rasterize trang 1 của PDF (bytes) -> PNG ở RASTER_DPI theo khổ thật của trang.
    Ném lỗi nếu PDF rỗng/hỏng."""
    doc = fitz.open(stream=data, filetype='pdf')
    try:
        if doc.page_count < 1:
            raise ValueError('PDF rỗng (không có trang)')
        page = doc.load_page(0)
        zoom = RASTER_DPI / 72.0
        longest = max(page.rect.width, page.rect.height) * zoom
        if longest > _MAX_RASTER_PX:
            zoom *= _MAX_RASTER_PX / longest
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        pix.save(out_png)
    finally:
        doc.close()


def _save_print_image(f, abs_dir, stem):
    """Lưu file tải lên thành ẢNH dùng được. Ảnh thường -> ghi nguyên. PDF (xuất từ
    Illustrator) -> rasterize trang 1 sang PNG ở RASTER_DPI (theo khổ thật của trang).
    Trả (filename, error_msg); một trong hai là None."""
    os.makedirs(abs_dir, exist_ok=True)
    data = f.read()
    name = getattr(f, 'name', '') or ''
    if _looks_pdf(name, data):
        fitz = _import_fitz()
        if fitz is None:
            return None, 'Server chưa cài thư viện đọc PDF (pymupdf) — chạy update.sh, hoặc up ảnh PNG/JPG.'
        fn = stem + '.png'
        try:
            _rasterize_pdf(data, os.path.join(abs_dir, fn), fitz)
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


def _resolve_raster(abs_path, label=''):
    """Bảo đảm abs_path là ẢNH BITMAP nhúng được. Tự sửa các trường hợp hỏng:
      • file thực ra là PDF (vd kho cũ lưu PDF nhưng đặt tên .png) -> rasterize lại
      • ảnh CMYK/đặc biệt -> chuyển RGB
    Trả (path_dùng_được | None, warning | None). Nếu None thì warning nêu RÕ lý do."""
    label = label or os.path.basename(abs_path or '?')
    if not abs_path or not os.path.exists(abs_path):
        return None, '%s: thiếu file ảnh' % label
    try:
        with open(abs_path, 'rb') as fh:
            head = fh.read(5)
    except OSError:
        return None, '%s: không đọc được file' % label
    # File thực chất là PDF (đuôi .png nhưng nội dung %PDF) -> rasterize lại
    if head[:5] == b'%PDF-':
        fitz = _import_fitz()
        if fitz is None:
            return None, '%s: %s' % (label, _PDF_HINT)
        png = os.path.splitext(abs_path)[0] + '_r.png'
        try:
            if not os.path.exists(png):
                with open(abs_path, 'rb') as fh:
                    _rasterize_pdf(fh.read(), png, fitz)
            abs_path = png
        except Exception as e:
            return None, '%s: lỗi đọc PDF (%s)' % (label, str(e)[:60])
    # Xác thực + chuẩn hoá màu bằng Pillow
    try:
        from PIL import Image
        im = Image.open(abs_path)
        im.load()
        if im.mode not in ('RGB', 'L'):
            norm = os.path.splitext(abs_path)[0] + '_rgb.png'
            if not os.path.exists(norm):
                im.convert('RGB').save(norm)
            abs_path = norm
        return abs_path, None
    except Exception as e:
        return None, '%s: ảnh hỏng/không mở được (%s)' % (label, str(e)[:50])


def _dpi_warning(path, w_cm, target_dpi, label):
    """Cảnh báo nếu ảnh thấp hơn DPI mục tiêu (in dễ mờ). Trả str hoặc None."""
    try:
        from PIL import Image
        with Image.open(path) as im:
            px = im.size[0]
        dpi = px / (w_cm / 2.54)
        if dpi < target_dpi - 5:
            return '%s: chỉ ~%d DPI (nên ≥ %d) — in có thể mờ' % (label, int(dpi), target_dpi)
    except Exception:
        pass
    return None


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


def _guillotine_pack(rects, W, gap=0):
    """Guillotine Best-Area-Fit (split trục ngắn + gộp free-rect). Thường xếp gọn
    hơn skyline với LÔ TRỘN CÂN BẰNG (vd nhiều 28 lẫn 38), nhưng có lúc tệ hơn ->
    dùng trong _pack_best (chạy cùng skyline rồi chọn cái ngắn hơn)."""
    INF = float('inf')
    placements = []
    order = sorted(range(len(rects)), key=lambda i: (-(rects[i]['h']), -(rects[i]['w'])))
    free = [(0, 0, W, INF)]

    def split_free(fr, used_w, used_h):
        fx, fy, fw, fh = fr
        parts = []
        leftover_w = fw - used_w
        leftover_h = (fh - used_h) if fh != INF else INF
        cmp_h = leftover_h if leftover_h != INF else (leftover_w + 1)
        if leftover_w <= cmp_h:
            if leftover_w > 0:
                parts.append((fx + used_w, fy, leftover_w, used_h))
            if leftover_h != 0:
                parts.append((fx, fy + used_h, fw, (fh - used_h) if fh != INF else INF))
        else:
            if leftover_h != 0:
                parts.append((fx, fy + used_h, used_w, (fh - used_h) if fh != INF else INF))
            if leftover_w > 0:
                parts.append((fx + used_w, fy, leftover_w, fh))
        return parts

    def contained(a, b):
        ax, ay, aw, ah = a
        bx, by, bw, bh = b
        return bx <= ax and by <= ay and ax + aw <= bx + bw and ay + ah <= by + bh

    def prune(fl):
        n = len(fl)
        removed = [False] * n
        for i in range(n):
            if removed[i]:
                continue
            for j in range(n):
                if i == j or removed[j]:
                    continue
                if contained(fl[i], fl[j]):
                    removed[i] = True
                    break
        return [fl[i] for i in range(n) if not removed[i]]

    def merge_free(fl):
        merged = True
        fl = list(fl)
        while merged:
            merged = False
            n = len(fl)
            for i in range(n):
                for j in range(i + 1, n):
                    ax, ay, aw, ah = fl[i]
                    bx, by, bw, bh = fl[j]
                    if ay == by and ah == bh:
                        if ax + aw == bx:
                            fl[i] = (ax, ay, aw + bw, ah); del fl[j]; merged = True; break
                        if bx + bw == ax:
                            fl[i] = (bx, by, aw + bw, ah); del fl[j]; merged = True; break
                    if ax == bx and aw == bw:
                        if ah != INF and ay + ah == by:
                            fl[i] = (ax, ay, aw, ah + bh if bh != INF else INF); del fl[j]; merged = True; break
                        if bh != INF and by + bh == ay:
                            fl[i] = (bx, by, aw, ah + bh if ah != INF else INF); del fl[j]; merged = True; break
                if merged:
                    break
        return fl

    for idx in order:
        r = rects[idx]
        rw = r['w'] + gap
        rh = r['h'] + gap
        best_i, best_score, best_y = -1, INF, INF
        for i, fr in enumerate(free):
            fx, fy, fw, fh = fr
            if rw <= fw and rh <= fh:
                score = (fw - rw) + 1e12 + fy if fh == INF else fw * fh - rw * rh
                if score < best_score - 1e-9 or (abs(score - best_score) <= 1e-9 and fy < best_y):
                    best_score, best_i, best_y = score, i, fy
        if best_i == -1:
            maxy = max((p['y'] + p['h'] for p in placements), default=0)
            placements.append({**r, 'x': 0, 'y': maxy, 'w': r['w'], 'h': r['h']})
            continue
        fr = free[best_i]
        placements.append({**r, 'x': fr[0], 'y': fr[1], 'w': r['w'], 'h': r['h']})
        del free[best_i]
        for p in split_free(fr, rw, rh):
            if p[2] > 0 and p[3] != 0:
                free.append(p)
        free = prune(merge_free(prune(free)))

    length = max((p['y'] + p['h'] for p in placements), default=0)
    used_area = sum(p['w'] * p['h'] for p in placements)
    return placements, length, used_area


def _column_pack(rects, W, gap=0):
    """Xếp theo CỘT cùng cỡ: gom tranh theo (rộng×cao), chọn số cột mỗi cỡ sao cho
    tổng bề ngang lấp đầy khổ vải nhất và CHIỀU DÀI (cột cao nhất) ngắn nhất, rồi
    rải đều tranh vào các cột. Cho layout sạch & kín như xếp tay (vd 4 cột 28 + 1 cột 38).
    Hợp khi lô gồm ít cỡ khác nhau (≤ vài loại)."""
    import math
    from collections import defaultdict
    from itertools import product
    groups = defaultdict(list)
    for r in rects:
        groups[(r['w'], r['h'])].append(r)
    glist = list(groups.items())                 # [((w,h),[rects...]), ...]
    keys = [k for k, _ in glist]
    counts = [len(v) for _, v in glist]
    maxcols = [max(1, min(counts[i], int(W // max(1, keys[i][0])))) for i in range(len(keys))]
    space = 1
    for m in maxcols:
        space *= m
    if space > 200000:                           # quá nhiều tổ hợp -> nhường packer khác
        return [], 0, 0

    def width_used(alloc):
        return sum(alloc[i] * keys[i][0] for i in range(len(keys))) + gap * (sum(alloc) - 1)

    def length_of(alloc):
        h = 0
        for i in range(len(keys)):
            rows = math.ceil(counts[i] / alloc[i])
            h = max(h, rows * keys[i][1] + (rows - 1) * gap)
        return h

    best = None
    for alloc in product(*[range(1, m + 1) for m in maxcols]):
        if width_used(alloc) > W + 1e-6:
            continue
        key = (length_of(alloc), -width_used(alloc))   # dài ngắn nhất, rồi kín nhất
        if best is None or key < best[0]:
            best = (key, alloc)
    if best is None:
        return [], 0, 0

    alloc = best[1]
    placements = []
    x = 0.0
    for i, (k, rs) in enumerate(glist):
        w_g, h_g = k
        c = alloc[i]
        n = counts[i]
        per = [n // c + (1 if j < n % c else 0) for j in range(c)]   # rải đều
        ri = 0
        for j in range(c):
            y = 0.0
            for _ in range(per[j]):
                r = rs[ri]; ri += 1
                placements.append({**r, 'x': x, 'y': y, 'w': w_g, 'h': h_g})
                y += h_g + gap
            x += w_g + gap
    length = max((p['y'] + p['h'] for p in placements), default=0)
    used = sum(p['w'] * p['h'] for p in placements)
    return placements, length, used


def _valid_layout(placements, width_mm, tol=0.5):
    """Kiểm tra layout hợp lệ: trong khổ vải + KHÔNG có 2 tranh chồng nhau."""
    for p in placements:
        if p['x'] < -tol or p['x'] + p['w'] > width_mm + tol or p['y'] < -tol:
            return False
    n = len(placements)
    for i in range(n):
        a = placements[i]; ax, ay, aw, ah = a['x'], a['y'], a['w'], a['h']
        for j in range(i + 1, n):
            b = placements[j]
            if ax < b['x'] + b['w'] - tol and b['x'] < ax + aw - tol and \
               ay < b['y'] + b['h'] - tol and b['y'] < ay + ah - tol:
                return False
    return True


def _pack_best(rects, width_mm, gap_mm=0, allow_rotate=False):
    """Chạy NHIỀU thuật toán (skyline + guillotine), loại layout lỗi, chọn cái NGẮN
    nhất. Skyline luôn là 1 ứng viên nên kết quả không bao giờ tệ hơn trước."""
    cands = []
    pl, L, u = _skyline_pack([dict(r) for r in rects], width_mm, gap_mm, allow_rotate)
    if _valid_layout(pl, width_mm):
        cands.append((L, u, pl))
    for _pk in (_guillotine_pack, _column_pack):
        try:
            pl2, L2, u2 = _pk([dict(r) for r in rects], width_mm, gap_mm)
            if pl2 and _valid_layout(pl2, width_mm):
                cands.append((L2, u2, pl2))
        except Exception:
            pass
    if not cands:                      # cực hiếm: trả skyline thô làm phương án cuối
        return pl, L, u
    cands.sort(key=lambda c: c[0])
    return cands[0][2], cands[0][0], cands[0][1]


def plan(items, width_cm=151.5, gap_cm=0.0, allow_rotate=False, overlap_cm=0.0):
    """items: [{id, image_path, w_cm, h_cm, qty, label}]. Trả dict kế hoạch xếp.

    overlap_cm > 0: CHỒNG MÍ — các tranh đè viền lên nhau `overlap_cm` để chia sẻ viền
    (tiết kiệm vải). Cách làm: xếp theo "diện tích chiếm chỗ" nhỏ hơn (cỡ thật − chồng mí)
    nên các ô không đè footprint (layout vẫn hợp lệ), nhưng VẼ ở cỡ thật → ảnh đè nhau
    đúng phần viền. Khi chồng mí thì khe hở bị bỏ qua và không xoay (giữ viền trùng khít).
    """
    width_mm = round(width_cm * MM_PER_CM)
    overlap_mm = max(0, round(overlap_cm * MM_PER_CM))
    gap_mm = 0 if overlap_mm > 0 else max(0, round(gap_cm * MM_PER_CM))
    if overlap_mm > 0:
        allow_rotate = False
    rects = []
    for it in items:
        dw = round(it['w_cm'] * MM_PER_CM)               # cỡ VẼ thật (có viền)
        dh = round(it['h_cm'] * MM_PER_CM)
        pw = max(1, dw - overlap_mm)                     # cỡ CHIẾM CHỖ khi xếp
        ph = max(1, dh - overlap_mm)
        for k in range(max(1, int(it['qty']))):
            rects.append({'id': it['id'], 'image_path': it.get('image_path'),
                          'label': it.get('label', ''), 'w': pw, 'h': ph,
                          'dw': dw, 'dh': dh,
                          'w_cm': it['w_cm'], 'h_cm': it['h_cm']})
    pack_width = max(1, width_mm - overlap_mm)
    placements, _lf, _uf = _pack_best(rects, pack_width, gap_mm, allow_rotate)
    # Chiều dài & độ phủ tính theo cỡ VẼ thật (gồm phần chồng mí)
    length_mm = max((p['y'] + p.get('dh', p['h']) for p in placements), default=0)
    sheet_area = width_mm * length_mm if length_mm else 1
    used_area = sum(p.get('dw', p['w']) * p.get('dh', p['h']) for p in placements)
    return {
        'placements': placements,
        'width_mm': width_mm, 'length_mm': length_mm,
        'width_cm': width_cm, 'length_cm': length_mm / MM_PER_CM,
        'count': len(placements),
        'utilization': round(min(100.0, used_area / sheet_area * 100), 1) if sheet_area else 0.0,
        'overlap_cm': overlap_mm / MM_PER_CM,
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
    embedded = 0

    def _miss_box(x_pt, y_pt, w_pt, h_pt, label):
        # Ô THIẾU ẢNH: tô đỏ nhạt + viền đỏ + chữ -> không bao giờ ra ô trắng "bí ẩn"
        c.saveState()
        c.setFillColorRGB(0.98, 0.86, 0.86)
        c.setStrokeColorRGB(0.8, 0.1, 0.1)
        c.setLineWidth(2)
        c.rect(x_pt, y_pt, w_pt, h_pt, stroke=1, fill=1)
        c.setFillColorRGB(0.7, 0, 0)
        fs = max(8, min(w_pt, h_pt) * 0.12)
        c.setFont('Helvetica-Bold', fs)
        txt = ('%s - THIEU ANH' % label) if label else 'THIEU ANH'
        c.drawCentredString(x_pt + w_pt / 2, y_pt + h_pt / 2 - fs / 2, txt[:40])
        c.restoreState()

    for p in planned['placements']:
        x_pt = p['x'] * PT_PER_MM
        w_pt = p.get('dw', p['w']) * PT_PER_MM          # vẽ ở cỡ THẬT (chồng mí nếu có)
        h_pt = p.get('dh', p['h']) * PT_PER_MM
        # PDF gốc ở góc dưới-trái; y nội bộ tính từ đáy -> trùng luôn
        y_pt = p['y'] * PT_PER_MM
        label = str(p.get('label') or p.get('id') or '')
        img = p.get('image_path')
        ok = False
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
                ok = True
                embedded += 1
            except Exception:
                ok = False
        if not ok:
            _miss_box(x_pt, y_pt, w_pt, h_pt, label)
    c.showPage()
    c.save()
    return embedded


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
        w = int(p.get('dw', p['w']) * scale / 10)
        h = int(p.get('dh', p['h']) * scale / 10)
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
        imgs = request.FILES.getlist('img')
        ws = request.POST.getlist('w')
        hs = request.POST.getlist('h')
        qtys = request.POST.getlist('qty')
        labels = request.POST.getlist('label')
        width_cm = _f(request.POST.get('width_cm'), 151.5)
        gap_cm = _f(request.POST.get('gap_cm'), 0.0)
        overlap_cm = max(0.0, _f(request.POST.get('overlap_cm'), 0.0))
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
            path, rerr = _resolve_raster(os.path.join(outdir, fn), f.name)
            if rerr:
                warnings.append(rerr)
                continue
            dw = _dpi_warning(path, w_cm, target_dpi, f.name)
            if dw:
                warnings.append(dw)
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
                rp, rerr = _resolve_raster(p, a.code)
                if rerr:
                    warnings.append('Kho ' + rerr)
                    continue
                dw = _dpi_warning(rp, a.w_cm, target_dpi, a.code)
                if dw:
                    warnings.append(dw)
                items.append({'id': a.code, 'image_path': rp, 'w_cm': a.w_cm, 'h_cm': a.h_cm,
                              'qty': qty, 'label': a.code})

        if not items:
            msg = 'Không nạp được tranh nào.'
            if warnings:
                msg += ' Lý do: ' + ' | '.join(warnings[:6])
            else:
                msg += ' Hãy chọn từ kho hoặc tải ảnh + nhập kích thước.'
            return JsonResponse({'ok': False, 'msg': msg, 'warnings': warnings})

        if overlap_cm >= 4:
            warnings.insert(0, 'Chồng mí %g cm khá lớn — có thể đè lên phần TRANH (viền thường chỉ ~4cm).' % overlap_cm)
        planned = plan(items, width_cm=width_cm, gap_cm=gap_cm, allow_rotate=rotate, overlap_cm=overlap_cm)
        pdf_rel = 'ghep/ghep_%s.pdf' % stamp
        prev_rel = 'ghep/ghep_%s.png' % stamp
        try:
            embedded = render_pdf(planned, os.path.join(settings.MEDIA_ROOT, pdf_rel), title='Ghep in DALI')
            render_preview(planned, os.path.join(settings.MEDIA_ROOT, prev_rel))
        except Exception as e:
            return JsonResponse({'ok': False, 'msg': 'Lỗi tạo file: ' + str(e)[:160]})
        # Chốt chặn: nếu KHÔNG nhúng được ảnh nào -> báo lỗi rõ ràng thay vì giao PDF ô trống
        if embedded == 0:
            msg = 'Ghép xong NHƯNG không nhúng được ảnh nào (PDF sẽ toàn ô trống). '
            msg += ('Lý do: ' + ' | '.join(warnings[:5])) if warnings else _PDF_HINT
            return JsonResponse({'ok': False, 'msg': msg, 'warnings': warnings})
        missing = planned['count'] - embedded
        if missing > 0:
            warnings.insert(0, '⚠ %d/%d ô KHÔNG có ảnh (ô đỏ trong PDF) — kiểm tra nguồn ảnh.' % (missing, planned['count']))
        if overlap_cm > 0:
            warnings.insert(0, '✓ Đã chồng mí %g cm (các tranh chia sẻ viền).' % overlap_cm)
        return JsonResponse({
            'ok': True, 'pdf': '/media/' + pdf_rel, 'preview': '/media/' + prev_rel,
            'count': planned['count'], 'embedded': embedded, 'meters': planned['meters'],
            'util': planned['utilization'], 'length_cm': round(planned['length_cm'], 1),
            'width_cm': round(planned['width_cm'], 1),
            'overlap_cm': planned.get('overlap_cm', 0), 'warnings': warnings,
        })

    arts = [{'id': a.id, 'code': a.code, 'url': '/media/' + a.image,
             'w': a.w_cm, 'h': a.h_cm, 'note': a.note,
             'missing': not os.path.exists(os.path.join(settings.MEDIA_ROOT, a.image or ''))}
            for a in PrintArt.objects.all()]
    return render(request, 'ghep_in.html', {'presets': PRESETS, 'arts': arts})
