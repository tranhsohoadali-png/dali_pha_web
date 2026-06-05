"""
Lưu trữ CÔNG THỨC PHA do người dùng tự nhập (chính xác theo thực tế xưởng).

Mỗi công thức:
  {
    "dali": "BXA.1",
    "hex": "FCFFBE",
    "components": [{"name": "Trắng", "grams": 265}, {"name": "215", "grams": 3.5}, ...]
  }
Lưu trong recipes.json (cùng thư mục).
"""
import json
import os
import re

_PATH = os.path.join(os.path.dirname(__file__), "recipes.json")
_RECIPES = []


def reload_recipes():
    global _RECIPES
    if os.path.exists(_PATH):
        with open(_PATH, "r", encoding="utf-8") as f:
            _RECIPES = json.load(f)
    else:
        _RECIPES = []
    return len(_RECIPES)


def _save():
    with open(_PATH, "w", encoding="utf-8") as f:
        json.dump(_RECIPES, f, ensure_ascii=False, indent=1)


reload_recipes()


def get_all():
    return list(_RECIPES)


def add_recipe(dali, hex_value, components):
    """Thêm/cập nhật 1 công thức (theo mã DALI). components: [{'name','grams'}]."""
    dali = str(dali).strip()
    hex_value = str(hex_value).strip().lstrip("#").upper()
    if not dali:
        return False, "Thiếu mã màu DALI."
    clean = []
    for c in components:
        name = str(c.get("name", "")).strip()
        try:
            g = float(c.get("grams"))
        except (TypeError, ValueError):
            continue
        if name and g > 0:
            clean.append({"name": name, "grams": round(g, 2)})
    if not clean:
        return False, "Chưa nhập màu gốc và khối lượng."
    item = {"dali": dali, "hex": hex_value, "components": clean}
    existed = any(r["dali"].strip().lower() == dali.lower() for r in _RECIPES)
    # Bỏ bản cũ cùng mã (nếu có) rồi chèn LÊN ĐẦU -> mới nhất hiện trên cùng.
    _RECIPES[:] = [r for r in _RECIPES if r["dali"].strip().lower() != dali.lower()]
    _RECIPES.insert(0, item)
    _save()
    return True, (f"Đã cập nhật công thức {dali}." if existed else f"Đã lưu công thức {dali}.")


def delete_recipe(dali):
    global _RECIPES
    dali = str(dali).strip().lower()
    before = len(_RECIPES)
    _RECIPES = [r for r in _RECIPES if r["dali"].strip().lower() != dali]
    removed = before - len(_RECIPES)
    if removed:
        _save()
    return removed


def total_grams(r):
    return round(sum(c["grams"] for c in r.get("components", [])), 2)


def as_formula(r):
    """'Trắng (265) + 215 (3.5) = BXA.1 (FCFFBE)'."""
    left = " + ".join(f"{c['name']} ({c['grams']})" for c in r.get("components", []))
    return f"{left} = {r['dali']} ({r['hex']})"
