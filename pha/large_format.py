"""KHỔ LỚN — tạo tranh TÔ SỐ siêu chi tiết cho khổ in lớn (vd 1×2m, 60 màu) ở độ phân
giải cao mà KHÔNG ngốn RAM: xử lý trên BẢN ĐỒ NHÃN uint8 (1 byte/pixel = chỉ số màu)
thay vì mảng RGB 3 byte + bỏ np.unique nặng -> LIỀN MẠCH (không ghép mảnh/đường nối),
vừa cả VPS 8GB ở ~70 triệu pixel. (Module riêng — tách engine khổ lớn cho rõ ràng.)

Quy trình: palette 60 màu (k-means) -> map nguồn thành bản đồ nhãn (theo dải, ít RAM) ->
GỘP ô không nhét nổi số (giữ lỗ chữ/counter) -> vẽ NÉT biên + ĐÁNH SỐ (cỡ tối thiểu theo
MM @ khổ thật) -> xuất: bản đồ số (PNG), thiết kế (PNG), bảng màu (JSON).
"""
import json
import os
import time
import uuid
from datetime import datetime

import cv2
import numpy as np
from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt

from pha.color_index_lib import (
    get_number_size, MIN_TEXT_SIZE, MEAN_TEXT_SIZE, PADDING_CIRCLE,
)
from pha import dali_match
from pha.views import staff_required, _img_executor, _prune_image_results

_FONT = cv2.FONT_HERSHEY_SIMPLEX
_LARGE_DIR = 'large'


def _target_long_px(long_cm, dpi):
    return int(round(float(long_cm) / 2.54 * float(dpi)))


def _build_palette(img_rgb, n):
    """k-means n màu (LAB) trên bản THU NHỎ -> centers RGB uint8 (k,3)."""
    h, w = img_rgb.shape[:2]
    sc = 1400.0 / max(h, w) if max(h, w) > 1400 else 1.0
    small = cv2.resize(img_rgb, (max(1, int(w * sc)), max(1, int(h * sc))),
                       interpolation=cv2.INTER_AREA)
    lab = cv2.cvtColor(small, cv2.COLOR_RGB2LAB).reshape(-1, 3).astype(np.float32)
    n = int(min(n, len(np.unique(lab, axis=0))))
    crit = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 12, 1.0)
    _, _, cen_lab = cv2.kmeans(lab, n, None, crit, 3, cv2.KMEANS_PP_CENTERS)
    cen_lab = np.clip(cen_lab, 0, 255).astype(np.uint8).reshape(-1, 1, 3)
    return cv2.cvtColor(cen_lab, cv2.COLOR_LAB2RGB).reshape(-1, 3)


def _to_labels(img_rgb, centers):
    """Map ảnh -> chỉ số tâm GẦN NHẤT (uint8). Theo DẢI + argmin TĂNG DẦN: chỉ giữ
    best_dist + best_idx (không ma trận pixel×k) -> RAM thấp ở khổ rất lớn."""
    H, W = img_rgb.shape[:2]
    lbl = np.empty((H, W), np.uint8)
    cen = centers.astype(np.int32)
    for y in range(0, H, 800):
        blk = img_rgb[y:y + 800].astype(np.int32)
        hh = blk.shape[0]
        best = np.full((hh, W), 1e18, np.float64)
        bidx = np.zeros((hh, W), np.uint8)
        for ci in range(len(cen)):
            d = ((blk - cen[ci]) ** 2).sum(2)
            m = d < best
            best[m] = d[m]
            bidx[m] = ci
        lbl[y:y + 800] = bidx
    return lbl


def _r_for(worst, need):
    """Bán kính nội tiếp cần để nhét số 'worst' cao 'need' px (nửa đường chéo + lề)."""
    sc, gw, gh = 0.05, need, need
    while sc < 6.0:
        (w0, h0), _ = cv2.getTextSize(worst, _FONT, sc, 1)
        if h0 >= need:
            gw, gh = float(w0), float(h0)
            break
        sc += 0.05
    return (gw * gw + gh * gh) ** 0.5 / 2.0 + PADDING_CIRCLE


def _in_boxes(cx, cy, boxes):
    for (bx, by, bw, bh) in boxes:
        if bx <= cx < bx + bw and by <= cy < by + bh:
            return True
    return False


def _merge_labels(lbl, n, min_h, max_pass=4, face_boxes=None, face_min_h=None):
    """GỘP ô quá nhỏ (không nhét nổi nhãn rộng nhất cao min_h) vào hàng xóm LỚN NHẤT;
    GIỮ lỗ kín (counter/lỗ chữ) tới r_floor. Sửa lbl TẠI CHỖ. (Port từ color_index_lib
    _merge_unnumberable nhưng chạy trên NHÃN -> nhẹ RAM.)
    face_boxes/face_min_h: vùng MẶT gộp NHẸ hơn (ngưỡng theo face_min_h < min_h) -> giữ
    chi tiết mắt/mũi/miệng."""
    worst = '9' * max(1, len(str(int(max(2, n)))))
    r_need = _r_for(worst, float(min_h)) * 1.15
    r_need_face = (_r_for(worst, float(face_min_h)) * 1.15) if face_min_h else r_need
    r_floor = _r_for(worst, float(MIN_TEXT_SIZE))
    boxes = face_boxes or []
    H, W = lbl.shape
    k3 = np.ones((3, 3), np.uint8)
    for _ in range(max_pass):
        area_all = np.bincount(lbl.reshape(-1), minlength=n)
        changed = False
        for ci in range(n):
            mask = (lbl == ci).astype(np.uint8)
            if not mask.any():
                continue
            nc, comp, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
            for k in range(1, nc):
                x, y = int(stats[k, cv2.CC_STAT_LEFT]), int(stats[k, cv2.CC_STAT_TOP])
                w, h = int(stats[k, cv2.CC_STAT_WIDTH]), int(stats[k, cv2.CC_STAT_HEIGHT])
                rn = (r_need_face if (boxes and _in_boxes(x + w // 2, y + h // 2, boxes))
                      else r_need)
                sub = (comp[y:y + h, x:x + w] == k).astype(np.uint8)
                subp = cv2.copyMakeBorder(sub, 1, 1, 1, 1, cv2.BORDER_CONSTANT, value=0)
                rad = float(cv2.distanceTransform(subp, cv2.DIST_L2, 3).max())
                if rad >= rn:
                    continue
                x0, y0 = max(x - 1, 0), max(y - 1, 0)
                x1, y1 = min(x + w + 1, W), min(y + h + 1, H)
                sub2 = comp[y0:y1, x0:x1] == k
                ring = (cv2.dilate(sub2.astype(np.uint8), k3) > 0) & (~sub2)
                nb = lbl[y0:y1, x0:x1][ring]
                nb = nb[nb != ci]
                if nb.size == 0:
                    continue
                if np.unique(nb).size == 1 and rad >= r_floor:
                    continue                              # lỗ kín (counter) -> GIỮ
                unb = np.unique(nb)
                nbc = int(unb[int(np.argmax(area_all[unb]))])   # gộp vào màu hàng xóm LỚN NHẤT
                yy, xx = np.where(sub2)
                lbl[y0 + yy, x0 + xx] = nbc
                changed = True
        if not changed:
            break
    return lbl


def _draw_outlines(lbl, canvas):
    """Tô đen (nét) nơi NHÃN ĐỔI (biên giữa các ô) lên canvas grayscale (đã trắng)."""
    d = np.zeros(lbl.shape, bool)
    d[1:, :] |= lbl[1:, :] != lbl[:-1, :]
    d[:, 1:] |= lbl[:, 1:] != lbl[:, :-1]
    canvas[d] = 0


def _place_numbers(lbl, n, numbers, canvas, min_h, mean_h, max_h,
                   face_boxes=None, face_min_h=None):
    """Đánh số 'numbers[ci]' vào tâm sâu nhất (polylabel ~ distanceTransform) mỗi ô.
    Số tối thiểu cao min_h px (đã quy từ mm); vùng MẶT dùng face_min_h (nhỏ hơn) để
    số mịn chui vừa ô nhỏ -> mặt có số chi tiết. Trả số ô đã đánh."""
    placed = 0
    boxes = face_boxes or []
    for ci in range(n):
        num = numbers[ci]
        if not num:
            continue
        mask = (lbl == ci).astype(np.uint8)
        if not mask.any():
            continue
        nc, comp, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
        for k in range(1, nc):
            if int(stats[k, cv2.CC_STAT_AREA]) < 6:
                continue
            x, y = int(stats[k, cv2.CC_STAT_LEFT]), int(stats[k, cv2.CC_STAT_TOP])
            w, h = int(stats[k, cv2.CC_STAT_WIDTH]), int(stats[k, cv2.CC_STAT_HEIGHT])
            mn = (face_min_h if (boxes and face_min_h and _in_boxes(x + w // 2, y + h // 2, boxes))
                  else min_h)
            subp = cv2.copyMakeBorder((comp[y:y + h, x:x + w] == k).astype(np.uint8),
                                      1, 1, 1, 1, cv2.BORDER_CONSTANT, value=0)
            dt = cv2.distanceTransform(subp, cv2.DIST_L2, 3)
            ly, lx = np.unravel_index(int(dt.argmax()), dt.shape)
            ctr = (x + int(lx) - 1, y + int(ly) - 1)
            ts, scale, th = get_number_size(num, float(dt[ly, lx]) * 2, mn, mean_h, max_h)
            if ts is None:
                continue
            org = (ctr[0] - ts[0] // 2, ctr[1] + ts[1] // 2)
            cv2.putText(canvas, num, org, _FONT, scale, 0, th, cv2.LINE_AA)
            placed += 1
    return placed


def _kmeans_rgb(pixels_rgb, k):
    """k-means k màu (LAB) trên TẬP PIXEL cho trước -> centers RGB (k,3). Lấy mẫu tối đa
    200k pixel cho nhanh. Dùng dựng PALETTE PHỤ cho vùng mặt."""
    px = pixels_rgb.reshape(-1, 3)
    if len(px) > 200000:
        idx = np.random.RandomState(0).choice(len(px), 200000, replace=False)
        px = px[idx]
    lab = cv2.cvtColor(px.reshape(-1, 1, 3).astype(np.uint8),
                       cv2.COLOR_RGB2LAB).reshape(-1, 3).astype(np.float32)
    k = int(min(k, len(np.unique(lab, axis=0))))
    if k < 1:
        return np.empty((0, 3), np.uint8)
    crit = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 12, 1.0)
    _, _, cen = cv2.kmeans(lab, k, None, crit, 3, cv2.KMEANS_PP_CENTERS)
    cen = np.clip(cen, 0, 255).astype(np.uint8).reshape(-1, 1, 3)
    return cv2.cvtColor(cen, cv2.COLOR_LAB2RGB).reshape(-1, 3)


def _detect_face_boxes(img_rgb, expand=1.7, grid=(4, 3), conf=0.5):
    """Dò KHUÔN MẶT (YuNet) theo Ô LƯỚI grid (gx×gy): mỗi ô tìm riêng (YuNet cap 4 mặt/
    lần + mặt to hơn so với ô -> bắt được NHIỀU mặt trong tranh nhiều cảnh). Gộp trùng.
    Trả list (x,y,w,h) full-res, NỚI rộng quanh mặt (tóc/cằm/cổ)."""
    try:
        from pha.face_features import _yunet_faces
    except Exception:
        return []
    H, W = img_rgb.shape[:2]
    gx, gy = grid
    raw = []
    for j in range(gy):
        for i in range(gx):
            x0, x1 = int(W * i / gx), int(W * (i + 1) / gx)
            y0, y1 = int(H * j / gy), int(H * (j + 1) / gy)
            tile = img_rgb[y0:y1, x0:x1]
            try:
                faces = _yunet_faces(tile, conf=conf, long_side=1200)
            except Exception:
                faces = []
            for f in faces:
                x, y, w, h = f['box']
                cx, cy = x0 + x + w / 2.0, y0 + y + h / 2.0
                bw, bh = w * expand, h * expand
                bx0, by0 = max(0, int(cx - bw / 2)), max(0, int(cy - bh / 2))
                bx1, by1 = min(W, int(cx + bw / 2)), min(H, int(cy + bh / 2))
                if bx1 > bx0 and by1 > by0:
                    raw.append([bx0, by0, bx1, by1])
    # gộp box TRÙNG (giao nhau nhiều) -> giữ box bao
    boxes = []
    for b in sorted(raw, key=lambda r: -(r[2] - r[0]) * (r[3] - r[1])):
        keep = True
        for (kx0, ky0, kw, kh) in boxes:
            ix = max(0, min(b[2], kx0 + kw) - max(b[0], kx0))
            iy = max(0, min(b[3], ky0 + kh) - max(b[1], ky0))
            if ix * iy > 0.45 * (b[2] - b[0]) * (b[3] - b[1]):
                keep = False
                break
        if keep:
            boxes.append((b[0], b[1], b[2] - b[0], b[3] - b[1]))
    return boxes


def _nearest_idx(sub_rgb, centers):
    """Chỉ số tâm GẦN NHẤT cho từng pixel của 'sub_rgb' (argmin tăng dần, ít RAM)."""
    cen = centers.astype(np.int32)
    blk = sub_rgb.astype(np.int32)
    h, w = blk.shape[:2]
    best = np.full((h, w), 1e18, np.float64)
    bidx = np.zeros((h, w), np.uint8)
    for ci in range(len(cen)):
        dd = ((blk - cen[ci]) ** 2).sum(2)
        m = dd < best
        best[m] = dd[m]
        bidx[m] = ci
    return bidx


def process_large(src_path, out_dir, long_cm=200.0, dpi=150, num_colors=60,
                  min_num_mm=3.0, name='kholon', boost_faces=True, face_extra=28):
    """Tạo tranh tô số KHỔ LỚN từ ảnh nét cao. Lưu bản đồ số + thiết kế + bảng màu vào
    out_dir; trả dict thống kê. Số tối thiểu theo MM @ khổ thật (long_cm)."""
    os.makedirs(out_dir, exist_ok=True)
    t0 = time.time()
    bgr = cv2.imread(src_path)
    if bgr is not None:
        img = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        del bgr
    else:
        # cv2 KHÔNG đọc nổi ảnh KHỔNG LỒ (vài trăm Mpx) -> đọc bằng PIL (đã bỏ giới hạn
        # bomb). Bắt buộc cho nguồn nét cao khổ lớn (vd 309 Mpx).
        from PIL import Image as _PILImage
        _PILImage.MAX_IMAGE_PIXELS = None
        try:
            with _PILImage.open(src_path) as _im:
                img = np.ascontiguousarray(np.array(_im.convert('RGB')))
        except Exception as e:
            raise ValueError('Không đọc được ảnh nguồn: ' + str(e)[:120])
    H0, W0 = img.shape[:2]
    long_px = _target_long_px(long_cm, dpi)
    sc = long_px / float(max(H0, W0))
    interp = cv2.INTER_AREA if sc < 1 else cv2.INTER_LANCZOS4
    img = cv2.resize(img, (max(1, int(W0 * sc)), max(1, int(H0 * sc))), interpolation=interp)
    H, W = img.shape[:2]
    px_per_mm = max(H, W) / (float(long_cm) * 10.0)
    min_h = max(2.0, float(min_num_mm) * px_per_mm)
    mean_h, max_h = min_h * 2.2, min_h * 4.0
    centers = _build_palette(img, num_colors)
    n_base = len(centers)
    # BOOST MẶT: dò mặt -> palette PHỤ riêng cho vùng mặt (skin/mắt/tóc) -> mặt đánh số
    # CHI TIẾT (60 màu toàn ảnh không cấp màu cho mặt nhỏ -> mặt bị phẳng). Vùng mặt được
    # map lại theo CẢ palette (giàu màu da) -> hiện mắt/mũi/miệng thành ô có số.
    face_boxes = _detect_face_boxes(img) if boost_faces else []
    if face_boxes:
        fpx = np.concatenate([img[y:y + h, x:x + w].reshape(-1, 3)
                              for (x, y, w, h) in face_boxes], axis=0)
        fcen = _kmeans_rgb(fpx, face_extra)
        if len(fcen):
            centers = np.concatenate([centers, fcen], axis=0)
    n = len(centers)
    lbl = _to_labels(img, centers[:n_base])            # cả ảnh -> 60 màu nền
    for (x, y, w, h) in face_boxes:                    # vùng mặt -> map lại theo CẢ palette
        lbl[y:y + h, x:x + w] = _nearest_idx(img[y:y + h, x:x + w], centers)
    del img
    # mặt nhỏ hơn nền: gộp NHẸ hơn ở vùng mặt -> giữ chi tiết mắt/mũi/miệng.
    face_min_h = max(float(MIN_TEXT_SIZE), min_h * 0.5)
    _merge_labels(lbl, n, min_h, face_boxes=face_boxes, face_min_h=face_min_h)
    # đánh số LIÊN TỤC 1..K theo các màu CÒN dùng (sau gộp) -> bảng gọn, không nhảy số
    used = list(int(c) for c in np.unique(lbl))
    numbers = ['' for _ in range(n)]
    for i, ci in enumerate(used):
        numbers[ci] = str(i + 1)
    canvas = np.full((H, W), 255, np.uint8)
    _draw_outlines(lbl, canvas)
    placed = _place_numbers(lbl, n, numbers, canvas, min_h, mean_h, max_h,
                            face_boxes=face_boxes, face_min_h=face_min_h)
    num_path = os.path.join(out_dir, f'{name}_so.png')
    cv2.imwrite(num_path, canvas)
    # bản XEM TRƯỚC nhỏ (file số đầy đủ rất nặng -> không hiện trực tiếp trên web)
    pmax = 1400.0
    psc = pmax / max(W, H) if max(W, H) > pmax else 1.0
    cv2.imwrite(os.path.join(out_dir, f'{name}_preview.png'),
                cv2.resize(canvas, (max(1, int(W * psc)), max(1, int(H * psc))),
                           interpolation=cv2.INTER_AREA))
    design = centers[lbl]
    cv2.imwrite(os.path.join(out_dir, f'{name}_thietke.png'),
                cv2.cvtColor(design, cv2.COLOR_RGB2BGR))
    legend = []
    for i, ci in enumerate(used):
        r, g, b = (int(v) for v in centers[ci])
        legend.append({'no': i + 1, 'hex': f'{r:02x}{g:02x}{b:02x}',
                       'dali': dali_match.nearest_dali((r, g, b))})
    with open(os.path.join(out_dir, f'{name}_bangmau.json'), 'w', encoding='utf-8') as f:
        json.dump(legend, f, ensure_ascii=False)
    # % pixel của màu LỚN NHẤT: cao (>~0.6) = ô bị GỘP sụp về nền (khổ quá nhỏ cho số màu
    # này -> số to không nhét nổi ô nhỏ) -> cảnh báo để tăng khổ / giảm màu.
    _h = np.bincount(lbl.reshape(-1))
    collapse_pct = round(float(_h.max()) / float(lbl.size), 2)
    return {'px': f'{W}x{H}', 'mau_dung': len(used), 'o_co_so': placed,
            'so_nho_nhat_mm': round(min_h / px_per_mm, 2), 'n_faces': len(face_boxes),
            'collapse_pct': collapse_pct,
            'giay': round(time.time() - t0, 1), 'num_path': num_path, 'legend': legend,
            'preview': f'{name}_preview.png'}


# ===================== WEB: ô upload "Khổ lớn" (mau.tranhdali.vn) =====================
def process_large_job(rec_id, src_name, long_cm, dpi, num_colors, min_mm):
    """Chạy NỀN: tạo tranh tô số khổ lớn -> cập nhật ImageResult để trang poll."""
    from pha.models import ImageResult
    obj = ImageResult.objects.get(id=rec_id)
    try:
        src = os.path.join(settings.MEDIA_ROOT, src_name)
        out_dir = os.path.join(settings.MEDIA_ROOT, _LARGE_DIR)
        base = os.path.splitext(os.path.basename(src_name))[0]
        st = process_large(src, out_dir, long_cm=long_cm, dpi=dpi,
                           num_colors=num_colors, min_num_mm=min_mm, name=base)
        obj.name_output = f'{_LARGE_DIR}/{base}_so.png'
        obj.design_name = f'{_LARGE_DIR}/{base}_thietke.png'
        p = dict(obj.params or {})
        p.update({'large': True, 'long_cm': long_cm, 'dpi': dpi, 'num_colors': num_colors,
                  'min_mm': min_mm, 'px': st['px'], 'mau_dung': st['mau_dung'],
                  'o_co_so': st['o_co_so'], 'giay': st['giay'], 'legend': st['legend'],
                  'preview': f'{_LARGE_DIR}/{base}_preview.png'})
        obj.params = p
        obj.status = ImageResult.STATUS_DONE
        obj.error_message = ''
        obj.save()
    except Exception as e:                              # noqa: BLE001
        obj.status = ImageResult.STATUS_ERROR
        obj.error_message = str(e)[:300]
        obj.save()


@staff_required
def kho_lon(request):
    """Trang KHỔ LỚN: upload ảnh nét cao + khổ/DPI -> tranh tô số siêu chi tiết."""
    from pha.models import ImageResult
    recent = [{'id': r.id, 'name': r.name, 'so': '/media/' + (r.name_output or ''),
               'px': (r.params or {}).get('px', ''), 'mau': (r.params or {}).get('mau_dung', '')}
              for r in ImageResult.objects.filter(status=ImageResult.STATUS_DONE)
              .order_by('-created_time')[:60] if (r.params or {}).get('large')][:8]
    return render(request, 'kho_lon.html', {'recent': recent})


@csrf_exempt
@staff_required
def kho_lon_upload(request):
    """Nhận ảnh nét cao + thông số -> tạo job NỀN. Trả {ok, id} để trang poll."""
    if request.method != 'POST' or not request.FILES.get('image'):
        return JsonResponse({'ok': False, 'msg': 'Thiếu ảnh.'})

    def _i(k, d, lo, hi):
        try:
            return max(lo, min(hi, int(float(request.POST.get(k) or d))))
        except (ValueError, TypeError):
            return d
    w_cm, h_cm = _i('w_cm', 100, 5, 600), _i('h_cm', 200, 5, 600)
    long_cm = max(w_cm, h_cm)
    dpi = _i('dpi', 150, 50, 220)
    num_colors = _i('num_colors', 60, 2, 120)
    try:
        min_mm = max(1.0, min(20.0, float(request.POST.get('min_mm') or 3)))
    except (ValueError, TypeError):
        min_mm = 3.0
    upload = request.FILES['image']
    fss = FileSystemStorage()
    name = f'{datetime.now():%Y-%m-%d_%H-%M-%S}_{uuid.uuid4().hex[:8]}_{upload.name}'
    name = fss.save(name, upload)                       # tên THẬT (chống lẫn ảnh)
    from pha.models import ImageResult
    rec = ImageResult.objects.create(
        name=name, status=ImageResult.STATUS_PROCESSING,
        user=getattr(request.user, 'username', ''),
        params={'large': True, 'long_cm': long_cm, 'dpi': dpi,
                'num_colors': num_colors, 'min_mm': min_mm})
    _img_executor.submit(process_large_job, rec.id, name, long_cm, dpi, num_colors, min_mm)
    _prune_image_results()
    return JsonResponse({'ok': True, 'id': rec.id})


@csrf_exempt
@staff_required
def kho_lon_status(request):
    """Tra trạng thái job khổ lớn theo id."""
    from pha.models import ImageResult
    try:
        rec = ImageResult.objects.get(id=int(request.GET.get('id', 0)))
    except (ImageResult.DoesNotExist, ValueError, TypeError):
        return JsonResponse({'ok': False, 'status': 'error', 'msg': 'Không tìm thấy job.'})
    if rec.status == ImageResult.STATUS_PROCESSING:
        return JsonResponse({'ok': True, 'status': 'processing'})
    if rec.status == ImageResult.STATUS_ERROR:
        return JsonResponse({'ok': True, 'status': 'error', 'msg': rec.error_message or 'Lỗi.'})
    p = rec.params or {}
    return JsonResponse({'ok': True, 'status': 'done',
                         'so_url': '/media/' + (rec.name_output or ''),
                         'thietke_url': '/media/' + (rec.design_name or ''),
                         'preview_url': '/media/' + p.get('preview', ''),
                         'legend': p.get('legend', []),
                         'stats': {'px': p.get('px'), 'mau': p.get('mau_dung'),
                                   'o': p.get('o_co_so'), 'giay': p.get('giay'),
                                   'long_cm': p.get('long_cm'), 'dpi': p.get('dpi')}})
