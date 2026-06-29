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

import cv2
import numpy as np

from pha.color_index_lib import (
    get_number_size, MIN_TEXT_SIZE, MEAN_TEXT_SIZE, PADDING_CIRCLE,
)
from pha import dali_match

_FONT = cv2.FONT_HERSHEY_SIMPLEX


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


def _merge_labels(lbl, n, min_h, max_pass=4):
    """GỘP ô quá nhỏ (không nhét nổi nhãn rộng nhất cao min_h) vào hàng xóm LỚN NHẤT;
    GIỮ lỗ kín (counter/lỗ chữ) tới r_floor. Sửa lbl TẠI CHỖ. (Port từ color_index_lib
    _merge_unnumberable nhưng chạy trên NHÃN -> nhẹ RAM.)"""
    worst = '9' * max(1, len(str(int(max(2, n)))))
    r_need = _r_for(worst, float(min_h)) * 1.15
    r_floor = _r_for(worst, float(MIN_TEXT_SIZE))
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
                sub = (comp[y:y + h, x:x + w] == k).astype(np.uint8)
                subp = cv2.copyMakeBorder(sub, 1, 1, 1, 1, cv2.BORDER_CONSTANT, value=0)
                rad = float(cv2.distanceTransform(subp, cv2.DIST_L2, 3).max())
                if rad >= r_need:
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


def _place_numbers(lbl, n, numbers, canvas, min_h, mean_h, max_h):
    """Đánh số 'numbers[ci]' vào tâm sâu nhất (polylabel ~ distanceTransform) mỗi ô.
    Số tối thiểu cao min_h px (đã quy từ mm). Trả số ô đã đánh."""
    placed = 0
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
            subp = cv2.copyMakeBorder((comp[y:y + h, x:x + w] == k).astype(np.uint8),
                                      1, 1, 1, 1, cv2.BORDER_CONSTANT, value=0)
            dt = cv2.distanceTransform(subp, cv2.DIST_L2, 3)
            ly, lx = np.unravel_index(int(dt.argmax()), dt.shape)
            ctr = (x + int(lx) - 1, y + int(ly) - 1)
            ts, scale, th = get_number_size(num, float(dt[ly, lx]) * 2, min_h, mean_h, max_h)
            if ts is None:
                continue
            org = (ctr[0] - ts[0] // 2, ctr[1] + ts[1] // 2)
            cv2.putText(canvas, num, org, _FONT, scale, 0, th, cv2.LINE_AA)
            placed += 1
    return placed


def process_large(src_path, out_dir, long_cm=200.0, dpi=150, num_colors=60,
                  min_num_mm=3.0, name='kholon'):
    """Tạo tranh tô số KHỔ LỚN từ ảnh nét cao. Lưu bản đồ số + thiết kế + bảng màu vào
    out_dir; trả dict thống kê. Số tối thiểu theo MM @ khổ thật (long_cm)."""
    os.makedirs(out_dir, exist_ok=True)
    t0 = time.time()
    bgr = cv2.imread(src_path)
    if bgr is None:
        raise ValueError('Không đọc được ảnh nguồn.')
    img = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    del bgr
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
    n = len(centers)
    lbl = _to_labels(img, centers)
    del img
    _merge_labels(lbl, n, min_h)
    # đánh số LIÊN TỤC 1..K theo các màu CÒN dùng (sau gộp) -> bảng gọn, không nhảy số
    used = list(int(c) for c in np.unique(lbl))
    numbers = ['' for _ in range(n)]
    for i, ci in enumerate(used):
        numbers[ci] = str(i + 1)
    canvas = np.full((H, W), 255, np.uint8)
    _draw_outlines(lbl, canvas)
    placed = _place_numbers(lbl, n, numbers, canvas, min_h, mean_h, max_h)
    num_path = os.path.join(out_dir, f'{name}_so.png')
    cv2.imwrite(num_path, canvas)
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
    return {'px': f'{W}x{H}', 'mau_dung': len(used), 'o_co_so': placed,
            'so_nho_nhat_mm': round(min_h / px_per_mm, 2),
            'giay': round(time.time() - t0, 1), 'num_path': num_path}
