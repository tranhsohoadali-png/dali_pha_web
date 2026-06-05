"""
Công thức pha màu: từ một bộ "màu gốc" (base colors), ước lượng tỉ lệ pha để ra
gần nhất một màu mục tiêu.

LƯU Ý: pha sơn là phép trừ màu (subtractive), không tuyến tính hoàn toàn. Mô hình
ở đây dùng trộn tuyến tính RGB trên đơn hình tỉ lệ (đúng tốt cho pha trắng/đen làm
sáng/tối; gần đúng cho pha tông màu). Kết quả là GỢI Ý — nên thử và chỉnh.

Cách tốt nhất để chuẩn: khai báo màu gốc = đúng màu sơn thật bạn đang có (đo/hút
từ swatch thật), rồi hệ thống tính tỉ lệ trộn các swatch đó.
"""
import json
import os

import numpy as np

_PATH = os.path.join(os.path.dirname(__file__), "base_colors.json")

_BASES = []          # [{'name','rgb'}]
_NAMES = []
_B = np.empty((0, 3), dtype=float)
_W = None            # ma trận tỉ lệ (M x k) các tổ hợp trên đơn hình
_MIX = None          # _W @ _B  (M x 3) -> màu sau khi trộn của từng tổ hợp


def _compositions(n, k):
    """Sinh các tuple k số nguyên không âm có tổng = n (stars & bars)."""
    if k == 1:
        yield (n,)
        return
    for i in range(n + 1):
        for rest in _compositions(n - i, k - 1):
            yield (i,) + rest


def _grid_step(k):
    """Chọn độ mịn lưới theo số màu gốc để số tổ hợp không quá lớn."""
    if k <= 1:
        return 1
    if k <= 4:
        return 20      # bước 5%
    if k <= 6:
        return 16
    if k <= 8:
        return 10
    return 6


def _rebuild():
    global _NAMES, _B, _W, _MIX
    _NAMES = [b["name"] for b in _BASES]
    _B = np.array([b["rgb"] for b in _BASES], dtype=float) if _BASES else np.empty((0, 3))
    k = len(_BASES)
    if k == 0:
        _W = None
        _MIX = None
        return
    n = _grid_step(k)
    comps = np.array(list(_compositions(n, k)), dtype=float) / float(n)
    _W = comps
    _MIX = comps @ _B


def reload_bases():
    global _BASES
    with open(_PATH, "r", encoding="utf-8") as f:
        _BASES = json.load(f)
    _rebuild()
    return len(_BASES)


def _save():
    with open(_PATH, "w", encoding="utf-8") as f:
        json.dump(_BASES, f, ensure_ascii=False, indent=1)


reload_bases()


def get_bases():
    return list(_BASES)


def _hex_to_rgb(hex_value):
    h = str(hex_value).strip().lstrip("#")
    return [int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)]


def add_base(name, hex_value):
    name = str(name).strip()
    h = str(hex_value).strip().lstrip("#").lower()
    if not name:
        return False, "Thiếu tên màu gốc."
    import re
    if not re.fullmatch(r"[0-9a-f]{6}", h):
        return False, "Mã HEX không hợp lệ (cần 6 ký tự)."
    rgb = _hex_to_rgb(h)
    for b in _BASES:
        if b["name"].lower() == name.lower():
            b["rgb"] = rgb
            _save(); _rebuild()
            return True, f"Đã cập nhật màu gốc '{name}'."
    _BASES.append({"name": name, "rgb": rgb})
    _save(); _rebuild()
    return True, f"Đã thêm màu gốc '{name}'."


def delete_base(name):
    global _BASES
    before = len(_BASES)
    _BASES = [b for b in _BASES if b["name"] != name]
    removed = before - len(_BASES)
    if removed:
        _save(); _rebuild()
    return removed


def _project_simplex(v):
    """Chiếu vector v lên đơn hình {w>=0, sum=1}."""
    n = len(v)
    u = np.sort(v)[::-1]
    css = np.cumsum(u) - 1.0
    ind = np.arange(1, n + 1)
    cond = (u - css / ind) > 0
    rho = ind[cond][-1]
    theta = css[cond][-1] / rho
    return np.maximum(v - theta, 0.0)


def _refine(w0, target, iters=500):
    """Giải tinh: min ||w·B - target||^2 với w>=0, sum w=1 (projected gradient)."""
    w = w0.astype(float).copy()
    L = 2.0 * (np.linalg.norm(_B, 2) ** 2) + 1e-9
    lr = 1.0 / L
    for _ in range(iters):
        r = w @ _B - target          # (3,)
        grad = 2.0 * (_B @ r)        # (k,)
        w = _project_simplex(w - lr * grad)
    return w


def mix_recipe(rgb, drop_pct=0.3):
    """
    Công thức pha chính xác cho màu rgb (giữ cả các thành phần rất nhỏ).
      {'recipe': [{'name','percent'(float)}...], 'mixed_rgb', 'closeness'}
    drop_pct: bỏ thành phần nhỏ hơn ngưỡng % (mặc định 0.3%) rồi chuẩn hoá.
    """
    if _MIX is None or not len(_BASES):
        return None
    target = np.array(rgb, dtype=float)
    # Seed bằng lưới thô rồi giải tinh
    seed = _W[int(np.argmin(np.sum((_MIX - target) ** 2, axis=1)))]
    w = _refine(seed, target)

    w = np.where(w < (drop_pct / 100.0), 0.0, w)
    if w.sum() == 0:
        w = seed.copy()
    w = w / w.sum()

    recipe = [{"name": _NAMES[i], "percent": round(float(w[i]) * 100, 1)}
              for i in range(len(_NAMES)) if w[i] > 0]
    recipe.sort(key=lambda x: -x["percent"])

    mixed = w @ _B
    dist = float(np.sqrt(np.sum((mixed - target) ** 2)))
    closeness = round(max(0.0, 100.0 - dist / 441.0 * 100.0), 1)
    return {
        "recipe": recipe,
        "weights": {_NAMES[i]: float(w[i]) for i in range(len(_NAMES)) if w[i] > 0},
        "mixed_rgb": [int(round(c)) for c in mixed],
        "closeness": closeness,
    }
