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
        "Redraw the EXACT same picture as a clean, simple paint-by-numbers "
        "coloring illustration. Keep the identical subject, characters, animals, "
        "objects, pose, layout, colors and composition as the input image. This "
        "is critical: do NOT replace, add, remove or reinvent anything; if the "
        "input shows a duck, the output must be the same duck (never another "
        "animal). Simplify the shading into flat, evenly filled color regions "
        "with a limited, clean palette, add consistent dark outlines around each "
        "region, and remove noise, gradients, glare and background clutter. The "
        "result must be clearly recognizable as the SAME image, just simplified "
        "for painting by numbers. Output only the redrawn image, same aspect ratio."
    ),
)

# Mặc định KHÔNG gửi ảnh mẫu kèm theo: model sinh ảnh hay 'copy' luôn chủ thể từ
# ảnh mẫu (vd vịt -> mèo). Bật lại bằng env AI_USE_STYLE_REFS=1 nếu thật sự cần.
AI_USE_STYLE_REFS = config("AI_USE_STYLE_REFS", default="0") == "1"


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


def enhance_image(input_path, output_path, prompt=None, reference_paths=None):
    """
    Gọi Google Gemini Image để tăng cường ảnh.

    input_path      : đường dẫn ảnh gốc (ảnh khách).
    output_path     : nơi ghi ảnh đã tăng cường (PNG).
    prompt          : ghi đè prompt mặc định (tùy chọn).
    reference_paths : danh sách ảnh mẫu phong cách gửi kèm (few-shot, tối đa ~3-4).

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
