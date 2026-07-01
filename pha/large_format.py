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
    _snap_to_design_palette, _quantize_rarity, _chaikin,
)

# SỐ tính theo MM @ KHỔ THẬT (không nhân min_h) -> ở 1.2×2m số KHÔNG phình to (lỗi cũ:
# max_h=min_h×4 ~ 20mm). lib get_number_size tự co số theo ô nên đây là TRẦN TRÊN.
# Cỡ vừa như bản Illustration TK325 (~3.4-4mm) -> số nhỏ gọn, đỡ choán ô.
MEAN_NUM_MM = 5.0          # cỡ số chuẩn (đa số ô)
MAX_NUM_MM = 7.0           # số to nhất (ô nền lớn)
SMOOTH_CONTOUR_CAP = 25000  # > số này -> vẽ biên thô (an toàn thời gian); ô thường ~6-9k
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


def _rarity_palette(img_rgb, n):
    """Bảng màu k-means CÓ TRỌNG SỐ ĐỘ HIẾM (_quantize_rarity) -> GIỮ vật thể nhỏ/hiếm
    (người ở xa, điểm nhấn) bằng cách cấp CỤM MÀU RIÊNG cho tông hiếm; k-means trơn
    (_build_palette) bỏ đói tông hiếm -> mất vật thể NGAY từ palette. Lấy palette trên
    bản THU NHỎ ~2400px (đủ giữ tông hiếm, nhẹ RAM) -> trả centers RGB uint8 (k,3)."""
    h, w = img_rgb.shape[:2]
    sc = 2400.0 / max(h, w) if max(h, w) > 2400 else 1.0
    small = (cv2.resize(img_rgb, (max(1, int(w * sc)), max(1, int(h * sc))),
                        interpolation=cv2.INTER_AREA) if sc < 1.0 else img_rgb)
    q = _quantize_rarity(small, int(max(2, n)))          # ảnh đã lượng tử (giữ tông hiếm)
    return np.unique(q.reshape(-1, 3), axis=0).astype(np.uint8)


def _looks_flat(img_rgb, cover=0.85, topk=64):
    """Nhận diện ảnh ĐÃ THIẾT KẾ PHẲNG (Illustrator: ÍT màu, mỗi màu phủ MẢNG lớn) vs ảnh
    CHỤP/AI (hàng nghìn màu trải mỏng). Đo bằng ĐỘ PHỦ của 'topk' màu phổ biến nhất: bản
    phẳng -> vài chục màu phủ ~hết; painterly -> top màu chỉ phủ phần nhỏ.
    (Bỏ metric "pixel trùng hàng xóm" cũ: gradient mượt 8-bit có dải bằng nhau -> dương
    tính giả khi NEAREST hạ ảnh nét cao.) Thu nhỏ NEAREST giữ ĐÚNG màu thật (không nhoè)."""
    h, w = img_rgb.shape[:2]
    sc = 1500.0 / max(h, w) if max(h, w) > 1500 else 1.0
    s = (cv2.resize(img_rgb, (max(1, int(w * sc)), max(1, int(h * sc))),
                    interpolation=cv2.INTER_NEAREST) if sc < 1.0 else img_rgb)
    flat = s.reshape(-1, 3)
    colors, counts = np.unique(flat, axis=0, return_counts=True)
    if len(colors) <= topk:
        return True                                      # rất ít màu -> chắc chắn phẳng
    top = int(np.sort(counts)[::-1][:topk].sum())
    return (top / float(flat.shape[0])) >= cover


def _cap_design_palette(colors, counts, cap):
    """Hạ số màu THIẾT KẾ về 'cap' bằng GỘP cặp màu GẦN GIỐNG nhau nhất (LAB), luôn GIỮ
    màu DIỆN TÍCH lớn hơn (màu THẬT trong file, KHÔNG tạo màu trung bình mới). Khoảng cách
    cặp TĨNH (giữ màu thật nên không đổi) -> chỉ cần argmin trên ma trận con còn sống."""
    colors = colors.astype(np.uint8)
    if len(colors) <= cap:
        return colors
    lab = cv2.cvtColor(colors.reshape(-1, 1, 3), cv2.COLOR_RGB2LAB).reshape(-1, 3).astype(np.float32)
    M = len(colors)
    D = ((lab[:, None, :] - lab[None, :, :]) ** 2).sum(2)
    np.fill_diagonal(D, 1e18)
    alive = np.ones(M, bool)
    cnt = counts.astype(np.float64).copy()
    n_alive = M
    while n_alive > cap:
        idx = np.where(alive)[0]
        sub = D[np.ix_(idx, idx)]
        a, b = np.unravel_index(int(sub.argmin()), sub.shape)
        i, j = int(idx[a]), int(idx[b])
        drop = j if cnt[i] >= cnt[j] else i
        keep = i if drop == j else j
        cnt[keep] += cnt[drop]
        alive[drop] = False
        n_alive -= 1
    return colors[alive]


def _flat_palette(img_rgb, cap):
    """Bảng màu cho ảnh PHẲNG: lấy ĐÚNG màu thiết kế (snap khử răng cưa về màu ≥0.03%,
    KHÔNG k-means/KHÔNG drop màu lớn) rồi CHẶN TRẦN 'cap' bằng gộp màu gần giống. Lấy màu
    trên bản thu nhỏ NEAREST (giữ y màu thật) -> nhẹ RAM. Trả centers RGB uint8 (k,3)."""
    h, w = img_rgb.shape[:2]
    sc = 2000.0 / max(h, w) if max(h, w) > 2000 else 1.0
    small = (cv2.resize(img_rgb, (max(1, int(w * sc)), max(1, int(h * sc))),
                        interpolation=cv2.INTER_NEAREST) if sc < 1.0 else img_rgb)
    snapped = _snap_to_design_palette(small)
    colors, counts = np.unique(snapped.reshape(-1, 3), axis=0, return_counts=True)
    MAXM = 800                                     # an toàn: quá nhiều màu thì giữ 800 lớn nhất
    if len(colors) > MAXM:
        order = counts.argsort()[::-1][:MAXM]
        colors, counts = colors[order], counts[order]
    return _cap_design_palette(colors, counts, int(max(2, cap)))


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


def _feature_protect_mask(H, W, face_data):
    """Mask BẢO VỆ NGŨ QUAN (B3): vẽ vòng quanh 5 điểm mốc YuNet (2 mắt, mũi, 2 mép miệng)
    của mỗi mặt -> ô rơi vào đây KHÔNG bị gộp (mắt/môi giữ nét, mặt sắc). Trả None nếu không
    có landmark (không tốn RAM). 1 mask uint8 full-res ~ 1 byte/px (vài chục MB, vẽ vài vòng)."""
    m = None
    for box, lms in (face_data or []):
        if lms is None:
            continue
        x, y, w, h = box
        r = max(3, int(min(w, h) * 0.12))              # bán kính bảo vệ quanh mỗi điểm mốc
        if m is None:
            m = np.zeros((H, W), np.uint8)
        for px, py in np.asarray(lms, np.float32).reshape(-1, 2):
            cv2.circle(m, (int(px), int(py)), r, 1, -1)
    return m


def _merge_labels(lbl, n, min_h, max_pass=4, face_boxes=None, face_min_h=None,
                  centers=None, flat=False, keep_delta_e=14.0, pad=1.08, floor_h=None,
                  protect_mask=None, protect_min_area=0.0):
    """GỘP ô quá nhỏ. KHÁC bản cũ ở 2 điểm để GIỮ CHI TIẾT:
    (1) Gộp vào hàng xóm GIỐNG MÀU NHẤT (LAB) thay vì DIỆN TÍCH lớn nhất -> ô bị nuốt ít
        lệch màu (trước gộp vào nền -> mắt/điểm nhấn bị nhuộm mất).
    (2) GIỮ ô (tới sàn floor_h = số nhỏ nhất còn đặt được) khi đáng giữ: bản PHẲNG (mọi vùng
        là chủ ý) / trong VÙNG MẶT / TƯƠNG PHẢN CAO với hàng xóm (ΔE>keep_delta_e: mắt, đốm
        sáng). Ô < sàn (vô-tô-được) vẫn gộp. Vẫn GIỮ lỗ kín counter. Sửa lbl TẠI CHỖ."""
    fl = float(MIN_TEXT_SIZE) if floor_h is None else max(float(MIN_TEXT_SIZE), float(floor_h))
    # NGƯỠNG GỘP THEO SỐ-CHỮ-SỐ THỰC của từng màu (thay worst='999' cứng áp cho MỌI ô — ở 120
    # màu khiến ô nhỏ bị gộp oan dù số thật chỉ 1-2 chữ). Tiền-gán digit theo thứ tự đánh số
    # 1..K (np.unique tăng dần) TRƯỚC gộp; merge chỉ XÓA màu nên digit thật cuối <= pre_digits
    # -> r_floor(pre) >= r_floor(final) -> ô GIỮ luôn nhét được số (KHÔNG tạo ô-giữ-mà-vô-số).
    # Ô mang số ngắn cần ít chỗ hơn -> giữ được NHIỀU ô nhỏ hơn = nhiều mảng đánh số hơn.
    used0 = [int(c) for c in np.unique(lbl)]
    pre_digits = [1] * n
    for _i, _ci in enumerate(used0):
        pre_digits[_ci] = len(str(_i + 1))
    maxd = max(1, len(str(int(max(2, n)))))
    rneed_by_d = {d: _r_for('9' * d, float(min_h)) * pad for d in range(1, maxd + 1)}
    rneedf_by_d = {d: (_r_for('9' * d, float(face_min_h)) * pad if face_min_h else rneed_by_d[d])
                   for d in range(1, maxd + 1)}
    rfloor_by_d = {d: _r_for('9' * d, fl) for d in range(1, maxd + 1)}
    boxes = face_boxes or []
    clab = None
    if centers is not None and len(centers) >= n:
        clab = cv2.cvtColor(np.asarray(centers[:n], np.uint8).reshape(-1, 1, 3),
                            cv2.COLOR_RGB2LAB).reshape(-1, 3).astype(np.float32)
    H, W = lbl.shape
    k3 = np.ones((3, 3), np.uint8)
    for _ in range(max_pass):
        area_all = np.bincount(lbl.reshape(-1), minlength=n)
        changed = False
        for ci in range(n):
            mask = (lbl == ci).astype(np.uint8)
            if not mask.any():
                continue
            # ngưỡng GỘP/GIỮ theo số-chữ-số THỰC của màu ci (thay worst='999' cứng)
            _d = pre_digits[ci]
            rn_body = rneed_by_d[_d]; rn_face_c = rneedf_by_d[_d]; r_floor = rfloor_by_d[_d]
            nc, comp, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
            for k in range(1, nc):
                x, y = int(stats[k, cv2.CC_STAT_LEFT]), int(stats[k, cv2.CC_STAT_TOP])
                w, h = int(stats[k, cv2.CC_STAT_WIDTH]), int(stats[k, cv2.CC_STAT_HEIGHT])
                area = int(stats[k, cv2.CC_STAT_AREA])
                in_face = bool(boxes and _in_boxes(x + w // 2, y + h // 2, boxes))
                # bán kính nội tiếp — TÍNH TRƯỚC cổng protect_mask để cổng đó cũng kiểm được rad.
                sub = (comp[y:y + h, x:x + w] == k).astype(np.uint8)
                subp = cv2.copyMakeBorder(sub, 1, 1, 1, 1, cv2.BORDER_CONSTANT, value=0)
                rad = float(cv2.distanceTransform(subp, cv2.DIST_L2, 3).max())
                # B3: GIỮ ô NGŨ QUAN (mắt/mũi/miệng) — chỉ ô ĐỦ TO (>=protect_min_area ~1.3mm²)
                # VÀ đủ chỗ nhét số (rad>=r_floor). Ô ngũ-quan quá nhỏ (TRƯỚC bị giữ mà TRỐNG
                # SỐ) nay rơi xuống dưới -> gộp vào màu da/mắt gần nhất -> HẾT ô-giữ-mà-vô-số ở mặt.
                if protect_mask is not None and area >= protect_min_area and \
                        protect_mask[min(y + h // 2, H - 1), min(x + w // 2, W - 1)] and rad >= r_floor:
                    continue
                rn = rn_face_c if in_face else rn_body
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
                unb = np.unique(nb)
                if unb.size == 1 and rad >= r_floor:
                    continue                              # lỗ kín (counter) -> GIỮ
                # hàng xóm GIỐNG MÀU NHẤT (LAB) -> ít lệch màu; fallback: diện tích lớn nhất
                if clab is not None:
                    dd = ((clab[unb] - clab[ci]) ** 2).sum(1)
                    j = int(dd.argmin()); nbc = int(unb[j]); de = float(dd[j]) ** 0.5
                else:
                    nbc = int(unb[int(np.argmax(area_all[unb]))]); de = None
                # GIỮ ô (còn đủ chỗ số nhỏ nhất) nếu là chi tiết đáng giữ. Trong VÙNG MẶT chỉ
                # giữ ô ĐỦ TO (>=protect_min_area) -> bớt dăm li ti ở mặt; nền giữ theo ΔE như cũ.
                if rad >= r_floor and (flat or (in_face and area >= protect_min_area)
                                       or (de is not None and de > keep_delta_e)):
                    continue
                yy, xx = np.where(sub2)
                lbl[y0 + yy, x0 + xx] = nbc
                changed = True
        if not changed:
            break
    return lbl


def _draw_outlines(lbl, canvas):
    """Tô đen (nét) nơi NHÃN ĐỔI (biên giữa các ô) lên canvas grayscale (đã trắng).
    THÔ: biên = bậc thang pixel (răng cưa). Dùng làm FALLBACK khi quá nhiều contour."""
    d = np.zeros(lbl.shape, bool)
    d[1:, :] |= lbl[1:, :] != lbl[:-1, :]
    d[:, 1:] |= lbl[:, 1:] != lbl[:, :-1]
    canvas[d] = 0


def _draw_smooth_outlines_hi(lbl, canvas, out_scale=1.0, eps=0.8, iters=2,
                             cap=SMOOTH_CONTOUR_CAP):
    """Vẽ biên MƯỢT 1-NÉT (như Illustration TK325). Cách: DỰNG LẠI bản-đồ-nhãn MƯỢT ở cỡ
    canvas rồi _draw_outlines (label-diff 1px) -> mỗi biên CHỈ 1 nét (KHÔNG double-line như
    vẽ polyline per-màu — vẽ 2 lần cho 2 vùng kề). Bản-đồ-nhãn mượt: per-màu findContours
    (RETR_EXTERNAL) -> approxPolyDP(eps) -> _chaikin(iters) -> NHÂN điểm × out_scale ->
    fillPoly VÙNG TO trước, nhỏ ĐÈ sau (lỗ/counter = vùng trong đè lên -> giữ). De-stair vì
    fillPoly ở hi-res từ điểm contour đã scale (không NEAREST pixel). Trả False nếu >cap."""
    Hh, Ww = lbl.shape[:2]
    Hs, Ws = canvas.shape[:2]
    wh = (Ww, Hh)
    items = []                                         # (area, ci, contour)
    for ci in (int(c) for c in np.unique(lbl)):
        mask = (lbl == ci).astype(np.uint8)
        cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        for c in cnts:
            items.append((float(cv2.contourArea(c)), ci, c))
        if len(items) > cap:
            return False
    items.sort(key=lambda t: -t[0])                    # VÙNG TO trước -> nhỏ đè lên sau
    lblsm = np.zeros((Hs, Ws), np.uint16)              # nhãn = ci+1 (0 = nền chưa tô)
    for _a, ci, c in items:
        if len(c) < 6:
            pts = c.reshape(-1, 2).astype(np.float32) * out_scale
        else:
            cc = cv2.approxPolyDP(c, eps, True)
            pts = np.asarray(_chaikin(cc.reshape(-1, 2), iters, wh=wh), np.float32) * out_scale
        cv2.fillPoly(lblsm, [np.round(pts).astype(np.int32).reshape(-1, 1, 2)], int(ci) + 1)
    # LẤP DẢI HỞ (FIX 2-vạch-biên + khe-không-số): làm mượt per-màu khiến hai đa giác của
    # BIÊN CHUNG (vùng A & vùng B kề nhau) lệch nhau ~1-3px -> để hở dải pixel nhãn 0 (nền
    # chưa tô) kẹp giữa. _draw_outlines (label-diff) khi đó thấy ĐỔI NHÃN ở CẢ HAI mép dải
    # (A->0 và 0->B) -> vẽ 2 NÉT đen, dải 0 ở giữa thành KHE TRẮNG = ô-ma KHÔNG được đánh số
    # (vì số đặt trên 'lbl' gốc, không có dải này). Khắc phục: LAN nhãn lân cận vào mọi pixel
    # 0 còn sót để bản-nhãn-mượt PHỦ KÍN -> mỗi biên chung chỉ còn 1 lần đổi nhãn = 1 NÉT, hết
    # khe trắng, hết ô-ma. (Bất biến BẮT BUỘC khi vẽ nét tô-số: bản nhãn phải phủ kín 100%.)
    if (lblsm == 0).any():
        _k3 = np.ones((3, 3), np.uint8)
        for _ in range(32):                            # khe ~1-3px -> hội tụ sau ~2-4 vòng
            zero = (lblsm == 0)
            if not zero.any():
                break
            grown = cv2.dilate(lblsm, _k3)
            lblsm[zero] = grown[zero]
            del grown
    _draw_outlines(lblsm, canvas)                      # label-diff -> 1 NÉT mỗi biên
    return True


def _puttext_thin_gray(canvas, number, org, scale, frac=0.8, ss=5):
    """Vẽ SỐ ĐEN (0) nét mảnh dưới-1px lên canvas GRAYSCALE nền trắng (255). cv2.putText chỉ
    nhận nét nguyên >=1; ở số nhỏ nét 1px còn quá dày -> render PHÓNG TO ss lần nét round(ss·frac)
    rồi thu INTER_AREA = nét xám AA dưới-1px, blend về ĐEN. Bản 1-kênh của _puttext_thin (bản
    gốc 3-kênh). frac>=1 -> putText thường (nhanh)."""
    if frac >= 0.999:
        cv2.putText(canvas, number, org, _FONT, scale, 0, 1, cv2.LINE_AA)
        return
    (gw, gh), base = cv2.getTextSize(number, _FONT, scale, 1)
    if gw <= 0 or gh <= 0:
        cv2.putText(canvas, number, org, _FONT, scale, 0, 1, cv2.LINE_AA)
        return
    th = max(1, int(round(ss * frac)))
    pad = ss
    cw, chh = gw * ss + 2 * pad, (gh + base) * ss + 2 * pad
    buf = np.zeros((chh, cw), np.uint8)
    cv2.putText(buf, number, (pad, gh * ss + pad), _FONT, scale * ss, 255, th, cv2.LINE_AA)
    small = cv2.resize(buf, (cw // ss, chh // ss), interpolation=cv2.INTER_AREA)
    sh, sw = small.shape
    H, W = canvas.shape[:2]
    x0, y0 = int(org[0]) - pad // ss, int(org[1]) - (gh + pad // ss)
    xa0, ya0 = max(0, x0), max(0, y0)
    xa1, ya1 = min(W, x0 + sw), min(H, y0 + sh)
    if xa1 <= xa0 or ya1 <= ya0:
        return
    a = small[ya0 - y0:ya1 - y0, xa0 - x0:xa1 - x0].astype(np.float32) / 255.0
    reg = canvas[ya0:ya1, xa0:xa1].astype(np.float32)
    reg = reg * (1.0 - a)                                # ink ĐEN=0 -> chỉ cần nhân (1-alpha)
    canvas[ya0:ya1, xa0:xa1] = np.clip(reg, 0, 255).astype(np.uint8)


def _place_numbers(lbl, n, numbers, canvas, min_h, mean_h, max_h,
                   face_boxes=None, face_min_h=None, floor_h=None, thin=True, out_scale=1.0):
    """Đánh số 'numbers[ci]' vào tâm sâu nhất (polylabel ~ distanceTransform) mỗi ô.
    Số to dần tới ~mean_h (vừa ô); SÀN = floor_h (số nhỏ nhất còn đặt được, ~2.5mm) để
    CỨU ô nhỏ đã được _merge_labels GIỮ (mắt/điểm nhấn) -> đánh số được cả chi tiết nhỏ.
    out_scale>1: TÍNH vị trí/cỡ số trên 'lbl' (work-res, nhẹ) nhưng VẼ lên canvas TO HƠN
    out_scale lần -> nét biên 1px thành MẢNH hơn (theo mm) mà không chạy lại bước nặng.
    Vẽ nét MẢNH (thin) cho số nhỏ sắc, không vỡ. Trả số ô đã đánh."""
    placed = 0
    os_ = float(out_scale)
    fl = float(MIN_TEXT_SIZE) if floor_h is None else max(float(MIN_TEXT_SIZE), float(floor_h))
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
            ts, scale, th = get_number_size(num, float(dt[ly, lx]) * 2, fl, mean_h, max_h)
            if ts is None:
                continue
            org = (ctr[0] - ts[0] // 2, ctr[1] + ts[1] // 2)
            dscale = scale
            if os_ != 1.0:                               # vẽ lên canvas to hơn -> nét mảnh hơn
                org = (int(round(org[0] * os_)), int(round(org[1] * os_)))
                dscale = scale * os_
            if thin:
                _puttext_thin_gray(canvas, num, org, dscale)
            else:
                cv2.putText(canvas, num, org, _FONT, dscale, 0, th, cv2.LINE_AA)
            placed += 1
    return placed


def _face_detail_sheet(canvas, lbl, numbers, n, face_boxes, mean_h, max_h, floor_h,
                       max_insets=6, target_w=1500, mark_scale=1.0):
    """ZOOM-INSET: với mỗi vùng MẶT (tối đa max_insets), (1) đánh dấu KHUNG + CHỮ CÁI lên
    bản số chính 'canvas' (tại chỗ); (2) trích nhãn vùng đó, PHÓNG TO (NEAREST) -> vẽ nét +
    đánh số đầy đủ trong ô riêng. Trả 1 ẢNH 'bản chi tiết mặt' (ghép dọc các ô phóng to) để
    lưu file riêng -> KHÔNG đổi kích thước _so/_thietke (giữ canh lề/đăng ký in). Số trong
    inset DÙNG CHUNG numbers[] với bản chính nên khớp tuyệt đối. None nếu không có mặt.
    mark_scale: canvas có thể to hơn lbl (xuất nét mảnh) -> toạ độ KHUNG/CHỮ ×mark_scale,
    còn crop nhãn vẫn theo lbl (work-res)."""
    if not face_boxes:
        return None
    H, W = canvas.shape[:2]
    boxes = sorted(face_boxes, key=lambda b: b[2] * b[3], reverse=True)[:max_insets]
    letters = 'ABCDEFGH'
    panels = []
    # KHÔNG vẽ khung A/B/C/D lên BẢN SỐ CHÍNH nữa (giữ bản số sạch); chỉ xuất bản chi tiết
    # mặt riêng (file _mat.png) làm tham chiếu. mark_scale giữ cho tương thích, không dùng.
    for i, (x, y, w, h) in enumerate(boxes):
        zf = max(1.0, target_w / float(max(1, w)))
        cw, ch = int(w * zf), int(h * zf)
        crop0 = lbl[y:y + h, x:x + w]                   # work-res (cho biên mượt scale ×zf)
        crop = cv2.resize(crop0, (cw, ch), interpolation=cv2.INTER_NEAREST)  # cho đặt số
        sub = np.full((ch, cw), 255, np.uint8)
        if not _draw_smooth_outlines_hi(crop0, sub, out_scale=zf):
            _draw_outlines(crop, sub)                   # fallback thô
        _place_numbers(crop, n, numbers, sub, mean_h, mean_h, max_h, floor_h=floor_h)
        title = np.full((68, cw), 255, np.uint8)
        cv2.putText(title, 'Vung ' + letters[i], (10, 50), _FONT, 1.6, 0, 3, cv2.LINE_AA)
        panels.append(np.vstack([title, sub]))
    maxw = max(p.shape[1] for p in panels)
    rows = []
    for p in panels:
        if p.shape[1] < maxw:
            p = cv2.copyMakeBorder(p, 0, 0, 0, maxw - p.shape[1], cv2.BORDER_CONSTANT, value=255)
        rows.append(p)
        rows.append(np.full((30, maxw), 255, np.uint8))
    return np.vstack(rows) if rows else None


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


def _detect_face_boxes(img_rgb, expand=1.7, conf=0.85, det_long=1600, with_lms=False):
    """Dò KHUÔN MẶT (YuNet) MỘT LẦN trên bản THU NHỎ (long_side<=det_long) -> NHANH + KHÔNG
    upscale FALSE-POSITIVE (lưới-tile cũ phóng hoa sen mờ thành "mặt"). LỌC score>=conf (mặt
    THẬT YuNet >=0.9; FP <=0.8). _yunet_faces tự thu nhỏ nội bộ + trả box/lms về toạ độ ảnh
    vào. Trả list (x,y,w,h) full-res NỚI rộng (tóc/cằm/cổ); with_lms -> list (box, lms)."""
    try:
        from pha.face_features import _yunet_faces
    except Exception:
        return []
    H, W = img_rgb.shape[:2]
    try:
        faces = _yunet_faces(img_rgb, conf=conf, long_side=det_long)
    except Exception:
        return []
    out = []
    for f in faces:
        if float(f.get('score', 0.0)) < conf:
            continue
        x, y, w, h = f['box']
        cx, cy = x + w / 2.0, y + h / 2.0
        bw, bh = w * expand, h * expand
        bx0, by0 = max(0, int(cx - bw / 2)), max(0, int(cy - bh / 2))
        bx1, by1 = min(W, int(cx + bw / 2)), min(H, int(cy + bh / 2))
        if bx1 > bx0 and by1 > by0:
            box = (bx0, by0, bx1 - bx0, by1 - by0)
            out.append((box, f.get('lms')) if with_lms else box)
    return out


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
                  min_num_mm=3.0, name='kholon', boost_faces=True, face_extra=20,
                  max_work_mpx=45.0, keep_floor_mm=2.5, line_render_scale=1.0):
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
    # Nhận diện PHẲNG trên ảnh GỐC (biên CỨNG) TRƯỚC khi resize (INTER_AREA làm nhoè biên
    # -> sai tỉ lệ pixel trùng). _looks_flat tự thu nhỏ NEAREST nên rẻ kể cả ảnh 308Mpx.
    flat_mode = _looks_flat(img)
    long_px = _target_long_px(long_cm, dpi)
    sc = long_px / float(max(H0, W0))
    tW, tH = max(1, int(W0 * sc)), max(1, int(H0 * sc))
    # CHẶN TRẦN số điểm ảnh LÀM VIỆC -> vừa RAM (VPS 8GB) + xử lý XONG DƯỚI ngưỡng poll.
    # Ô tô đếm theo MM @ khổ THẬT (min_h = min_num_mm * px_per_mm) nên hạ độ phân giải
    # KHÔNG giảm số ô đánh số -> chỉ bớt độ nét raster (vẫn thừa cho in khổ lớn). Trước đây
    # dpi=150 @ 200cm -> ~84Mpx + 120 màu -> >5 phút -> frontend bỏ poll = "không ra kết quả".
    cap = float(max_work_mpx) * 1e6
    if tW * tH > cap:
        s2 = (cap / (tW * tH)) ** 0.5
        tW, tH = max(1, int(tW * s2)), max(1, int(tH * s2))
    interp = cv2.INTER_AREA if (tW < W0) else cv2.INTER_LANCZOS4
    img = cv2.resize(img, (tW, tH), interpolation=interp)
    H, W = img.shape[:2]
    px_per_mm = max(H, W) / (float(long_cm) * 10.0)
    # SỐ theo MM @ KHỔ THẬT: min/chuẩn/to-nhất là HẰNG mm (KHÔNG nhân min_h) -> ở 1.2×2m
    # số không phình (lỗi cũ max_h=min_h×4 ~20mm). get_number_size tự co số theo ô.
    min_h = max(2.0, float(min_num_mm) * px_per_mm)
    mean_h = max(min_h, MEAN_NUM_MM * px_per_mm)
    max_h = max(mean_h, MAX_NUM_MM * px_per_mm)
    # TỰ NHẬN DIỆN bản phẳng (Illustrator...) vs ảnh chụp/AI:
    #  - PHẲNG -> GIỮ NGUYÊN bảng màu thiết kế (bỏ k-means/"tự lọc màu"), chỉ chặn trần số
    #    màu bằng gộp màu gần giống; KHÔNG boost mặt (giữ đúng màu file, không thêm màu lạ).
    #  - ẢNH CHỤP/AI -> k-means + boost mặt như cũ.
    face_data = []                                     # (box, lms) cho B3 bảo vệ ngũ quan
    if flat_mode:
        centers = _flat_palette(img, num_colors)
        n_base = len(centers)
        face_boxes = []
    else:
        # num_colors = TỔNG số hũ sơn MUỐN có. Nếu boost mặt -> DÀNH face_extra slot cho mặt,
        # phần nền = num_colors - face_extra -> tổng ~ num_colors (không vượt "120 màu").
        face_data = _detect_face_boxes(img, with_lms=True) if boost_faces else []
        face_boxes = [b for (b, _l) in face_data]
        reserve = face_extra if face_boxes else 0
        base_k = max(2, int(num_colors) - reserve)
        centers = _rarity_palette(img, base_k)         # GIỮ vật thể hiếm (R1) thay k-means trơn
        n_base = len(centers)
        # BOOST MẶT: palette PHỤ vùng mặt (skin/mắt/tóc) ghép vào -> mặt giàu màu, đánh số chi tiết.
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
    # mặt: ngưỡng gộp NHẸ hơn (face_min_h ~0.75×) -> giữ chi tiết mắt/mũi/miệng.
    face_min_h = max(float(MIN_TEXT_SIZE), min_h * 0.75)
    # SÀN số ~2.5mm @ khổ thật: số nhỏ nhất còn ĐẶT được -> cứu ô nhỏ "đáng giữ" (mắt/điểm
    # nhấn). Dùng CHUNG cho _merge_labels (giữ tới sàn) lẫn _place_numbers (đặt số tới sàn)
    # -> không tạo ô-giữ-mà-vô-số. bản phẳng giữ mọi vùng numberable.
    floor_h = max(float(MIN_TEXT_SIZE), float(keep_floor_mm) * px_per_mm)
    # B3: GIỮ ô NGŨ QUAN (mắt/mũi/miệng) theo landmark -> mặt SẮC; nhưng chỉ ô ĐỦ TO
    # (>= ~1.3mm²) -> diệt DĂM noise 1-3px ở mặt (đo: 1657 ô <9px bị protect_mask giữ oan).
    protect_mask = _feature_protect_mask(H, W, face_data)
    protect_min_area = max(9.0, 1.3 * px_per_mm * px_per_mm)   # ~1.3mm² @ khổ thật
    _merge_labels(lbl, n, min_h, face_boxes=face_boxes, face_min_h=face_min_h,
                  centers=centers, flat=flat_mode, floor_h=floor_h, protect_mask=protect_mask,
                  protect_min_area=protect_min_area)
    # đánh số LIÊN TỤC 1..K theo các màu CÒN dùng (sau gộp) -> bảng gọn, không nhảy số
    used = list(int(c) for c in np.unique(lbl))
    numbers = ['' for _ in range(n)]
    for i, ci in enumerate(used):
        numbers[ci] = str(i + 1)
    # NÉT MẢNH theo khổ to: xuất bản SỐ ở độ phân giải cao hơn (line_render_scale) -> nét
    # biên 1px thành mảnh hơn (theo mm). Vị trí số tính ở work-res (nhẹ), chỉ VẼ ở canvas to.
    LS = max(1.0, float(line_render_scale))
    Hs, Ws = (int(round(H * LS)), int(round(W * LS))) if LS > 1.01 else (H, W)
    canvas = np.full((Hs, Ws), 255, np.uint8)
    # BIÊN MƯỢT (vector-like như TK325): vẽ contour mượt scale-up; nếu quá nhiều contour ->
    # fallback biên thô (NEAREST upscale) cho an toàn thời gian.
    if not _draw_smooth_outlines_hi(lbl, canvas, out_scale=LS):
        canvas[:] = 255
        if LS > 1.01:
            lbl_hi = cv2.resize(lbl, (Ws, Hs), interpolation=cv2.INTER_NEAREST)
            _draw_outlines(lbl_hi, canvas)
            del lbl_hi
        else:
            _draw_outlines(lbl, canvas)
    placed = _place_numbers(lbl, n, numbers, canvas, min_h, mean_h, max_h,
                            face_boxes=face_boxes, face_min_h=face_min_h, floor_h=floor_h,
                            out_scale=LS)
    # ZOOM-INSET: dò mặt nay LỌC score>=0.85 (sạch false-positive) nên LUÔN xuất bản chi tiết
    # cho 1..6 mặt (mặt montage NHỎ ~0.8% diện tích MỚI cần inset nhất). Lấy top-6 theo diện
    # tích, phóng to + đánh số đầy đủ + đánh dấu khung A..F trên bản số.
    detail_name = ''
    if face_boxes:
        cand = sorted(face_boxes, key=lambda b: b[2] * b[3], reverse=True)[:6]
        sheet = _face_detail_sheet(canvas, lbl, numbers, n, cand, mean_h, max_h, floor_h,
                                   mark_scale=LS)
        if sheet is not None:
            detail_name = f'{name}_mat.png'
            cv2.imwrite(os.path.join(out_dir, detail_name), sheet)
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
            'collapse_pct': collapse_pct, 'flat': bool(flat_mode),
            'giay': round(time.time() - t0, 1), 'num_path': num_path, 'legend': legend,
            'preview': f'{name}_preview.png', 'detail_sheet': detail_name}


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
                  'preview': f'{_LARGE_DIR}/{base}_preview.png',
                  'detail_sheet': (f'{_LARGE_DIR}/{st["detail_sheet"]}' if st.get('detail_sheet') else '')})
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
    num_colors = _i('num_colors', 99, 2, 250)
    try:                                                  # SÀN 4mm: số nhỏ hơn in vải nhòe
        min_mm = max(4.0, min(20.0, float(request.POST.get('min_mm') or 5)))
    except (ValueError, TypeError):
        min_mm = 5.0
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
