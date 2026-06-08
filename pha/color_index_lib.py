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
MIN_TEXT_SIZE = config("MIN_TEXT_SIZE", default=4, cast=int)
MEAN_TEXT_SIZE = config("MEAN_TEXT_SIZE", default=22, cast=int)
MAX_TEXT_SIZE = config("MAX_TEXT_SIZE", default=40, cast=int)
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


def get_number_size(text: str, max_size: float) -> Tuple[Tuple, float, float]:
    """
    :param text:
    :param max_size:
    :return: text size, scale and thickness
    """
    text_size = (0, 0)
    scale = 0.05
    thickness = 1
    while min(text_size) < MEAN_TEXT_SIZE and max(text_size) < MAX_TEXT_SIZE and max(
            text_size) + PADDING_CIRCLE < max_size:
        text_size = get_text_size(text, scale, thickness)
        scale += 0.05
    if min(text_size) < MIN_TEXT_SIZE:
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


def get_draw_number(img: np.ndarray, center: Tuple[int, int], max_size: float, number: str, debug=False) -> Tuple:
    text_size, scale, thickness = get_number_size(number, max_size)
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


def _quantize_file(path, n, smooth=0):
    """Gom ảnh về tối đa n màu (median-cut) rồi lưu file tạm. Trả (đường_dẫn_tạm).
    smooth (0..3): làm phẳng vùng bằng mean-shift trước khi gom màu — biến ảnh
    màu nước/ảnh chụp (chuyển sắc mượt, nhiều chi tiết) thành các MẢNG ĐẶC sạch,
    giống tranh tô màu. Càng cao càng gộp mạnh (ít chi tiết hơn)."""
    import os
    import tempfile
    im = Image.open(path).convert('RGB')
    if smooth and int(smooth) > 0:
        # sp = bán kính không gian, sr = bán kính màu. sr lớn -> gộp nhiều màu hơn.
        sp, sr = {1: (9, 18), 2: (16, 32), 3: (26, 50)}.get(int(smooth), (16, 32))
        arr = np.array(im)[:, :, ::-1].copy()              # RGB -> BGR cho cv2
        h, w = arr.shape[:2]
        scale = 1.0
        if max(h, w) > 1200:                               # hạ cỡ để mean-shift nhanh
            scale = 1200.0 / max(h, w)
            arr = cv2.resize(arr, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
        arr = cv2.pyrMeanShiftFiltering(arr, sp, sr)
        if scale != 1.0:
            arr = cv2.resize(arr, (w, h), interpolation=cv2.INTER_NEAREST)
        im = Image.fromarray(arr[:, :, ::-1])              # BGR -> RGB
    target = max(2, n)
    # Gom DƯ nhiều màu trước (chia-đôi hay đẻ ra nhiều sắc gần giống của tông nền),
    # rồi HỢP NHẤT các màu cùng tông theo cảm nhận mắt (LAB) xuống đúng `target`.
    k_work = min(64, max(target * 4, 40))
    q = im.quantize(colors=k_work, method=Image.MEDIANCUT, dither=Image.Dither.NONE).convert('RGB')
    arr = np.array(q)
    arr = _reduce_palette_perceptual(arr, target)
    fd, out = tempfile.mkstemp(suffix='.png', prefix='quant_')
    os.close(fd)
    Image.fromarray(arr).save(out)
    return out


def _reduce_palette_perceptual(img_rgb, target_n):
    """Hợp nhất bảng màu xuống target_n bằng cách GỘP DẦN 2 màu GIỐNG NHAU NHẤT
    (khoảng cách trong không gian LAB), không phụ thuộc diện tích. Nhờ vậy nhiều
    sắc cùng tông (vd hàng loạt xanh lá nền) dồn lại, nhường suất cho các tông
    khác biệt (hồng, cam, xanh dương) -> tranh đặc sắc + đỡ 'dăm'."""
    flat = img_rgb.reshape(-1, 3)
    colors, counts = np.unique(flat, axis=0, return_counts=True)
    K = len(colors)
    if K <= target_n:
        return img_rgb
    lab = cv2.cvtColor(colors.reshape(-1, 1, 3).astype('uint8'),
                       cv2.COLOR_RGB2LAB).reshape(-1, 3).astype(float)
    clusters = {i: {'lab': lab[i].copy(), 'cnt': float(counts[i]), 'members': [i]}
                for i in range(K)}
    while len(clusters) > target_n:
        ids = list(clusters.keys())
        best, pair = None, None
        for a in range(len(ids)):
            la = clusters[ids[a]]['lab']
            for b in range(a + 1, len(ids)):
                d = float(((la - clusters[ids[b]]['lab']) ** 2).sum())
                if best is None or d < best:
                    best, pair = d, (ids[a], ids[b])
        i, j = pair
        ci, cj = clusters[i], clusters[j]
        tot = ci['cnt'] + cj['cnt']
        ci['lab'] = (ci['lab'] * ci['cnt'] + cj['lab'] * cj['cnt']) / tot
        ci['cnt'] = tot
        ci['members'] += cj['members']
        del clusters[j]
    # Đại diện mỗi nhóm = màu có nhiều pixel nhất (giữ màu thật, không bị xỉn).
    rep_of = np.zeros((K, 3), dtype=np.uint8)
    for cl in clusters.values():
        best_m = max(cl['members'], key=lambda m: counts[m])
        rep = colors[best_m]
        for m in cl['members']:
            rep_of[m] = rep
    out = np.zeros_like(flat)
    for k, c in enumerate(colors):
        out[np.all(flat == c, axis=1)] = rep_of[k]
    return out.reshape(img_rgb.shape)


def index_color(path, debug=False, num_colors=0, min_area=0, smooth=0, design_out=None):
    """num_colors > 0: gom ảnh về tối đa N màu (để trống = DEFAULT_NUM_COLORS).
    min_area > 0: bỏ các mảng màu nhỏ hơn N pixel (đỡ lấm tấm).
    smooth (0..3): làm phẳng vùng (mean-shift) trước khi gom — dọn ảnh màu nước/chụp.
    design_out: nếu có, lưu ảnh THIẾT KẾ (bản màu phẳng đã gom) ra đường dẫn này."""
    import os
    import shutil
    effective_n = num_colors if (num_colors and num_colors > 0) else DEFAULT_NUM_COLORS
    work_path = _quantize_file(path, effective_n, smooth=smooth)
    if design_out:
        try:
            shutil.copyfile(work_path, design_out)   # bản màu phẳng để xem trước
        except OSError:
            pass
    colors, pixel_count = extract_colors(work_path)
    colors = list(colors)

    img = load_image(work_path, debug=debug)

    # edge_img = get_edges(img)
    # edge_img = cv2.bitwise_not(edge_img)

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
        range_img = _remove_small_components(range_img, min_area)

        contours, hierarchy = cv2.findContours(range_img, cv2.RETR_TREE, cv2.CHAIN_APPROX_NONE)
        # Sau khi lọc mảng nhỏ, màu này có thể không còn vùng nào -> bỏ qua.
        if hierarchy is None or not contours:
            color_idx += 1
            continue
        cv2.drawContours(img_white, contours, -1, (0, 0, 0), 1)

        centers, dists = get_center_poly_from_contours(contours, hierarchy, range_img, np.array(img), debug=debug)
        count_number = 0
        for c, d in zip(centers, dists):
            d = d * 2
            draw = get_draw_number(img_white, (int(c[0]), int(c[1])), d, f'{len(color_mapping) + 1}', debug=debug)
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
