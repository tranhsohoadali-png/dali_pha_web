"""
Xử lý ảnh: tạo bản đồ màu đánh số (paint-by-numbers) + khớp mã DALI.
Port từ phần mềm ảnh trên máy tính, chạy nền bằng ThreadPoolExecutor.
"""
import os
import time
from datetime import datetime

import cv2
from PIL import Image
from django.conf import settings

from pha.color_index_lib import index_color, get_draw_result
from pha.dali_match import nearest_dali
from pha.models import ImageResult


def split_list(pagination, img_color):
    out, tmp = [], []
    for i in img_color:
        tmp.append(i)
        if len(tmp) == pagination:
            out.append(tmp); tmp = []
    out.append(tmp)
    return out


def convert_to_hex(colors):
    res = []
    for i in colors:
        hx = '#%02x%02x%02x' % i[1]
        res.append([i[0], hx.upper()])
    return res


def save_img(edge_img, dpi=(72, 72)):
    now = datetime.fromtimestamp(time.time()).strftime("%Y-%m-%d_%H-%M-%S")
    name_output = now + "_result.png"
    rgb = cv2.cvtColor(edge_img, cv2.COLOR_BGR2RGB)
    os.makedirs(settings.MEDIA_ROOT, exist_ok=True)
    Image.fromarray(rgb).save(os.path.join(settings.MEDIA_ROOT, name_output), dpi=dpi)
    return name_output


def create_image_color(color_mapping, hex_list, percentages=None):
    result = []
    for i in range(len(color_mapping)):
        rgb = color_mapping[i][1]
        dali = nearest_dali(rgb)
        pct = percentages[i] if percentages and i < len(percentages) else 0
        result.append([color_mapping[i][0], hex_list[i][1], dali, pct])
    return result


# Trần cứng (giây) cho TOÀN BỘ khâu tăng cường AI trong 1 job (mọi lần thử cộng
# lại). Quá trần -> bỏ AI, xử lý ảnh gốc ngay. Phải NHỎ hơn nhiều so với thời
# gian khách sẵn sàng đợi; Google quá tải là chuyện thường gặp.
AI_BUDGET_S = 210

# Job kẹt PROCESSING quá lâu = tiến trình nền đã chết giữa chừng (hết RAM bị kill,
# service restart giữa lúc chạy...) hoặc xếp hàng sau quá nhiều job -> coi như
# hỏng. Khi poll thấy quá ngưỡng thì đánh dấu LỖI RÕ RÀNG để giao diện không chờ
# trống vô hạn. (Nếu job thật ra vẫn chạy xong sau đó, kết quả sẽ tự đè lại.)
STUCK_MINUTES = 15


def mark_if_stuck(obj):
    """Trả True nếu vừa chuyển job kẹt sang trạng thái lỗi (kèm hướng dẫn)."""
    from datetime import timedelta
    from django.utils import timezone
    if obj.status != ImageResult.STATUS_PROCESSING:
        return False
    if timezone.now() - obj.created_time < timedelta(minutes=STUCK_MINUTES):
        return False
    obj.status = ImageResult.STATUS_ERROR
    obj.error_message = (f'Quá {STUCK_MINUTES} phút chưa xong — tiến trình xử lý có thể '
                         'đã bị ngắt (server hết RAM / khởi động lại giữa chừng). '
                         'Hãy thử lại; nếu lặp lại nhiều lần, kiểm tra RAM/CPU server '
                         '(journalctl -u phaweb; dmesg | grep -i oom).')
    obj.save(update_fields=['status', 'error_message'])
    return True


def process_image(rec_id, name, enhance=False, style_category=None, color_limit=0,
                  min_area=0, smooth=0, ai_prompt=None, use_refs=False, print_long_cm=0):
    """Chạy nền: (tùy chọn) tăng cường ảnh bằng AI, rồi xử lý + cập nhật ImageResult.

    enhance=True: gọi Google AI làm sạch/nâng cấp ảnh khách trước khi đánh số.
    style_category: nếu có, chọn ảnh mẫu trong kho cùng nhãn làm tham chiếu phong cách.
    color_limit: số màu tối đa (áp cho cả AI vẽ lại lẫn bước tách màu; 0 = không giới hạn).
    min_area: bỏ các mảng màu nhỏ hơn N pixel ở bản đồ đánh số (0 = không lọc).
    Khâu đánh số + khớp mã DALI luôn chạy như cũ trên ảnh (đã hoặc chưa tăng cường).
    """
    obj = ImageResult.objects.get(id=rec_id)
    warn = ''
    try:
        path = os.path.join(settings.MEDIA_ROOT, name)
        if enhance:
            # AI tách riêng: nếu lỗi/timeout -> BỎ QUA, xử lý ảnh gốc (không treo).
            # TRẦN CỨNG AI_BUDGET_S giây cho TOÀN BỘ khâu AI (kể cả 2 lần thử +
            # trường hợp SDK treo không timeout): chạy trong luồng phụ daemon,
            # quá trần thì bỏ rơi luồng đó và xử lý ảnh gốc ngay — Google quá tải
            # KHÔNG được ghim 1 trong 2 slot xử lý làm tắc cả hàng đợi.
            try:
                import threading
                from pha.ai_enhance import enhance_image
                from pha import style_library
                refs = (style_library.pick_references(path, category=style_category, n=3)
                        if use_refs else [])
                enhanced_name = f'{os.path.splitext(name)[0]}_ai.png'
                enhanced_path = os.path.join(settings.MEDIA_ROOT, enhanced_name)
                box = {}

                def _run_ai():
                    try:
                        enhance_image(path, enhanced_path, prompt=ai_prompt,
                                      reference_paths=refs, color_limit=color_limit,
                                      use_refs=use_refs)
                        box['ok'] = True
                    except Exception as e:          # noqa: BLE001
                        box['err'] = e

                th = threading.Thread(target=_run_ai, daemon=True)
                th.start()
                th.join(AI_BUDGET_S)
                if th.is_alive():
                    raise TimeoutError(f'quá {AI_BUDGET_S}s — Google chậm/quá tải')
                if 'err' in box:
                    raise box['err']
                obj.enhanced_name = enhanced_name
                obj.save(update_fields=['enhanced_name'])
                path = enhanced_path  # số hoá trên ảnh đã tăng cường
            except Exception as e:
                warn = 'Bỏ qua tăng cường AI (' + str(e)[:140] + '). Đã xử lý ảnh gốc.'
        design_name = f'{os.path.splitext(name)[0]}_design.png'
        design_path = os.path.join(settings.MEDIA_ROOT, design_name)
        edge_img, color_mapping, percentages = index_color(
            path, debug=False, num_colors=color_limit, min_area=min_area, smooth=smooth,
            design_out=design_path, print_long_cm=print_long_cm)
        dpi = Image.open(path).info.get('dpi', (72, 72))
        name_output = save_img(edge_img, dpi)
        colors = create_image_color(color_mapping, convert_to_hex(color_mapping), percentages)
        obj.name_output = name_output
        obj.design_name = design_name if os.path.exists(design_path) else ''
        obj.colors = colors
        obj.status = ImageResult.STATUS_DONE
        obj.error_message = warn          # cảnh báo nhẹ nếu AI bị bỏ qua (vẫn có kết quả)
        obj.save()
    except Exception as e:
        obj.status = ImageResult.STATUS_ERROR
        obj.error_message = str(e)
        obj.save()


def get_paint_image(file_path, image_name, option, orientation='portrait'):
    """Tạo bản in theo khổ + bản A3 từ ảnh kết quả. Trả (file_paint, file_a3)."""
    full = os.path.join(settings.MEDIA_ROOT, file_path.replace('/media/', ''))
    width, height = option.split('x')
    image_paint, image_a3 = get_draw_result(full, int(width), int(height), image_name, orientation=orientation)
    fn_paint = f'/media/{image_name}_painting.png'
    fn_a3 = f'/media/{image_name}_a3.png'
    cv2.imwrite(os.path.join(settings.MEDIA_ROOT, f'{image_name}_painting.png'), image_paint)
    cv2.imwrite(os.path.join(settings.MEDIA_ROOT, f'{image_name}_a3.png'), image_a3)
    return fn_paint, fn_a3
