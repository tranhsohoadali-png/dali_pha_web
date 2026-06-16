"""
Xử lý ảnh: tạo bản đồ màu đánh số (paint-by-numbers) + khớp mã DALI.
Port từ phần mềm ảnh trên máy tính, chạy nền bằng ThreadPoolExecutor.
"""
import os
import time
from datetime import datetime

import cv2
import numpy as np
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
AI_BUDGET_S = 300

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


def _boost_lip_color(path):
    """GIỮ MÀU MÔI sau khi AI vẽ lại: Gemini hay làm môi nhạt lẫn vào màu da,
    khiến bước tách màu không còn cụm môi riêng. Cách cứu: tìm khuôn mặt (Haar
    cascade), trong vùng miệng (nửa dưới mặt) tăng độ rực các pixel HỒNG/ĐỎ hơn
    nền da (kênh a* LAB) -> môi nổi rõ, k-means giữ được cụm môi.
    Mọi lỗi đều bỏ qua êm (ảnh giữ nguyên, job không hỏng)."""
    try:
        img = cv2.imread(path)                                   # BGR
        if img is None:
            return
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        cas = cv2.CascadeClassifier(
            cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')
        faces = cas.detectMultiScale(gray, 1.1, 5, minSize=(40, 40))
        if len(faces) == 0:
            return
        lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB).astype(np.float32)
        changed = False
        for (x, y, w, h) in faces:
            # Vùng miệng: ~55%..100% chiều cao mặt, chừa 2 mép.
            y0, y1 = y + int(h * 0.55), min(y + h, lab.shape[0])
            x0, x1 = x + int(w * 0.18), min(x + int(w * 0.82), lab.shape[1])
            if y1 <= y0 or x1 <= x0:
                continue
            roi = lab[y0:y1, x0:x1]
            a = roi[:, :, 1]
            skin_a = float(np.median(a))
            # Pixel "kiểu môi": hồng/đỏ hơn da rõ rệt, không quá tối/sáng.
            m = (a > skin_a + 5) & (roi[:, :, 0] > 35) & (roi[:, :, 0] < 235)
            if m.sum() < m.size * 0.004:        # không thấy môi -> bỏ qua mặt này
                continue
            mask = (m * 255).astype(np.uint8)
            mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
            mask = cv2.GaussianBlur(mask, (7, 7), 0).astype(np.float32) / 255.0
            # Kéo a* (hồng-đỏ) ra xa nền da 1.6 lần, trần 168 (đỏ son vừa phải);
            # kênh b* ấm nhẹ 1.15 cho môi hồng tự nhiên thay vì tím.
            a_new = np.minimum(skin_a + (a - skin_a) * 1.6, 168.0)
            b = roi[:, :, 2]
            b_new = 128.0 + (b - 128.0) * 1.15
            roi[:, :, 1] = a * (1 - mask) + a_new * mask
            roi[:, :, 2] = b * (1 - mask) + b_new * mask
            lab[y0:y1, x0:x1] = roi
            changed = True
        if changed:
            out = cv2.cvtColor(lab.clip(0, 255).astype(np.uint8), cv2.COLOR_LAB2BGR)
            cv2.imwrite(path, out)
    except Exception:
        pass


def process_image(rec_id, name, enhance=False, style_category=None, color_limit=0,
                  min_area=0, smooth=0, ai_prompt=None, use_refs=False, print_long_cm=0,
                  detail=False, face_priority=False):
    """Chạy nền: (tùy chọn) tăng cường ảnh bằng AI, rồi xử lý + cập nhật ImageResult.

    enhance=True: gọi Google AI làm sạch/nâng cấp ảnh khách trước khi đánh số.
    style_category: nếu có, chọn ảnh mẫu trong kho cùng nhãn làm tham chiếu phong cách.
    color_limit: số màu tối đa (áp cho cả AI vẽ lại lẫn bước tách màu; 0 = không giới hạn).
    min_area: bỏ các mảng màu nhỏ hơn N pixel ở bản đồ đánh số (0 = không lọc).
    face_priority=True: ảnh CHÂN DUNG thật (preset 'photo') — dò & bảo vệ ngũ quan
    (mắt/mũi/miệng) khi tách màu + đánh số. CHỈ bật cho preset chân dung.
    (Cứu màu môi _boost_lip_color vẫn chạy cho MỌI ảnh enhance như cũ — tự no-op
    nếu không có mặt — nên luồng API bán hàng không đổi.)
    Khâu đánh số + khớp mã DALI luôn chạy như cũ trên ảnh (đã hoặc chưa tăng cường).
    """
    obj = ImageResult.objects.get(id=rec_id)
    warn = ''
    zoom_path = None                  # bản crop tạm (xoá ở finally để không rác đĩa)
    try:
        path = os.path.join(settings.MEDIA_ROOT, name)
        orig_dpi = (72, 72)           # giữ DPI ảnh GỐC (bản zoom .png không có DPI)
        try:
            orig_dpi = Image.open(path).info.get('dpi', (72, 72)) or (72, 72)
        except Exception:
            orig_dpi = (72, 72)
        # CHÂN DUNG: TỰ ZOOM vào người NGAY ĐẦU LUỒNG (trước AI) nếu là 1 mặt NHỎ trong
        # ảnh rộng -> dồn độ phân giải + màu cho người -> mặt nét hơn (cả AI lẫn đánh số
        # đều nhận mặt to). Chỉ preset 'photo' (face_priority); ảnh đã cận / nhiều mặt /
        # ảnh GỐC nhỏ (zoom vô ích) -> tự bỏ qua. Mọi lỗi nuốt êm (giữ ảnh gốc). KHÔNG
        # đụng luồng API bán hàng (nó không truyền face_priority -> nhánh này không chạy).
        if face_priority and not detail:
            try:
                from pha.color_index_lib import WORK_MAX_SIDE
                with Image.open(path) as _im:          # đọc CỠ, không giải mã pixel
                    _big = max(_im.size) > (WORK_MAX_SIDE or 1400)
                if _big:                                # ảnh nhỏ: zoom KHÔNG thêm pixel -> bỏ
                    from pha.face_features import subject_crop_box
                    _src = cv2.imread(path)
                    cb = (subject_crop_box(cv2.cvtColor(_src, cv2.COLOR_BGR2RGB))
                          if _src is not None else None)
                    if cb is not None:
                        x0, y0, x1, y1 = cb
                        zpath = os.path.join(
                            settings.MEDIA_ROOT, f'{os.path.splitext(name)[0]}_zoom.png')
                        if cv2.imwrite(zpath, _src[y0:y1, x0:x1]):   # CHỈ đổi khi GHI THẬT
                            path = zpath                # AI + đánh số chạy trên bản zoom
                            zoom_path = zpath
                            warn = 'Đã tự zoom vào người (mặt nhỏ trong ảnh rộng). '
                    del _src                            # giải phóng ảnh gốc to NGAY
            except Exception:
                pass
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
                _boost_lip_color(enhanced_path)   # giữ màu môi (AI hay làm nhạt); tự no-op nếu ảnh không có mặt
                obj.enhanced_name = enhanced_name
                obj.save(update_fields=['enhanced_name'])
                path = enhanced_path  # số hoá trên ảnh đã tăng cường
            except Exception as e:
                warn += 'Bỏ qua tăng cường AI (' + str(e)[:140] + '). Đã xử lý ảnh gốc.'
        design_name = f'{os.path.splitext(name)[0]}_design.png'
        design_path = os.path.join(settings.MEDIA_ROOT, design_name)
        edge_img, color_mapping, percentages = index_color(
            path, debug=False, num_colors=color_limit, min_area=min_area, smooth=smooth,
            design_out=design_path, print_long_cm=print_long_cm, detail=detail,
            face_priority=face_priority)
        name_output = save_img(edge_img, orig_dpi)
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
    finally:
        if zoom_path:                     # dọn bản crop tạm (đã xong số hoá) -> không rác đĩa
            try:
                os.remove(zoom_path)
            except OSError:
                pass


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
