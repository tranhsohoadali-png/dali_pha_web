"""
DALI color matching.

Ported from the django_dali project (uploadexcel/views.py: nearest_colour).
Instead of reading the DALI reference from a database populated via an uploaded
Excel file, the reference table (hex -> DALI code) is shipped as a static JSON
file (dali_reference.json) that was extracted from the original `bangMau.xlsx`.

This keeps the merged program self-contained: it needs no pandas / openpyxl at
runtime, only the Python standard library + numpy (already bundled in
python_embed).
"""
import json
import os
import re

import numpy as np

# dali_reference.json = SEED (commit kèm repo, chỉ ĐỌC). Mọi thêm/sửa/xoá ghi vào
# dali_reference_runtime.json (KHÔNG commit — xem .gitignore) -> deploy/git pull không
# bao giờ đụng dữ liệu runtime nữa => hết cảnh file bị ghi đè/xung đột/hỏng khi deploy.
_REF_PATH = os.path.join(os.path.dirname(__file__), "dali_reference.json")
_RUN_PATH = os.path.join(os.path.dirname(__file__), "dali_reference_runtime.json")


def _data_path():
    """File dữ liệu hiện hành: runtime nếu đã có, không thì seed (lần đầu)."""
    return _RUN_PATH if os.path.exists(_RUN_PATH) else _REF_PATH

# Các biến module-level được dựng lại mỗi khi _rebuild() chạy.
_REFERENCE = []
_REF_RGB = np.empty((0, 3), dtype=np.int32)
_REF_DALI = []
_REF_HEX = []
_HEX_TO_DALI = {}


def _rebuild():
    """Dựng lại các cấu trúc tra cứu từ _REFERENCE."""
    global _REF_RGB, _REF_DALI, _REF_HEX, _HEX_TO_DALI
    if _REFERENCE:
        _REF_RGB = np.array([item["rgb"] for item in _REFERENCE], dtype=np.int32)
    else:
        _REF_RGB = np.empty((0, 3), dtype=np.int32)
    _REF_DALI = [item["dali"] for item in _REFERENCE]
    _REF_HEX = [item["hex"] for item in _REFERENCE]
    _HEX_TO_DALI = {}
    for item in _REFERENCE:
        _HEX_TO_DALI.setdefault(item["hex"].lower(), item["dali"])


def _read_colors(path):
    """Đọc 1 file JSON bảng màu, CHỊU LỖI (thử strict=False nếu có ký tự điều khiển lạ).
    Trả list màu; None nếu file không có / không đọc được."""
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError) as e:
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.loads(f.read(), strict=False)         # bỏ qua ký tự điều khiển
            print("dali_match: %s đọc nới lỏng do lỗi: %s" % (os.path.basename(path), e))
            return data
        except Exception as e2:
            print("dali_match: KHÔNG đọc được %s (%s)" % (os.path.basename(path), e2))
            return None


def reload_reference():
    """Đọc lại bảng màu, dựng lại bộ nhớ. ƯU TIÊN runtime (màu thêm/sửa trên server); NHƯNG
    nếu runtime RỖNG / HỎNG / không có thì TỰ QUAY VỀ seed dali_reference.json (4468 màu)
    -> file runtime rỗng KHÔNG còn che mất seed (sửa lỗi 'mất hết màu'). KHÔNG bao giờ sập trang
    (trước đây JSON hỏng làm cả /dali-colors 500)."""
    global _REFERENCE
    data = _read_colors(_RUN_PATH)              # runtime trước
    if not data:                               # rỗng / hỏng / không có -> lùi về seed
        if data is not None:
            print("dali_match: runtime rỗng/hỏng -> dùng seed dali_reference.json (4468 màu)")
        data = _read_colors(_REF_PATH) or []
    _REFERENCE = data if isinstance(data, list) else []
    _rebuild()
    return len(_REFERENCE)


def _save():
    # LUÔN ghi vào file runtime (không commit) -> bền qua deploy.
    with open(_RUN_PATH, "w", encoding="utf-8") as f:
        json.dump(_REFERENCE, f, ensure_ascii=False, indent=0)


# Nạp lần đầu khi import module.
reload_reference()


def _now_str():
    """Mốc thời gian thêm/sửa màu (giờ VN nếu có)."""
    try:
        from pha.views import _now
        return _now().strftime('%Y-%m-%d %H:%M')
    except Exception:
        import datetime
        return datetime.datetime.now().strftime('%Y-%m-%d %H:%M')


def get_all():
    """Trả về bản sao danh sách tham chiếu [{hex, dali, rgb}, ...]."""
    return list(_REFERENCE)


def get_recent():
    """Danh sách màu sắp theo MỚI THÊM GẦN NHẤT: mục có mốc 'added' lên đầu (thời gian
    giảm dần), các màu gốc (chưa có mốc) xếp sau theo thứ tự thêm vào file (mới append sau
    = mới hơn). Trả bản sao."""
    out = [dict(it, _i=i) for i, it in enumerate(_REFERENCE)]
    out.sort(key=lambda x: (x.get('added', ''), x['_i']), reverse=True)
    for it in out:
        it.pop('_i', None)
    return out


def find_by_dali(code):
    """Tìm mục theo mã DALI (không phân biệt hoa/thường, bỏ khoảng trắng)."""
    key = str(code).strip().lower().replace(' ', '')
    for it in _REFERENCE:
        if it["dali"].strip().lower().replace(' ', '') == key:
            return it
    return None


def add_entry(hex_value, dali):
    """
    Thêm/cập nhật một màu DALI. hex_value: 'RRGGBB' hoặc '#RRGGBB'.
    Trả về (ok, message).
    """
    h = str(hex_value).strip().lstrip('#').lower()
    dali = str(dali).strip()
    if not re.fullmatch(r'[0-9a-f]{6}', h):
        return False, "Mã HEX không hợp lệ (cần 6 ký tự 0-9a-f)."
    if not dali:
        return False, "Thiếu mã DALI."
    rgb = [int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)]
    # nếu hex đã tồn tại -> cập nhật mã DALI
    for item in _REFERENCE:
        if item["hex"].lower() == h:
            item["dali"] = dali
            item["rgb"] = rgb
            item["added"] = _now_str()
            _save(); _rebuild()
            return True, f"Đã cập nhật {h} -> {dali}."
    _REFERENCE.append({"hex": h, "dali": dali, "rgb": rgb, "added": _now_str()})
    _save(); _rebuild()
    return True, f"Đã thêm {h} -> {dali}."


def import_entries(pairs, replace=False):
    """
    Nhập hàng loạt các cặp (hex, dali). replace=True -> xoá sạch bảng cũ trước.
    Lưu file + dựng lại 1 lần (nhanh). Trả về (added, updated).
    """
    global _REFERENCE
    if replace:
        _REFERENCE = []
    index = {it["hex"].lower(): it for it in _REFERENCE}
    added = updated = 0
    stamp = _now_str()
    for hex_value, dali in pairs:
        h = str(hex_value).strip().lstrip('#').lower()
        dali = str(dali).strip()
        if not re.fullmatch(r'[0-9a-f]{6}', h) or not dali:
            continue
        rgb = [int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)]
        if h in index:
            index[h]["dali"] = dali
            index[h]["rgb"] = rgb
            index[h]["added"] = stamp
            updated += 1
        else:
            item = {"hex": h, "dali": dali, "rgb": rgb, "added": stamp}
            _REFERENCE.append(item)
            index[h] = item
            added += 1
    _save()
    _rebuild()
    return added, updated


def delete_entry(hex_value, dali=None):
    """Xoá màu theo hex (và mã dali nếu cung cấp). Trả về số mục đã xoá."""
    global _REFERENCE
    h = str(hex_value).strip().lstrip('#').lower()
    before = len(_REFERENCE)
    _REFERENCE = [it for it in _REFERENCE
                  if not (it["hex"].lower() == h and (dali is None or it["dali"] == dali))]
    removed = before - len(_REFERENCE)
    if removed:
        _save(); _rebuild()
    return removed


def rgb_to_hex(rgb):
    """(r, g, b) -> 'rrggbb' (lowercase, no '#')."""
    r, g, b = rgb
    return "{:02x}{:02x}{:02x}".format(int(r), int(g), int(b))


def nearest_dali(rgb):
    """
    Find the DALI color code closest to the given (r, g, b) tuple.

    1. If the exact hex exists in the reference table, return that DALI code.
    2. Otherwise return the DALI code of the reference color with the smallest
       Euclidean distance in RGB space.

    Returns an empty string only if the reference table is empty.
    """
    if not _REFERENCE:
        return ""

    hex_code = rgb_to_hex(rgb)
    exact = _HEX_TO_DALI.get(hex_code)
    if exact is not None:
        return exact

    query = np.array(rgb, dtype=np.int32)
    distances = np.sum((_REF_RGB - query) ** 2, axis=1)
    idx = int(np.argmin(distances))
    return _REF_DALI[idx]


def reference_size():
    return len(_REFERENCE)
