"""
Quản lý CÔNG THỨC PHA — lưu trong DATABASE (model Recipe).

Trước đây lưu trong recipes.json; nay chuyển sang DB để an toàn khi nhiều người
sửa cùng lúc (tránh hỏng file). Giữ nguyên các hàm cũ để view không phải đổi.
"""
from pha.models import Recipe


def reload_recipes():
    return Recipe.objects.count()


def _to_dict(r):
    return {'dali': r.dali, 'hex': r.hex, 'components': r.components or []}


def get_all():
    return [_to_dict(r) for r in Recipe.objects.all()]   # ordering trong Meta


def add_recipe(dali, hex_value, components):
    dali = str(dali).strip()
    hex_value = str(hex_value).strip().lstrip('#').upper()
    if not dali:
        return False, "Thiếu mã màu DALI."
    clean = []
    for c in components:
        name = str(c.get('name', '')).strip()
        try:
            g = float(c.get('grams'))
        except (TypeError, ValueError):
            continue
        if name and g > 0:
            clean.append({'name': name, 'grams': round(g, 2)})
    if not clean:
        return False, "Chưa nhập màu gốc và khối lượng."

    existing = Recipe.objects.filter(dali__iexact=dali).first()
    if existing:
        existing.dali = dali
        existing.hex = hex_value
        existing.components = clean
        existing.save()
        return True, f"Đã cập nhật công thức {dali}."
    Recipe.objects.create(dali=dali, hex=hex_value, components=clean)
    return True, f"Đã lưu công thức {dali}."


def delete_recipe(dali):
    n, _ = Recipe.objects.filter(dali__iexact=str(dali).strip()).delete()
    return n


def total_grams(r):
    return round(sum(c['grams'] for c in r.get('components', [])), 2)


def as_formula(r):
    left = " + ".join(f"{c['name']} ({c['grams']})" for c in r.get('components', []))
    return f"{left} = {r['dali']} ({r['hex']})"
