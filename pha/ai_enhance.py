"""
Tăng cường ảnh khách bằng Google AI (Gemini Image) trước khi số hoá.

Mục tiêu: làm sạch / nâng cấp ảnh chụp tranh thật của khách (mờ, ngược sáng,
nền lộn xộn) thành ảnh rõ nét, màu sạch — DỄ số hoá hơn cho bước index_color.

Thiết kế:
- KHÔNG thay thế khâu đánh số + khớp mã DALI. Chỉ chạy ở đầu pipeline.
- Tùy chọn: chỉ chạy khi nhân viên bật, không tự động mọi ảnh (tiết kiệm chi phí).
- Khoá API đọc từ biến môi trường GOOGLE_API_KEY (không hardcode).
- Lỗi (thiếu khoá / thiếu thư viện / API trả về rỗng) -> ném exception rõ ràng
  để process_image ghi vào ImageResult.error_message.
"""
import os

from decouple import config

# Model sinh/sửa ảnh của Google ("Nano Banana"). Có thể đổi qua env.
AI_ENHANCE_MODEL = config("AI_ENHANCE_MODEL", default="gemini-2.5-flash-image")

# Prompt mặc định: ĐƠN GIẢN HOÁ ảnh khách thành tranh tô màu (paint-by-numbers),
# nhưng GIỮ NGUYÊN tuyệt đối chủ thể/bố cục (không được vẽ sang con/vật khác).
DEFAULT_ENHANCE_PROMPT = config(
    "AI_ENHANCE_PROMPT",
    default=(
        "ROLE & GOAL. You are preparing artwork for a PAINT-BY-NUMBERS kit. Your "
        "output image will be fed to a program that traces each flat color area, "
        "outlines it and prints a small number inside it; a customer then paints "
        "each area by hand. Therefore the image MUST be built from a FEW LARGE, "
        "clean, FLAT color regions with bold closed outlines — like a tidy kawaii "
        "(Sanrio / chibi) sticker coloring page. A busy, detailed or painterly "
        "image is useless because it produces thousands of tiny unpaintable spots. "
        "\n\n"
        "TASK. Redraw the input picture in that clean cel-shaded sticker style "
        "while keeping the EXACT same main subject, character, pose, clothing and "
        "overall composition. CRITICAL: never replace or reinvent the subject — if "
        "the input is a white duck wearing a floral headscarf, the output must be "
        "the SAME white duck with the SAME headscarf, only re-rendered cleanly "
        "(never turn it into another animal or scene). "
        "\n\n"
        "STRICT STYLE RULES:\n"
        "1) OUTLINES: every shape has a bold, smooth, evenly-thick dark outline "
        "that is FULLY CLOSED (no gaps), like clean vector line art. No sketchy, "
        "broken, doubled, faded or textured lines.\n"
        "2) COLORS: FLAT solid fills only (cel shading). Absolutely NO gradients, "
        "no watercolor wash, no soft shadows, no highlights, no grain, no texture. "
        "Exactly one even color per region.\n"
        "3) SIMPLIFY THE BACKGROUND AGGRESSIVELY — THIS IS THE MOST IMPORTANT "
        "RULE. Turn any busy background into a calm, mostly-empty one: keep a "
        "large plain flat background color and cut clutter drastically. Replace "
        "dozens of tiny flowers / leaves / grass blades / dots / sparkles with "
        "only a FEW (about 4-8) LARGE simple shapes. Delete small specks, thin "
        "stems, scattered petals and repeated fine details. The subject must "
        "clearly stand out against a simple background.\n"
        "4) BIG REGIONS: make every color area large enough to comfortably paint "
        "and hold a number. Merge small adjacent details into their neighbour. No "
        "thin slivers, no tiny isolated spots, no fragmented areas.\n"
        "5) LIMITED PALETTE: use only a small set of clean, clearly distinct flat "
        "colors.\n\n"
        "The final result must look like a NEAT, printable coloring template with "
        "few large shapes and clean bold lines — the OPPOSITE of a detailed, "
        "painterly or photographic image. Keep the same aspect ratio, full-bleed, "
        "and output ONLY the redrawn image."
    ),
)

# Mặc định KHÔNG gửi ảnh mẫu kèm theo: model sinh ảnh hay 'copy' luôn chủ thể từ
# ảnh mẫu (vd vịt -> mèo). Bật lại bằng env AI_USE_STYLE_REFS=1 nếu thật sự cần.
AI_USE_STYLE_REFS = config("AI_USE_STYLE_REFS", default="0") == "1"


# ===================== GÓI CẤU HÌNH THEO LOẠI TRANH (PRESETS) =====================
# Mỗi preset đóng gói: prompt AI riêng + bộ thông số tách màu phù hợp.
# Chọn preset trên giao diện -> tự điền thông số + dùng đúng prompt khi bật AI.
_PROMPT_PHOTO = config("AI_PROMPT_PHOTO", default=(
    "ROLE & GOAL. Convert this REAL PHOTOGRAPH into a simple PAINT-BY-NUMBERS "
    "coloring template made of a few large, clean, FLAT color regions with bold "
    "closed outlines — like a tidy cartoon/anime poster. The output feeds a program "
    "that traces flat areas and numbers them, so detail and texture are harmful.\n"
    "TASK. Keep the SAME people/subject, pose, identity and composition. Do not add "
    "or remove anyone. Re-draw, do not just filter.\n"
    "RULES: (1) Bold, smooth, fully-closed dark outlines. (2) FLAT solid colors only "
    "— smooth skin, hair and clothes into a few even tones; NO photographic gradients, "
    "shadows, pores, noise or texture. (3) Simplify hair into a few big locks, skin "
    "into 2-3 flat tones, and DECLUTTER the background heavily into a few large flat "
    "shapes. (4) Big paintable regions, merge tiny details. (5) Limited, clean palette. "
    "Result must look hand-drawn and printable, NOT photographic. Same aspect ratio, "
    "output only the redrawn image."
))
_PROMPT_DESIGN = config("AI_PROMPT_DESIGN", default=(
    "ROLE & GOAL. This is already a clean flat/vector-style DESIGN. Standardize it "
    "into a tidy PAINT-BY-NUMBERS coloring template without changing the artwork.\n"
    "TASK. Keep the EXACT same subject, shapes, layout and colors. Minimal change.\n"
    "RULES: (1) Make all outlines bold, smooth and fully closed (vector-like). "
    "(2) Force every fill to ONE flat solid color (remove any gradient/soft edge). "
    "(3) Merge only the tiniest specks into neighbours; keep the intended shapes. "
    "(4) Keep the palette clean and limited. Same aspect ratio, output only the image."
))

PRESETS = {
    'anime': {
        'label': 'Anime / tranh nhỏ (dưới 12 màu)',
        'desc': 'Tranh anime/chibi, nét sạch, ít màu. Số nhỏ gọn, nét mượt.',
        'color_limit': 12, 'smooth': 1, 'min_area': 60, 'enhance': False,
        'prompt': DEFAULT_ENHANCE_PROMPT,
    },
    'photo': {
        'label': 'Ảnh thật khách hàng (đang phát triển)',
        'desc': 'Ảnh chụp người/cảnh thật -> đơn giản hoá mạnh thành tranh tô màu.',
        'color_limit': 18, 'smooth': 3, 'min_area': 150, 'enhance': True,
        'prompt': _PROMPT_PHOTO,
    },
    'design': {
        'label': 'Tranh thiết kế (đang phát triển)',
        'desc': 'Ảnh thiết kế phẳng/vector sẵn -> chuẩn hoá nét + màu phẳng.',
        'color_limit': 16, 'smooth': 0, 'min_area': 40, 'enhance': False,
        'prompt': _PROMPT_DESIGN,
    },
}
DEFAULT_PRESET = 'anime'


def get_preset(key):
    """Trả về gói cấu hình của preset (mặc định 'anime' nếu key lạ)."""
    return PRESETS.get(key, PRESETS[DEFAULT_PRESET])


def presets_for_ui():
    """Danh sách preset gọn cho giao diện (JSON-able)."""
    return {k: {'label': v['label'], 'desc': v['desc'],
                'color_limit': v['color_limit'], 'smooth': v['smooth'],
                'min_area': v['min_area'], 'enhance': v['enhance']}
            for k, v in PRESETS.items()}


class AIEnhanceError(Exception):
    """Lỗi trong quá trình tăng cường ảnh bằng AI."""
    pass


def get_api_key():
    """Trả về khoá API đang dùng, hoặc '' nếu chưa có.

    Ưu tiên khoá lưu trong DB (nhập qua trang Cài đặt AI), sau đó mới tới biến
    môi trường GOOGLE_API_KEY / GEMINI_API_KEY.
    """
    try:
        from pha.models import AppSetting
        v = (AppSetting.get("GOOGLE_API_KEY") or "").strip()
        if v:
            return v
    except Exception:
        pass
    return os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY") or ""


def is_configured():
    """True nếu đã có khoá API để dùng tính năng AI."""
    return bool(get_api_key())


def _get_api_key():
    key = get_api_key()
    if not key:
        raise AIEnhanceError(
            "Chưa cấu hình Google API key — vào trang Cài đặt AI để nhập khoá."
        )
    return key


# Hướng dẫn thêm khi có ảnh mẫu tham chiếu (chỉ dùng nếu bật AI_USE_STYLE_REFS).
STYLE_REF_INSTRUCTION = (
    " The FIRST images are style references ONLY: copy nothing from them except the "
    "drawing style (outline thickness, flatness, palette size). You MUST ignore "
    "their subjects/objects entirely. The LAST image is the only one to redraw — "
    "keep its exact subject and composition."
)


def enhance_image(input_path, output_path, prompt=None, reference_paths=None,
                  color_limit=0):
    """
    Gọi Google Gemini Image để tăng cường ảnh.

    input_path      : đường dẫn ảnh gốc (ảnh khách).
    output_path     : nơi ghi ảnh đã tăng cường (PNG).
    prompt          : ghi đè prompt mặc định (tùy chọn).
    reference_paths : danh sách ảnh mẫu phong cách gửi kèm (few-shot, tối đa ~3-4).
    color_limit     : số màu tối đa AI được dùng (0 = không giới hạn).

    Trả về output_path khi thành công. Ném AIEnhanceError nếu lỗi.
    """
    key = _get_api_key()

    try:
        from google import genai  # noqa: WPS433 (import trong hàm: chỉ nạp khi cần)
    except ImportError as e:
        raise AIEnhanceError(
            "Thiếu thư viện google-genai. Cài bằng: pip install google-genai"
        ) from e

    from PIL import Image

    try:
        src = Image.open(input_path)
        src.load()
    except Exception as e:
        raise AIEnhanceError(f"Không mở được ảnh gốc: {e}") from e

    # Chỉ nạp ảnh mẫu khi bật AI_USE_STYLE_REFS (mặc định tắt để không lẫn chủ thể).
    refs = []
    if AI_USE_STYLE_REFS:
        for rp in (reference_paths or []):
            try:
                ref = Image.open(rp)
                ref.load()
                refs.append(ref)
            except Exception:
                continue  # bỏ qua mẫu lỗi, không làm hỏng cả lần chạy

    text = prompt or DEFAULT_ENHANCE_PROMPT
    try:
        cl = int(color_limit or 0)
    except (TypeError, ValueError):
        cl = 0
    if cl > 0:
        text += (
            f" Use at most {cl} distinct flat colors in total for the whole image; "
            f"merge similar shades so the final artwork has no more than {cl} colors."
        )
    if refs:
        text += STYLE_REF_INSTRUCTION
    # Ảnh mẫu (nếu có) đặt TRƯỚC, ảnh khách đặt CUỐI = ảnh cần vẽ lại.
    contents = [text] + refs + [src]

    try:
        client = genai.Client(api_key=key)
        resp = client.models.generate_content(
            model=AI_ENHANCE_MODEL,
            contents=contents,
        )
    except Exception as e:
        raise AIEnhanceError(f"Gọi Google AI thất bại: {e}") from e

    image_bytes = _extract_image_bytes(resp)
    if image_bytes is None:
        raise AIEnhanceError("Google AI không trả về ảnh (có thể bị chặn nội dung hoặc hết hạn mức).")

    try:
        with open(output_path, "wb") as f:
            f.write(image_bytes)
        # Chuẩn hoá về PNG/RGB để chắc chắn bước OpenCV đọc được.
        out = Image.open(output_path)
        if out.mode not in ("RGB", "RGBA"):
            out = out.convert("RGB")
        out.save(output_path, format="PNG")
    except Exception as e:
        raise AIEnhanceError(f"Không lưu được ảnh đã tăng cường: {e}") from e

    return output_path


def _extract_image_bytes(resp):
    """Lấy bytes ảnh đầu tiên từ phản hồi generate_content."""
    candidates = getattr(resp, "candidates", None) or []
    for cand in candidates:
        content = getattr(cand, "content", None)
        parts = getattr(content, "parts", None) or []
        for part in parts:
            inline = getattr(part, "inline_data", None)
            if inline is not None and getattr(inline, "data", None):
                return inline.data
    return None
