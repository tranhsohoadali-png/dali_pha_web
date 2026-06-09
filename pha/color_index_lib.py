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
# Cỡ số (px) trên ảnh làm việc. Số nhỏ gọn, tỉ lệ theo vùng:
#  - MIN: vùng nhỏ hơn mức này thì BỎ số (tránh số tí hon tràn/lệch).
#  - MEAN: trần cỡ số cho vùng to (không để số phình quá).
#  - NUMBER_FILL: số chiếm ~bao nhiêu phần đường kính vùng.
MIN_TEXT_SIZE = config("MIN_TEXT_SIZE", default=7, cast=int)
MEAN_TEXT_SIZE = config("MEAN_TEXT_SIZE", default=14, cast=int)
MAX_TEXT_SIZE = config("MAX_TEXT_SIZE", default=24, cast=int)
NUMBER_FILL = config("NUMBER_FILL", default=0.5, cast=float)
# Cỡ số tính theo KHỔ IN THẬT (cm): khi biết khổ in, ngưỡng đánh số + cỡ số sẽ
# theo cm -> khổ to (40x50) thì vùng nhỏ vẫn đủ chỗ đánh số, và số không quá to.
MIN_NUMBER_CM = config("MIN_NUMBER_CM", default=0.22, cast=float)   # vùng nhỏ hơn -> bỏ số
NUMBER_CM = config("NUMBER_CM", default=0.34, cast=float)           # cỡ số nhắm tới
MAX_NUMBER_CM = config("MAX_NUMBER_CM", default=0.6, cast=float)    # trần cỡ số
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

# ===== Ưu tiên KHUÔN MẶT cho tranh chân dung =====
# Dồn nhiều màu vào vùng mặt bằng cách OVERSAMPLE pixel mặt khi xây bảng màu
# (k-means LAB), giữ chi tiết mắt/mũi/môi (min_radius nhỏ trong mặt) và làm mềm
# da (bilateral) trước khi gom. Một bảng màu duy nhất -> không seam mặt/nền.
FACE_PRIORITY = config("FACE_PRIORITY", default=True, cast=bool)
FACE_OVERSAMPLE = config("FACE_OVERSAMPLE", default=12, cast=int)    # mức ưu tiên màu cho mặt+tóc
FACE_BILATERAL_D = config("FACE_BILATERAL_D", default=7, cast=int)    # làm mềm da (0 = tắt)
KMEANS_MAX_SIDE = config("KMEANS_MAX_SIDE", default=900, cast=int)    # downscale CHỈ để xây bảng màu
FACE_MIN_RADIUS = config("FACE_MIN_RADIUS", default=3.0, cast=float)  # ngưỡng giữ chi tiết trong mặt
# ----- NGŨ QUAN (mắt/mày/mũi/miệng): giữ nét + dồn thêm màu để mặt sống động -----
FACE_FEATURE_OVERSAMPLE = config("FACE_FEATURE_OVERSAMPLE", default=13, cast=int)  # dồn THÊM màu cho ngũ quan (0 = tắt)
FEATURE_MIN_RADIUS = config("FEATURE_MIN_RADIUS", default=2.2, cast=float)         # giữ chi tiết rất nhỏ trong ngũ quan
FACE_SHARPEN = config("FACE_SHARPEN", default=0.5, cast=float)                     # tăng nét ngũ quan trước khi gom màu (0 = tắt)
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
    """Cỡ số TỈ LỆ theo vùng: nhắm chiều lớn của số ~ NUMBER_FILL * đường kính vùng,
    kẹp trong [min_t, mean_t] (trần max_t) và luôn lọt vùng.
    Vùng quá nhỏ (số không đạt min_t) -> trả None để BỎ số.
    min_t/mean_t/max_t: cỡ số (px) tính theo KHỔ IN thật; None = dùng mặc định.
    """
    min_t = MIN_TEXT_SIZE if min_t is None else min_t
    mean_t = MEAN_TEXT_SIZE if mean_t is None else mean_t
    max_t = MAX_TEXT_SIZE if max_t is None else max_t
    # cỡ mong muốn = theo CHIỀU CAO chữ (mới quyết định đọc được hay không).
    # BỀ RỘNG chỉ dùng để KHỚP vùng. Số 2+ chữ số ("10","11"...) rộng > cao; nếu
    # nhắm chiều lớn thì bề rộng chạm trần khi số còn lùn tịt (cao < min_t) -> bị
    # loại oan -> tranh chốt cứng ở 9 màu. Nhắm chiều cao thì số nào cũng đủ cao.
    target = max_size * NUMBER_FILL
    if target > mean_t:
        target = mean_t
    scale = 0.1
    thickness = 1
    text_size = get_text_size(text, scale, thickness)
    while True:
        nxt = get_text_size(text, scale + 0.1, thickness)   # nxt = (rộng, cao)
        if (nxt[1] > target or nxt[1] > max_t
                or max(nxt) + PADDING_CIRCLE >= max_size):   # max() = bề rộng/cao lớn nhất -> khớp vùng
            break
        scale += 0.1
        text_size = nxt
    # Bỏ số nếu CHIỀU CAO chưa đủ đọc, HOẶC bao chữ không lọt vùng.
    if text_size[1] < min_t or max(text_size) + PADDING_CIRCLE >= max_size:
        return None, None, None
    # số to thì nét dày hơn chút cho rõ
    thickness = 2 if text_size[1] >= 16 else 1
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
    hierarchy = hierarchy[0]
    contour_parents = {}
    processed = set()

    def add_sub_to_parent(parent_idx, idx):
        if parent_idx not in contour_parents:
            contour_parents[parent_idx] = []
            c_t_parent = normalize_contour(contours[parent_idx])
            contour_parents[parent_idx].append(c_t_parent)
            processed.add(parent_idx)
        if idx != parent_idx:
            c_t = normalize_contour(contours[idx])
            contour_parents[parent_idx].append(c_t)
            processed.add(idx)

    def get_contour_same_parent_not_processed(parent_idx):
        output = []
        for sub, (sub_nxt, sub_prev, sub_first_child, sub_parent) in enumerate(hierarchy):
            if parent_idx == sub_parent and sub not in processed:
                c_t = normalize_contour(contours[sub])
                output.append(c_t)
        return output

    for i, (nxt, prev, first_child, parent) in enumerate(hierarchy):
        if debug:
            cv2.drawContours(img, contours, i, GREEN, thickness=2)
            show_img(img, 'contour', timeout=1000)
        if i in processed:
            continue
        if parent == -1:
            add_sub_to_parent(i, i)
            continue
        c_t = normalize_contour(contours[i])
        if first_child == -1:
            center, dist = polylabelfast(c_t)
            if range_img[int(center[1]), int(center[0])] == 255:
                add_sub_to_parent(i, i)
            else:
                add_sub_to_parent(parent, i)

                for sub, (sub_nxt, sub_prev, sub_first_child, sub_parent) in enumerate(hierarchy):
                    if parent == sub_parent and sub not in processed:
                        add_sub_to_parent(parent, sub)
            continue

        # ghép hiện tại với cha
        if parent in contour_parents:
            group_contour = contour_parents[parent].copy()
        else:
            c_t_parent = normalize_contour(contours[parent])
            group_contour = [c_t_parent]

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
            for sub, (sub_nxt, sub_prev, sub_first_child, sub_parent) in enumerate(hierarchy):
                if i == sub_parent and sub not in processed:
                    add_sub_to_parent(i, sub)
            continue

    contours_merged = merge_contours(contour_parents)
    centers = [polylabelfast(contour) for contour in contours_merged]
    centers, dists = list(zip(*centers))
    return centers, dists


import threading as _threading
_tls = _threading.local()


def _get_cascades():
    """Lazy + thread-local (detectMultiScale không reentrant nên không share).
    Trả 3 cascade khuôn mặt; mắt lấy riêng qua _get_eye_cascade()."""
    if not hasattr(_tls, 'front'):
        base = cv2.data.haarcascades
        _tls.front = cv2.CascadeClassifier(base + 'haarcascade_frontalface_default.xml')
        _tls.alt2 = cv2.CascadeClassifier(base + 'haarcascade_frontalface_alt2.xml')
        _tls.profile = cv2.CascadeClassifier(base + 'haarcascade_profileface.xml')
    return _tls.front, _tls.alt2, _tls.profile


def _get_eye_cascade():
    """Haar mắt (lazy, thread-local). None nếu không nạp được."""
    if not hasattr(_tls, 'eye'):
        try:
            _tls.eye = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_eye.xml')
        except Exception:
            _tls.eye = None
    if _tls.eye is None or _tls.eye.empty():
        return None
    return _tls.eye


# YuNet: nhận diện mặt + 5 ĐIỂM MỐC (2 mắt, mũi, 2 khoé miệng) -> định vị ngũ quan
# CHÍNH XÁC (tốt hơn Haar nhiều). Model nhỏ (~230KB) kèm trong repo. cv2 cũ (<4.8)
# có thể không nạp được -> tự fallback sang Haar.
_YUNET_PATH = os.path.join(os.path.dirname(__file__), 'models_data', 'yunet.onnx')


def _get_yunet():
    if not hasattr(_tls, 'yunet'):
        _tls.yunet = None
        try:
            if os.path.exists(_YUNET_PATH):
                _tls.yunet = cv2.FaceDetectorYN.create(
                    _YUNET_PATH, "", (320, 320), 0.6, 0.3, 5000)
        except Exception:
            _tls.yunet = None
    return _tls.yunet


def _face_mask_yunet(bgr):
    """Dùng YuNet landmark -> (face_mask, feature_mask, n) hoặc None nếu không dùng được."""
    fd = _get_yunet()
    if fd is None:
        return None
    try:
        import math
        H, W = bgr.shape[:2]
        fd.setInputSize((W, H))
        _, faces = fd.detect(bgr)
        if faces is None or len(faces) == 0:
            return None
        face_mask = np.zeros((H, W), np.uint8)
        feat = np.zeros((H, W), np.uint8)
        n = 0

        def _ell(mask, cx, cy, ax, ay):
            cv2.ellipse(mask, (int(cx), int(cy)), (max(1, int(ax)), max(1, int(ay))),
                        0, 0, 360, 255, -1)

        for f in faces:
            if float(f[14]) < 0.6:
                continue
            x, y, w, h = float(f[0]), float(f[1]), float(f[2]), float(f[3])
            rex, rey, lex, ley = float(f[4]), float(f[5]), float(f[6]), float(f[7])
            nx, ny = float(f[8]), float(f[9])
            rmx, rmy, lmx, lmy = float(f[10]), float(f[11]), float(f[12]), float(f[13])
            # face ellipse: nới ôm tóc/cằm như Haar
            cx = x + w / 2.0
            x0 = max(0, cx - w * 0.78); x1 = min(W, cx + w * 0.78)
            y0 = max(0, y - h * 0.5); y1 = min(H, y + h * 1.3)
            _ell(face_mask, (x0 + x1) / 2, (y0 + y1) / 2, (x1 - x0) / 2, (y1 - y0) / 2)
            # khoảng cách 2 mắt làm chuẩn tỉ lệ ngũ quan
            ed = max(8.0, math.hypot(lex - rex, ley - rey))
            for ex, ey in ((rex, rey), (lex, ley)):
                _ell(feat, ex, ey, ed * 0.36, ed * 0.30)               # mắt
                _ell(feat, ex, ey - ed * 0.30, ed * 0.36, ed * 0.20)   # lông mày (nới lên)
            _ell(feat, nx, ny, ed * 0.30, ed * 0.36)                   # mũi
            mcx, mcy = (rmx + lmx) / 2.0, (rmy + lmy) / 2.0
            mw = max(ed * 0.5, math.hypot(lmx - rmx, lmy - rmy) * 0.8)
            _ell(feat, mcx, mcy, mw * 0.6, ed * 0.28)                  # miệng
            n += 1
        if not n:
            return None
        feat = cv2.bitwise_and(feat, face_mask)
        return face_mask, feat, n
    except Exception:
        _tls.yunet = None          # cv2 cũ không chạy được YuNet -> tắt, dùng Haar
        return None


def _face_mask(bgr):
    """Trả (face_mask, feature_mask, số mặt) hoặc (None, None, 0).

    - face_mask : ellipse phủ mặt + tóc + cằm (để dồn màu, làm mềm da).
    - feature_mask : NGŨ QUAN (mắt/mày/mũi/miệng) — vùng cần GIỮ NÉT + dồn THÊM màu.
      ƯU TIÊN YuNet landmark (chính xác); nếu không có thì dò bằng Haar mắt + ước
      lượng hình học. feature_mask luôn nằm trong face_mask.
    """
    yn = _face_mask_yunet(bgr)        # ưu tiên landmark chính xác (YuNet)
    if yn is not None:
        return yn
    try:
        front, alt2, profile = _get_cascades()
        gray = cv2.equalizeHist(cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY))
        H, W = gray.shape
        minsz = (max(24, int(0.06 * min(H, W))),) * 2
        faces = front.detectMultiScale(gray, 1.1, 5, minSize=minsz)
        if len(faces) == 0:
            faces = alt2.detectMultiScale(gray, 1.1, 5, minSize=minsz)
        if len(faces) == 0:
            faces = profile.detectMultiScale(gray, 1.1, 5, minSize=minsz)
        if len(faces) == 0:
            pf = profile.detectMultiScale(cv2.flip(gray, 1), 1.1, 5, minSize=minsz)
            faces = [(W - x - w, y, w, h) for (x, y, w, h) in pf]
        mask = np.zeros((H, W), np.uint8)
        feat = np.zeros((H, W), np.uint8)
        eye_cc = _get_eye_cascade()
        n = 0
        for (x, y, w, h) in faces:
            if not (0.6 <= w / float(h) <= 1.7):
                continue
            cx = x + w / 2.0
            # Nới ôm thêm TÓC quanh mặt (hai bên + đỉnh + xuống cổ) nhưng không phình
            # ra nền (giữ tốc độ + đúng "ưu tiên mặt").
            x0 = int(max(0, cx - w * 0.78)); x1 = int(min(W, cx + w * 0.78))
            y0 = int(max(0, y - h * 0.5)); y1 = int(min(H, y + h * 1.3))
            ctr = ((x0 + x1) // 2, (y0 + y1) // 2)
            axes = (max(1, (x1 - x0) // 2), max(1, (y1 - y0) // 2))
            cv2.ellipse(mask, ctr, axes, 0, 0, 360, 255, -1)

            # ----- NGŨ QUAN -----
            found_eye = False
            if eye_cc is not None:
                ry1 = min(H, y + int(0.62 * h))
                roi = gray[y:ry1, x:min(W, x + w)]
                try:
                    eyes = eye_cc.detectMultiScale(roi, 1.1, 6,
                                                   minSize=(max(8, int(0.10 * w)),) * 2)
                except Exception:
                    eyes = []
                for (ex, ey, ew, eh) in eyes:
                    gx0 = x + ex; gy0 = y + ey
                    # mở lên trên để lấy LÔNG MÀY, nới ngang chút cho đủ khoé mắt
                    fx0 = max(0, gx0 - int(0.10 * ew)); fx1 = min(W, gx0 + ew + int(0.10 * ew))
                    fy0 = max(0, gy0 - int(0.65 * eh)); fy1 = min(H, gy0 + eh + int(0.20 * eh))
                    cv2.rectangle(feat, (fx0, fy0), (fx1, fy1), 255, -1)
                    found_eye = True
            if not found_eye:
                # dải MẮT + MÀY ước lượng theo tỉ lệ mặt
                cv2.rectangle(feat, (int(x + 0.08 * w), int(y + 0.20 * h)),
                              (int(x + 0.92 * w), int(y + 0.50 * h)), 255, -1)
            # dải MŨI + MIỆNG (giữa–dưới mặt) — luôn thêm
            cv2.rectangle(feat, (int(x + 0.22 * w), int(y + 0.55 * h)),
                          (int(x + 0.78 * w), int(y + 0.88 * h)), 255, -1)
            n += 1
        if not n:
            return None, None, 0
        feat = cv2.bitwise_and(feat, mask)   # ngũ quan luôn trong mặt
        return mask, feat, n
    except Exception:
        return None, None, 0


def count_faces(path, max_side=768):
    """Đếm số khuôn mặt trong ảnh (dò nhanh trên bản THU NHỎ). Dùng cho auto-gợi-ý
    preset chân dung khi người dùng chọn ảnh. Trả 0 nếu không có mặt / đọc lỗi."""
    try:
        bgr = cv2.imread(path)
        if bgr is None:
            return 0
        h, w = bgr.shape[:2]
        if max(h, w) > max_side:
            s = max_side / float(max(h, w))
            bgr = cv2.resize(bgr, (max(1, int(w * s)), max(1, int(h * s))),
                             interpolation=cv2.INTER_AREA)
        _, _, n = _face_mask(bgr)
        return int(n or 0)
    except Exception:
        return 0


def _quantize_face_priority(arr_rgb, target, face_mask, feature_mask=None, smooth_level=2):
    """Xây bảng màu bằng k-means LAB, OVERSAMPLE pixel vùng mặt -> mặt giành nhiều
    màu hơn. NGŨ QUAN (feature_mask) được ưu tiên MẠNH hơn: KHÔNG làm mềm (giữ nét),
    TĂNG NÉT (unsharp) và oversample THÊM -> mắt/mũi/miệng có màu riêng, sống động.
    smooth_level: LÀM PHẲNG (mean-shift) toàn ảnh TRƯỚC k-means -> hết vón cục, mượt.
    Map TOÀN ẢNH về center gần nhất (đúng K màu) -> không seam."""
    bgr = arr_rgb[:, :, ::-1].copy()
    H, W = bgr.shape[:2]
    # 0) LÀM PHẲNG mean-shift (de-speckle) -> k-means không bị loang/vón cục.
    if smooth_level and int(smooth_level) > 0:
        sp, sr = {1: (9, 18), 2: (16, 32), 3: (26, 50)}.get(int(smooth_level), (16, 32))
        scale = 1.0
        a = bgr
        if max(H, W) > 900:
            scale = 900.0 / max(H, W)
            a = cv2.resize(bgr, (int(W * scale), int(H * scale)), interpolation=cv2.INTER_AREA)
        a = cv2.pyrMeanShiftFiltering(a, sp, sr)
        bgr = cv2.resize(a, (W, H), interpolation=cv2.INTER_NEAREST) if scale != 1.0 else a
        bgr = np.ascontiguousarray(bgr)
    # 1) Làm mềm DA: bilateral trên crop mặt, NHƯNG CHỪA ngũ quan (giữ nét mắt/miệng)
    if FACE_BILATERAL_D > 0:
        ys, xs = np.where(face_mask > 0)
        if len(xs):
            x0, x1, y0, y1 = xs.min(), xs.max() + 1, ys.min(), ys.max() + 1
            crop = bgr[y0:y1, x0:x1].copy()
            sm = cv2.bilateralFilter(crop, FACE_BILATERAL_D, 60, 60)
            m = face_mask[y0:y1, x0:x1] > 0
            if feature_mask is not None:
                m &= (feature_mask[y0:y1, x0:x1] == 0)   # không bôi mềm ngũ quan
            crop[m] = sm[m]
            bgr[y0:y1, x0:x1] = crop
    # 2) TĂNG NÉT ngũ quan (unsharp mask) -> iris/môi tách rõ khỏi da khi gom màu
    if FACE_SHARPEN > 0 and feature_mask is not None and np.any(feature_mask):
        blur = cv2.GaussianBlur(bgr, (0, 0), 1.0)
        sharp = cv2.addWeighted(bgr, 1.0 + FACE_SHARPEN, blur, -FACE_SHARPEN, 0)
        fmm = feature_mask > 0
        bgr[fmm] = sharp[fmm]
    # 3) Mẫu huấn luyện bảng màu (downscale cho nhanh), oversample mặt + ngũ quan
    scale = 1.0
    if max(H, W) > KMEANS_MAX_SIDE:
        scale = KMEANS_MAX_SIDE / float(max(H, W))
    if scale < 1.0:
        small = cv2.resize(bgr, (int(W * scale), int(H * scale)), interpolation=cv2.INTER_AREA)
        smask = cv2.resize(face_mask, (small.shape[1], small.shape[0]), interpolation=cv2.INTER_NEAREST)
        sfeat = (cv2.resize(feature_mask, (small.shape[1], small.shape[0]), interpolation=cv2.INTER_NEAREST)
                 if feature_mask is not None else None)
    else:
        small, smask, sfeat = bgr, face_mask, feature_mask
    lab_small = cv2.cvtColor(small, cv2.COLOR_BGR2LAB).reshape(-1, 3).astype(np.float32)
    fm = smask.reshape(-1) > 0
    bg = lab_small[~fm][::2]                       # giảm bớt mẫu nền
    fc = lab_small[fm]
    if len(fc):
        fc = np.repeat(fc, max(1, FACE_OVERSAMPLE), axis=0)
    parts = [bg, fc] if len(fc) else [lab_small]
    if sfeat is not None and FACE_FEATURE_OVERSAMPLE > 0:
        vc = lab_small[sfeat.reshape(-1) > 0]      # ngũ quan: dồn THÊM màu
        if len(vc):
            parts.append(np.repeat(vc, FACE_FEATURE_OVERSAMPLE, axis=0))
    samples = np.ascontiguousarray(np.vstack(parts), dtype=np.float32)
    # 4) k-means -> K center (LAB)
    cv2.setRNGSeed(0)
    K = max(2, int(target))
    crit = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 12, 1.0)
    _, _, centers = cv2.kmeans(samples, K, None, crit, 1, cv2.KMEANS_PP_CENTERS)
    centers = centers.astype(np.float32)
    # 5) Map toàn ảnh (full-res) về center gần nhất
    full = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB).reshape(-1, 3).astype(np.float32)
    best = np.zeros(len(full), np.int32)
    bestd = ((full - centers[0]) ** 2).sum(1)
    for ci in range(1, K):
        d = ((full - centers[ci]) ** 2).sum(1)
        m = d < bestd
        best[m] = ci
        bestd[m] = d[m]
    centers_bgr = cv2.cvtColor(centers.reshape(1, K, 3).astype(np.uint8),
                               cv2.COLOR_LAB2BGR).reshape(K, 3)
    out_bgr = centers_bgr[best].reshape(H, W, 3)
    return out_bgr[:, :, ::-1]                     # -> RGB


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


def _quantize_file(path, n, smooth=0, min_area=0, face_priority=True, print_long_cm=0):
    """Gom ảnh về tối đa n màu (median-cut) rồi lưu file tạm. Trả (đường_dẫn_tạm).
    smooth (0..3): làm phẳng vùng bằng mean-shift trước khi gom màu — biến ảnh
    màu nước/ảnh chụp (chuyển sắc mượt, nhiều chi tiết) thành các MẢNG ĐẶC sạch,
    giống tranh tô màu. Càng cao càng gộp mạnh (ít chi tiết hơn)."""
    import os
    import tempfile
    im = Image.open(path).convert('RGB')
    target = max(2, n)

    # ƯU TIÊN MẶT: phát hiện mặt + ngũ quan để (1) GIỮ NÉT ngũ quan khỏi mean-shift,
    # (2) BẢO VỆ màu mắt/mũi/miệng khi gom. Vẫn dùng LUỒNG MƯỢT (mean-shift + median-cut
    # + gộp tông) cho cả ảnh -> mượt như mong muốn, KHÔNG dùng k-means (gây vón cục).
    face_mask = None
    feature_mask = None
    if FACE_PRIORITY and face_priority:
        try:
            face_mask, feature_mask, _nf = _face_mask(np.array(im)[:, :, ::-1].copy())
        except Exception:
            face_mask = feature_mask = None

    src_rgb = np.array(im)
    sm_level = int(smooth) if (smooth and int(smooth) > 0) else 0

    if face_mask is not None:
        # ƯU TIÊN MẶT (k-means oversample) — nhưng LÀM PHẲNG (mean-shift) bên trong
        # trước khi k-means để KHÔNG vón cục; mặt giành nhiều màu (môi/mắt/da chi tiết).
        face_sm = sm_level if sm_level > 0 else 2          # ảnh chân dung cần phẳng -> tối thiểu Vừa
        arr = _quantize_face_priority(src_rgb, target, face_mask, feature_mask, face_sm)
        ksize_sm = 7                                       # làm mượt biên mạnh hơn cho k-means
    else:
        if sm_level > 0:
            sp, sr = {1: (9, 18), 2: (16, 32), 3: (26, 50)}.get(sm_level, (16, 32))
            a = src_rgb[:, :, ::-1].copy()                 # RGB -> BGR
            h, w = a.shape[:2]
            scale = 1.0
            if max(h, w) > 900:
                scale = 900.0 / max(h, w)
                a = cv2.resize(a, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
            a = cv2.pyrMeanShiftFiltering(a, sp, sr)
            if scale != 1.0:
                a = cv2.resize(a, (w, h), interpolation=cv2.INTER_NEAREST)
            im = Image.fromarray(np.ascontiguousarray(a[:, :, ::-1]))   # BGR -> RGB
        # Gom DƯ nhiều màu rồi HỢP NHẤT các màu cùng tông (LAB), bảo vệ màu rực.
        k_work = min(96, max(target * 5, 48))
        q = im.quantize(colors=k_work, method=Image.MEDIANCUT, dither=Image.Dither.NONE).convert('RGB')
        arr = np.array(q)
        arr = _reduce_palette_perceptual(arr, target)
        ksize_sm = 5

    # GỘP các vùng không đánh được số vào hàng xóm -> hết 'dăm', mọi ô đều numberable.
    # Ngưỡng gộp theo KHỔ IN: khổ to -> giữ được vùng nhỏ hơn (đánh số được khi in).
    if print_long_cm and print_long_cm > 0:
        H0, W0 = arr.shape[:2]
        px_per_cm = max(H0, W0) / float(print_long_cm)
        min_t_px = max(4, int(round(MIN_NUMBER_CM * px_per_cm)))
    else:
        min_t_px = MIN_TEXT_SIZE
    min_radius = (min_t_px + 2 * PADDING_CIRCLE) / 2.0 + 1.0
    arr = _merge_small_regions(arr, min_area=min_area, min_radius=min_radius, max_pass=4,
                               face_mask=face_mask, face_min_radius=FACE_MIN_RADIUS,
                               feature_mask=feature_mask, feature_min_radius=FEATURE_MIN_RADIUS)
    # LÀM MƯỢT biên vùng (median trên nhãn màu) -> bỏ răng cưa/mảnh thừa, nét trơn.
    protect = feature_mask if FEATURE_PROTECT_SMOOTH else None
    arr = _smooth_boundaries(arr, ksize=ksize_sm, protect_mask=protect)
    arr = _merge_small_regions(arr, min_area=0, min_radius=min_radius, max_pass=2,
                               face_mask=face_mask, face_min_radius=FACE_MIN_RADIUS,
                               feature_mask=feature_mask, feature_min_radius=FEATURE_MIN_RADIUS)
    fd, out = tempfile.mkstemp(suffix='.png', prefix='quant_')
    os.close(fd)
    Image.fromarray(arr).save(out)
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


def index_color(path, debug=False, num_colors=0, min_area=0, smooth=0, design_out=None,
                face_priority=True, print_long_cm=0):
    """num_colors > 0: gom ảnh về tối đa N màu (để trống = DEFAULT_NUM_COLORS).
    min_area > 0: bỏ các mảng màu nhỏ hơn N pixel (đỡ lấm tấm).
    smooth (0..3): làm phẳng vùng (mean-shift) trước khi gom — dọn ảnh màu nước/chụp.
    design_out: nếu có, lưu ảnh THIẾT KẾ (bản màu phẳng đã gom) ra đường dẫn này.
    print_long_cm > 0: khổ in cạnh dài (cm) -> cỡ số + ngưỡng đánh số tính theo cm
      thật (khổ to thì vùng nhỏ vẫn được đánh số, số không quá to)."""
    import os
    import shutil
    effective_n = num_colors if (num_colors and num_colors > 0) else DEFAULT_NUM_COLORS
    work_path = _quantize_file(path, effective_n, smooth=smooth, min_area=min_area,
                               face_priority=face_priority, print_long_cm=print_long_cm)
    if design_out:
        try:
            shutil.copyfile(work_path, design_out)   # bản màu phẳng để xem trước
        except OSError:
            pass
    colors, pixel_count = extract_colors(work_path)
    colors = list(colors)

    img = load_image(work_path, debug=debug)

    # Cỡ số theo KHỔ IN thật: vùng nhỏ hơn min_t -> bỏ số; số nhắm mean_t (trần max_t).
    H_i, W_i = img.shape[:2]
    if print_long_cm and print_long_cm > 0:
        px_per_cm = max(H_i, W_i) / float(print_long_cm)
        min_t = max(4, int(round(MIN_NUMBER_CM * px_per_cm)))
        mean_t = max(min_t + 2, int(round(NUMBER_CM * px_per_cm)))
        max_t = max(mean_t + 1, int(round(MAX_NUMBER_CM * px_per_cm)))
    else:
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
