# -*- coding: utf-8 -*-
"""ĐẾM PHỤ KIỆN TKB THEO ĐỢT (xưởng in 3D dưới quê -> gửi Hà Nội).

Vấn đề: nhân viên dưới quê in bảng thời khóa biểu nhưng phụ kiện đi kèm (mặt trời, cầu vồng, máy bay, cá heo,
mèo...) hay in thiếu/không đều -> khi gom gửi lên Hà Nội không đủ bộ. Trang /dot-in cho nhân viên:
  - đếm SỐ BẢNG từng mẫu (Bầu trời, Đại dương, Mèo con...) và SỐ PHỤ KIỆN từng loại đã in;
  - hệ thống so với ĐỊNH MỨC (mỗi bảng cần bao nhiêu mỗi phụ kiện) -> báo ĐỦ BAO NHIÊU BỘ + THIẾU MÓN NÀO
    (kèm ước lượng số KHAY in bù);
  - xong đợt: "Gửi lên Hà Nội" -> lưu lịch sử đợt (phiếu gửi) rồi đưa tất cả về 0 làm đợt mới.
Thẻ môn: không đếm theo môn, chỉ đếm "Bộ thẻ môn" (số bộ) dùng chung cho mọi mẫu bảng.

Lưu JSON `runtime/dotin/dotin.json` (KHÔNG model/migration — đúng quy ước), ghi dưới KHOÁ FILE (nhiều người đếm cùng
lúc không ghi đè nhau; cộng/trừ là thao tác nguyên tử phía server). Đặt ở module riêng để khỏi đụng views.py.
"""
import contextlib
import csv
import io
import json
import math
import os
import re
import secrets
import shutil
import time

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.utils import timezone

from pha.views import staff_required

_SUB = 'dotin'
_FILE = 'dotin.json'
_MAX_LOG = 300
_MAX_HIST = 200
_MAX_VAL = 1000000

# Danh mục MẶC ĐỊNH (lần đầu). Chủ chốt 21/11/2026: phụ kiện KHÔNG tách lẻ từng món mà gom thành GÓI PHỤ KIỆN đi kèm
# từng mẫu bảng (gói Bầu trời đi với bảng Bầu trời...) -> mỗi mẫu 1 mục "Gói phụ kiện", định mức 1 gói/bảng.
# Dưới xưởng Cường tự biết in bao nhiêu gói/khay nên KHÔNG ước lượng khay (khay=0). Chủ vẫn sửa được ở
# mục "Danh mục & định mức" trên trang (vẫn thêm được phụ kiện lẻ nếu sau này cần).
_DEFAULT_MAU = [
    {'key': 'bt', 'ten': 'Bầu trời', 'mau': '#3FA9F5', 'phu_kien': [
        {'key': 'bt__goi', 'ten': 'Gói phụ kiện', 'dm': 1, 'khay': 0},
    ]},
    {'key': 'dd', 'ten': 'Đại dương', 'mau': '#17A2B8', 'phu_kien': [
        {'key': 'dd__goi', 'ten': 'Gói phụ kiện', 'dm': 1, 'khay': 0},
    ]},
    {'key': 'meo', 'ten': 'Mèo con', 'mau': '#F59F00', 'phu_kien': [
        {'key': 'meo__goi', 'ten': 'Gói phụ kiện', 'dm': 1, 'khay': 0},
    ]},
]
_DEFAULT_CHUNG = [
    {'key': 'chung__bothemon', 'ten': 'Bộ thẻ môn', 'dm': 1, 'khay': 0},
]


# ------------------------------------------------------------------ lưu trữ + khoá file
def _dir():
    # runtime/dotin (KHÔNG để trong media/: nginx phục vụ /media/ công khai -> lộ lịch sử gửi, người gửi, mã vận đơn;
    # runtime/ cũng nằm trong bản sao lưu mặc định và đã được .gitignore). Cùng cách giaoviec.py.
    d = getattr(settings, 'DOTIN_DIR', None) or os.environ.get('DOTIN_DIR') \
        or os.path.join(str(settings.BASE_DIR), 'runtime', _SUB)
    os.makedirs(d, exist_ok=True)
    return d


def _path():
    return os.path.join(_dir(), _FILE)


@contextlib.contextmanager
def _lock():
    """Khoá liên tiến trình (gunicorn nhiều worker): flock trên Linux, file O_EXCL nơi không có fcntl."""
    base = os.path.join(_dir(), 'dotin.lock')
    try:
        import fcntl
    except ImportError:
        fcntl = None
    if fcntl is not None:
        fd = os.open(base, os.O_CREAT | os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)
        return
    try:
        import msvcrt                   # Windows (máy dev): khoá vùng byte của file — không có chuyện "cướp khoá"
    except ImportError:
        msvcrt = None
    if msvcrt is not None:
        fd = os.open(base, os.O_CREAT | os.O_RDWR)
        got = False
        try:
            deadline = time.time() + 20
            while True:
                try:
                    os.lseek(fd, 0, 0)
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                    got = True
                    break
                except OSError:
                    if time.time() > deadline:
                        raise RuntimeError('Khoá dữ liệu đang bận, thử lại sau giây lát.')
                    time.sleep(0.02)
            yield
        finally:
            try:
                if got:
                    os.lseek(fd, 0, 0)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            finally:
                os.close(fd)
        return
    x = base + '.x'
    deadline = time.time() + 10
    while True:
        try:
            os.close(os.open(x, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            break
        except FileExistsError:
            try:
                if time.time() - os.path.getmtime(x) > 15:      # khoá mồ côi
                    os.remove(x)
            except OSError:
                pass
            if time.time() > deadline:
                raise RuntimeError('Khoá dữ liệu đang bận, thử lại sau giây lát.')
            time.sleep(0.03)
    try:
        yield
    finally:
        try:
            os.remove(x)
        except OSError:
            pass


def _now_iso():
    return timezone.localtime(timezone.now()).strftime('%Y-%m-%dT%H:%M:%S')


def _new_state():
    import copy
    return {
        'v': 1,
        'mau': copy.deepcopy(_DEFAULT_MAU),
        'chung': copy.deepcopy(_DEFAULT_CHUNG),
        'dem': {}, 'muc_tieu': {},
        'dot': {'id': 1, 'bat_dau': _now_iso()},
        'lich_su': [], 'nhat_ky': [],
    }


def _doc_file(p):
    for i in range(4):                  # Windows: phần mềm diệt virus có thể giữ file vài chục ms
        try:
            with open(p, encoding='utf-8') as f:
                st = json.load(f)
            break
        except PermissionError:
            if i == 3:
                raise
            time.sleep(0.05)
    if not isinstance(st, dict) or not isinstance(st.get('mau'), list):
        raise ValueError('hỏng')
    return st


def _cat_file_hong(p):
    """Cất file hỏng sang dotin.corrupt-<giờ>.json để KHÔNG bị ghi đè mất (còn để chủ cứu)."""
    try:
        os.replace(p, p[:-5] + '.corrupt-%s.json' % time.strftime('%Y%m%d-%H%M%S'))
    except OSError:
        pass


def _load():
    """Đọc trạng thái. CHỈ 'chưa có file' mới coi là mới; file hỏng -> cất lại + dùng bản .bak gần nhất;
    lỗi I/O khác (khoá, quyền...) -> báo lỗi thay vì lặng lẽ khởi tạo lại rồi ghi đè dữ liệu thật."""
    p = _path()
    try:
        st = _doc_file(p)
    except FileNotFoundError:
        try:
            st = _doc_file(p + '.bak')
        except (OSError, ValueError):
            return _new_state()
    except ValueError:                      # JSON hỏng / sai cấu trúc (JSONDecodeError cũng là ValueError)
        _cat_file_hong(p)
        try:
            st = _doc_file(p + '.bak')
        except (OSError, ValueError):
            return _new_state()
    st.setdefault('chung', [])
    for k, d in (('dem', {}), ('muc_tieu', {}), ('lich_su', []), ('nhat_ky', [])):
        if not isinstance(st.get(k), type(d)):
            st[k] = d
    if not isinstance(st.get('dot'), dict) or not st['dot'].get('id'):
        st['dot'] = {'id': 1, 'bat_dau': _now_iso()}
    return st


def _save(st):
    st['nhat_ky'] = st['nhat_ky'][-_MAX_LOG:]
    st['lich_su'] = st['lich_su'][-_MAX_HIST:]
    p = _path()
    tmp = p + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(st, f, ensure_ascii=False)
        f.flush()
        try:
            os.fsync(f.fileno())        # mất điện giữa chừng cũng không để file rỗng
        except OSError:
            pass
    if os.path.exists(p):               # giữ bản liền trước làm .bak (cứu khi file chính hỏng)
        try:
            shutil.copyfile(p, p + '.bak')
        except OSError:
            pass
    for i in range(6):                  # ghi nguyên tử: không bao giờ để lại file nửa chừng
        try:
            os.replace(tmp, p)
            break
        except PermissionError:         # Windows: file đích đang bị quét/giữ tạm
            if i == 5:
                raise
            time.sleep(0.05)


def _int(v):
    """int() chịu mọi đầu vào xấu (None, chuỗi, Infinity, 1e400, list...) -> luôn ValueError."""
    if isinstance(v, bool) or v is None:
        raise ValueError('không phải số')
    try:
        return int(v)
    except (TypeError, ValueError, OverflowError):
        raise ValueError('không phải số')


# ------------------------------------------------------------------ tính ĐỦ / THIẾU
def _bang_key(m):
    return m['key'] + '__bang'


def _all_keys(st):
    """Mọi khoá đếm hợp lệ: bảng từng mẫu, phụ kiện, thẻ môn chung, mục tiêu từng mẫu (mt__<mẫu>)."""
    ks = set()
    for m in st['mau']:
        ks.add(_bang_key(m))
        ks.add('mt__' + m['key'])
        for p in m['phu_kien']:
            ks.add(p['key'])
    for c in st['chung']:
        ks.add(c['key'])
    return ks


def _val(st, key):
    if key.startswith('mt__'):
        return int(st['muc_tieu'].get(key[4:], 0) or 0)
    return int(st['dem'].get(key, 0) or 0)


def _set(st, key, v):
    v = max(0, min(_MAX_VAL, int(v)))
    if key.startswith('mt__'):
        if v:
            st['muc_tieu'][key[4:]] = v
        else:
            st['muc_tieu'].pop(key[4:], None)
    else:
        if v:
            st['dem'][key] = v
        else:
            st['dem'].pop(key, None)
    return v


def _tinh(st):
    """Kết quả cho giao diện: từng mẫu (bảng, mục tiêu, phụ kiện co/can/thieu/du), thẻ môn chung, tóm tắt."""
    dem, mt = st['dem'], st['muc_tieu']
    mau_out, thieu_list = [], []
    tong_bang = tong_bo_du = tong_can = 0
    bang_can_in = []
    for m in st['mau']:
        bang = int(dem.get(_bang_key(m), 0) or 0)
        muc = int(mt.get(m['key'], 0) or 0)
        can_bang = max(bang, muc)
        bo_du = bang
        pks = []
        for p in m['phu_kien']:
            co = int(dem.get(p['key'], 0) or 0)
            dm = int(p.get('dm') or 0)
            khay = int(p.get('khay') or 0)
            can = can_bang * dm
            thieu = max(0, can - co)
            du = max(0, co - can) if can_bang > 0 else 0
            if dm > 0:
                bo_du = min(bo_du, co // dm)
            so_khay = int(math.ceil(thieu / float(khay))) if (khay > 0 and thieu > 0) else 0
            pks.append({'key': p['key'], 'ten': p['ten'], 'dm': dm, 'khay': khay,
                        'co': co, 'can': can, 'thieu': thieu, 'du': du, 'so_khay': so_khay})
            if thieu > 0:
                thieu_list.append({'mau': m['ten'], 'ten': p['ten'], 'thieu': thieu, 'khay': khay, 'so_khay': so_khay})
        if muc > bang:
            bang_can_in.append({'mau': m['ten'], 'thieu': muc - bang})
        mau_out.append({'key': m['key'], 'ten': m['ten'], 'mau': m.get('mau') or '#0F9C9C',
                        'bang_key': _bang_key(m), 'bang': bang, 'muc_tieu': muc, 'can_bang': can_bang,
                        'bang_thieu': max(0, muc - bang), 'bo_du': bo_du, 'phu_kien': pks})
        tong_bang += bang
        tong_bo_du += bo_du
        tong_can += can_bang
    chung_out, gioi_han = [], None
    for c in st['chung']:
        co = int(dem.get(c['key'], 0) or 0)
        dm = int(c.get('dm') or 0)
        can = tong_can * dm
        thieu = max(0, can - co)
        if dm > 0:
            gh = co // dm
            gioi_han = gh if gioi_han is None else min(gioi_han, gh)
        chung_out.append({'key': c['key'], 'ten': c['ten'], 'dm': dm, 'co': co, 'can': can, 'thieu': thieu,
                          'du': max(0, co - can) if tong_can > 0 else 0})
        if thieu > 0:
            thieu_list.append({'mau': 'Chung', 'ten': c['ten'], 'thieu': thieu, 'khay': 0, 'so_khay': 0})
    gui_duoc = tong_bo_du if gioi_han is None else min(tong_bo_du, gioi_han)
    tong_dem = sum(int(v or 0) for v in dem.values())
    return {
        'dot': st['dot'], 'mau': mau_out, 'chung': chung_out,
        'tom_tat': {'tong_bang': tong_bang, 'bo_du': tong_bo_du, 'gui_duoc': gui_duoc, 'tong_dem': tong_dem,
                    'thieu': thieu_list, 'bang_can_in': bang_can_in,
                    'du_het': tong_bang > 0 and not thieu_list},
    }


def _rows_phieu(st):
    """Danh sách dòng phiếu gửi (nhóm, tên, số lượng) từ số đếm HIỆN TẠI — chụp vào lịch sử khi gửi."""
    rows = []
    for m in st['mau']:
        rows.append({'nhom': m['ten'], 'ten': 'Bảng', 'sl': int(st['dem'].get(_bang_key(m), 0) or 0)})
        for p in m['phu_kien']:
            rows.append({'nhom': m['ten'], 'ten': p['ten'], 'sl': int(st['dem'].get(p['key'], 0) or 0)})
    for c in st['chung']:
        rows.append({'nhom': 'Chung', 'ten': c['ten'], 'sl': int(st['dem'].get(c['key'], 0) or 0)})
    return rows


def _lich_su_tom(st):
    """Bản rút gọn lịch sử cho giao diện (mới nhất trước), kèm dòng phiếu."""
    out = []
    for e in reversed(st['lich_su']):
        out.append({k: e.get(k) for k in ('id', 'dot_id', 'bat_dau', 'gui_luc', 'nguoi', 'ghi_chu', 'tong_bang', 'bo_du',
                                            'rows')})
    return out


def _payload(st):
    d = _tinh(st)
    d['lich_su'] = _lich_su_tom(st)
    d['nhat_ky'] = list(reversed(st['nhat_ky'][-40:]))
    last = st['lich_su'][-1] if st['lich_su'] else None
    dang_trong = d['tom_tat']['tong_dem'] == 0 and not st['muc_tieu']
    d['co_the_khoi_phuc'] = bool(last and dang_trong and last.get('dot_id') == st['dot']['id'] - 1)
    return d


def _user(request):
    return getattr(request.user, 'first_name', '') or getattr(request.user, 'username', '') or ''


def _ghi_nk(st, request, loai, key='', ten='', truoc=None, sau=None, ghi_chu=''):
    st['nhat_ky'].append({'t': _now_iso(), 'nguoi': _user(request), 'loai': loai, 'key': key, 'ten': ten,
                          'truoc': truoc, 'sau': sau, 'ghi_chu': ghi_chu[:300]})


def _ten_key(st, key):
    if key.startswith('mt__'):
        for m in st['mau']:
            if m['key'] == key[4:]:
                return '%s — mục tiêu' % m['ten']
    for m in st['mau']:
        if _bang_key(m) == key:
            return '%s — Bảng' % m['ten']
        for p in m['phu_kien']:
            if p['key'] == key:
                return '%s — %s' % (m['ten'], p['ten'])
    for c in st['chung']:
        if c['key'] == key:
            return c['ten']
    return key


def _json_body(request):
    try:
        d = json.loads(request.body.decode('utf-8') or '{}')
        return d if isinstance(d, dict) else {}
    except Exception:
        return None


def _err(msg, status=200, **kw):
    return JsonResponse(dict({'ok': False, 'error': msg}, **kw), status=status)


def _kiem_dot(st, d):
    """Máy khác còn hiện ĐỢT CŨ (vừa có người Gửi) mà vẫn bấm +/gõ số -> chặn, để số không rơi vào đợt mới.
    Client gửi kèm dot_id; không gửi (bản trang cũ) thì bỏ qua kiểm."""
    raw = d.get('dot_id')
    if raw is None or raw == '':
        return None
    try:
        did = _int(raw)
    except ValueError:
        return _err('Mã đợt không hợp lệ.', 400)
    if did != int(st['dot']['id']):
        return _err('Vừa có người Gửi lên Hà Nội — trang đang hiện đợt cũ. Đã tải lại số mới, bấm lại giúp.', 409,
                    reload=True)
    return None


# ------------------------------------------------------------------ trang + API
@login_required(login_url='/login')
def dot_in(request):
    with _lock():
        st = _load()
    return render(request, 'dot_in.html', {'init': _payload(st), 'is_chu': bool(request.user.is_staff)})


@login_required(login_url='/login')
def dot_in_tt(request):
    with _lock():
        st = _load()
    return JsonResponse({'ok': True, **_payload(st)})


@login_required(login_url='/login')
def dot_in_cong(request):
    """Cộng/trừ NGUYÊN TỬ 1 ô đếm. Body {key, delta}. Không bao giờ âm."""
    if request.method != 'POST':
        return _err('POST', 405)
    d = _json_body(request)
    if d is None:
        return _err('JSON hỏng', 400)
    key = str(d.get('key') or '')
    try:
        delta = _int(d.get('delta'))
    except ValueError:
        return _err('Số lượng không hợp lệ.')
    if abs(delta) > _MAX_VAL:
        return _err('Số quá lớn.')
    with _lock():
        st = _load()
        loi = _kiem_dot(st, d)
        if loi:
            return loi
        if key not in _all_keys(st):
            return _err('Ô đếm không còn trong danh mục (chủ vừa sửa danh mục?). Tải lại trang.', 409, reload=True)
        truoc = _val(st, key)
        sau = _set(st, key, truoc + delta)
        if sau != truoc:
            _ghi_nk(st, request, 'cong', key, _ten_key(st, key), truoc, sau)
        _save(st)
        return JsonResponse({'ok': True, 'key': key, 'value': sau, **_payload(st)})


@login_required(login_url='/login')
def dot_in_dat(request):
    """Đặt số tuyệt đối cho 1 ô (gõ tay). Body {key, value}."""
    if request.method != 'POST':
        return _err('POST', 405)
    d = _json_body(request)
    if d is None:
        return _err('JSON hỏng', 400)
    key = str(d.get('key') or '')
    try:
        value = _int(d.get('value'))
    except ValueError:
        return _err('Số lượng không hợp lệ.')
    if value < 0 or value > _MAX_VAL:
        return _err('Số phải từ 0 đến %d.' % _MAX_VAL)
    with _lock():
        st = _load()
        loi = _kiem_dot(st, d)
        if loi:
            return loi
        if key not in _all_keys(st):
            return _err('Ô đếm không còn trong danh mục. Tải lại trang.', 409, reload=True)
        truoc = _val(st, key)
        sau = _set(st, key, value)
        if sau != truoc:
            _ghi_nk(st, request, 'dat', key, _ten_key(st, key), truoc, sau)
        _save(st)
        return JsonResponse({'ok': True, 'key': key, 'value': sau, **_payload(st)})


@login_required(login_url='/login')
def dot_in_gui(request):
    """GỬI LÊN HÀ NỘI: chụp phiếu vào lịch sử rồi đưa toàn bộ đếm + mục tiêu về 0, mở đợt mới.
    Body {dot_id, ghi_chu}. dot_id chống bấm 2 lần / 2 máy cùng gửi."""
    if request.method != 'POST':
        return _err('POST', 405)
    d = _json_body(request)
    if d is None:
        return _err('JSON hỏng', 400)
    try:
        dot_id = _int(d.get('dot_id'))
    except ValueError:
        return _err('Mã đợt không hợp lệ.', 400)
    with _lock():
        st = _load()
        if dot_id != int(st['dot']['id']):
            return _err('Đợt này đã được gửi rồi (hoặc đã đổi). Đã tải lại để xem đợt mới.', 409, reload=True)
        t = _tinh(st)
        if t['tom_tat']['tong_dem'] == 0:
            return _err('Chưa đếm gì trong đợt này nên chưa có gì để gửi.')
        rec = {
            'id': 'G%d' % int(time.time() * 1000),
            'dot_id': st['dot']['id'], 'bat_dau': st['dot']['bat_dau'], 'gui_luc': _now_iso(),
            'nguoi': _user(request), 'ghi_chu': str(d.get('ghi_chu') or '')[:300],
            'tong_bang': t['tom_tat']['tong_bang'], 'bo_du': t['tom_tat']['gui_duoc'],
            'rows': _rows_phieu(st),
            'dem': dict(st['dem']), 'muc_tieu': dict(st['muc_tieu']),
        }
        st['lich_su'].append(rec)
        st['dem'], st['muc_tieu'] = {}, {}
        st['dot'] = {'id': st['dot']['id'] + 1, 'bat_dau': rec['gui_luc']}
        _ghi_nk(st, request, 'gui', '', 'Gửi lên Hà Nội — đợt #%d' % rec['dot_id'], None, None, rec['ghi_chu'])
        _save(st)
        return JsonResponse({'ok': True, 'entry_id': rec['id'], **_payload(st)})


@login_required(login_url='/login')
def dot_in_khoi_phuc(request):
    """Hoàn tác lần GỬI gần nhất (bấm nhầm): chỉ khi đợt mới còn trống. Body {id}."""
    if request.method != 'POST':
        return _err('POST', 405)
    d = _json_body(request)
    if d is None:
        return _err('JSON hỏng', 400)
    with _lock():
        st = _load()
        last = st['lich_su'][-1] if st['lich_su'] else None
        if not last or last.get('id') != d.get('id'):
            return _err('Chỉ khôi phục được lần gửi GẦN NHẤT.')
        cur = _tinh(st)
        if cur['tom_tat']['tong_dem'] > 0 or st['muc_tieu']:
            return _err('Đợt mới đã có số liệu — không khôi phục để khỏi ghi đè. Xóa hết số đếm của đợt mới trước.')
        # chỉ trả lại các ô CÒN trong danh mục hiện tại (chủ có thể đã xóa mục -> không để số đếm mồ côi vô hình)
        keep = _all_keys(st)
        st['dem'] = {k: v for k, v in (last.get('dem') or {}).items() if k in keep and not k.startswith('mt__')}
        st['muc_tieu'] = {k: v for k, v in (last.get('muc_tieu') or {}).items() if 'mt__' + k in keep}
        st['dot'] = {'id': last['dot_id'], 'bat_dau': last['bat_dau']}
        st['lich_su'].pop()
        _ghi_nk(st, request, 'khoi-phuc', '', 'Khôi phục đợt #%d (hoàn tác Gửi)' % last['dot_id'])
        _save(st)
        return JsonResponse({'ok': True, **_payload(st)})


def _slug(s):
    """'Bầu trời' -> 'bautroi' (bỏ dấu tiếng Việt, chỉ a-z0-9)."""
    import unicodedata
    s = unicodedata.normalize('NFD', (s or '').lower().replace('đ', 'd'))
    s = ''.join(ch for ch in s if unicodedata.category(ch) != 'Mn')
    s = re.sub(r'[^0-9a-z]+', '', s)
    return s or secrets.token_hex(2)


def _chuan_hoa_danh_muc(d, st):
    """Kiểm + chuẩn hoá danh mục chủ gửi lên; giữ khoá cũ để số đếm không mất, tạo khoá cho mục mới.
    Ném ValueError(thông điệp) nếu dữ liệu sai kiểu / vượt giới hạn (KHÔNG lặng lẽ cắt bớt rồi mất số đếm)."""
    def nz(v, hi):
        try:
            return max(0, min(hi, _int(v)))
        except ValueError:
            return 0

    ds_mau = d.get('mau') if d.get('mau') is not None else []
    ds_chung = d.get('chung') if d.get('chung') is not None else []
    if not isinstance(ds_mau, list) or not isinstance(ds_chung, list):
        raise ValueError('Danh mục sai định dạng.')
    if len(ds_mau) > 20 or len(ds_chung) > 15:
        raise ValueError('Quá nhiều mục (tối đa 20 mẫu, 15 mục dùng chung).')
    reserved_mau = {'mt', 'chung'}          # tiền tố khoá hệ thống (mt__<mẫu> = mục tiêu, chung__* = dùng chung)

    mau_new, used = [], set()
    for m in ds_mau:
        if not isinstance(m, dict):
            raise ValueError('Danh mục sai định dạng.')
        ten = str(m.get('ten') or '').strip()[:40]
        if not ten:
            continue
        mk = str(m.get('key') or '')
        if not re.match(r'^[a-z0-9]{1,24}$', mk) or mk in used or mk in reserved_mau:
            mk = _slug(ten)[:20]
            while mk in used or mk in reserved_mau:
                mk += secrets.token_hex(1)
        used.add(mk)
        ds_pk = m.get('phu_kien') if m.get('phu_kien') is not None else []
        if not isinstance(ds_pk, list):
            raise ValueError('Danh mục sai định dạng.')
        if len(ds_pk) > 30:
            raise ValueError('Mẫu "%s" có quá nhiều phụ kiện (tối đa 30).' % ten)
        pks, pk_used = [], {mk + '__bang'}  # khoá bảng của chính mẫu là khoá dành riêng
        for p in ds_pk:
            if not isinstance(p, dict):
                raise ValueError('Danh mục sai định dạng.')
            pt = str(p.get('ten') or '').strip()[:40]
            if not pt:
                continue
            pk = str(p.get('key') or '')
            if not pk.startswith(mk + '__') or pk in pk_used or not re.match(r'^[a-z0-9_]{3,60}$', pk):
                base = mk + '__' + _slug(pt)[:24]
                pk, n = base, 2
                while pk in pk_used:
                    pk, n = '%s%d' % (base, n), n + 1
            pk_used.add(pk)
            pks.append({'key': pk, 'ten': pt, 'dm': nz(p.get('dm'), 1000), 'khay': nz(p.get('khay'), 5000)})
        col = str(m.get('mau') or '')
        mau_new.append({'key': mk, 'ten': ten, 'mau': col if re.match(r'^#[0-9a-fA-F]{6}$', col) else '#0F9C9C',
                        'phu_kien': pks})
    chung_new, c_used = [], set()
    for c in ds_chung:
        if not isinstance(c, dict):
            raise ValueError('Danh mục sai định dạng.')
        ct = str(c.get('ten') or '').strip()[:40]
        if not ct:
            continue
        ck = str(c.get('key') or '')
        if not ck.startswith('chung__') or ck in c_used or not re.match(r'^[a-z0-9_]{8,60}$', ck):
            base = 'chung__' + _slug(ct)[:24]
            ck, n = base, 2
            while ck in c_used:
                ck, n = '%s%d' % (base, n), n + 1
        c_used.add(ck)
        chung_new.append({'key': ck, 'ten': ct, 'dm': nz(c.get('dm'), 1000), 'khay': 0})
    return mau_new, chung_new


@staff_required
def dot_in_danh_muc(request):
    """CHỦ sửa danh mục mẫu bảng + phụ kiện + định mức + khay. Body {mau:[...], chung:[...], confirm_drop?}
    (thay toàn bộ). Số đếm của mục còn giữ khoá được GIỮ. Mục bị xóa mà ĐANG CÓ số đếm: trả 409 kèm danh sách
    `mat`, chỉ xóa thật khi gửi lại với confirm_drop=true (và số mất được ghi vào nhật ký)."""
    if request.method != 'POST':
        return _err('POST', 405)
    d = _json_body(request)
    if d is None:
        return _err('JSON hỏng', 400)
    with _lock():
        st = _load()
        try:
            mau_new, chung_new = _chuan_hoa_danh_muc(d, st)
        except ValueError as e:
            return _err(str(e), 400)
        if not mau_new:
            return _err('Cần ít nhất 1 mẫu bảng.')
        mat = []                                        # số đếm sẽ mất vì mục bị xóa (đặt tên theo danh mục CŨ)
        keep = _all_keys(dict(st, mau=mau_new, chung=chung_new))
        for k, v in list(st['dem'].items()):
            if int(v or 0) > 0 and (k not in keep or k.startswith('mt__')):
                mat.append({'ten': _ten_key(st, k), 'sl': int(v)})
        for k, v in list(st['muc_tieu'].items()):
            if int(v or 0) > 0 and ('mt__' + k) not in keep:
                mat.append({'ten': _ten_key(st, 'mt__' + k), 'sl': int(v)})
        if mat and d.get('confirm_drop') is not True:
            return _err('Xóa mục đang có số đếm sẽ MẤT các số này.', 409, mat=mat)
        st['mau'], st['chung'] = mau_new, chung_new
        st['dem'] = {k: v for k, v in st['dem'].items() if k in keep and not k.startswith('mt__')}
        st['muc_tieu'] = {k: v for k, v in st['muc_tieu'].items() if 'mt__' + k in keep}
        ghi = ('Bỏ số đếm: ' + '; '.join('%s=%d' % (m['ten'], m['sl']) for m in mat)) if mat else ''
        _ghi_nk(st, request, 'mau', '', 'Sửa danh mục & định mức', ghi_chu=ghi)
        _save(st)
        return JsonResponse({'ok': True, **_payload(st)})


def _csv_response(rows, name):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(['Nhóm', 'Mục', 'Số lượng'])
    for r in rows:
        w.writerow([r['nhom'], r['ten'], r['sl']])
    resp = HttpResponse('﻿' + buf.getvalue(), content_type='text/csv; charset=utf-8')
    resp['Content-Disposition'] = 'attachment; filename="%s"' % name
    return resp


@login_required(login_url='/login')
def dot_in_xuat(request):
    """Tải CSV phiếu: ?id=hien-tai (số đếm đang có) hoặc ?id=<mã lần gửi>."""
    rid = request.GET.get('id') or 'hien-tai'
    with _lock():
        st = _load()
    if rid == 'hien-tai':
        return _csv_response(_rows_phieu(st), 'phu-kien-TKB-dot-%d-hien-tai.csv' % st['dot']['id'])
    for e in st['lich_su']:
        if e.get('id') == rid:
            return _csv_response(e.get('rows') or [], 'phieu-gui-ha-noi-dot-%s.csv' % e.get('dot_id'))
    return HttpResponse('Không tìm thấy phiếu.', status=404)
