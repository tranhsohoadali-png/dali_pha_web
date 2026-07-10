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
# Timeout (mili-giây) cho mỗi lần gọi Google AI -> tránh treo luồng xử lý.
AI_TIMEOUT_MS = config("AI_TIMEOUT_MS", default=150000, cast=int)
# Cạnh dài tối đa của ảnh gửi cho AI. Ảnh điện thoại 4000px làm Google xử lý
# rất lâu -> hay 504 DEADLINE_EXCEEDED; model vẽ lại nên không cần ảnh gốc to.
AI_MAX_EDGE = config("AI_MAX_EDGE", default=1280, cast=int)

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
        "colors.\n"
        "6) IF THE SUBJECT IS A PERSON: keep the face lively — lips always get "
        "their own clearly rosy/red flat color (never skin-coloured), eyes keep a "
        "dark iris distinct from skin, cheeks may have a soft blush shape.\n\n"
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
# gpt-image-1 + input_fidelity=high: prompt NHẸ, KHÔNG "posterize/vector-trace" (mâu thuẫn với
# fidelity=high, lại khiến model vẽ lại kiểu anime). Việc posterize để index_color lo; AI chỉ
# khử nhiễu + dọn nền + GIỮ NGƯỜI THẬT. (Gemini vẫn dùng prompt này, hợp lý.)
_PROMPT_PHOTO = config("AI_PROMPT_PHOTO", default=(
    "Edit this real photograph with a light, non-destructive pass. This is NOT a "
    "redraw and NOT a stylisation.\n"
    "KEEP UNCHANGED (do not regenerate): the exact same real person, real face, real "
    "facial features, proportions, expression, pose, hair and clothing. Keep the real "
    "skin tone and real colours. Fully photographic — absolutely NO anime, cartoon, "
    "illustration, 3D-render, painting or line-art look. Do not beautify, slim or "
    "reshape the face.\n"
    "ONLY DO THIS, gently: reduce photographic noise, grain, skin pores and stray hair "
    "wisps; smooth harsh JPEG artefacts. Keep the eyes, eyebrows, nose and lips sharp "
    "and clearly separated. Keep the lips in their own natural rosy tone, a little more "
    "saturated than the skin, never skin-coloured.\n"
    "BACKGROUND ONLY: calm and de-clutter it — remove small distracting objects (wires, "
    "poles, leaves, signage, clutter) and lower its contrast so the person stands out. "
    "Do not invent new background detail.\n"
    "Keep the same framing and aspect ratio. Output only the edited image."
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
        'use_refs': False, 'face_priority': False, 'prompt': DEFAULT_ENHANCE_PROMPT,
    },
    'photo': {
        'label': 'Ảnh thật khách hàng (chân dung, 30–48 màu)',
        'desc': 'Giữ NGUYÊN ảnh thật (đúng người/màu/mặt), chỉ giản lược thành tranh '
                'tô màu (posterize) — KHÔNG đổi sang anime/AI. Bật AI = posterize sạch '
                'hơn; tắt AI = giản lược thuần thuật toán. Cả hai đều giữ ảnh thật.',
        'color_limit': 40, 'smooth': 2, 'min_area': 30, 'enhance': True,
        'use_refs': True, 'face_priority': True, 'prompt': _PROMPT_PHOTO,
    },
    'design': {
        'label': 'Tranh thiết kế (chi tiết, số nhỏ)',
        'desc': 'Ảnh thiết kế phẳng/vector sẵn -> chuẩn hoá nét + màu phẳng, ĐÁNH SỐ '
                'CHI TIẾT như cây/hoa (giữ nhiều ô nhỏ, số bé, ít gộp mảng).',
        'color_limit': 24, 'smooth': 0, 'min_area': 0, 'enhance': False,
        'use_refs': False, 'face_priority': False, 'detail': True,
        'prompt': _PROMPT_DESIGN,
    },
    'cayhoa': {
        'label': 'Cây / Hoa (chi tiết, số nhỏ)',
        'desc': 'Tranh cây/hoa/phong cảnh rậm: GIỮ nhiều chi tiết, đánh số nhỏ — '
                'KHÔNG gộp mảng mạnh. Số màu cao, không lọc đốm. Dùng cho ai muốn '
                'tranh chi tiết (số bé vẫn đọc được). Tắt AI để giữ nguyên ảnh.',
        'color_limit': 24, 'smooth': 0, 'min_area': 0, 'enhance': False,
        'use_refs': False, 'face_priority': False, 'detail': True,
        'prompt': _PROMPT_DESIGN,
    },
    'kholon': {
        'label': 'Tranh khổ TO siêu chi tiết (120 màu)',
        'desc': 'Cho tranh KHỔ LỚN (vd 1.2×2m): tải ảnh NÉT CAO + chọn Khổ (cm) lớn -> '
                'đánh số theo TỈ LỆ THẬT (số/nét tính bằng mm @ khổ to), GIỮ vật thể/bối '
                'cảnh (palette rarity), 120 màu (+boost mặt). Độ phân giải cao + giữ ô nhỏ '
                'tới ~2mm. Có bản chi tiết mặt nếu là chân dung. Xử lý nền ~vài phút.',
        'color_limit': 120, 'smooth': 0, 'min_area': 0, 'enhance': False,
        'use_refs': False, 'face_priority': False, 'detail': True, 'large': True,
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
                'min_area': v['min_area'], 'enhance': v['enhance'],
                # 'detail' -> web bật/tắt nút "Độ chi tiết đánh số"; 'large' -> khổ TO.
                'detail': bool(v.get('detail')), 'large': bool(v.get('large'))}
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
    """True nếu đã có ÍT NHẤT một khoá AI (Google HOẶC OpenAI)."""
    return bool(get_api_key() or get_openai_key())


# ===================== ĐA NHÀ CUNG CẤP AI (Gemini + OpenAI) =====================
# Model ảnh của OpenAI (chính là model tạo/sửa ảnh của ChatGPT). Đổi qua env nếu cần.
AI_OPENAI_MODEL = config("AI_OPENAI_MODEL", default="gpt-image-1")
# GIỮ MẶT/MÀU NGƯỜI THẬT (chống "ra anime"): input_fidelity='high' bám sát ảnh gốc; mặc định
# OpenAI là 'low' -> tái tạo mạnh -> vẽ lại mặt kiểu hoạt hình. quality cao = chi tiết hơn.
# (Cần SDK openai đủ mới ~>=1.75; nếu SDK cũ báo tham số lạ thì tự bỏ 2 tham số này.)
AI_OPENAI_FIDELITY = config("AI_OPENAI_FIDELITY", default="high")   # high | low
AI_OPENAI_QUALITY = config("AI_OPENAI_QUALITY", default="high")     # low | medium | high | auto


def get_openai_key():
    """Khoá OpenAI (ChatGPT) — ưu tiên DB (Cài đặt AI), rồi biến môi trường OPENAI_API_KEY."""
    try:
        from pha.models import AppSetting
        v = (AppSetting.get("OPENAI_API_KEY") or "").strip()
        if v:
            return v
    except Exception:
        pass
    return os.environ.get("OPENAI_API_KEY") or ""


def get_provider():
    """Nhà cung cấp AI CHÍNH: 'gemini' (mặc định) hoặc 'openai' — từ AppSetting AI_PROVIDER."""
    try:
        from pha.models import AppSetting
        p = (AppSetting.get("AI_PROVIDER") or "").strip().lower()
        if p in ("gemini", "openai"):
            return p
    except Exception:
        pass
    return (os.environ.get("AI_PROVIDER") or "gemini").lower()


def get_fallback():
    """Có TỰ DỰ PHÒNG sang nhà cung cấp kia khi nhà chính lỗi (vd 429 hết quota) không?
    Mặc định BẬT -> AI ít khi 'chết' vì hết hạn mức một bên."""
    try:
        from pha.models import AppSetting
        v = AppSetting.get("AI_FALLBACK")
        if v is not None and str(v).strip() != "":
            return str(v).strip().lower() not in ("0", "false", "no", "off")
    except Exception:
        pass
    return True


def _provider_key(prov):
    return get_api_key() if prov == "gemini" else get_openai_key()


def _get_api_key():
    key = get_api_key()
    if not key:
        raise AIEnhanceError(
            "Chưa cấu hình Google API key — vào trang Cài đặt AI để nhập khoá."
        )
    return key


# Hướng dẫn khi gửi kèm ảnh mẫu tham chiếu phong cách. Viết CHẶT cho CHÂN DUNG:
# chỉ học cách vẽ, tuyệt đối không copy khuôn mặt/người từ ảnh mẫu.
STYLE_REF_INSTRUCTION = (
    " The FIRST image(s) are STYLE REFERENCES — examples of our shop's finished "
    "paint-by-numbers artwork. Learn ONLY their drawing style from them: outline "
    "thickness, how skin/hair are flattened into a few tones, region size and "
    "palette feel. You MUST NOT copy their faces, people, identity, pose, clothing "
    "or background. Redraw ONLY the LAST image (the customer's photo), keeping the "
    "EXACT same person, face, identity, pose and composition as that last image."
)


def _enhance_gemini(input_path, output_path, prompt=None, reference_paths=None,
                    color_limit=0, use_refs=None):
    """
    Gọi Google Gemini Image để tăng cường ảnh. (1 nhà cung cấp — dispatch ở enhance_image.)

    input_path      : đường dẫn ảnh gốc (ảnh khách).
    output_path     : nơi ghi ảnh đã tăng cường (PNG).
    prompt          : ghi đè prompt mặc định (tùy chọn).
    reference_paths : danh sách ảnh mẫu phong cách gửi kèm (few-shot, tối đa ~3-4).
    color_limit     : số màu tối đa AI được dùng (0 = không giới hạn).
    use_refs        : có gửi ảnh mẫu kèm không (None = theo AI_USE_STYLE_REFS).

    Trả về output_path khi thành công. Ném AIEnhanceError nếu lỗi.
    """
    key = _get_api_key()
    if use_refs is None:
        use_refs = AI_USE_STYLE_REFS

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

    # Thu nhỏ trước khi gửi AI: giảm mạnh thời gian xử lý + tỉ lệ 504.
    if max(src.size) > AI_MAX_EDGE:
        src = src.copy()
        src.thumbnail((AI_MAX_EDGE, AI_MAX_EDGE), Image.LANCZOS)

    # Nạp ảnh mẫu chỉ khi preset cho phép (use_refs).
    refs = []
    if use_refs:
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
        # Timeout để KHÔNG treo luồng xử lý nếu Google chậm/không phản hồi.
        try:
            from google.genai import types as _gtypes
            client = genai.Client(
                api_key=key,
                http_options=_gtypes.HttpOptions(timeout=AI_TIMEOUT_MS),
            )
        except Exception:
            client = genai.Client(api_key=key)   # SDK cũ không có HttpOptions
        # Thử tối đa 2 lần: lỗi tạm của Google (504/503/quá tải) hay tự hết
        # khi gọi lại ngay sau vài giây.
        import time as _time
        resp = None
        for _attempt in (1, 2, 3):
            try:
                resp = client.models.generate_content(
                    model=AI_ENHANCE_MODEL,
                    contents=contents,
                )
                break
            except Exception as e:  # noqa: PERF203
                _msg = str(e)
                # Lỗi TẠM của Google -> thử lại (gồm 500 INTERNAL hay gặp lúc Google
                # quá tải ban ngày). Trần AI_BUDGET_S vẫn chặn tổng thời gian.
                _transient = any(t in _msg for t in (
                    '500', 'INTERNAL', '503', '504', 'DEADLINE', 'imeout',
                    'UNAVAILABLE', 'overloaded'))
                if _attempt < 3 and _transient:
                    _time.sleep(3 * _attempt)   # giãn 3s, 6s
                    continue
                raise AIEnhanceError(f"Gọi Google AI thất bại: {e}") from e
    except AIEnhanceError:
        raise
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


def _enhance_openai(input_path, output_path, prompt=None, reference_paths=None,
                    color_limit=0, use_refs=None):
    """Tăng cường ảnh bằng OpenAI (gpt-image-1, images.edit). Cùng prompt/ảnh-mẫu như Gemini."""
    key = get_openai_key()
    if not key:
        raise AIEnhanceError("Chưa cấu hình OpenAI API key — vào trang Cài đặt AI để nhập khoá.")
    try:
        from openai import OpenAI
    except ImportError as e:
        raise AIEnhanceError("Thiếu thư viện openai. Cài bằng: pip install openai") from e
    import io
    import base64
    from PIL import Image

    if use_refs is None:
        use_refs = AI_USE_STYLE_REFS
    try:
        src = Image.open(input_path)
        src.load()
    except Exception as e:
        raise AIEnhanceError(f"Không mở được ảnh gốc: {e}") from e
    if max(src.size) > AI_MAX_EDGE:
        src = src.copy()
        src.thumbnail((AI_MAX_EDGE, AI_MAX_EDGE), Image.LANCZOS)
    # OpenAI: CHỈ gửi ảnh khách (KHÔNG gửi ảnh mẫu — tránh trộn mặt/người từ ảnh mẫu vào
    # chân dung; prompt đã tả đủ phong cách tô-màu-số).
    text = prompt or DEFAULT_ENHANCE_PROMPT
    try:
        cl = int(color_limit or 0)
    except (TypeError, ValueError):
        cl = 0
    if cl > 0:
        text += (f" Use at most {cl} distinct flat colors in total for the whole image; "
                 f"merge similar shades so the final artwork has no more than {cl} colors.")

    def _png(im):
        b = io.BytesIO()
        im.convert("RGB").save(b, format="PNG")
        b.seek(0)
        b.name = "image.png"
        return b

    imgs = [_png(src)]
    client = OpenAI(api_key=key, timeout=AI_TIMEOUT_MS / 1000.0)
    _kwargs = dict(
        model=AI_OPENAI_MODEL,
        image=imgs if len(imgs) > 1 else imgs[0],
        prompt=text[:32000],
        size="auto",                       # GIỮ auto -> KHÔNG ép 1024x1024 (méo ảnh dọc)
    )
    if AI_OPENAI_FIDELITY:
        _kwargs["input_fidelity"] = AI_OPENAI_FIDELITY   # 'high' = giữ mặt/người thật (chống anime)
    if AI_OPENAI_QUALITY:
        _kwargs["quality"] = AI_OPENAI_QUALITY
    try:
        resp = client.images.edit(**_kwargs)
    except Exception as e:
        # CHỈ retry-bỏ-2-tham-số khi lỗi nói về tham số LẠ (SDK/endpoint cũ chưa hỗ trợ).
        # Lỗi thật (429 hết quota, ảnh hỏng) -> ném lên để enhance_image fallback Gemini.
        msg = str(e).lower()
        if any(k in msg for k in ("input_fidelity", "quality", "unexpected keyword",
                                  "unknown", "unrecognized", "not permitted", "invalid_request")):
            _kwargs.pop("input_fidelity", None)
            _kwargs.pop("quality", None)
            try:
                resp = client.images.edit(**_kwargs)
            except Exception as e2:
                raise AIEnhanceError(f"Gọi OpenAI thất bại: {e2}") from e2
        else:
            raise AIEnhanceError(f"Gọi OpenAI thất bại: {e}") from e

    try:
        image_bytes = base64.b64decode(resp.data[0].b64_json)
    except Exception as e:
        raise AIEnhanceError(f"OpenAI không trả về ảnh: {e}") from e

    try:
        with open(output_path, "wb") as f:
            f.write(image_bytes)
        out = Image.open(output_path)
        if out.mode not in ("RGB", "RGBA"):
            out = out.convert("RGB")
        out.save(output_path, format="PNG")
    except Exception as e:
        raise AIEnhanceError(f"Không lưu được ảnh đã tăng cường: {e}") from e
    return output_path


def enhance_image(input_path, output_path, prompt=None, reference_paths=None,
                  color_limit=0, use_refs=None):
    """TĂNG CƯỜNG ẢNH — TỰ CHỌN nhà cung cấp (Gemini/OpenAI) + DỰ PHÒNG.

    Chạy nhà CHÍNH (AI_PROVIDER, mặc định gemini); nếu lỗi (vd 429 hết quota) và bật dự
    phòng (AI_FALLBACK) thì TỰ thử nhà kia. Chỉ dùng nhà đã có khoá. Trả output_path khi
    thành công; ném AIEnhanceError nếu MỌI nhà đều lỗi (process_image sẽ bỏ qua AI, xử lý ảnh gốc)."""
    primary = get_provider()
    order = [primary]
    if get_fallback():
        order.append("openai" if primary == "gemini" else "gemini")
    order = [p for p in order if _provider_key(p)]     # bỏ nhà chưa có khoá
    if not order:
        raise AIEnhanceError("Chưa cấu hình khoá AI nào (Google hoặc OpenAI) — vào trang Cài đặt AI.")

    errors = []
    for prov in order:
        fn = _enhance_gemini if prov == "gemini" else _enhance_openai
        try:
            return fn(input_path, output_path, prompt=prompt, reference_paths=reference_paths,
                      color_limit=color_limit, use_refs=use_refs)
        except Exception as e:
            errors.append("%s: %s" % (prov, str(e)[:140]))
            continue
    raise AIEnhanceError("AI thất bại — " + " | ".join(errors))


# Model đọc/hiểu ảnh (vision) để ĐẾM SỐ MÀU từ bảng chú giải. Rẻ & nhanh hơn model ảnh.
AI_COUNT_MODEL = config("AI_COUNT_MODEL", default="gemini-2.5-flash")


def ai_count_colors(image_path):
    """Dùng Gemini đọc BẢNG CHÚ GIẢI (legend) trong ảnh tô màu số để lấy SỐ MÀU.

    Trả về int (>=1) nếu đọc được, None nếu không có khoá / lỗi / không chắc.
    Ảnh loại này có cột đánh số 1,2,3,... mỗi dòng = 1 màu -> số lớn nhất = số màu.
    """
    key = get_api_key()
    if not key:
        return None
    try:
        from google import genai
    except ImportError:
        return None
    from PIL import Image
    try:
        img = Image.open(image_path)
        img.load()
    except Exception:
        return None

    prompt = (
        "This is a PAINT-BY-NUMBERS template. Somewhere in the image (usually a "
        "column on the right or bottom) there is a COLOR LEGEND: a numbered list "
        "1, 2, 3, ... where each row is one paint color (a number, a color swatch "
        "and a color code such as BCA.10, BLA.4, 'Den', 'Trang'). "
        "Count how many colors the legend lists (i.e. the largest number in that "
        "list). Answer with ONLY that single integer, no words, no punctuation."
    )
    try:
        try:
            from google.genai import types as _gtypes
            client = genai.Client(api_key=key,
                                  http_options=_gtypes.HttpOptions(timeout=30000))
        except Exception:
            client = genai.Client(api_key=key)
        resp = client.models.generate_content(model=AI_COUNT_MODEL,
                                              contents=[prompt, img])
    except Exception:
        return None

    import re
    txt = (getattr(resp, "text", "") or "").strip()
    m = re.search(r"\d+", txt)
    if not m:
        return None
    n = int(m.group())
    return n if 1 <= n <= 200 else None


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
