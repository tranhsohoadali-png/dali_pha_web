"""THỬ NGHIỆM "Mức độ AI" (nhẹ / vừa / mạnh) — module RIÊNG, chỉ dùng nội bộ
mau.tranhdali.vn để TINH CHỈNH prompt trước khi gắn vào trang chính.

KHÔNG đụng tới: process_image, xu_ly_anh, API bán hàng /api/xu-ly-anh. Luồng web
sang vẫn y nguyên. Ở đây chỉ gọi lại enhance_image với 3 PROMPT khác nhau rồi cho
owner xem 4 ảnh cạnh nhau (gốc + 3 mức) để chọn mức ít "ảo" nhất.

Trục "mức độ AI" = mức can thiệp của AI vào KHUÔN MẶT:
  - nhẹ : khoá danh tính tối đa — giữ ĐÚNG người, chỉ posterize nhẹ (chống "ảo").
  - vừa : giữ người + dọn nhẹ (mụn/nhiễu/nền), vẫn nhận ra là họ.
  - mạnh: nghệ thuật hoá mạnh (cel-shaded sạch, nét đậm) — đẹp nhưng xa người thật.
Endpoint: /anh-ai-test (trang), /anh-ai-test-run (POST chạy), /anh-ai-test-status.
"""
import os
import threading
from datetime import datetime

from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt

from pha.models import ImageResult
from pha.views import staff_required, _img_executor, _prune_image_results

# ---- 3 PROMPT theo mức can thiệp (tinh chỉnh được qua env nếu muốn) ----

# NHẸ: trung thực tuyệt đối — chống "ảo". Giữ đúng người, đúng màu, chỉ làm phẳng.
PROMPT_NHE = (
    "ROLE. Convert this REAL PHOTOGRAPH into a paint-by-numbers design that looks "
    "like the SAME REAL PERSON — a faithful, lightly posterised version of THIS "
    "exact photo, like Adobe Illustrator 'Image Trace (High Fidelity Photo)'.\n"
    "IDENTITY LOCK (never break): keep the EXACT same face — same face shape, same "
    "jawline, same eye shape and size, same nose, same mouth, same eyebrows, same "
    "expression, same skin tone, same hairstyle, same pose and clothing. Do NOT "
    "beautify, do NOT slim or reshape the face, do NOT enlarge the eyes, do NOT "
    "smooth skin into plastic, do NOT make it anime/cartoon/3D, do NOT generate a "
    "new prettier face. The result MUST be instantly recognisable as the same "
    "person; a stranger comparing the two images must say 'same person'.\n"
    "RENDER: split smooth gradients into many adjacent FLAT tone bands that follow "
    "the real light/shadow (skin = highlight/mid/soft-shadow; hair = a few flat "
    "strands). Use a GENEROUS number of flat tones (about 36-48) so skin and hair "
    "stay soft and three-dimensional, never a harsh 2-3 colour poster. Keep the REAL "
    "colours of the photo; do not invent a new palette or mood.\n"
    "FACE FEATURES: render eyes (dark iris + small catch-light), eyebrows, nose and "
    "lips crisply exactly where they really are; lips in their own clearly rosy/red "
    "tone (never skin-coloured), eyes/lashes not washing into skin. Keep edges clean, "
    "never blurred or melted. NO black cartoon outlines — shapes are defined by the "
    "boundaries between flat colour areas, like the photo.\n"
    "Remove only photographic noise/grain. Keep the background as-is, just flattened "
    "into calm flat shapes with its real muted colours. Same framing and aspect "
    "ratio. Output ONLY the rendered image."
)

# VỪA: giữ người NHƯNG cho phép dọn nhẹ thẩm mỹ (mụn/nhiễu/nền). Cân bằng.
PROMPT_VUA = (
    "ROLE. Convert this REAL PHOTOGRAPH into a clean, flattering paint-by-numbers "
    "portrait that still clearly looks like the SAME REAL PERSON (realistic style, "
    "NOT anime/cartoon/3D).\n"
    "KEEP IDENTITY: same face shape, features, proportions, expression, hair, pose "
    "and clothing — must be recognisable as the same person. You MAY gently tidy: "
    "remove blemishes/spots, even out skin, remove photographic noise and stray hair "
    "wisps, brighten slightly — but do NOT reshape the face, do NOT enlarge eyes, do "
    "NOT slim the jaw, do NOT replace the face with an idealised one.\n"
    "RENDER as smooth FLAT tone bands following the real light/shadow, around 30-44 "
    "flat tones so skin/hair look soft and dimensional. Keep skin warm and healthy "
    "(never grey/muddy). LIPS in their own saturated rosy/red tone, clearly different "
    "from skin; eyes with a clear dark iris and catch-light; defined eyebrows and "
    "nose. Sharp clean feature edges, no melting. NO black cartoon outlines.\n"
    "Simplify the background into a few calm flat shapes keeping its real muted "
    "colours so the person stands out. Same framing and aspect ratio. Output ONLY "
    "the rendered image."
)

# MẠNH: nghệ thuật hoá mạnh — cel-shaded sạch, nét đậm, ít mảng. Đẹp, "có gu",
# nhưng xa ảnh thật hơn (dành cho khách thích phong cách tranh điệu).
PROMPT_MANH = (
    "ROLE & GOAL. Redraw this photo as a NEAT, stylised PAINT-BY-NUMBERS coloring "
    "template — clean cel-shaded illustration with few large flat regions and bold "
    "smooth closed outlines, like a tidy poster/sticker portrait.\n"
    "Keep the SAME person, pose, hairstyle, clothing and composition recognisable, "
    "and keep the lips rosy and eyes lively — but you MAY render it in a polished, "
    "idealised illustration style (smooth clean skin, elegant simplified features). "
    "Do NOT change it into a different person, animal or scene.\n"
    "STYLE: (1) bold, smooth, evenly-thick dark outlines, fully closed (vector-like). "
    "(2) FLAT solid fills only — no gradients, grain or soft shadows; one even colour "
    "per region. (3) merge small details into big paintable regions; no thin slivers "
    "or tiny specks. (4) simplify the background aggressively into a few large flat "
    "shapes. (5) limited palette of clean distinct colours. Same aspect ratio. "
    "Output ONLY the redrawn image."
)

# NỀN TỐI GIẢN: giữ NGƯỜI trung thực (như mức Vừa) + ÉP NỀN đơn giản mạnh ->
# tách chủ thể, nền chỉ vài mảng lớn dễ tô (thử cho ảnh nền lộn xộn: cầu, phố, cây).
PROMPT_NEN = (
    "ROLE. Convert this REAL PHOTOGRAPH into a clean paint-by-numbers portrait that "
    "clearly looks like the SAME REAL PERSON (realistic, NOT anime/cartoon/3D).\n"
    "PERSON (keep faithful + detailed): same face shape, features, proportions, "
    "expression, hair, pose and clothing — instantly recognisable. Render as smooth "
    "flat tone bands; lips in their own rosy/red tone; eyes with clear dark iris + "
    "catch-light; defined eyebrows and nose; sharp clean feature edges, skin warm "
    "and healthy. You MAY gently remove blemishes/noise but do NOT reshape the face.\n"
    "BACKGROUND — SIMPLIFY AGGRESSIVELY (very important): the person is the focus and "
    "must stay detailed; the BACKGROUND must be reduced to only a FEW (about 4-7) "
    "large, calm, flat colour shapes. Aggressively DELETE background clutter: cables, "
    "wires, railings, poles, road markings, window/beam edges, signage, individual "
    "leaves and twigs, and any small or thin objects. Merge the background into broad "
    "simple masses (e.g. one flat sky, one flat road, one simplified building block, "
    "one soft tree mass). Lower the background's detail, contrast and saturation so it "
    "visibly recedes and the person pops; keep background colours roughly recognisable "
    "but much simpler. Do NOT invent new background detail. The background must look "
    "like a minimal, easy-to-paint backdrop — the OPPOSITE of a busy photo.\n"
    "NO black cartoon outlines. Same framing and aspect ratio. Output ONLY the image."
)

LEVELS = [
    ('nhe', 'Nhẹ — giữ nét thật', PROMPT_NHE),
    ('vua', 'Vừa — cân bằng', PROMPT_VUA),
    ('manh', 'Mạnh — nghệ thuật', PROMPT_MANH),
    ('nen', 'Nền tối giản', PROMPT_NEN),
]

# Trần thời gian (giây) cho MỖI mức (3 mức chạy SONG SONG nên tổng ~ 1 lần gọi).
LEVEL_BUDGET_S = 200


def process_ai_levels(rec_id, name):
    """Chạy nền: gọi Google AI cho TỪNG mức trong LEVELS, SONG SONG trên cùng 1 ảnh,
    lưu mỗi mức 1 file. Ghi đường dẫn vào params để trang poll. Mỗi mức có trần
    riêng (Google quá tải 1 mức không kéo theo các mức kia)."""
    from pha.ai_enhance import enhance_image
    obj = ImageResult.objects.get(id=rec_id)
    try:
        src = os.path.join(settings.MEDIA_ROOT, name)
        base = os.path.splitext(name)[0]
        results, errors = {}, {}

        def _run(key, prompt):
            out_rel = f'{base}_lv_{key}.png'
            out_abs = os.path.join(settings.MEDIA_ROOT, out_rel)
            box = {}

            def _call():
                try:
                    enhance_image(src, out_abs, prompt=prompt, reference_paths=[],
                                  color_limit=0, use_refs=False)
                    box['ok'] = True
                except Exception as e:                 # noqa: BLE001
                    box['err'] = e

            t = threading.Thread(target=_call, daemon=True)
            t.start()
            t.join(LEVEL_BUDGET_S)
            if t.is_alive():
                errors[key] = f'Quá {LEVEL_BUDGET_S}s — Google chậm/quá tải'
            elif 'err' in box:
                errors[key] = str(box['err'])[:160]
            else:
                results[key] = out_rel

        threads = [threading.Thread(target=_run, args=(k, p), daemon=True)
                   for k, _label, p in LEVELS]
        for t in threads:
            t.start()
        for t in threads:
            t.join(LEVEL_BUDGET_S + 30)

        params = dict(obj.params or {})
        params['levels'] = results
        params['errors'] = errors
        obj.params = params
        obj.status = ImageResult.STATUS_DONE
        obj.error_message = '' if results else 'Cả 3 mức đều lỗi (xem chi tiết từng mức).'
        obj.save()
    except Exception as e:
        obj.status = ImageResult.STATUS_ERROR
        obj.error_message = str(e)
        obj.save()


@staff_required
def anh_ai_test(request):
    """Trang thử "Mức độ AI" — chỉ nội bộ, để tinh chỉnh prompt."""
    return render(request, 'ai_levels_test.html', {'levels': [
        {'key': k, 'label': lb} for k, lb, _p in LEVELS]})


@csrf_exempt
@staff_required
def anh_ai_test_run(request):
    """POST 1 ảnh -> tạo job chạy 3 mức. Trả {ok, id} để trang poll."""
    if request.method != 'POST' or not request.FILES.get('image'):
        return JsonResponse({'ok': False, 'msg': 'Thiếu ảnh.'})
    from pha.ai_enhance import is_configured
    if not is_configured():
        return JsonResponse({'ok': False, 'msg': 'Chưa cấu hình Google API key (vào Cài đặt AI).'})
    upload = request.FILES['image']
    fss = FileSystemStorage()
    name = f'{datetime.now():%Y-%m-%d_%H-%M-%S}_aitest_{upload.name}'
    fss.save(name, upload)
    rec = ImageResult.objects.create(
        name=name, status=ImageResult.STATUS_PROCESSING,
        user=getattr(request.user, 'username', ''),
        params={'ai_test': True})
    _img_executor.submit(process_ai_levels, rec.id, name)
    _prune_image_results()
    return JsonResponse({'ok': True, 'id': rec.id})


@staff_required
def anh_ai_test_status(request):
    """GET ?id= -> trạng thái + URL ảnh gốc & 3 mức (hoặc lỗi từng mức)."""
    from pha.imageproc import mark_if_stuck
    try:
        rec = ImageResult.objects.get(id=int(request.GET.get('id', 0)))
    except (ImageResult.DoesNotExist, ValueError, TypeError):
        return JsonResponse({'status': 'error', 'error': 'Không tìm thấy job.'})
    if rec.status == ImageResult.STATUS_PROCESSING:
        if not mark_if_stuck(rec):
            return JsonResponse({'status': 'processing'})
    if rec.status == ImageResult.STATUS_ERROR:
        return JsonResponse({'status': 'error', 'error': rec.error_message})
    p = rec.params or {}
    lv = p.get('levels', {})
    return JsonResponse({
        'status': 'done',
        'original': '/media/' + rec.name,
        'levels': {k: '/media/' + v for k, v in lv.items()},
        'errors': p.get('errors', {}),
        'warn': rec.error_message or '',
    })
