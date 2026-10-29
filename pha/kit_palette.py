# -*- coding: utf-8 -*-
"""BỘ MÀU CỐ ĐỊNH (kit) cho /xu-ly-anh.

- resolve_kit(text): người dùng DÁN danh sách mã DALI (phẩy / cách / xuống dòng) ->
  trả (rgb_list, code_list, unknown): palette RGB + mã chuẩn, GIỮ THỨ TỰ, bỏ trùng.
  Hiểu 'BDD11' = 'BDD.11' (bỏ dấu chấm/khoảng trắng), 'Trắng'/'white' -> (245,245,245),
  và các màu nền pha (562, ĐEN...) nếu có trong base_colors.
- snap_to_kit(arr_rgb, kit_rgb): ÉP mọi pixel về đúng bộ màu kit. Gán 1-1 (bijection)
  các màu TRỘI của ảnh sang màu kit theo tổng khoảng cách LAB nhỏ nhất (tham lam) để
  VÀNG-RỰC không nhảy nhầm sang KEM (nearest thuần bị lỗi này); màu phụ/anti-alias ->
  màu kit gần nhất. Trả ảnh uint8 chứa ĐÚNG các màu kit (để đánh số khớp mã).
"""
import re
import unicodedata

import numpy as np

_WHITE_RGB = (245, 245, 245)


def _norm(s):
    """Chuẩn hoá token: bỏ dấu tiếng Việt, thường hoá, bỏ khoảng trắng + dấu chấm."""
    s = unicodedata.normalize('NFKD', str(s))
    s = ''.join(c for c in s if not unicodedata.combining(c))
    return s.strip().lower().replace(' ', '').replace('.', '')


def resolve_kit(text):
    """Trả (rgb_list, code_list, unknown). rgb_list/code_list song song, giữ thứ tự dán."""
    from pha import dali_match
    tokens = [t.strip() for t in re.split(r'[\s,;/|]+', text or '') if t.strip()]

    # Bản đồ BỎ DẤU CHẤM (chỉ dùng khi KHÔNG khớp chính xác) — tiện cho người gõ 'BDD11'.
    loose_ref = {}
    for it in dali_match.get_all():              # [{'hex','dali','rgb'}]
        loose_ref.setdefault(_norm(it['dali']), it)

    bases = {}
    try:
        from pha import mixing
        for b in mixing.get_bases():             # [{'name','rgb'}] gồm Trắng/ĐEN/562...
            bases.setdefault(_norm(b['name']), b)
    except Exception:
        pass

    rgb_list, code_list, unknown, seen = [], [], [], set()
    for tok in tokens:
        key = _norm(tok)
        if not key:
            continue
        if key in ('trang', 'white'):
            rgb, code = _WHITE_RGB, 'Trắng'
        else:
            # 1) KHỚP CHÍNH XÁC (giữ dấu chấm) — ĐÚNG như find_by_dali của app, tránh
            #    gộp nhầm các mã chỉ khác nhau dấu chấm (IDE.1 vs IDE1...).
            it = dali_match.find_by_dali(tok)
            if it is None:                       # 2) người gõ thiếu dấu chấm -> bỏ chấm dò lại
                it = loose_ref.get(key)
            if it is not None:
                rgb, code = tuple(int(v) for v in it['rgb']), it['dali']
            elif key in bases:                   # 3) màu nền pha (562, ĐEN...)
                b = bases[key]
                rgb, code = tuple(int(v) for v in b['rgb']), b['name']
            else:
                unknown.append(tok)
                continue
        if rgb in seen:                          # bỏ mã trùng (giữ lần đầu)
            continue
        seen.add(rgb)
        rgb_list.append(rgb)
        code_list.append(code)
    return rgb_list, code_list, unknown


def _to_lab(rgb_int):
    """Nx3 int RGB -> Nx3 float LAB (khớp theo cảm nhận mắt)."""
    import cv2
    a = np.clip(rgb_int, 0, 255).astype(np.uint8).reshape(-1, 1, 3)
    return cv2.cvtColor(a, cv2.COLOR_RGB2LAB).reshape(-1, 3).astype(np.float32)


def snap_to_kit(arr_rgb, kit_rgb):
    """Ép ảnh RGB (HxWx3 uint8) về đúng bộ màu kit (list các (r,g,b)). Trả uint8 cùng shape."""
    kit = np.asarray(kit_rgb, np.int32)
    K = len(kit)
    if K == 0:
        return arr_rgb
    flat = arr_rgb.reshape(-1, 3)
    colors, inv, counts = np.unique(flat, axis=0, return_inverse=True, return_counts=True)
    inv = inv.reshape(-1)
    colors_i = colors.astype(np.int32)

    lab_c = _to_lab(colors_i)                    # Nx3
    lab_k = _to_lab(kit)                         # Kx3
    D = ((lab_c[:, None, :] - lab_k[None, :, :]) ** 2).sum(2)   # N x K (khoảng cách LAB^2)

    assign = np.full(len(colors), -1, np.int64)
    # Bijection: các màu TRỘI nhất (nhiều pixel) giành màu kit riêng theo cặp D nhỏ nhất.
    dominant = np.argsort(-counts)[:K]
    sub = D[dominant].copy()                     # Kp x K
    Kp = len(dominant)
    done_row = np.zeros(Kp, bool)
    used_col = np.zeros(K, bool)
    for _ in range(min(Kp, K)):
        m = sub.copy()
        m[done_row, :] = np.inf
        m[:, used_col] = np.inf
        di, ki = divmod(int(np.argmin(m)), K)
        if not np.isfinite(m[di, ki]):
            break
        assign[dominant[di]] = ki
        done_row[di] = True
        used_col[ki] = True
    # Màu còn lại (phụ / anti-alias) -> màu kit gần nhất.
    rest = assign < 0
    if rest.any():
        assign[rest] = D[rest].argmin(1)

    out_colors = kit[assign].astype(np.uint8)    # Nx3
    return out_colors[inv].reshape(arr_rgb.shape)
