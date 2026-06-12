import os
from typing import Tuple, List, Dict

import cv2
import numpy as np
from PIL import Image
from PIL.Image import Resampling
from decouple import config
from extcolors import extract_from_path

# Tìm tâm trong cùng của vùng (pole of inaccessibility).
# Trên PC dùng polylabelfast (C++); trên VPS Linux dùng python-polylabel (thuần Python,
# API y hệt). Tự chọn cái nào có sẵn.
try:
    from polylabelfast import polylabelfast
except ImportError:
    from polylabel import polylabel as _polylabel

    def polylabelfast(ring):
        return _polylabel(ring, with_distance=True)

EDGE_COLOR = (0, 0, 0)
MAX_CIRCLE_RADIUS = config("MAX_CIRCLE_RADIUS", default=10, cast=int)
# Cỡ số (px) trên ảnh làm việc — CHUẨN (số TO, tỉ lệ theo vùng, KHÔNG theo cm):
#  - MIN: số nhỏ hơn cỡ này -> BỎ số (vùng quá bé, không lọt số).
#  - MEAN: phóng số lớn dần tới khi chiều NHỎ của số đạt mức này (số đầy đặn, dễ đọc).
#  - MAX: trần chiều LỚN của số (số 2-3 chữ số không phình quá).
MIN_TEXT_SIZE = config("MIN_TEXT_SIZE", default=4, cast=int)
MEAN_TEXT_SIZE = config("MEAN_TEXT_SIZE", default=22, cast=int)
MAX_TEXT_SIZE = config("MAX_TEXT_SIZE", default=40, cast=int)
# Thu nhỏ ảnh LÀM VIỆC để đánh số nhanh (polylabel rất chậm trên ảnh lớn -> ảnh
# 2000px+ mất >2 phút, vượt thời gian chờ của trình duyệt -> "không ra kết quả").
# 1400px thừa nét cho tranh tô màu; bản in được vẽ lại theo DPI khi tải. 0 = tắt.
WORK_MAX_SIDE = config("WORK_MAX_SIDE", default=1400, cast=int)
# Ảnh THIẾT KẾ được xử lý ở 2x (ngũ quan nhỏ như mắt/mũi có gấp 4 diện tích -> giữ
# nét như Illustrator trace), nhưng không vượt cạnh dài này (giới hạn thời gian/RAM).
DESIGN_MAX_SIDE = config("DESIGN_MAX_SIDE", default=2800, cast=int)
GREEN = (0, 255, 0)
BLUE = (255, 0, 0)
PADDING_CIRCLE = config("PADDING_CIRCLE", default=1, cast=int)

CANNY_LOWER_THRESHOLD = config("CANNY_LOWER_THRESHOLD", default=10, cast=int)
CANNY_UPPER_THRESHOLD = config("CANNY_UPPER_THRESHOLD", default=50, cast=int)

CORRECT_EDGE = config("CORRECT_EDGE", default=True, cast=bool)

LIMIT_NUM_COLOR = config("LIMIT_NUM_COLOR", default=250, cast=int)
TOLERANCE = config("TOLERANCE", default=0, cast=int)
# Số màu gom mặc định khi người dùng để TRỐNG ô "Số màu tối đa".
# Ảnh mượt/AI có vô số sắc gần nhau -> phải gom lại nếu không bản đồ sẽ lấm tấm.
DEFAULT_NUM_COLORS = config("DEFAULT_NUM_COLORS", default=24, cast=int)
DEFAULT_TOLERANCE = config("DEFAULT_TOLERANCE", default=32, cast=int)
THRESHOLD_PERCENT_COLOR = config("THRESHOLD_PERCENT_COLOR", default=0.0003, cast=float)

# Màu RỰC (chroma > ngưỡng) như MÔI ĐỎ / tông nổi -> bảo vệ khỏi bị gộp mất khi giảm màu.
VIVID_CHROMA = config("VIVID_CHROMA", default=55, cast=int)
FEATURE_PROTECT_SMOOTH = config("FEATURE_PROTECT_SMOOTH", default=True, cast=bool)  # KHÔNG median-smooth làm mất chi tiết ngũ quan

PADDING_IN_CM = config("PADDING_IN_CM", default=4, cast=int)
SUB_PADDING_IN_PIXEL = config("SUB_PADDING_IN_PIXEL", default=10, cast=int)
NAME_FONT = cv2.FONT_HERSHEY_SIMPLEX
NAME_SCALE = config("NAME_SCALE", default=2, cast=int)
NAME_THICKNESS = config("NAME_THICKNESS", default=2, cast=int)


class NotFoundImageException(Exception):
    pass


def show_img(img: np.ndarray, title: str = "title", timeout=10000, resize=False):
    if resize:
        img_pil = Image.fromarray(img)
        img_pil.thumbnail((1000, 1000), Resampling.LANCZOS)
        img = np.array(img_pil)
    cv2.imshow(title, img)
    cv2.waitKey(timeout)


def show_img2(img: np.ndarray, title: str = "title", timeout=10000, resize=False):
    img_pil = Image.fromarray(img)
    if resize:
        img_pil.thumbnail((1000, 1000), Resampling.LANCZOS)
    img_pil.show(title)


def load_image(path: str, load_alpha=False, debug=False) -> np.ndarray:
    if load_alpha:
        img = cv2.imread(path, cv2.IMREAD_UNCHANGED)
    else:
        img = cv2.imread(path)
    if img is None:
        raise NotFoundImageException(path)
    if debug:
        show_img(img, 'origin image')
    return img


def get_color_areas(img: np.ndarray, lower: Tuple[int, int, int], upper: Tuple[int, int, int] = None, color_idx=-1,
                    debug=False):
    if upper is None:
        upper = lower
    range_img = cv2.inRange(img, lower, upper)  # TODO: fix me
    # show_img2(range_img)
    kernel = cv2.getStructuringElement(cv2.MORPH_CROSS, (3, 3))
    range_img = cv2.morphologyEx(range_img, cv2.MORPH_DILATE, kernel)
    # show_img2(range_img)
    # time.sleep(1000)
    if debug:
        title = f'range color {color_idx} image' if color_idx >= 0 else f'range color {lower} - {upper} image'
        show_img(range_img, title)
    return range_img


def get_edges(img: np.ndarray, debug=False) -> np.ndarray:
    canny = cv2.Canny(img, CANNY_LOWER_THRESHOLD, CANNY_UPPER_THRESHOLD, apertureSize=3, L2gradient=False)
    if CORRECT_EDGE:
        kernel = cv2.getStructuringElement(cv2.MORPH_CROSS, (3, 3))
        canny = cv2.morphologyEx(canny, cv2.MORPH_CLOSE, kernel)
    if debug:
        show_img(canny, 'edges')
    return canny


def get_text_size(text: str, scale: float = 1, thickness=1, font=cv2.FONT_HERSHEY_SIMPLEX):
    text_size, _ = cv2.getTextSize(text, font, scale, thickness)
    return text_size


def get_number_size(text: str, max_size: float,
                    min_t=None, mean_t=None, max_t=None) -> Tuple[Tuple, float, float]:
    """CHUẨN (bản đánh số gốc): phóng số LỚN DẦN tới khi chiều NHỎ của số đạt
    mean_t (mặc định 22) HOẶC chiều LỚN đạt max_t (40) HOẶC chạm mép vùng. Bỏ số
    nếu chiều nhỏ < min_t (4). Số TO, tỉ lệ theo vùng — KHÔNG theo cm.
    (min_t/mean_t/max_t để None = dùng hằng số mặc định.)"""
    min_t = MIN_TEXT_SIZE if min_t is None else min_t
    mean_t = MEAN_TEXT_SIZE if mean_t is None else mean_t
    max_t = MAX_TEXT_SIZE if max_t is None else max_t
    text_size = (0, 0)
    scale = 0.05
    thickness = 1
    while (min(text_size) < mean_t and max(text_size) < max_t
           and max(text_size) + PADDING_CIRCLE < max_size):
        text_size = get_text_size(text, scale, thickness)
        scale += 0.05
    if min(text_size) < min_t:
        return None, None, None
    return text_size, scale, thickness


def draw_number(img: np.ndarray, center: Tuple[int, int], max_size: float, number: str, debug=False) -> bool:
    text_size, scale, thickness = get_number_size(number, max_size)
    if text_size is None:
        return False

    text_origin = (center[0] - text_size[0] // 2, center[1] + text_size[1] // 2)

    if PADDING_CIRCLE > 1:
        radis = max(text_size[0] // 2, text_size[1] // 2) + PADDING_CIRCLE
        cv2.circle(img, center, radis, EDGE_COLOR, 1)
    cv2.putText(img, number, text_origin, cv2.FONT_HERSHEY_SIMPLEX, scale, EDGE_COLOR, thickness, cv2.LINE_AA)
    if debug:
        show_img(img, f"draw_number {number}")
    return True


def get_draw_number(img: np.ndarray, center: Tuple[int, int], max_size: float, number: str,
                    debug=False, min_t=None, mean_t=None, max_t=None) -> Tuple:
    text_size, scale, thickness = get_number_size(number, max_size, min_t, mean_t, max_t)
    if text_size is None:
        return None

    text_origin = (center[0] - text_size[0] // 2, center[1] + text_size[1] // 2)

    if PADDING_CIRCLE > 1:
        radis = max(text_size[0] // 2, text_size[1] // 2) + PADDING_CIRCLE
    else:
        radis = None
        # cv2.circle(img, center, radis, EDGE_COLOR, 1)
    # cv2.putText(img, number, text_origin, cv2.FONT_HERSHEY_SIMPLEX, scale, EDGE_COLOR, thickness, cv2.LINE_AA)
    return text_size, center, radis, number, text_origin, scale, thickness


def _filter_color(colors: List, pixel_count: int):
    lower_count = pixel_count * THRESHOLD_PERCENT_COLOR
    return filter(lambda x: x[1] > lower_count, colors)


def extract_colors(path: str):
    print("Extracting colors from:", path)
    colors, pixel_count = extract_from_path(path, tolerance=TOLERANCE, limit=LIMIT_NUM_COLOR)
    return _filter_color(colors, pixel_count), pixel_count


def _get_origin_parent(hierarchy, i):
    if hierarchy[i][3] == -1:
        return i
    return _get_origin_parent(hierarchy, hierarchy[i][3])


def merge_contours(contour_parents: Dict) -> List:
    results = []
    for k, contour_parent in contour_parents.items():
        cp = merge_contour(contour_parent)
        results.append(cp)
    return results


def merge_contour(contours):
    cp = []
    for c in contours:
        cp.extend(c)
    return cp


def normalize_contour(contour):
    return [contour.reshape(contour.shape[0], contour.shape[2]).tolist()]


def get_center_poly_from_contours(contours, hierarchy, range_img, img, debug=False):
    # TỐI ƯU (giữ NGUYÊN kết quả): (1) chỉ mục CON theo CHA dựng 1 lần -> bỏ việc
    # quét TOÀN BỘ hierarchy mỗi lần tìm anh em (O(n^2) -> O(n) khi ảnh nhiều
    # mảnh/lỗ, vd chân dung); (2) cache normalize_contour (tolist tốn -> mỗi
    # contour chỉ chuyển 1 lần). Thứ tự duyệt anh em vẫn theo chỉ số như cũ.
    from collections import defaultdict
    hierarchy = hierarchy[0]
    contour_parents = {}
    processed = set()

    _norm_cache = {}

    def norm(idx):
        c = _norm_cache.get(idx)
        if c is None:
            c = normalize_contour(contours[idx])
            _norm_cache[idx] = c
        return c

    children_of = defaultdict(list)          # cha -> [chỉ số con] (theo thứ tự chỉ số)
    for _idx in range(len(hierarchy)):
        children_of[hierarchy[_idx][3]].append(_idx)

    def add_sub_to_parent(parent_idx, idx):
        if parent_idx not in contour_parents:
            contour_parents[parent_idx] = [norm(parent_idx)]
            processed.add(parent_idx)
        if idx != parent_idx:
            contour_parents[parent_idx].append(norm(idx))
            processed.add(idx)

    def get_contour_same_parent_not_processed(parent_idx):
        return [norm(sub) for sub in children_of[parent_idx] if sub not in processed]

    for i, (nxt, prev, first_child, parent) in enumerate(hierarchy):
        if debug:
            cv2.drawContours(img, contours, i, GREEN, thickness=2)
            show_img(img, 'contour', timeout=1000)
        if i in processed:
            continue
        if parent == -1:
            add_sub_to_parent(i, i)
            continue
        c_t = norm(i)
        if first_child == -1:
            center, dist = polylabelfast(c_t)
            if range_img[int(center[1]), int(center[0])] == 255:
                add_sub_to_parent(i, i)
            else:
                add_sub_to_parent(parent, i)
                for sub in children_of[parent]:
                    if sub not in processed:
                        add_sub_to_parent(parent, sub)
            continue

        # ghép hiện tại với cha
        if parent in contour_parents:
            group_contour = contour_parents[parent].copy()
        else:
            group_contour = [norm(parent)]

        group_contour += get_contour_same_parent_not_processed(parent)
        merge_parent = merge_contour(group_contour)
        center, dist = polylabelfast(merge_parent)
        if range_img[int(center[1]), int(center[0])] == 255:
            add_sub_to_parent(parent, i)
            continue

        # ghép hiện tại với con
        group_contour = [c_t]
        group_contour += get_contour_same_parent_not_processed(i)
        merge_child = merge_contour(group_contour)
        center, dist = polylabelfast(merge_child)
        if range_img[int(center[1]), int(center[0])] == 255:
            add_sub_to_parent(i, first_child)
            for sub in children_of[i]:
                if sub not in processed:
                    add_sub_to_parent(i, sub)
            continue

    contours_merged = merge_contours(contour_parents)
    centers = [polylabelfast(contour) for contour in contours_merged]
    centers, dists = list(zip(*centers))
    return centers, dists


import threading as _threading
_tls = _threading.local()


# YuNet: nhận diện mặt + 5 ĐIỂM MỐC (2 mắt, mũi, 2 khoé miệng) -> định vị ngũ quan
# CHÍNH XÁC (tốt hơn Haar nhiều). Model nhỏ (~230KB) kèm trong repo. cv2 cũ (<4.8)
# có thể không nạp được -> tự fallback sang Haar.
_YUNET_PATH = os.path.join(os.path.dirname(__file__), 'models_data', 'yunet.onnx')


def _remove_small_components(mask, min_area):
    """Xoá các đốm (connected component) nhỏ hơn min_area pixel khỏi mask 0/255."""
    if not min_area or min_area <= 0:
        return mask
    try:
        num, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    except cv2.error:
        return mask
    out = np.zeros_like(mask)
    for i in range(1, num):
        if stats[i, cv2.CC_STAT_AREA] >= min_area:
            out[labels == i] = 255
    return out


def _smooth_boundaries(img_rgb, ksize=5, protect_mask=None):
    """Làm mượt biên giữa các vùng màu: median trên ẢNH NHÃN (mỗi màu = 1 nhãn).
    Median chọn nhãn ĐA SỐ trong cửa sổ -> cắt răng cưa, bỏ lồi lõm 1–2px, không
    tạo màu lạ (luôn là 1 trong các màu đang có).
    protect_mask: vùng GIỮ NGUYÊN (không median) -> không xoá chi tiết ngũ quan."""
    flat = img_rgb.reshape(-1, 3)
    colors, inv = np.unique(flat, axis=0, return_inverse=True)
    if len(colors) > 256:
        return img_rgb
    lbl = inv.reshape(img_rgb.shape[:2]).astype(np.uint8)
    sm = cv2.medianBlur(lbl, ksize)
    if protect_mask is not None:
        keep = protect_mask > 0
        sm[keep] = lbl[keep]
    return colors[sm.reshape(-1)].reshape(img_rgb.shape)


def _merge_small_regions(img_rgb, min_area=0, min_radius=5.5, max_pass=6,
                         face_mask=None, face_min_radius=None,
                         feature_mask=None, feature_min_radius=None):
    """GỘP các vùng KHÔNG ĐÁNH ĐƯỢC SỐ vào màu hàng xóm (để tranh hết 'dăm').
    Một vùng bị gộp nếu: diện tích < min_area, HOẶC bán kính nội tiếp < min_radius
    (vùng quá mảnh/nhỏ, số không lọt). Lặp tới khi không còn vùng nào phải gộp.
    Trong vùng MẶT (face_mask) dùng face_min_radius nhỏ hơn; trong NGŨ QUAN
    (feature_mask) dùng feature_min_radius nhỏ hơn nữa -> GIỮ chi tiết mắt/mũi/môi.
    Trả ảnh đã sạch: mọi vùng còn lại đều đủ chỗ để đánh số."""
    img = img_rgb.copy()
    H, W = img.shape[:2]
    k3 = np.ones((3, 3), np.uint8)
    has_face = face_mask is not None and face_min_radius is not None
    has_feat = feature_mask is not None and feature_min_radius is not None
    for _ in range(max_pass):
        flat = img.reshape(-1, 3)
        colors, inv = np.unique(flat, axis=0, return_inverse=True)
        label_img = inv.reshape(H, W).astype(np.int32)
        changed = False
        for ci in range(len(colors)):
            mask = (label_img == ci).astype(np.uint8)
            if not mask.any():
                continue
            dist = cv2.distanceTransform(mask, cv2.DIST_L2, 3)
            num, comp, stats, cents = cv2.connectedComponentsWithStats(mask, connectivity=8)
            for k in range(1, num):
                area = stats[k, cv2.CC_STAT_AREA]
                cm = comp == k
                rad = float(dist[cm].max())
                thr, eff_area = min_radius, min_area
                if has_face:
                    cxx, cyy = int(cents[k][0]), int(cents[k][1])
                    inb = 0 <= cyy < H and 0 <= cxx < W
                    if inb and has_feat and feature_mask[cyy, cxx] > 0:
                        thr, eff_area = feature_min_radius, 0  # ngũ quan: giữ chi tiết nhỏ nhất
                    elif inb and face_mask[cyy, cxx] > 0:
                        thr, eff_area = face_min_radius, 0     # trong mặt: giữ chi tiết nhỏ
                too_small = (eff_area and area < eff_area) or (rad < thr)
                if not too_small:
                    continue
                dil = cv2.dilate(cm.astype(np.uint8), k3) > 0
                nb = label_img[dil & (~cm)]
                nb = nb[nb != ci]
                if nb.size == 0:
                    continue
                new_ci = int(np.bincount(nb).argmax())
                img[cm] = colors[new_ci]
                label_img[cm] = new_ci
                changed = True
        if not changed:
            break
    return img


def _quantize_rarity(src_rgb, k, rar_pow=0.5, vivid_chroma=48, vivid_boost=0.02,
                     seed=7):
    """Chọn bảng màu bằng K-MEANS LAB CÓ TRỌNG SỐ ĐỘ HIẾM: pixel màu hiếm (môi đỏ,
    bóng mũi, má hồng — nhỏ nhưng quan trọng) được lấy mẫu nhiều hơn -> CÓ CỤM RIÊNG.
    Median-cut chia ô theo SỐ LƯỢNG pixel nên tông hiếm bị nuốt (môi đỏ 0.14% ảnh
    mất ngay cả ở k=256) — đây là lý do đổi sang cách này."""
    # GHI CHÚ RAM: chạy trên VPS nhỏ, 2 job song song (ThreadPoolExecutor) — tính
    # toán theo khối + giải phóng mảng tạm NGAY sau khi dùng, tránh giữ float64 to.
    H, W = src_rgb.shape[:2]
    lab8 = cv2.cvtColor(src_rgb, cv2.COLOR_RGB2LAB)          # uint8, nhẹ
    flat8 = lab8.reshape(-1, 3)
    P = flat8.shape[0]
    # Độ hiếm: histogram LAB 8x8x8 bin (>>5 trên uint8, khỏi cần mảng float).
    bid = (flat8[:, 0].astype(np.int32) >> 5) * 64 \
        + (flat8[:, 1].astype(np.int32) >> 5) * 8 \
        + (flat8[:, 2].astype(np.int32) >> 5)
    freq = np.bincount(bid, minlength=512).astype(np.float64)
    w = 1.0 / np.power(freq[bid], rar_pow)
    w /= w.sum()
    del bid, freq
    rng = np.random.default_rng(seed)              # seed cố định -> kết quả ổn định
    N = min(150_000, P)
    idx = rng.choice(P, size=N, replace=True, p=w)
    del w
    samples = flat8[idx].astype(np.float32)
    del idx
    # Ép thêm pixel RỰC (môi đỏ...) chiếm >= vivid_boost tỉ lệ mẫu.
    chroma = np.abs(flat8[:, 1].astype(np.int16) - 128) \
        + np.abs(flat8[:, 2].astype(np.int16) - 128)
    viv = np.where(chroma > vivid_chroma)[0]
    del chroma
    if viv.size:
        rep = viv[rng.integers(0, viv.size, size=int(N * vivid_boost))]
        samples = np.vstack([samples, flat8[rep].astype(np.float32)])
    del viv
    k = max(2, min(int(k), len(np.unique(samples, axis=0))))
    crit = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 25, 0.5)
    _, _, centers = cv2.kmeans(samples, k, None, crit, 2, cv2.KMEANS_PP_CENTERS)
    del samples
    # Gán mỗi pixel về center gần nhất. Dùng khai triển ||a-b||² = a²-2ab+b²
    # (ma trận 2D, KHÔNG broadcast 3D — ảnh 2x broadcast 3D tốn ~400MB RAM).
    lbl = np.empty(P, dtype=np.int32)
    c2 = (centers ** 2).sum(1)[None, :]
    for s in range(0, P, 400_000):
        chunk = flat8[s:s + 400_000].astype(np.float32)
        d = c2 - 2.0 * (chunk @ centers.T)         # bỏ a² (hằng theo hàng, không đổi argmin)
        lbl[s:s + 400_000] = d.argmin(1)
    # Màu đại diện = trung bình RGB của cụm (bincount từng kênh, không giữ float64 to).
    out = np.zeros((k, 3), np.float64)
    cnt = np.bincount(lbl, minlength=k).astype(np.float64)
    flat_rgb = src_rgb.reshape(-1, 3)
    for c in range(3):
        out[:, c] = np.bincount(lbl, weights=flat_rgb[:, c].astype(np.float64),
                                minlength=k)
    out = (out / np.maximum(cnt, 1)[:, None]).round().clip(0, 255).astype(np.uint8)
    return out[lbl].reshape(H, W, 3)


def _sweep_dust(lbl, n_colors, dust_area=4):
    """QUÉT BỤI vector hoá: mọi đốm <= dust_area px (vụn k-means, vô hình ở bản in)
    nhận nhãn của hàng xóm sát cạnh — 1 lượt O(P), giảm ~90% số component phải vào
    vòng phân tích chậm phía sau. Trả True nếu có thay đổi."""
    H, W = lbl.shape
    dust = np.zeros((H, W), bool)
    for ci in range(n_colors):
        mask = (lbl == ci).astype(np.uint8)
        if not mask.any():
            continue
        num, comp, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
        lut = np.zeros(num, bool)
        lut[1:] = stats[1:, cv2.CC_STAT_AREA] <= dust_area
        dust |= lut[comp]
    if not dust.any():
        return False
    lbl[dust] = -1
    for _ in range(4):                       # bụi <=4px: vài lượt lan là hết
        holes = lbl < 0
        if not holes.any():
            break
        for dy, dx in ((0, 1), (0, -1), (1, 0), (-1, 0)):
            nbr = np.roll(lbl, (dy, dx), axis=(0, 1))
            # không cho cuộn vòng qua mép ảnh
            if dy == 1:
                nbr[0, :] = -1
            elif dy == -1:
                nbr[-1, :] = -1
            if dx == 1:
                nbr[:, 0] = -1
            elif dx == -1:
                nbr[:, -1] = -1
            fill = holes & (nbr >= 0)
            lbl[fill] = nbr[fill]
            holes &= ~fill
    if (lbl < 0).any():                      # bụi kẹt (suy biến): trả về nhãn 0
        lbl[lbl < 0] = 0
    return True


def _merge_keep_features(arr, r_keep, de_keep, min_area=0, max_pass=4):
    """Gộp mảng nhỏ vào hàng xóm NHƯNG GIỮ chi tiết ngũ quan: đốm nhỏ TRÒN có màu
    TƯƠNG PHẢN CAO với xung quanh (lòng trắng mắt, lỗ mũi, viền môi) được GIỮ;
    chỉ gộp bụi thật (rad<1), sliver dẹt (thon dài sát biên) và mảng màu GẦN GIỐNG
    hàng xóm (deltaE < de_keep). Trả (ảnh, mask các chi tiết đã giữ)."""
    img = arr.copy()
    H, W = img.shape[:2]
    k3 = np.ones((3, 3), np.uint8)
    feature = np.zeros((H, W), np.uint8)
    for _ in range(max_pass):
        flat = img.reshape(-1, 3)
        colors, inv = np.unique(flat, axis=0, return_inverse=True)
        lbl = inv.reshape(H, W).astype(np.int32)
        lab = cv2.cvtColor(colors.reshape(-1, 1, 3).astype('uint8'),
                           cv2.COLOR_RGB2LAB).reshape(-1, 3).astype(float)
        changed = _sweep_dust(lbl, len(colors))          # bụi vụn đi trước, rẻ
        if changed:
            img = colors[lbl.reshape(-1)].reshape(img.shape)
        feature[:] = 0
        for ci in range(len(colors)):
            mask = (lbl == ci).astype(np.uint8)
            if not mask.any():
                continue
            dist = cv2.distanceTransform(mask, cv2.DIST_L2, 3)
            num, comp, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
            for kk in range(1, num):
                area = stats[kk, cv2.CC_STAT_AREA]
                x, y, w, h = stats[kk, 0], stats[kk, 1], stats[kk, 2], stats[kk, 3]
                # Cửa sổ NỚI 1px (kẹp biên ảnh): bbox sít làm vòng dilate không nở
                # ra ngoài được -> đốm lấp đầy bbox không bao giờ tìm thấy hàng xóm.
                x0, y0 = max(x - 1, 0), max(y - 1, 0)
                x1, y1 = min(x + w + 1, W), min(y + h + 1, H)
                sub = comp[y0:y1, x0:x1] == kk
                rad = float(dist[y0:y1, x0:x1][sub].max())
                if rad >= r_keep and (not min_area or area >= min_area):
                    continue
                dil = cv2.dilate(sub.astype(np.uint8), k3) > 0
                ring = dil & (~sub)
                nb = lbl[y0:y1, x0:x1][ring]
                nb = nb[(nb != ci) & (nb >= 0)]
                if nb.size == 0:
                    continue
                nb_ci = int(np.bincount(nb).argmax())
                de = float(np.sqrt(((lab[ci] - lab[nb_ci]) ** 2).sum()))
                elong = area / max(rad * rad, 1e-6)   # ~3.14 = tròn; lớn = dẹt/dài
                true_dust = rad < 1.0
                sliver = (rad < 1.6) and (elong > 8.0)
                if true_dust or sliver or de < de_keep:
                    yy, xx = np.where(sub)
                    img[y0 + yy, x0 + xx] = colors[nb_ci]
                    lbl[y0 + yy, x0 + xx] = nb_ci
                    changed = True
                else:
                    yy, xx = np.where(sub)
                    feature[y0 + yy, x0 + xx] = 255        # ngũ quan nhỏ: GIỮ
        if not changed:
            break
    return img, feature


def _smooth_labels_voting(arr, sigma, protect=None):
    """Làm mượt biên kiểu VECTOR-TRACE: mỗi màu (nhãn) blur Gaussian, pixel theo
    nhãn có 'phiếu' cao nhất -> biên cong mượt, KHÔNG tạo màu lạ, không răng cưa.
    protect: vùng giữ nguyên nhãn gốc (chi tiết ngũ quan nhỏ đã được giữ)."""
    flat = arr.reshape(-1, 3)
    colors, inv = np.unique(flat, axis=0, return_inverse=True)
    H, W = arr.shape[:2]
    lbl = inv.reshape(H, W).astype(np.int32)
    best_v = np.full((H, W), -1.0, np.float32)
    best_l = np.zeros((H, W), np.int32)
    for ci in range(len(colors)):
        m = (lbl == ci).astype(np.float32)
        if not m.any():
            continue
        g = cv2.GaussianBlur(m, (0, 0), sigma)
        upd = g > best_v
        best_v[upd] = g[upd]
        best_l[upd] = ci
    if protect is not None:
        keep = protect > 0
        best_l[keep] = lbl[keep]
    return colors[best_l.reshape(-1)].reshape(arr.shape)


def _quantize_file(path, n, smooth=0, min_area=0, print_long_cm=0, design_out=None):
    """Tạo ảnh THIẾT KẾ chất lượng Illustrator-trace rồi lưu file LÀM VIỆC tạm (1x)
    cho khâu đánh số. Trả đường_dẫn_tạm.

    Chuỗi mới (thay median-cut + median-blur cũ — cái làm MẤT môi đỏ/bệt mặt):
      1. (smooth>=2) mean-shift dọn ảnh chụp/màu nước.
      2. Phóng 2x (tối đa DESIGN_MAX_SIDE) -> ngũ quan nhỏ đủ diện tích giữ nét.
      3. K-means LAB trọng-số-độ-hiếm -> tông hiếm (môi, bóng mũi) có cụm riêng.
      4. Gộp mảng GIỮ-CHI-TIẾT (đốm tròn tương phản cao = ngũ quan -> giữ).
      5. Làm mượt biên Gaussian-voting (cong mượt kiểu vector).
    design_out: lưu bản THIẾT KẾ 2x (đẹp) ra đường dẫn này; file làm việc 1x dùng
    đánh số (palette y hệt bản 2x).
    smooth (0..1): không tiền xử lý (ảnh AI/anime đã phẳng); (2..3): mean-shift."""
    import os
    import tempfile
    im = Image.open(path).convert('RGB')
    # Thu nhỏ ảnh làm việc -> đánh số nhanh, tránh treo/quá thời gian chờ trên ảnh lớn.
    if WORK_MAX_SIDE and max(im.size) > WORK_MAX_SIDE:
        im = im.copy()
        im.thumbnail((WORK_MAX_SIDE, WORK_MAX_SIDE), Resampling.LANCZOS)
    target = max(2, n)
    W1, H1 = im.size
    sm_level = int(smooth) if (smooth and int(smooth) > 0) else 0

    if sm_level >= 2:
        # LÀM PHẲNG (mean-shift) trước khi gom -> dọn ảnh chụp/màu nước cho sạch mảng.
        sp, sr = {2: (16, 32), 3: (26, 50)}.get(sm_level, (16, 32))
        a = np.array(im)[:, :, ::-1].copy()            # RGB -> BGR
        h, w = a.shape[:2]
        scale = 1.0
        if max(h, w) > 900:
            scale = 900.0 / max(h, w)
            a = cv2.resize(a, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
        a = cv2.pyrMeanShiftFiltering(a, sp, sr)
        if scale != 1.0:
            a = cv2.resize(a, (w, h), interpolation=cv2.INTER_NEAREST)
        im = Image.fromarray(np.ascontiguousarray(a[:, :, ::-1]))   # BGR -> RGB

    # Xử lý ở 2x (giới hạn DESIGN_MAX_SIDE) -> mắt/mũi/môi có 4x diện tích, giữ nét.
    s = 1.0
    if DESIGN_MAX_SIDE:
        s = max(1.0, min(2.0, DESIGN_MAX_SIDE / float(max(W1, H1))))
    im2 = im.resize((int(W1 * s), int(H1 * s)), Resampling.LANCZOS) if s > 1.0 else im

    arr = _quantize_rarity(np.array(im2), k=target)
    r_keep = ((MIN_TEXT_SIZE + 2 * PADDING_CIRCLE) / 2.0 + 1.0) * s
    arr, feat = _merge_keep_features(arr, r_keep=r_keep, de_keep=18.0,
                                     min_area=int(min_area * s * s), max_pass=4)
    arr = _smooth_labels_voting(arr, sigma=2.2 * s, protect=feat)
    arr, _ = _merge_keep_features(arr, r_keep=1.8 * s, de_keep=10.0, max_pass=2)

    if design_out:
        try:
            Image.fromarray(arr).save(design_out)      # bản thiết kế 2x sắc nét
        except OSError:
            pass
    # File LÀM VIỆC 1x cho đánh số (NEAREST giữ nguyên bảng màu).
    work = Image.fromarray(arr)
    if s > 1.0:
        work = work.resize((W1, H1), Resampling.NEAREST)
    fd, out = tempfile.mkstemp(suffix='.png', prefix='quant_')
    os.close(fd)
    work.save(out)
    return out


def _reduce_palette_perceptual(img_rgb, target_n, protect_mask=None):
    """Hợp nhất bảng màu xuống target_n bằng cách GỘP DẦN 2 màu GIỐNG NHAU NHẤT
    (khoảng cách trong không gian LAB), không phụ thuộc diện tích. Nhờ vậy nhiều
    sắc cùng tông (vd hàng loạt xanh lá nền) dồn lại, nhường suất cho các tông
    khác biệt (hồng, cam, xanh dương) -> tranh đặc sắc + đỡ 'dăm'."""
    flat = img_rgb.reshape(-1, 3)
    colors, inv, counts = np.unique(flat, axis=0, return_inverse=True, return_counts=True)
    K = len(colors)
    if K <= target_n:
        return img_rgb
    lab = cv2.cvtColor(colors.reshape(-1, 1, 3).astype('uint8'),
                       cv2.COLOR_RGB2LAB).reshape(-1, 3).astype(float)
    L = lab[:, 0]
    chroma = np.abs(lab[:, 1] - 128) + np.abs(lab[:, 2] - 128)
    # BẢO VỆ (gộp sau cùng + không dồn vào đen/trắng):
    #  - màu RỰC (chroma cao) như MÔI ĐỎ / tông nổi -> đỡ bị gộp mất;
    #  - màu chủ yếu nằm trong protect_mask (vd ngũ quan) nếu có.
    prot = chroma > VIVID_CHROMA
    if protect_mask is not None and protect_mask.any():
        inmask = np.bincount(inv[protect_mask.reshape(-1) > 0], minlength=K)
        prot = prot | ((inmask / np.maximum(counts, 1)) > 0.35)
    clusters = {i: {'lab': lab[i].copy(), 'cnt': float(counts[i]), 'members': [i],
                    'prot': bool(prot[i])}
                for i in range(K)}

    def _merge_into(base, others):
        for m in others:
            if m == base or m not in clusters or base not in clusters:
                continue
            cb, cm = clusters[base], clusters[m]
            tot = cb['cnt'] + cm['cnt']
            cb['lab'] = (cb['lab'] * cb['cnt'] + cm['lab'] * cm['cnt']) / tot
            cb['cnt'] = tot
            cb['members'] += cm['members']
            del clusters[m]

    # LUẬT MẮT NGƯỜI: mọi sắc rất TỐI dồn thành 1 "đen", các sắc gần-TRẮNG TRUNG
    # TÍNH dồn thành 1 "trắng". Ngưỡng TRẮNG siết chặt (L>240) để KHÔNG nuốt các
    # tông sáng có màu (trời/vùng sáng hơi xanh) -> tránh nhuộm vịt sang tông lạnh.
    # (cv2-LAB: L 0-255; chroma quanh 128.)
    L = lab[:, 0]
    chroma = np.abs(lab[:, 1] - 128) + np.abs(lab[:, 2] - 128)
    # KHÔNG dồn màu ngũ quan (prot) vào đen/trắng -> mắt/mày/môi không bị mất tông.
    darks = [i for i in range(K) if L[i] < 55 and chroma[i] < 36 and not prot[i]]
    whites = [i for i in range(K) if L[i] > 240 and chroma[i] < 24 and not prot[i]]
    if len(darks) > 1:
        _merge_into(min(darks, key=lambda m: L[m]), darks)        # gốc = tối nhất
    if len(whites) > 1:
        _merge_into(max(whites, key=lambda m: L[m]), whites)      # gốc = sáng nhất

    PROT_PEN = 1e7   # phạt lớn để màu ngũ quan gộp SAU CÙNG (khi đã hết màu thường)
    while len(clusters) > target_n:
        ids = list(clusters.keys())
        best, pair = None, None
        for a in range(len(ids)):
            ca = clusters[ids[a]]; la = ca['lab']
            for b in range(a + 1, len(ids)):
                cb = clusters[ids[b]]
                d = float(((la - cb['lab']) ** 2).sum())
                if ca['prot']:
                    d += PROT_PEN
                if cb['prot']:
                    d += PROT_PEN
                if best is None or d < best:
                    best, pair = d, (ids[a], ids[b])
        i, j = pair
        ci, cj = clusters[i], clusters[j]
        tot = ci['cnt'] + cj['cnt']
        ci['lab'] = (ci['lab'] * ci['cnt'] + cj['lab'] * cj['cnt']) / tot
        ci['cnt'] = tot
        ci['members'] += cj['members']
        ci['prot'] = ci['prot'] or cj['prot']
        del clusters[j]
    # Đại diện mỗi nhóm:
    #  - nhóm sáng & trung tính -> lấy màu TRẮNG NHẤT (tránh ám xanh do vùng lạnh
    #    lớn lấn át, vd vịt trắng);
    #  - nhóm tối & trung tính -> lấy màu ĐEN NHẤT;
    #  - còn lại -> màu nhiều pixel nhất (giữ tông thật).
    rep_of = np.zeros((K, 3), dtype=np.uint8)
    for cl in clusters.values():
        mem = cl['members']
        cL, cC = cl['lab'][0], abs(cl['lab'][1] - 128) + abs(cl['lab'][2] - 128)
        if cL > 225 and cC < 22:
            best_m = max(mem, key=lambda m: L[m] - chroma[m])      # trắng & trung tính nhất
        elif cL < 65 and cC < 30:
            best_m = min(mem, key=lambda m: L[m] + chroma[m])      # đen & trung tính nhất
        else:
            best_m = max(mem, key=lambda m: counts[m])
        rep = colors[best_m]
        for m in mem:
            rep_of[m] = rep
    out = np.zeros_like(flat)
    for k, c in enumerate(colors):
        out[np.all(flat == c, axis=1)] = rep_of[k]
    return out.reshape(img_rgb.shape)


def _snap_to_design_palette(img_rgb):
    """GIỮ NGUYÊN bảng màu THIẾT KẾ (các màu đủ lớn ≥ 0.03% như index_color gốc),
    chỉ SNAP pixel răng cưa / nhiễu về màu thiết kế gần nhất (RGB Euclid). KHÔNG giảm
    số màu thiết kế: ảnh vốn đã phẳng (<=256 màu) -> trả NGUYÊN, không đổi 1 pixel.
    Chỉ ảnh NHIỀU màu (răng cưa / ảnh chụp) mới bị dồn về các màu nổi bật để đánh
    số được (palette KHÔNG phụ thuộc min_area -> kết quả đơn điệu theo min_area)."""
    flat = img_rgb.reshape(-1, 3)
    colors, inv, counts = np.unique(flat, axis=0, return_inverse=True, return_counts=True)
    if len(colors) <= 256:
        return img_rgb                                   # đã phẳng -> giữ nguyên 100%
    total = int(flat.shape[0])
    thr = max(total * THRESHOLD_PERCENT_COLOR, 1.0)
    keep = counts >= thr
    if keep.sum() < 2:                                   # ảnh quá nhiễu: giữ ~64 màu lớn nhất
        cut = np.sort(counts)[-min(64, len(counts))]
        keep = counts >= cut
    keep_colors = colors[keep].astype(np.int32)
    # Mỗi màu unique -> màu GIỮ gần nhất (RGB Euclid). Chia KHỐI để bó bộ nhớ
    # (ảnh JPEG nhiễu có thể hàng chục nghìn màu -> ma trận U×K rất lớn).
    nearest = np.empty(len(colors), dtype=np.int64)
    src = colors.astype(np.int32)
    for s in range(0, len(src), 4096):
        chunk = src[s:s + 4096]
        d = ((chunk[:, None, :] - keep_colors[None, :, :]) ** 2).sum(2)
        nearest[s:s + 4096] = d.argmin(1)
    remap = keep_colors[nearest].astype(np.uint8)
    return remap[inv].reshape(img_rgb.shape)


def _flat_work_file(path, min_area=0):
    """Chuẩn bị ảnh LÀM VIỆC cho nhánh PHẲNG: thu nhỏ về WORK_MAX_SIDE, GIỮ NGUYÊN
    bảng màu thiết kế (KHÔNG median-cut, KHÔNG giảm màu). Nếu min_area > 0: dồn răng
    cưa về bảng màu thiết kế rồi GỘP đốm < min_area px vào hàng xóm (sạch để đánh số).
    Trả đường dẫn file tạm."""
    import os
    import tempfile
    im = Image.open(path).convert('RGB')
    if WORK_MAX_SIDE and max(im.size) > WORK_MAX_SIDE:
        im.thumbnail((WORK_MAX_SIDE, WORK_MAX_SIDE), Resampling.LANCZOS)
    arr = np.array(im)
    arr = _snap_to_design_palette(arr)                   # khử răng cưa (ảnh >256 màu); phẳng -> giữ nguyên
    if min_area and int(min_area) > 0:                   # min_area CHỈ gộp mảng nhỏ, không đụng palette
        arr = _merge_small_regions(arr, min_area=int(min_area), min_radius=0, max_pass=2)
    fd, out = tempfile.mkstemp(suffix='.png', prefix='flat_')
    os.close(fd)
    Image.fromarray(arr).save(out)
    return out


def _number_work_image(work_path, design_out=None, debug=False):
    """ĐÁNH SỐ + đếm % trên 1 ảnh LÀM VIỆC đã chuẩn bị — đây là phần index_color GỐC
    (extract màu -> contour từng màu -> polylabel -> vẽ số). design_out: lưu ảnh work
    (bản màu phẳng) để xem trước. Xoá file work tạm khi xong."""
    import os
    import shutil
    if design_out:
        try:
            shutil.copyfile(work_path, design_out)   # bản màu phẳng để xem trước
        except OSError:
            pass
    colors, pixel_count = extract_colors(work_path)
    colors = list(colors)

    img = load_image(work_path, debug=debug)

    # Cỡ số CHUẨN: cố định theo px (số to, tỉ lệ vùng) — KHÔNG theo cm.
    min_t, mean_t, max_t = MIN_TEXT_SIZE, MEAN_TEXT_SIZE, MAX_TEXT_SIZE

    img_white = np.zeros([img.shape[0], img.shape[1], 1], dtype=np.uint8)
    img_white.fill(255)

    img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    color_mapping = []
    color_counts = []
    color_idx = 1
    draws = []
    for color, count in colors:
        # print(f"Processing color {color_idx}: {color} with count: {count}")
        range_img = get_color_areas(img_rgb, color, color, color_idx, debug=debug)
        # (Mảng nhỏ đã được GỘP vào hàng xóm ở bước _quantize_file -> không xoá nữa.)

        contours, hierarchy = cv2.findContours(range_img, cv2.RETR_TREE, cv2.CHAIN_APPROX_NONE)
        # Phòng trường hợp màu không còn vùng nào -> bỏ qua.
        if hierarchy is None or not contours:
            color_idx += 1
            continue
        cv2.drawContours(img_white, contours, -1, (0, 0, 0), 1)

        centers, dists = get_center_poly_from_contours(contours, hierarchy, range_img, np.array(img), debug=debug)
        count_number = 0
        for c, d in zip(centers, dists):
            d = d * 2
            draw = get_draw_number(img_white, (int(c[0]), int(c[1])), d, f'{len(color_mapping) + 1}',
                                   debug=debug, min_t=min_t, mean_t=mean_t, max_t=max_t)
            if draw is not None:
                count_number += 1
                draws.append(draw)
        if count_number > 0:
            color_mapping.append(color)
            color_counts.append(count)
        color_idx += 1

    # img_white = cv2.cvtColor(img_white, cv2.COLOR_RGB2GRAY)
    img_white = 255 - img_white
    img_white = cv2.ximgproc.thinning(img_white, None, 1)
    img_white = 255 - img_white
    img_white = cv2.cvtColor(img_white, cv2.COLOR_GRAY2RGB)

    for text_size, center, radis, number, text_origin, scale, thickness in draws:
        # print(f"Drawing number {number} at center {center} with radius {radis} and text origin {text_origin}")
        if radis is not None:
            cv2.circle(img_white, center, radis, EDGE_COLOR, 1)
        cv2.putText(img_white, number, text_origin, cv2.FONT_HERSHEY_SIMPLEX, scale, EDGE_COLOR, thickness, cv2.LINE_AA)

    color_mapping = [(i + 1, c) for i, c in enumerate(color_mapping)]
    total_count = sum(color_counts) if color_counts else 0
    if total_count > 0:
        percentages = [round(c / total_count * 100, 2) for c in color_counts]
    else:
        percentages = [0 for _ in color_counts]
    if debug:
        show_img(img_white, "output")

    try:
        os.remove(work_path)   # dọn file tạm đã gom màu
    except OSError:
        pass

    print("Done indexing colors")
    return img_white, color_mapping, percentages


def index_color(path, debug=False, num_colors=0, min_area=0, smooth=0, design_out=None,
                print_long_cm=0):
    """num_colors > 0: gom ảnh về tối đa N màu (để trống = DEFAULT_NUM_COLORS).
    min_area > 0: gộp các mảng màu nhỏ hơn N pixel vào hàng xóm (đỡ lấm tấm).
    smooth (0..3): làm phẳng vùng (mean-shift) trước khi gom — dọn ảnh màu nước/chụp.
    design_out: nếu có, lưu ảnh THIẾT KẾ (bản màu phẳng đã gom) ra đường dẫn này.
    print_long_cm: nhận cho tương thích, không dùng (cỡ số cố định)."""
    effective_n = num_colors if (num_colors and num_colors > 0) else DEFAULT_NUM_COLORS
    # design_out được ghi NGAY trong _quantize_file (bản 2x sắc nét);
    # _number_work_image chỉ đánh số trên file làm việc 1x (palette y hệt).
    work_path = _quantize_file(path, effective_n, smooth=smooth, min_area=min_area,
                               print_long_cm=print_long_cm, design_out=design_out)
    return _number_work_image(work_path, design_out=None, debug=debug)


def index_color_flat(path, min_area=0, design_out=None):
    """ẢNH ĐÃ THIẾT KẾ PHẲNG: GIỮ NGUYÊN bảng màu thiết kế (KHÔNG quantize / KHÔNG
    giảm màu) — đúng phần mềm 'index_color' gốc + (tuỳ chọn) gộp đốm < min_area px
    vào hàng xóm. Chỉ đánh số; khớp mã DALI làm ở bước ngoài (≡ 'django_dali').
    min_area=0 -> y hệt index_color gốc (chỉ tự lọc màu chiếm < 0.03% diện tích)."""
    work_path = _flat_work_file(path, min_area=min_area)
    return _number_work_image(work_path, design_out=design_out)


def cal_padding_in_pixel(im_path: str) -> int:
    im = Image.open(im_path)
    dpi = im.info['dpi'][0]
    return PADDING_IN_CM * dpi / 2.54


def rotate_image(mat, angle):
    """
    Rotates an image (angle in degrees) and expands image to avoid cropping
    """

    height, width = mat.shape[:2]  # image shape has 3 dimensions
    image_center = (
        width / 2,
        height / 2)  # getRotationMatrix2D needs coordinates in reverse order (width, height) compared to shape

    rotation_mat = cv2.getRotationMatrix2D(image_center, angle, 1.)

    # rotation calculates the cos and sin, taking absolutes of those.
    abs_cos = abs(rotation_mat[0, 0])
    abs_sin = abs(rotation_mat[0, 1])

    # find the new width and height bounds
    bound_w = int(height * abs_sin + width * abs_cos)
    bound_h = int(height * abs_cos + width * abs_sin)

    # subtract old image center (bringing image back to origo) and adding the new image center coordinates
    rotation_mat[0, 2] += bound_w / 2 - image_center[0]
    rotation_mat[1, 2] += bound_h / 2 - image_center[1]

    # rotate image with the new bounds and translated rotation matrix
    rotated_mat = cv2.warpAffine(mat, rotation_mat, (bound_w, bound_h))
    return rotated_mat


def draw_result(edge_img, dpi, width, height, img_name: str = None, debug=False):
    """
    40x50, 20x20, 30x30, 50x65
    A3: 29.7 x 42.0
    :param edge_img:
    :param dpi:
    :param width:
    :param height:
    :return:
    """
    dpi = list(dpi)
    if edge_img.shape[0] > edge_img.shape[1] and width > height:
        t = width
        width = height
        height = t
        t = dpi[0]
        dpi[0] = dpi[1]
        dpi[1] = t
    elif edge_img.shape[0] < edge_img.shape[1] and width < height:
        t = width
        width = height
        height = t
        t = dpi[0]
        dpi[0] = dpi[1]
        dpi[1] = t

    new_im_width = int(width * dpi[0] / 2.54)
    new_im_height = int(height * dpi[1] / 2.54)

    edge_img = cv2.resize(edge_img, (new_im_width, new_im_height))

    im_height, im_width = edge_img.shape[:2]

    dpcm_w = im_width / width
    dpcm_h = im_height / height
    width_padding = int(dpcm_w * PADDING_IN_CM)
    height_padding = int(dpcm_h * PADDING_IN_CM)

    image_paint = np.zeros((im_height + height_padding * 2, im_width + width_padding * 2), np.uint8)
    image_paint[:] = (255)
    image_paint[height_padding: height_padding + im_height, width_padding: width_padding + im_width] = edge_img
    image_paint = cv2.rectangle(image_paint, (width_padding, height_padding),
                                (width_padding + im_width, height_padding + im_height), EDGE_COLOR,
                                thickness=NAME_THICKNESS)
    if debug:
        # show_img(image_paint, 'background image', timeout=3000)
        cv2.imwrite('app/data/image_paint.png', image_paint)

    if img_name:
        RATE_ALIGN = 0.08
        text_size, _ = cv2.getTextSize(img_name, NAME_FONT, NAME_SCALE, NAME_THICKNESS)

        # draw top left
        start_name_pos_w = int(width_padding + im_width * RATE_ALIGN)
        start_name_pos_h = height_padding - text_size[1] - SUB_PADDING_IN_PIXEL
        cv2.putText(image_paint, img_name, (start_name_pos_w, start_name_pos_h), NAME_FONT, NAME_SCALE, EDGE_COLOR,
                    NAME_THICKNESS)

        # draw made in vietnam bottom left
        start_name_pos_h = int(im_height + height_padding + text_size[1] + SUB_PADDING_IN_PIXEL)
        cv2.putText(image_paint, "MADE IN VIETNAM", (start_name_pos_w, start_name_pos_h), NAME_FONT, NAME_SCALE,
                    EDGE_COLOR, NAME_THICKNESS)

        # draw bottom right
        start_name_pos_w = int(image_paint.shape[1] - (text_size[0] + width_padding + RATE_ALIGN * im_width))
        start_name_pos_h = int(im_height + height_padding + text_size[1] + SUB_PADDING_IN_PIXEL)
        cv2.putText(image_paint, img_name, (start_name_pos_w, start_name_pos_h), NAME_FONT, NAME_SCALE, EDGE_COLOR,
                    NAME_THICKNESS)

        # Get text image
        text_img = np.zeros((text_size[1], text_size[0]), np.uint8)
        text_img[:] = (255)
        cv2.putText(text_img, img_name, (0, text_size[1]), NAME_FONT, NAME_SCALE, EDGE_COLOR, NAME_THICKNESS)
        if debug:
            cv2.imwrite('app/data/text.png', text_img)

        # draw bottom left
        start_name_pos_w = int(width_padding - text_size[1] - SUB_PADDING_IN_PIXEL)
        start_name_pos_h = int(image_paint.shape[0] - (text_size[0] + height_padding + im_height * RATE_ALIGN))

        text_img_bottom_left = rotate_image(text_img, 90)
        image_paint[start_name_pos_h: start_name_pos_h + text_img_bottom_left.shape[0],
        start_name_pos_w: start_name_pos_w + text_img_bottom_left.shape[1]] = text_img_bottom_left

        # draw top right
        text_img_top_right = rotate_image(text_img, -90)
        start_name_pos_w = int(width_padding + im_width + SUB_PADDING_IN_PIXEL)
        start_name_pos_h = int(height_padding + im_height * RATE_ALIGN)
        image_paint[start_name_pos_h: start_name_pos_h + text_img_top_right.shape[0],
        start_name_pos_w: start_name_pos_w + text_img_top_right.shape[1]] = text_img_top_right

        if debug:
            # show_img(image_paint, 'background image', timeout=3000)
            cv2.imwrite('app/data/image_paint_name.png', image_paint)

        return image_paint


def draw_result_a3(edge_img, dpi, img_name: str, debug=False, orientation='portrait'):
    """

    :param edge_img:
    :param dpi:
    :param img_name:
    :param debug:
    :param orientation: portrait, landscape
    :return:
    """
    if orientation == 'auto':
        if edge_img.shape[1] > edge_img.shape[0]:
            orientation = 'landscape'
        else:
            orientation = 'portrait'

    print(f"draw a3 of image: {img_name} with orientation = {orientation}")
    height, width = edge_img.shape[:2]
    a3_width = 29.7
    a3_height = 42.0
    a3_width = int(a3_width * dpi[0] / 2.54)
    a3_height = int(a3_height * dpi[1] / 2.54)

    padding_width = int(1 * dpi[0] / 2.54)
    padding_height = int(1 * dpi[1] / 2.54)

    image_paint = np.zeros((a3_height, a3_width), np.uint8)
    image_paint[:] = (255)

    bottom_img = cv2.imread('app/data/bottom_img.png', cv2.IMREAD_GRAYSCALE)
    text_size, _ = cv2.getTextSize(img_name, NAME_FONT, NAME_SCALE, NAME_THICKNESS)
    start_name_pos_w = int(a3_width * 0.9 - text_size[0])
    start_name_pos_h = int(text_size[1] + 10)
    cv2.putText(bottom_img, img_name, (start_name_pos_w, start_name_pos_h), NAME_FONT, NAME_SCALE, EDGE_COLOR,
                NAME_THICKNESS)
    scale = (a3_width - 2 * padding_width) / bottom_img.shape[1]
    bottom_img = cv2.resize(bottom_img, (a3_width - 2 * padding_width, int(scale * bottom_img.shape[0])))
    if debug:
        cv2.imwrite("app/data/bottom_img_fill_name.png", bottom_img)
    start_name_pos_h = image_paint.shape[0] - bottom_img.shape[0] - padding_height
    image_paint[start_name_pos_h:start_name_pos_h + bottom_img.shape[0],
    padding_width:padding_width + bottom_img.shape[1]] = bottom_img

    if orientation == 'landscape':
        image_paint = rotate_image(image_paint, 90)

    im_es_w = image_paint.shape[1] - padding_width * 2
    im_es_h = image_paint.shape[0] - padding_height * 2
    scale = min(im_es_h / height, im_es_w / width)
    new_im_width, new_im_height = int(width * scale), int(height * scale)

    edge_img = cv2.resize(edge_img, (new_im_width, new_im_height))
    image_paint[padding_height: padding_height + edge_img.shape[0],
    padding_width: padding_width + edge_img.shape[1]] = edge_img
    image_paint = cv2.rectangle(image_paint, (padding_width, padding_height),
                                (padding_width + edge_img.shape[1], padding_height + edge_img.shape[0]), EDGE_COLOR,
                                thickness=NAME_THICKNESS)
    return image_paint


def get_draw_result(path, width, height, img_name: str = None, orientation='portrait'):
    img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
    dpi = Image.open(path).info.get('dpi', (72, 72))
    image_paint = draw_result(img, dpi, width=width, height=height, img_name=img_name, debug=False)
    image_a3 = draw_result_a3(img, dpi, img_name, orientation=orientation, debug=False)
    return image_paint, image_a3


if __name__ == '__main__':
    filepath = '../data_test/Asset 2@4x.png'
    img, color_mapping, percentages = index_color(filepath, debug=False)

    print(color_mapping)
    cv2.imwrite("../data_test/result.png", img)
    dpi = Image.open(filepath).info['dpi']
    image_paint = draw_result(img, dpi, width=40, height=50, img_name="N0097", debug=False)
    cv2.imwrite("../data_test/result_30x30.png", image_paint)
    image_paint = draw_result_a3(img, dpi, "N0097", orientation='landscape', debug=True)
    cv2.imwrite("../data_test/result_a3.png", image_paint)
