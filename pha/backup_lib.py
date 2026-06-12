# -*- coding: utf-8 -*-
"""SAO LƯU TỰ ĐỘNG — bảo hiểm cho db.sqlite3 (chứa toàn bộ: công thức, màu gốc,
chấm công, lịch sử rót, cấu hình, khoá API).

- Bản sao lưu = file .zip trong thư mục backups/ (gitignore): db.sqlite3 (snapshot an toàn
  bằng sqlite3 backup API — không hỏng khi đang ghi) + tuỳ chọn toàn bộ media/.
- Cron mỗi đêm:  0 2 * * *  curl -s "https://mau.tranhdali.vn/sao-luu-chay?key=<BACKUP_KEY>" >/dev/null
- Trang /sao-luu (quản lý): danh sách bản sao lưu, tải về, bấm "Sao lưu ngay".
- Tự giữ 14 bản mới nhất, bản cũ hơn tự xoá.
"""
import hmac
import os
import sqlite3
import zipfile

from django.conf import settings
from django.http import FileResponse, Http404, JsonResponse
from django.views.decorators.csrf import csrf_exempt

KEEP = 14
BACKUP_DIR = os.path.join(str(settings.BASE_DIR), 'backups')


def _backup_key():
    from pha.models import AppSetting
    k = (AppSetting.get('BACKUP_KEY', '') or '').strip()
    if not k:
        import secrets
        k = 'bk_' + secrets.token_urlsafe(16)
        AppSetting.set('BACKUP_KEY', k)
    return k


def _snapshot_db(dest_path):
    """Chụp db.sqlite3 an toàn bằng sqlite3 backup API (không copy file thô khi đang ghi)."""
    db_path = str(settings.DATABASES['default']['NAME'])
    src = sqlite3.connect(db_path)
    try:
        dst = sqlite3.connect(dest_path)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()


def run_backup(include_media=False):
    """Tạo 1 bản sao lưu .zip; trả (tên file, kích thước bytes). Tự dọn bản cũ."""
    from pha.views import _now
    os.makedirs(BACKUP_DIR, exist_ok=True)
    stamp = _now().strftime('%Y-%m-%d_%H%M')
    name = f'dali_backup_{stamp}.zip'
    out = os.path.join(BACKUP_DIR, name)

    tmp_db = os.path.join(BACKUP_DIR, f'_snap_{stamp}.sqlite3')
    _snapshot_db(tmp_db)
    try:
        with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
            z.write(tmp_db, 'db.sqlite3')
            if include_media:
                mroot = str(settings.MEDIA_ROOT)
                for root, _dirs, files in os.walk(mroot):
                    for f in files:
                        p = os.path.join(root, f)
                        z.write(p, os.path.join('media', os.path.relpath(p, mroot)))
    finally:
        try:
            os.remove(tmp_db)
        except OSError:
            pass

    # Giữ KEEP bản mới nhất
    zips = sorted(f for f in os.listdir(BACKUP_DIR) if f.endswith('.zip'))
    for old in zips[:-KEEP]:
        try:
            os.remove(os.path.join(BACKUP_DIR, old))
        except OSError:
            pass
    return name, os.path.getsize(out)


def list_backups():
    if not os.path.isdir(BACKUP_DIR):
        return []
    out = []
    for f in sorted(os.listdir(BACKUP_DIR), reverse=True):
        if f.endswith('.zip'):
            p = os.path.join(BACKUP_DIR, f)
            out.append({'name': f, 'size_mb': round(os.path.getsize(p) / 1048576, 2)})
    return out


@csrf_exempt
def backup_run_view(request):
    """Chạy sao lưu: cron dùng ?key=BACKUP_KEY; quản lý đăng nhập bấm nút (?media=1 kèm ảnh)."""
    is_staff = bool(getattr(request.user, 'is_staff', False))
    key = request.headers.get('X-API-Key') or request.GET.get('key', '')
    if not is_staff and not hmac.compare_digest(str(key), str(_backup_key())):
        return JsonResponse({'ok': False, 'error': 'Sai khoá'}, status=401)
    try:
        name, size = run_backup(include_media=(request.GET.get('media') == '1'))
    except Exception as e:
        return JsonResponse({'ok': False, 'error': str(e)[:160]})
    return JsonResponse({'ok': True, 'file': name, 'size_mb': round(size / 1048576, 2),
                         'kept': len(list_backups())})


def backup_page(request):
    """Trang quản lý sao lưu (chỉ quản lý)."""
    from django.shortcuts import render, redirect
    if not getattr(request.user, 'is_staff', False):
        return redirect('/login')
    return render(request, 'sao_luu.html', {
        'backups': list_backups(),
        'backup_key': _backup_key(),
        'api_base': request.scheme + '://' + request.get_host(),
    })


def backup_download(request):
    """Tải 1 bản sao lưu về máy (chỉ quản lý)."""
    if not getattr(request.user, 'is_staff', False):
        raise Http404
    f = os.path.basename(request.GET.get('f', '') or '')
    if not (f.startswith('dali_backup_') and f.endswith('.zip')):
        raise Http404
    p = os.path.join(BACKUP_DIR, f)
    if not os.path.exists(p):
        raise Http404
    return FileResponse(open(p, 'rb'), as_attachment=True, filename=f)
