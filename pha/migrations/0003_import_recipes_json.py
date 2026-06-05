"""Tự nhập công thức từ recipes.json (nếu có) vào DB — chạy 1 lần khi migrate."""
import json
import os

from django.db import migrations


def load_json(apps, schema_editor):
    Recipe = apps.get_model('pha', 'Recipe')
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'recipes.json')
    if not os.path.exists(path):
        return
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception:
        return
    for r in data:
        dali = str(r.get('dali', '')).strip()
        if not dali:
            continue
        if Recipe.objects.filter(dali__iexact=dali).exists():
            continue
        Recipe.objects.create(
            dali=dali,
            hex=str(r.get('hex', '')).lstrip('#').upper(),
            components=r.get('components', []) or [],
        )


class Migration(migrations.Migration):
    dependencies = [('pha', '0002_recipe')]
    operations = [migrations.RunPython(load_json, migrations.RunPython.noop)]
