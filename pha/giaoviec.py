# -*- coding: utf-8 -*-
"""GIAO VIỆC THEO PHÒNG BAN (/giao-viec) — bắt chước luồng "giao rót":
quản lý giao -> push -> người làm thấy trong "Việc của tôi" -> bấm 1 chạm đổi trạng thái.

Đặt ở module RIÊNG (không đụng views.py). KHÔNG model/migration — lưu JSON:

- SEED (file kế hoạch): chứa dữ liệu NỘI BỘ -> TUYỆT ĐỐI KHÔNG để trong git (repo CÔNG KHAI;
  .gitignore đã chặn *giaoviec*seed*.json, seed_*.json). Quản trị tải lên ở tab Cài đặt
  (api/tai-seed: chỉ JSON, ≤2MB, kiểm cấu trúc, tên file do server đặt) -> lưu
  runtime/giaoviec/seed_<chuong_trinh>.json -> "Xem trước" -> "Áp dụng" (hợp nhất theo id).
  Sinh seed từ bản kế hoạch bằng tools/tao_seed_giaoviec.py (ghi RA NGOÀI repo).
- RUNTIME (gitignore): <BASE_DIR>/runtime/giaoviec/
    seed_<ct>.json kế hoạch đã tải lên (danh sách phòng lấy từ đây)
    viec.json      {phien, chuong_trinh{}, viec{id: việc}}
    phong.json     {phong{SH:{truong[],thanh_vien[]}}, nguoi{u:{ten}}, quan_tri[], chu[], bi_danh{}}
    ma.json        bảng mã chính thức (ghi đè seed)
    nhat_ky.jsonl  nhật ký ghi nối
    da_nhac.json   sổ chống nhắc lặp
  CỐ Ý KHÔNG để dưới media/: nginx (và urls.py) phục vụ /media/ CÔNG KHAI, không cần đăng nhập.
  Đổi chỗ được bằng settings.GIAOVIEC_DIR hoặc biến môi trường GIAOVIEC_DIR.
- Ghi an toàn: khoá fcntl.flock (3 worker gunicorn) + ghi file tạm rồi os.replace + giữ .bak;
  đọc chịu lỗi (strict=False, hỏng -> đọc .bak -> rỗng; KHÔNG trả 500).

Quyền (phía server, không tin client):
- Mọi tài khoản đã đăng nhập: xem việc mọi phòng (tiêu đề/hạn/trạng thái), đổi trạng thái +
  checklist + ghi chú + xin lùi hạn cho việc MÌNH được giao.
- Trưởng phòng (theo phong.json): giao/đổi người + đổi trạng thái mọi việc trong phòng mình.
- Quản trị giao việc: superuser + danh sách quan_tri trong phong.json. Danh sách quan_tri TRỐNG
  -> CHỈ superuser là quản trị (staff thường KHÔNG tự thành quản trị).
- Chủ (phong.json 'chu', trống = superuser): người DUY NHẤT duyệt việc "Cổng" (quản trị không
  duyệt thay). Danh sách 'chu' chỉ superuser được sửa. Chủ duyệt / trả lại / ghi chú việc Cổng
  KỂ CẢ khi không được giao việc đó, và xem được bảng Toàn xưởng.
- "Việc Cổng" là CỜ CỐ ĐỊNH (t['la_cong']) đặt lúc tạo / nạp kế hoạch — không suy lại từ tiêu đề
  (quản trị sửa tiêu đề cũng không gỡ được bước chủ duyệt).

Push: tái dùng pha.push (_send_one). Gửi khi bấm Giao / Thông báo / đổi trạng thái có ý nghĩa
(chờ duyệt, duyệt, mở khoá việc sau), xin/duyệt lùi hạn, ghi chú. "Hoàn tác" (cờ hoan_tac, chỉ
hợp lệ khi đảo đúng thay đổi vừa làm của chính mình trong 2 phút) thì KHÔNG gửi push.
Nhắc hạn: `python manage.py giaoviec_nhac` (KHUYẾN NGHỊ, chạy bằng cron của www-data) hoặc
/giao-viec/nhac?key=... (dự phòng: khoá nằm trên URL nên có thể lộ ở log) — KHÔNG tự gửi khi tải trang.
"""
import copy
import datetime as dt
import hmac
import json
import logging
import os
import re
import threading
import time
import unicodedata
from collections import defaultdict
from contextlib import contextmanager

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render

try:
    import fcntl  # Linux (VPS)
except ImportError:  # Windows dev: chỉ khoá trong tiến trình
    fcntl = None

log = logging.getLogger(__name__)

# Kế hoạch (seed) do quản trị TẢI LÊN -> runtime/giaoviec/seed_<ct>.json (không bao giờ nằm trong git)
RE_CT = re.compile(r'^[a-z0-9_]{1,40}$')
RE_SEED_FILE = re.compile(r'^seed_([a-z0-9_]{1,40})\.json$')
RE_PHONG = re.compile(r'^[A-Z]{2}$')
SEED_TOI_DA = 2 * 1024 * 1024          # 2MB
CT_TAY = 'tay'                         # chương trình của việc thêm tay khi chưa có kế hoạch nào
HOAN_TAC_GIAY = 120                    # cửa sổ hợp lệ của "Hoàn tác"

TRANG_THAI = ('chua_lam', 'dang_lam', 'cho_duyet', 'xong', 'tam_dung')
TT_TEN = {'chua_lam': 'Chưa làm', 'dang_lam': 'Đang làm', 'cho_duyet': 'Chờ duyệt',
          'xong': 'Xong', 'tam_dung': 'Tạm dừng'}
DONG = ('xong', 'tam_dung')          # việc không còn phải làm
BI_DANH_MAC_DINH = {'claude': 'Trợ lý Claude'}
BAO_MAT_PHONG = {'KT'}                # chi tiết việc KT (số liệu tài chính): chỉ KT + quản trị (chờ chủ chốt)
TRUONG_CHU = ('tieu_de', 'chi_tiet', 'san_pham', 'cong_cu', 'phu_thuoc', 'ma_tam',
              'dung_chatgpt', 'dai_han', 'goi_y_nguoi', 'phong')
THU = ['T2', 'T3', 'T4', 'T5', 'T6', 'T7', 'CN']
RE_MA = re.compile(r'^[A-Z]{1,3}\d{1,3}$')
RE_ID = re.compile(r'^[A-Z]{2}-\d{2,3}$')


class _Loi(Exception):
    def __init__(self, msg, status=400):
        super().__init__(msg)
        self.status = status


# =============================== LƯU TRỮ ===============================
def _dir():
    d = getattr(settings, 'GIAOVIEC_DIR', None) or os.environ.get('GIAOVIEC_DIR') \
        or os.path.join(str(settings.BASE_DIR), 'runtime', 'giaoviec')
    os.makedirs(d, exist_ok=True)
    return d


def _p(name):
    return os.path.join(_dir(), name)


_TLOCK = threading.Lock()
_TL = threading.local()


@contextmanager
def _lock():
    """Khoá ghi cho cả thư mục (giữa các worker gunicorn). Lồng nhau trong cùng luồng thì bỏ qua."""
    if getattr(_TL, 'depth', 0):
        _TL.depth += 1
        try:
            yield
        finally:
            _TL.depth -= 1
        return
    with _TLOCK:
        fd = None
        if fcntl is not None:
            # O_RDONLY: flock vẫn chạy, và www-data mở được kể cả khi file do root (cron) tạo
            fd = os.open(_p('.lock'), os.O_RDONLY | os.O_CREAT, 0o664)
            fcntl.flock(fd, fcntl.LOCK_EX)
        _TL.depth = 1
        try:
            yield
        finally:
            _TL.depth = 0
            if fd is not None:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                finally:
                    os.close(fd)


_CACHE = {}


def _read_json(name, default, cache=False):
    """Đọc JSON chịu lỗi: file hỏng -> thử .bak -> default. cache=True: dùng lại theo mtime
    (CHỈ cho đường đọc, không được sửa object trả về)."""
    path = _p(name)
    if cache:
        try:
            st = os.stat(path)
            hit = _CACHE.get(path)
            if hit and hit[0] == (st.st_mtime_ns, st.st_size):
                return hit[1]
        except OSError:
            pass
    for cand in (path, path + '.bak'):
        try:
            with open(cand, encoding='utf-8') as f:
                data = json.loads(f.read(), strict=False)
            if not isinstance(data, type(default)):
                continue
            if cache and cand == path:
                try:
                    st = os.stat(path)
                    _CACHE[path] = ((st.st_mtime_ns, st.st_size), data)
                except OSError:
                    pass
            return data
        except Exception:
            continue
    return default


def _write_json(name, data):
    """Ghi atomic: file tạm -> (bản cũ thành .bak) -> os.replace."""
    path = _p(name)
    tmp = '%s.%d.tmp' % (path, os.getpid())
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, separators=(',', ':'))
        f.flush()
        try:
            os.fsync(f.fileno())
        except OSError:
            pass
    if os.path.exists(path):
        try:
            os.replace(path, path + '.bak')   # lúc giữa 2 lệnh, người đọc tự lấy .bak
        except OSError:
            pass
    os.replace(tmp, path)


def _load_viec(cache=False):
    d = _read_json('viec.json', {}, cache=cache)
    if not isinstance(d.get('viec'), dict):
        d = {'phien': 0, 'chuong_trinh': {}, 'viec': {}}
    return d


def _phong_trong():
    return {'phong': {}, 'nguoi': {}, 'quan_tri': [], 'chu': [], 'bi_danh': dict(BI_DANH_MAC_DINH)}


def _load_phong(cache=False):
    d = _read_json('phong.json', {}, cache=cache)
    base = _phong_trong()
    for k in base:
        if isinstance(d.get(k), type(base[k])):
            # CHÉP SÂU: d có thể là object trong _CACHE — chuẩn hoá bên dưới không được sửa vào cache
            base[k] = copy.deepcopy(d[k])
    for m in _phong_ma():
        p = base['phong'].get(m)
        if not isinstance(p, dict):
            p = {}
        base['phong'][m] = {'truong': list(p.get('truong') or []),
                            'thanh_vien': list(p.get('thanh_vien') or [])}
    if not base['bi_danh']:
        base['bi_danh'] = dict(BI_DANH_MAC_DINH)
    return base


def _seed_file(ct):
    """Tên file seed do SERVER đặt từ mã chương trình đã kiểm (không bao giờ theo tên file người gửi)."""
    return 'seed_%s.json' % ct if isinstance(ct, str) and RE_CT.match(ct) else None


def _ds_ct():
    """Các chương trình đã tải kế hoạch lên, cũ -> mới (theo lúc tải)."""
    out = []
    try:
        names = os.listdir(_dir())
    except OSError:
        return []
    for f in names:
        m = RE_SEED_FILE.match(f)
        if m:
            try:
                out.append((os.stat(_p(f)).st_mtime_ns, m.group(1)))
            except OSError:
                pass
    return [ct for _, ct in sorted(out)]


def _ct_mac_dinh():
    ds = _ds_ct()
    return ds[-1] if ds else ''


def _seed(ct=None):
    """Seed đã tải lên của chương trình ct (mặc định: bản tải lên gần nhất). Không có -> None."""
    name = _seed_file(ct if ct is not None else _ct_mac_dinh())
    if not name:
        return None
    path = _p(name)
    try:
        st = os.stat(path)
        hit = _CACHE.get(path)
        if hit and hit[0] == (st.st_mtime_ns, st.st_size):
            return hit[1]
        with open(path, encoding='utf-8') as f:
            data = json.loads(f.read(), strict=False)
        if not isinstance(data, dict):
            return None
        _CACHE[path] = ((st.st_mtime_ns, st.st_size), data)
        return data
    except Exception:
        return None


def _phong_ds():
    """Danh sách phòng = hợp các seed đã tải lên (bản mới ghi đè tên/nhiệm vụ; giữ thứ tự xuất hiện)."""
    by, thu_tu = {}, []
    for ct in _ds_ct():
        for p in ((_seed(ct) or {}).get('phong') or []):
            if not isinstance(p, dict) or not p.get('ma'):
                continue
            if p['ma'] not in by:
                thu_tu.append(p['ma'])
            by[p['ma']] = {'ma': p['ma'], 'ten': p.get('ten', ''), 'nhiem_vu': p.get('nhiem_vu', ''),
                           'vai_tro': p.get('vai_tro', '')}
    return [by[m] for m in thu_tu]


def _phong_ma():
    return [p['ma'] for p in _phong_ds()]


def _load_ma():
    mm, dk = {}, {}
    for ct in _ds_ct():
        s = _seed(ct) or {}
        if isinstance(s.get('ma_map'), dict):
            mm.update(s['ma_map'])
        if isinstance(s.get('ma_du_kien'), dict):
            dk.update(s['ma_du_kien'])
    d = _read_json('ma.json', {}, cache=True)       # bảng mã sửa trên trang: ghi đè seed
    if isinstance(d.get('ma_map'), dict):
        mm = d['ma_map']
    if isinstance(d.get('ma_du_kien'), dict):
        dk = d['ma_du_kien']
    return {'ma_map': mm, 'ma_du_kien': dk}


_GH_PHONG = (('ten', 120, True), ('nhiem_vu', 4000, False), ('vai_tro', 4000, False))
_GH_VIEC = (('tieu_de', 300, True), ('chi_tiet', 8000, False), ('san_pham', 2000, False), ('cong_cu', 300, False))


def _ngay_chat(s):
    if not isinstance(s, str) or len(s) != 10:
        return None
    try:
        return dt.date.fromisoformat(s)
    except ValueError:
        return None


def kiem_seed(d, ct_mac_dinh=''):
    """Kiểm + chuẩn hoá file kế hoạch tải lên. Trả (seed_chuẩn, []) hoặc (None, [lỗi...]).
    Cấu trúc: {chuong_trinh, ten, phien_ban, phong:[{ma,ten,nhiem_vu,vai_tro}],
               viec:[{id,phong,tieu_de,chi_tiet,bat_dau,han,phu_thuoc[],san_pham,cong_cu,
                      dung_chatgpt,ma_tam[],goi_y_nguoi[],dai_han}], ma_map{}, ma_du_kien{}}"""
    loi = []

    def L(msg):
        if len(loi) < 15:
            loi.append(msg)

    if not isinstance(d, dict):
        return None, ['File phải là 1 object JSON {chuong_trinh, ten, phong[], viec[]}.']
    ct = str(d.get('chuong_trinh') or ct_mac_dinh or '').strip().lower()
    if not RE_CT.match(ct):
        L('chuong_trinh phải gồm chữ thường/số/_ (1–40 ký tự), ví dụ "tet2027".')
    ten = d.get('ten') or ct
    if not isinstance(ten, str) or len(ten) > 120:
        L('ten phải là chuỗi ≤120 ký tự.')
    pb = d.get('phien_ban', 1)
    if isinstance(pb, bool) or not isinstance(pb, int):
        L('phien_ban phải là số nguyên.')

    phong_in = d.get('phong')
    if not isinstance(phong_in, list) or not phong_in or len(phong_in) > 30:
        L('Thiếu "phong": danh sách 1–30 phòng ({ma, ten, ...}).')
        phong_in = phong_in if isinstance(phong_in, list) else []
    phong, ma_ph = [], set()
    for i, p in enumerate(phong_in):
        if not isinstance(p, dict):
            L('phong[%d] không phải object.' % i)
            continue
        ma = p.get('ma')
        if not isinstance(ma, str) or not RE_PHONG.match(ma):
            L('phong[%d].ma "%s" phải là 2 chữ IN HOA (vd SH).' % (i, str(ma)[:10]))
            continue
        if ma in ma_ph:
            L('Trùng mã phòng %s.' % ma)
            continue
        x = {'ma': ma}
        for k, toi_da, bat_buoc in _GH_PHONG:
            v = p.get(k)
            v = '' if v is None else v
            if not isinstance(v, str) or len(v) > toi_da or (bat_buoc and not v.strip()):
                L('Phòng %s: "%s" phải là chuỗi%s ≤%d ký tự.' % (ma, k, ' không trống' if bat_buoc else '', toi_da))
                v = ''
            x[k] = v
        ma_ph.add(ma)
        phong.append(x)

    viec_in = d.get('viec')
    if not isinstance(viec_in, list) or not viec_in or len(viec_in) > 3000:
        L('Thiếu "viec": danh sách 1–3000 việc.')
        viec_in = viec_in if isinstance(viec_in, list) else []
    tat_ca = {v.get('id') for v in viec_in if isinstance(v, dict) and isinstance(v.get('id'), str)}
    viec, ids = [], set()
    for i, v in enumerate(viec_in):
        if not isinstance(v, dict):
            L('viec[%d] không phải object.' % i)
            continue
        vid = v.get('id')
        if not isinstance(vid, str) or not RE_ID.match(vid):
            L('viec[%d].id "%s" phải dạng AB-01.' % (i, str(vid)[:20]))
            continue
        if vid in ids:
            L('Trùng mã việc %s.' % vid)
            continue
        ids.add(vid)
        ph = v.get('phong')
        if ph not in ma_ph:
            L('%s: phòng "%s" không có trong danh sách "phong".' % (vid, str(ph)[:10]))
            continue
        if vid.split('-')[0] != ph:
            L('%s: mã việc phải bắt đầu bằng mã phòng %s.' % (vid, ph))
        if int(vid.split('-')[1]) > 100:
            L('%s: số thứ tự phải ≤100 (từ 101 dành cho việc thêm tay).' % vid)
        x = {'id': vid, 'phong': ph}
        for k, toi_da, bat_buoc in _GH_VIEC:
            s = v.get(k)
            s = '' if s is None else s
            if not isinstance(s, str) or len(s) > toi_da or (bat_buoc and not s.strip()):
                L('%s: "%s" phải là chuỗi%s ≤%d ký tự.' % (vid, k, ' không trống' if bat_buoc else '', toi_da))
                s = ''
            x[k] = s
        han = _ngay_chat(v.get('han'))
        if not han:
            L('%s: "han" phải là ngày YYYY-MM-DD.' % vid)
        bd = han if v.get('bat_dau') in (None, '') else _ngay_chat(v.get('bat_dau'))
        if v.get('bat_dau') not in (None, '') and not bd:
            L('%s: "bat_dau" phải là ngày YYYY-MM-DD.' % vid)
        if bd and han and bd > han:
            L('%s: ngày bắt đầu sau hạn.' % vid)
        x['bat_dau'] = bd.isoformat() if bd else None
        x['han'] = han.isoformat() if han else None
        pt = v.get('phu_thuoc')
        pt = [] if pt is None else pt
        if not isinstance(pt, list) or not all(isinstance(y, str) for y in pt):
            L('%s: "phu_thuoc" phải là danh sách mã việc.' % vid)
            pt = []
        sai = [y for y in pt if y not in tat_ca or y == vid]
        if sai:
            L('%s: phụ thuộc không tồn tại: %s' % (vid, ', '.join(sai[:5])))
        x['phu_thuoc'] = list(pt)
        for k in ('ma_tam', 'goi_y_nguoi'):
            arr = v.get(k)
            arr = [] if arr is None else arr
            if not isinstance(arr, list) or len(arr) > 50 or not all(isinstance(y, str) and len(y) <= 40 for y in arr):
                L('%s: "%s" phải là danh sách chuỗi ngắn.' % (vid, k))
                arr = []
            x[k] = list(arr)
        x['dung_chatgpt'] = bool(v.get('dung_chatgpt'))
        x['dai_han'] = bool(v.get('dai_han'))
        viec.append(x)

    bang = {}
    for k in ('ma_map', 'ma_du_kien'):
        m = d.get(k)
        m = {} if m is None else m
        if not isinstance(m, dict):
            L('"%s" phải là object {mã tạm: mã chính thức}.' % k)
            m = {}
        bang[k] = {}
        for a, b in m.items():
            if not (isinstance(b, str) and RE_MA.match(str(a)) and RE_MA.match(b)):
                L('%s: mã không hợp lệ %s → %s' % (k, str(a)[:12], str(b)[:12]))
                continue
            bang[k][a] = b
    if loi:
        return None, loi
    out = {'chuong_trinh': ct, 'ten': ten, 'phien_ban': pb, 'phong': phong, 'viec': viec, **bang}
    for k in ('dot', 'moc', 'lich_mau'):          # thông tin thêm của bản kế hoạch (chưa dùng) — giữ nếu là list
        if isinstance(d.get(k), list):
            out[k] = d[k]
    return out, []


def _ghi_nhat_ky(rows):
    if not rows:
        return
    try:
        with open(_p('nhat_ky.jsonl'), 'a', encoding='utf-8') as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + '\n')
    except OSError:
        pass


def _doc_nhat_ky(vid, toi_da=60):
    path = _p('nhat_ky.jsonl')
    out = []
    try:
        size = os.path.getsize(path)
        with open(path, 'rb') as f:
            if size > 4 * 1024 * 1024:
                f.seek(size - 4 * 1024 * 1024)
                f.readline()
            for line in f:
                try:
                    r = json.loads(line.decode('utf-8'), strict=False)
                except Exception:
                    continue
                if r.get('viec') == vid:
                    out.append(r)
    except OSError:
        return []
    return out[-toi_da:]


# =============================== NGÀY ===============================
def _now():
    from pha.views import _now as vnow
    return vnow()


def _today():
    return _now().date()


def _d(s):
    try:
        return dt.date.fromisoformat(str(s)[:10])
    except Exception:
        return None


def _fmt(d):
    d = _d(d) if not isinstance(d, dt.date) else d
    return '%s %02d/%02d' % (THU[d.weekday()], d.day, d.month) if d else ''


class _Lich:
    """Ngày làm việc theo cấu hình chấm công (views._att_cfg + ngày lễ). Lỗi -> T2–T7."""

    def __init__(self):
        try:
            from pha.views import _att_cfg, _holiday_name
            self.workdays = _att_cfg()['workdays']
            self._hol = _holiday_name
        except Exception:
            self.workdays = {0, 1, 2, 3, 4, 5}
            self._hol = lambda day: ''
        self._memo = {}

    def la_ngay_lam(self, d):
        if d not in self._memo:
            try:
                le = self._hol(d.isoformat())
            except Exception:
                le = ''
            self._memo[d] = (d.weekday() in self.workdays) and not le
        return self._memo[d]

    def ke_tiep(self, d):
        x = d + dt.timedelta(days=1)
        for _ in range(30):
            if self.la_ngay_lam(x):
                return x
            x += dt.timedelta(days=1)
        return d + dt.timedelta(days=1)

    def so_ngay_lam_giua(self, a, b):
        """Số ngày làm việc trong (a, b]."""
        n, x = 0, a
        while x < b and n < 400:
            x += dt.timedelta(days=1)
            if self.la_ngay_lam(x):
                n += 1
        return n


# =============================== VAI TRÒ ===============================
def _superusers():
    try:
        from django.contrib.auth.models import User
        return list(User.objects.filter(is_superuser=True, is_active=True)
                    .values_list('username', flat=True))
    except Exception:
        return []


def _ds_quan_tri(pcfg):
    return list(pcfg.get('quan_tri') or []) or _superusers()


def _ds_chu(pcfg):
    return list(pcfg.get('chu') or []) or _superusers()


def _vai_tro(user, pcfg=None):
    pcfg = pcfg or _load_phong(cache=True)
    u = user.username
    # Quản trị = superuser + danh sách quan_tri. Danh sách trống -> CHỈ superuser (staff KHÔNG tự thành quản trị).
    la_qt = bool(user.is_superuser or u in (pcfg.get('quan_tri') or []))
    truong = [m for m, p in pcfg['phong'].items() if u in p.get('truong', [])]
    thuoc = [m for m, p in pcfg['phong'].items()
             if u in p.get('truong', []) or u in p.get('thanh_vien', [])]
    # Chủ (duyệt việc Cổng) = superuser + danh sách chu. Quản trị KHÔNG tự có quyền duyệt.
    la_chu = bool(user.is_superuser or u in (pcfg.get('chu') or []))
    return {'username': u, 'la_quan_tri': la_qt, 'la_chu': la_chu, 'super': bool(user.is_superuser),
            'truong_phong': truong, 'phong': thuoc,
            # chủ cần bảng Toàn xưởng để thấy danh sách "Cổng chờ chủ duyệt"
            'xem_tong': la_qt or la_chu or bool(truong) or bool(user.is_staff),
            'ten': _ten(u, pcfg, user)}


def _ten(u, pcfg, user=None):
    t = ((pcfg.get('nguoi') or {}).get(u) or {}).get('ten')
    if t:
        return t
    if u in (pcfg.get('bi_danh') or {}):
        return pcfg['bi_danh'][u]
    if user is not None:
        try:
            return user.get_full_name() or u
        except Exception:
            pass
    return u


def _quyen_sua(vt, t):
    return vt['la_quan_tri'] or t.get('phong') in vt['truong_phong'] or vt['username'] in (t.get('nguoi') or [])


def _quyen_giao(vt, t):
    return vt['la_quan_tri'] or t.get('phong') in vt['truong_phong']


def _chu_duyet(vt, t):
    """Chủ với việc Cổng: được duyệt / trả lại / ghi chú dù KHÔNG được giao việc đó."""
    return bool(vt['la_chu'] and _la_cong(t))


def _xem_chi_tiet(vt, t):
    if t.get('phong') not in BAO_MAT_PHONG:
        return True
    return vt['la_quan_tri'] or t.get('phong') in vt['phong'] or vt['username'] in (t.get('nguoi') or []) \
        or _chu_duyet(vt, t)          # chủ phải xem được nội dung việc Cổng mình duyệt


# =============================== TÍNH TOÁN ===============================
def _cong_tu_noi_dung(t):
    """Suy 'việc Cổng' từ nội dung — CHỈ dùng lúc tạo / nạp (và cho việc cũ chưa có cờ la_cong)."""
    # chuẩn hoá NFC: tiêu đề gõ bằng Unicode tổ hợp (Unikey chế độ tổ hợp) vẫn nhận ra chữ "Cổng"
    td = unicodedata.normalize('NFC', str(t.get('tieu_de', '')))
    return td.startswith('Cổng') or 'chu' in (t.get('goi_y_nguoi') or [])


def _la_cong(t):
    """Cờ cố định đặt lúc tạo / nạp. KHÔNG suy lại từ tiêu đề đang có (tiêu đề sửa được)."""
    if 'la_cong' in t:
        return bool(t['la_cong'])
    return _cong_tu_noi_dung(t)       # dữ liệu cũ (trước khi có cờ): suy 1 lần, api_sua/nạp sẽ chốt lại


def _nguoc(viec):
    """id -> [id việc phụ thuộc vào nó]."""
    out = defaultdict(list)
    for vid, t in viec.items():
        if t.get('an'):
            continue
        for x in t.get('phu_thuoc') or []:
            out[x].append(vid)
    return out


def _xin_lui_cho(vt, t):
    """Yêu cầu lùi hạn trả cho người xem: lý do có thể chứa số liệu nội bộ (vd việc KT) ->
    người không xem được chi tiết việc chỉ thấy CÓ yêu cầu + hạn mới. vt=None -> ẩn (mặc định an toàn)."""
    x = t.get('xin_lui_han')
    if not x or not isinstance(x, dict):
        return None
    if vt is not None and _xem_chi_tiet(vt, t):
        return x
    return {'han_moi': x.get('han_moi'), 'ai': x.get('ai'), 'luc': x.get('luc'), 'ly_do': '', 'an_ly_do': True}


def _tom_tat(t, viec, nguoc, today, vt=None):
    han = _d(t.get('han')) or today
    con = (han - today).days
    tt = t.get('trang_thai', 'chua_lam')
    dong = tt in DONG
    cho = [x for x in (t.get('phu_thuoc') or [])
           if x in viec and not viec[x].get('an') and viec[x].get('trang_thai') != 'xong']
    bd = _d(t.get('bat_dau')) or today
    bi_chan = bool(cho) and not dong and bd <= today
    tre = (not dong) and con < 0
    if tt == 'xong':
        nhom = 'xong'
    elif tt == 'tam_dung':
        nhom = 'tam_dung'
    elif tre:
        nhom = 'tre'
    elif con <= 1:
        nhom = 'gan'
    elif cho:
        nhom = 'cho'
    elif con <= 7:
        nhom = 'sap'
    else:
        nhom = 'sau'
    ck = t.get('checklist') or []
    return {
        'id': t['id'], 'phong': t.get('phong'), 'tieu_de': t.get('tieu_de', ''),
        'bat_dau': t.get('bat_dau'), 'han': t.get('han'), 'han_goc': t.get('han_goc'),
        'trang_thai': tt, 'nguoi': list(t.get('nguoi') or []), 'ma_tam': t.get('ma_tam') or [],
        'dung_chatgpt': bool(t.get('dung_chatgpt')), 'dai_han': bool(t.get('dai_han')),
        'la_cong': _la_cong(t), 'rev': t.get('rev', 0), 'an': bool(t.get('an')),
        'tre': tre, 'so_ngay_con': con, 'cho': cho, 'bi_chan': bi_chan,
        'mo_khoa': list(nguoc.get(t['id'], [])), 'nhom': nhom,
        'ck_xong': sum(1 for c in ck if c.get('xong')), 'ck_tong': len(ck),
        'n_ghi_chu': len(t.get('ghi_chu') or []),
        'xin_lui_han': _xin_lui_cho(vt, t),
        'cap_nhat_luc': t.get('cap_nhat_luc', ''), 'cap_nhat_boi': t.get('cap_nhat_boi', ''),
    }


def dem_viec(username):
    """Số cho badge: {mo, tre, han_mai} của 1 người. Đọc rẻ (cache theo mtime), lỗi -> 0."""
    out = {'mo': 0, 'tre': 0, 'han_mai': 0}
    try:
        if not username or not os.path.exists(_p('viec.json')):
            return out
        data = _load_viec(cache=True)
        today = _today()
        for t in data['viec'].values():
            if t.get('an') or t.get('trang_thai') in DONG or username not in (t.get('nguoi') or []):
                continue
            out['mo'] += 1
            han = _d(t.get('han'))
            if han and han < today:
                out['tre'] += 1
            elif han and (han - today).days <= 1:
                out['han_mai'] += 1
    except Exception:
        pass
    return out


# =============================== PUSH ===============================
def _push(users, title, body, url='/giao-viec', tag='gv'):
    """Gửi 1 thông báo cho danh sách username (bỏ bí danh). Trả số máy đã gửi. Lỗi -> 0."""
    try:
        from pha import push
        if not push.is_available():
            return 0
        from pha.models import PushSubscription
        pcfg = _load_phong(cache=True)
        bd = set(pcfg.get('bi_danh') or {}) | set(BI_DANH_MAC_DINH)
        us = sorted({u for u in users if u and u not in bd})
        if not us:
            return 0
        payload = json.dumps({'title': title, 'body': body[:240], 'url': url, 'tag': tag,
                              'icon': '/media/icon-192.png'}, ensure_ascii=False)
        n = 0
        for s in PushSubscription.objects.filter(username__in=us):
            push._send_one(s, payload)
            n += 1
        return n
    except Exception:
        return 0


def _gui(outbox):
    """outbox: [(users, title, body, url, tag)] — gọi SAU khi nhả khoá."""
    n = 0
    for item in outbox:
        n += _push(*item)
    return n


def _gop_giao(moi_theo_nguoi, viec, actor):
    """{u: [id...]} -> outbox (1 push/người; nhiều việc thì gộp)."""
    out = []
    for u, ids in moi_theo_nguoi.items():
        if u == actor or not ids:
            continue
        ids = sorted(set(ids), key=lambda i: viec[i].get('han', ''))
        if len(ids) == 1:
            t = viec[ids[0]]
            out.append(([u], '📌 Việc mới %s' % t['id'],
                        '%s · hạn %s' % (t.get('tieu_de', ''), _fmt(t.get('han'))),
                        '/giao-viec?viec=%s' % t['id'], 'gv-%s' % t['id']))
        else:
            out.append(([u], '📌 Bạn được giao %d việc' % len(ids),
                        '%s · hạn sớm nhất %s' % (', '.join(ids[:6]) + ('…' if len(ids) > 6 else ''),
                                                 _fmt(viec[ids[0]].get('han'))),
                        '/giao-viec?tab=toi', 'gv-giao'))
    return out


# =============================== TRANG ===============================
@login_required(login_url='/login')
def trang(request):
    vt = _vai_tro(request.user)
    boot = {
        'toi': vt, 'phong': _phong_ds(), 'tt_ten': TT_TEN,
        'tab': request.GET.get('tab') or 'toi', 'viec': request.GET.get('viec') or '',
    }
    return render(request, 'giao_viec.html', {'boot': boot})


def manifest(request):
    data = {
        "name": "DALI Giao việc", "short_name": "Giao việc",
        "start_url": "/giao-viec", "scope": "/", "display": "standalone", "orientation": "portrait",
        "background_color": "#ffffff", "theme_color": "#198754",
        "icons": [
            {"src": "/media/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any maskable"},
            {"src": "/media/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"},
        ],
        "shortcuts": [
            {"name": "Việc của tôi", "short_name": "Của tôi", "url": "/giao-viec?tab=toi"},
            {"name": "Theo phòng", "short_name": "Phòng", "url": "/giao-viec?tab=phong"},
        ],
    }
    return JsonResponse(data, content_type='application/manifest+json')


# =============================== API ===============================
def _body(request):
    try:
        d = json.loads(request.body.decode('utf-8') or '{}')
        if not isinstance(d, dict):
            raise ValueError
        return d
    except Exception:
        raise _Loi('Dữ liệu gửi lên không đọc được (JSON hỏng).')


def _can_qt(vt):
    if not vt['la_quan_tri']:
        raise _Loi('Chỉ quản trị giao việc được làm thao tác này.', 403)


def _lay(viec, vid):
    t = viec.get(str(vid or ''))
    if not t:
        raise _Loi('Không tìm thấy việc %s.' % vid, 404)
    return t


def _cham(t, user):
    t['rev'] = int(t.get('rev', 0)) + 1
    t['cap_nhat_luc'] = _now().strftime('%Y-%m-%d %H:%M')
    t['cap_nhat_boi'] = user


def _nk(ai, vid, hd, tu='', den=''):
    return {'luc': _now().strftime('%Y-%m-%d %H:%M:%S'), 'ai': ai, 'viec': vid, 'hd': hd,
            'tu': tu, 'den': den}


def api_ds(request, vt):
    data = _load_viec(cache=True)
    viec = data['viec']
    today = _today()
    nguoc = _nguoc(viec)
    hien_an = request.GET.get('an') == '1' and vt['la_quan_tri']
    out = [_tom_tat(t, viec, nguoc, today, vt) for t in viec.values() if hien_an or not t.get('an')]
    out.sort(key=lambda x: (x['han'] or '', x['id']))
    pcfg = _load_phong(cache=True)
    nguoi = {}
    try:
        from django.contrib.auth.models import User
        for u in User.objects.filter(is_active=True).order_by('username'):
            nguoi[u.username] = _ten(u.username, pcfg, u)
    except Exception:
        pass
    for k, v in (pcfg.get('bi_danh') or {}).items():
        nguoi[k] = v
    return JsonResponse({
        'ok': True, 'hom_nay': today.isoformat(), 'hom_nay_txt': _fmt(today),
        'toi': _vai_tro(request.user, pcfg), 'phong': _phong_ds(),
        'phan_cong': {m: p for m, p in pcfg['phong'].items()},
        'nguoi': nguoi, **_load_ma(), 'da_nap': bool(viec),
        'chuong_trinh': data.get('chuong_trinh') or {}, 'viec': out,
    })


def api_dem(request, vt):
    return JsonResponse({'ok': True, **dem_viec(request.user.username)})


def api_viec(request, vt):
    data = _load_viec(cache=True)
    viec = data['viec']
    t = _lay(viec, request.GET.get('id'))
    today = _today()
    nguoc = _nguoc(viec)
    tt = _tom_tat(t, viec, nguoc, today, vt)
    xem = _xem_chi_tiet(vt, t)
    sua = _quyen_sua(vt, t)
    chu_cong = _chu_duyet(vt, t)
    tt.update({
        'chi_tiet': t.get('chi_tiet', '') if xem else '',
        'san_pham': t.get('san_pham', '') if xem else '',
        'cong_cu': t.get('cong_cu', ''),
        'checklist': (t.get('checklist') or []) if xem else [],
        'ghi_chu': (t.get('ghi_chu') or [])[-50:] if xem else [],
        'an_chi_tiet': not xem,
        'truoc': [{'id': x, 'tieu_de': viec[x].get('tieu_de', ''), 'phong': viec[x].get('phong'),
                   'trang_thai': viec[x].get('trang_thai', 'chua_lam')}
                  for x in (t.get('phu_thuoc') or []) if x in viec],
        'sau': [{'id': x, 'tieu_de': viec[x].get('tieu_de', ''), 'phong': viec[x].get('phong'),
                 'trang_thai': viec[x].get('trang_thai', 'chua_lam')} for x in nguoc.get(t['id'], [])],
        'nhat_ky': _doc_nhat_ky(t['id']) if xem else [],
        # Chủ + việc Cổng: sua=True (duyệt / trả lại / mở lại) dù không được giao; chi_duyet=True khi chủ
        # KHÔNG được giao -> trang ẩn checklist / xin lùi hạn (server cũng chặn những thao tác đó).
        'quyen': {'sua': sua or chu_cong, 'chi_duyet': chu_cong and not sua,
                  'giao': _quyen_giao(vt, t), 'quan_tri': vt['la_quan_tri'],
                  'duyet': vt['la_chu'],
                  'nhan': (not t.get('nguoi')) and t.get('phong') in vt['phong']},
    })
    return JsonResponse({'ok': True, 'viec': tt})


def api_trang_thai(request, vt):
    d = _body(request)
    moi = d.get('trang_thai')
    if moi not in TRANG_THAI:
        raise _Loi('Trạng thái không hợp lệ.')
    user = vt['username']
    outbox, nk = [], []
    with _lock():
        data = _load_viec()
        viec = data['viec']
        t = _lay(viec, d.get('id'))
        if d.get('rev') is not None and int(d.get('rev') or 0) != int(t.get('rev', 0)):
            today = _today()
            return JsonResponse({'ok': False, 'xung_dot': True, 'error': 'Có người vừa cập nhật việc này.',
                                 'viec': _tom_tat(t, viec, _nguoc(viec), today, vt)})
        sua = _quyen_sua(vt, t)
        if not (sua or _chu_duyet(vt, t)):
            raise _Loi('Bạn không được giao việc này.', 403)
        cu = t.get('trang_thai', 'chua_lam')
        if moi == 'cho_duyet' and not _la_cong(t):
            raise _Loi('Chỉ việc Cổng mới gửi duyệt.')
        duyet = vt['la_chu']            # việc Cổng: CHỈ chủ duyệt (quản trị không duyệt thay)
        # "Hoàn tác" chỉ được im lặng (không push) khi đảo ĐÚNG thay đổi vừa làm của CHÍNH người này
        # trong HOAN_TAC_GIAY giây — không cho dùng cờ để lặng lẽ đổi trạng thái tuỳ ý.
        # Tính TRƯỚC bước đổi Xong -> Chờ duyệt, để hoàn tác "Mở lại" việc Cổng đã duyệt quay về đúng Xong.
        x = t.get('doi_tt_cuoi') or {}
        hoan_tac = bool(d.get('hoan_tac')) and x.get('ai') == user and x.get('den') == cu \
            and x.get('tu') == moi and -5 <= time.time() - float(x.get('luc') or 0) <= HOAN_TAC_GIAY
        if cu == 'cho_duyet' and moi == 'xong' and not duyet:
            raise _Loi('Việc đang chờ chủ duyệt — chỉ chủ duyệt được.', 403)
        if _la_cong(t) and moi == 'xong' and not duyet and cu != 'xong' and not hoan_tac:
            moi = 'cho_duyet'           # việc cổng: người làm chỉ gửi duyệt (đã Xong thì giữ nguyên, không hạ về Chờ duyệt)
        if moi == cu:
            return JsonResponse({'ok': True, 'viec': _tom_tat(t, viec, _nguoc(viec), _today(), vt)})
        if not sua:
            # Chủ KHÔNG được giao việc Cổng này: chỉ duyệt (Chờ duyệt -> Xong), trả lại (-> Đang làm),
            # mở lại việc đã duyệt (Xong -> Đang làm) hoặc Hoàn tác đúng thao tác vừa làm của mình —
            # không bấm Bắt đầu / Tạm dừng / Gửi duyệt thay người làm.
            if not ((cu == 'cho_duyet' and moi in ('xong', 'dang_lam')) or (cu == 'xong' and moi == 'dang_lam')
                    or hoan_tac):
                raise _Loi('Chủ chỉ duyệt / trả lại việc Cổng đang chờ duyệt (việc này không giao cho bạn).', 403)
        t['trang_thai'] = moi
        _cham(t, user)
        t['doi_tt_cuoi'] = {'ai': user, 'tu': cu, 'den': moi, 'luc': int(time.time())}
        nk.append(_nk(user, t['id'], 'hoan_tac' if hoan_tac else 'trang_thai', cu, moi))
        pcfg = _load_phong()
        lam = [u for u in (t.get('nguoi') or []) if u != user]
        if moi == 'cho_duyet':
            outbox.append(([u for u in _ds_chu(pcfg) + _ds_quan_tri(pcfg) if u != user],
                           '🔒 Chờ duyệt %s' % t['id'], t.get('tieu_de', ''),
                           '/giao-viec?viec=%s' % t['id'], 'gv-duyet-%s' % t['id']))
        elif cu == 'cho_duyet' and moi == 'xong':
            outbox.append((lam, '✅ Đã duyệt %s' % t['id'], t.get('tieu_de', ''),
                           '/giao-viec?viec=%s' % t['id'], 'gv-%s' % t['id']))
        elif cu == 'cho_duyet' and moi in ('dang_lam', 'chua_lam') and duyet:
            outbox.append((lam, '↩️ Trả lại %s' % t['id'], t.get('tieu_de', '') + ' — xem ghi chú',
                           '/giao-viec?viec=%s' % t['id'], 'gv-%s' % t['id']))
        if moi == 'xong':
            for sid in _nguoc(viec).get(t['id'], []):
                s = viec[sid]
                if s.get('trang_thai') in DONG:
                    continue
                con_cho = [x for x in s.get('phu_thuoc') or []
                           if x in viec and not viec[x].get('an') and viec[x].get('trang_thai') != 'xong']
                if not con_cho:
                    nhan = list(s.get('nguoi') or []) or pcfg['phong'].get(s.get('phong'), {}).get('truong', [])
                    outbox.append(([u for u in nhan if u != user], '🔓 Đã mở: %s' % sid,
                                   '%s làm được rồi (%s xong)' % (sid, t['id']),
                                   '/giao-viec?viec=%s' % sid, 'gv-%s' % sid))
        if hoan_tac:
            outbox = []                 # hoàn tác: không báo ai (thay đổi trước đó coi như chưa xảy ra)
        _write_json('viec.json', data)
        _ghi_nhat_ky(nk)
        res = _tom_tat(t, viec, _nguoc(viec), _today(), vt)
    _gui(outbox)
    return JsonResponse({'ok': True, 'viec': res, 'tu': cu, 'hoan_tac': hoan_tac})


def api_ghi_chu(request, vt):
    d = _body(request)
    nd = str(d.get('noi_dung') or '').strip()[:1000]
    if not nd:
        raise _Loi('Ghi chú trống.')
    user = vt['username']
    with _lock():
        data = _load_viec()
        t = _lay(data['viec'], d.get('id'))
        # chủ ghi chú được việc Cổng (cần cho "Trả lại — xem ghi chú") dù không được giao
        if not (_quyen_sua(vt, t) or _chu_duyet(vt, t) or _xem_chi_tiet(vt, t) and t.get('phong') in vt['phong']):
            raise _Loi('Bạn không có quyền ghi chú việc này.', 403)
        t.setdefault('ghi_chu', []).append({'luc': _now().strftime('%Y-%m-%d %H:%M'), 'ai': user, 'nd': nd})
        _cham(t, user)
        _write_json('viec.json', data)
        _ghi_nhat_ky([_nk(user, t['id'], 'ghi_chu', '', nd[:120])])
        pcfg = _load_phong()
        nhan = set(t.get('nguoi') or []) | set(pcfg['phong'].get(t.get('phong'), {}).get('truong', []))
        nhan.discard(user)
        ghi_chu = t['ghi_chu'][-50:]
        rev = t['rev']
    _gui([(sorted(nhan), '💬 Ghi chú %s' % t['id'], '%s: %s' % (user, nd[:150]),
           '/giao-viec?viec=%s' % t['id'], 'gv-gc-%s' % t['id'])])
    return JsonResponse({'ok': True, 'ghi_chu': ghi_chu, 'rev': rev})


def api_checklist(request, vt):
    d = _body(request)
    items = []
    for it in (d.get('items') or [])[:50]:
        if not isinstance(it, dict):
            continue
        txt = str(it.get('t') or '').strip()[:200]
        if not txt:
            continue
        x = {'t': txt, 'xong': bool(it.get('xong'))}
        if it.get('han') and _d(it.get('han')):
            x['han'] = _d(it.get('han')).isoformat()
        items.append(x)
    user = vt['username']
    with _lock():
        data = _load_viec()
        viec = data['viec']
        t = _lay(viec, d.get('id'))
        if not _quyen_sua(vt, t):
            raise _Loi('Bạn không được giao việc này.', 403)
        if d.get('rev') is not None and int(d.get('rev') or 0) != int(t.get('rev', 0)):
            return JsonResponse({'ok': False, 'xung_dot': True, 'error': 'Có người vừa cập nhật việc này.'})
        cu = t.get('checklist') or []
        t['checklist'] = items
        _cham(t, user)
        _write_json('viec.json', data)
        _ghi_nhat_ky([_nk(user, t['id'], 'checklist', '%d/%d' % (sum(c.get('xong', False) for c in cu), len(cu)),
                          '%d/%d' % (sum(c['xong'] for c in items), len(items)))])
        rev = t['rev']
    return JsonResponse({'ok': True, 'checklist': items, 'rev': rev})


def _kiem_nguoi(ds, pcfg):
    from django.contrib.auth.models import User
    ds = [str(u).strip() for u in (ds or []) if str(u).strip()]
    bd = set(pcfg.get('bi_danh') or {})
    co = set(User.objects.filter(username__in=ds).values_list('username', flat=True))
    sai = [u for u in ds if u not in co and u not in bd]
    if sai:
        raise _Loi('Không có tài khoản: %s' % ', '.join(sai))
    out = []
    for u in ds:
        if u not in out:
            out.append(u)
    return out


def api_giao(request, vt):
    """{ids[], nguoi[], thong_bao=true}. Quản trị / trưởng phòng; hoặc tự NHẬN việc chưa giao của phòng mình."""
    d = _body(request)
    ids = [str(x) for x in (d.get('ids') or [])][:200]
    if not ids:
        raise _Loi('Chưa chọn việc.')
    user = vt['username']
    thong_bao = d.get('thong_bao', True) is not False
    outbox, nk = [], []
    moi_theo_nguoi = defaultdict(list)
    with _lock():
        pcfg = _load_phong()
        nguoi = _kiem_nguoi(d.get('nguoi'), pcfg)
        data = _load_viec()
        viec = data['viec']
        for vid in ids:
            t = _lay(viec, vid)
            tu_nhan = (nguoi == [user] and not t.get('nguoi') and t.get('phong') in vt['phong'])
            if not (_quyen_giao(vt, t) or tu_nhan):
                raise _Loi('Bạn không được giao việc %s (chỉ trưởng phòng %s hoặc quản trị).'
                           % (vid, t.get('phong')), 403)
        for vid in ids:
            t = viec[vid]
            cu = list(t.get('nguoi') or [])
            if cu == nguoi:
                continue
            t['nguoi'] = list(nguoi)
            _cham(t, user)
            nk.append(_nk(user, vid, 'giao', ','.join(cu), ','.join(nguoi)))
            for u in nguoi:
                if u not in cu:
                    moi_theo_nguoi[u].append(vid)
        if nk:
            _write_json('viec.json', data)
            _ghi_nhat_ky(nk)
        if thong_bao:
            outbox = _gop_giao(moi_theo_nguoi, viec, user)
    n = _gui(outbox)
    return JsonResponse({'ok': True, 'doi': len(nk), 'so_may': n})


def api_thong_bao(request, vt):
    """Nhắc lại cho người đang được giao: {ids[]} hoặc {phong:'SH'} (việc chưa xong)."""
    d = _body(request)
    data = _load_viec(cache=True)
    viec = data['viec']
    if d.get('phong'):
        ids = [k for k, t in viec.items() if t.get('phong') == d['phong'] and not t.get('an')
               and t.get('trang_thai') not in DONG]
    else:
        ids = [str(x) for x in (d.get('ids') or []) if str(x) in viec]
    if not ids:
        raise _Loi('Không có việc nào để thông báo.')
    theo = defaultdict(list)
    for vid in ids:
        t = viec[vid]
        if not _quyen_giao(vt, t):
            raise _Loi('Bạn không quản lý việc %s.' % vid, 403)
        if t.get('trang_thai') in DONG:
            continue
        for u in t.get('nguoi') or []:
            theo[u].append(vid)
    outbox = []
    for u, vs in theo.items():
        if u == vt['username']:
            continue
        vs.sort(key=lambda i: viec[i].get('han', ''))
        body = ', '.join('%s (%s)' % (i, _fmt(viec[i].get('han'))) for i in vs[:5]) + ('…' if len(vs) > 5 else '')
        url = '/giao-viec?viec=%s' % vs[0] if len(vs) == 1 else '/giao-viec?tab=toi'
        outbox.append(([u], '🔔 Nhắc việc (%d)' % len(vs), body, url, 'gv-nhac'))
    n = _gui(outbox)
    return JsonResponse({'ok': True, 'so_nguoi': len(outbox), 'so_may': n})


def api_sua(request, vt):
    _can_qt(vt)
    d = _body(request)
    user = vt['username']
    with _lock():
        data = _load_viec()
        viec = data['viec']
        t = _lay(viec, d.get('id'))
        doi = []
        # Chốt cờ Cổng TRƯỚC khi sửa tiêu đề (việc cũ chưa có cờ): bỏ chữ "Cổng" không gỡ được bước chủ duyệt
        if 'la_cong' not in t:
            t['la_cong'] = _cong_tu_noi_dung(t)
        for k in ('tieu_de', 'chi_tiet', 'san_pham', 'cong_cu'):
            if k in d:
                v = unicodedata.normalize('NFC', str(d.get(k) or '')).strip()[:4000]
                if k == 'tieu_de' and not v:
                    raise _Loi('Tiêu đề trống.')
                if v != t.get(k, ''):
                    t[k] = v
                    doi.append(k)
        for k in ('han', 'bat_dau'):
            if k in d:
                x = _d(d.get(k))
                if not x:
                    raise _Loi('Ngày không hợp lệ.')
                if x.isoformat() != t.get(k):
                    doi.append(k)
                    if k == 'han':
                        t['han_sua_tay'] = True
                    t[k] = x.isoformat()
        if 'phu_thuoc' in d:
            pt = [str(x) for x in (d.get('phu_thuoc') or [])]
            sai = [x for x in pt if x not in viec or x == t['id']]
            if sai:
                raise _Loi('Phụ thuộc không hợp lệ: %s' % ', '.join(sai))
            if pt != t.get('phu_thuoc'):
                t['phu_thuoc'] = pt
                doi.append('phu_thuoc')
        if 'an' in d and bool(d['an']) != bool(t.get('an')):
            t['an'] = bool(d['an'])
            t['an_ly_do'] = 'tay'
            doi.append('an')
        if (_d(t.get('bat_dau')) or dt.date.min) > (_d(t.get('han')) or dt.date.max):
            raise _Loi('Ngày bắt đầu sau hạn.')
        if doi:
            _cham(t, user)
            _write_json('viec.json', data)
            _ghi_nhat_ky([_nk(user, t['id'], 'sua', '', ','.join(doi))])
        res = _tom_tat(t, viec, _nguoc(viec), _today(), vt)
    return JsonResponse({'ok': True, 'doi': doi, 'viec': res})


def api_tao(request, vt):
    _can_qt(vt)
    d = _body(request)
    phong = str(d.get('phong') or '')
    if phong not in _phong_ma():
        raise _Loi('Phòng không hợp lệ.')
    td = unicodedata.normalize('NFC', str(d.get('tieu_de') or '')).strip()[:300]
    if not td:
        raise _Loi('Nhập tiêu đề.')
    today = _today()
    bd = _d(d.get('bat_dau')) or today
    han = _d(d.get('han')) or bd
    if bd > han:
        raise _Loi('Ngày bắt đầu sau hạn.')
    user = vt['username']
    with _lock():
        pcfg = _load_phong()
        nguoi = _kiem_nguoi(d.get('nguoi'), pcfg) or list(pcfg['phong'][phong]['truong'])
        data = _load_viec()
        viec = data['viec']
        # việc thêm tay đánh số từ 101 để không đụng id của kế hoạch (seed) khi nạp lại
        so = [int(k.split('-')[1]) for k in viec if k.startswith(phong + '-') and k.split('-')[1].isdigit()]
        vid = '%s-%03d' % (phong, max([100] + [x for x in so if x > 100]) + 1)
        pt = [str(x) for x in (d.get('phu_thuoc') or []) if str(x) in viec]
        viec[vid] = {
            'id': vid, 'chuong_trinh': str(d.get('chuong_trinh') or _ct_mac_dinh() or CT_TAY)[:40], 'phong': phong,
            'tieu_de': td, 'chi_tiet': str(d.get('chi_tiet') or '')[:4000], 'bat_dau': bd.isoformat(),
            'han': han.isoformat(), 'han_goc': han.isoformat(), 'phu_thuoc': pt,
            'san_pham': str(d.get('san_pham') or '')[:1000], 'cong_cu': str(d.get('cong_cu') or '')[:200],
            'dung_chatgpt': bool(d.get('dung_chatgpt')), 'ma_tam': [], 'goi_y_nguoi': [],
            'dai_han': (han - bd).days > 14, 'nguoi': nguoi, 'trang_thai': 'chua_lam',
            'checklist': [], 'ghi_chu': [], 'rev': 0, 'an': False, 'nguon': 'tay',
            'la_cong': td.startswith('Cổng'),   # cờ cố định lúc tạo (sửa tiêu đề sau KHÔNG đổi)
        }
        _cham(viec[vid], user)
        _write_json('viec.json', data)
        _ghi_nhat_ky([_nk(user, vid, 'tao', '', td[:120])])
        outbox = _gop_giao({u: [vid] for u in nguoi}, viec, user) if d.get('thong_bao', True) else []
        res = _tom_tat(viec[vid], viec, _nguoc(viec), today, vt)
    _gui(outbox)
    return JsonResponse({'ok': True, 'viec': res})


def api_xin_lui_han(request, vt):
    d = _body(request)
    han_moi = _d(d.get('han_moi'))
    ly_do = str(d.get('ly_do') or '').strip()[:500]
    if not han_moi:
        raise _Loi('Chọn hạn mới.')
    if not ly_do:
        raise _Loi('Ghi lý do xin lùi hạn.')
    user = vt['username']
    with _lock():
        data = _load_viec()
        t = _lay(data['viec'], d.get('id'))
        if not _quyen_sua(vt, t):
            raise _Loi('Bạn không được giao việc này.', 403)
        if han_moi.isoformat() <= t.get('han', ''):
            raise _Loi('Hạn mới phải sau hạn hiện tại (%s).' % _fmt(t.get('han')))
        t['xin_lui_han'] = {'han_moi': han_moi.isoformat(), 'ly_do': ly_do, 'ai': user,
                            'luc': _now().strftime('%Y-%m-%d %H:%M')}
        _cham(t, user)
        _write_json('viec.json', data)
        _ghi_nhat_ky([_nk(user, t['id'], 'xin_lui_han', t.get('han'), han_moi.isoformat())])
        pcfg = _load_phong()
        rev = t['rev']
    _gui([([u for u in _ds_quan_tri(pcfg) if u != user], '⏳ Xin lùi hạn %s' % t['id'],
           '%s xin lùi %s → %s: %s' % (user, _fmt(t.get('han')), _fmt(han_moi), ly_do[:120]),
           '/giao-viec?viec=%s' % t['id'], 'gv-lui-%s' % t['id'])])
    return JsonResponse({'ok': True, 'rev': rev})


def api_duyet_lui_han(request, vt):
    _can_qt(vt)
    d = _body(request)
    dong_y = bool(d.get('dong_y'))
    user = vt['username']
    with _lock():
        data = _load_viec()
        viec = data['viec']
        t = _lay(viec, d.get('id'))
        x = t.get('xin_lui_han')
        if not x:
            raise _Loi('Việc này không có yêu cầu lùi hạn.')
        cu = t.get('han')
        if dong_y:
            t['han'] = x['han_moi']
            t['han_sua_tay'] = True
        t['xin_lui_han'] = None
        _cham(t, user)
        _write_json('viec.json', data)
        _ghi_nhat_ky([_nk(user, t['id'], 'duyet_lui_han' if dong_y else 'tu_choi_lui_han', cu, t['han'])])
        res = _tom_tat(t, viec, _nguoc(viec), _today(), vt)
    _gui([([x.get('ai')] if x.get('ai') != user else [],
           ('✅ Đồng ý lùi hạn %s' if dong_y else '❌ Không lùi hạn %s') % t['id'],
           'Hạn: %s' % _fmt(t['han']), '/giao-viec?viec=%s' % t['id'], 'gv-lui-%s' % t['id'])])
    return JsonResponse({'ok': True, 'viec': res})


def api_phong(request, vt):
    _can_qt(vt)          # cả đọc lẫn ghi: chỉ quản trị (người thường đã có phan_cong trong api/ds)
    if request.method == 'GET':
        pcfg = _load_phong(cache=True)
        nguoi = []
        try:
            from django.contrib.auth.models import User
            from pha.models import PushSubscription
            co_push = set(PushSubscription.objects.values_list('username', flat=True))
            for u in User.objects.filter(is_active=True).order_by('username'):
                nguoi.append({'username': u.username, 'ten': _ten(u.username, pcfg, u),
                              'staff': u.is_staff, 'super': u.is_superuser,
                              'co_push': u.username in co_push})
        except Exception:
            pass
        return JsonResponse({'ok': True, 'cfg': pcfg, 'nguoi': nguoi, 'phong': _phong_ds()})
    d = _body(request)
    with _lock():
        pcfg = _load_phong()
        ma = _phong_ma()
        moi = _phong_trong()
        moi['bi_danh'] = pcfg.get('bi_danh') or dict(BI_DANH_MAC_DINH)
        # Phòng không có trong kế hoạch hiện hành (vd file seed chưa tải lại / bị hỏng): GIỮ nguyên, không xoá
        for m, p in pcfg['phong'].items():
            if m not in ma and isinstance(p, dict):
                moi['phong'][m] = {'truong': list(p.get('truong') or []), 'thanh_vien': list(p.get('thanh_vien') or [])}
        for m in ma:
            p = (d.get('phong') or {}).get(m) or {}
            bd = moi['bi_danh']
            truong = [u for u in _kiem_nguoi(p.get('truong'), moi) if u not in bd]
            tv = [u for u in _kiem_nguoi(p.get('thanh_vien'), moi) if u not in truong and u not in bd]
            moi['phong'][m] = {'truong': truong[:3], 'thanh_vien': tv}
        moi['quan_tri'] = [u for u in _kiem_nguoi(d.get('quan_tri'), moi) if u not in moi['bi_danh']]
        moi['chu'] = [u for u in _kiem_nguoi(d.get('chu'), moi) if u not in moi['bi_danh']]
        if not request.user.is_superuser and set(moi['chu']) != set(pcfg.get('chu') or []):
            raise _Loi('Chỉ superuser được sửa danh sách Chủ (người duyệt việc Cổng).', 403)
        if not request.user.is_superuser and vt['username'] not in moi['quan_tri']:
            raise _Loi('Không tự gỡ mình khỏi quản trị được (nhờ chủ/superuser).')
        for u, info in (d.get('nguoi') or {}).items():
            ten = str((info or {}).get('ten') or '').strip()[:60]
            if ten:
                moi['nguoi'][str(u)[:80]] = {'ten': ten}
        _write_json('phong.json', moi)
        _ghi_nhat_ky([_nk(vt['username'], '', 'phong', '', json.dumps(
            {m: p['truong'] for m, p in moi['phong'].items()}, ensure_ascii=False)[:300])])
    return JsonResponse({'ok': True, 'cfg': moi})


def api_ma(request, vt):
    if request.method == 'GET':
        return JsonResponse({'ok': True, **_load_ma()})
    _can_qt(vt)
    d = _body(request)
    out = {'ma_map': {}, 'ma_du_kien': {}}
    for k in ('ma_map', 'ma_du_kien'):
        for a, b in (d.get(k) or {}).items():
            a, b = str(a).strip().upper(), str(b).strip().upper()
            if not a:
                continue
            if not RE_MA.match(a) or (b and not RE_MA.match(b)):
                raise _Loi('Mã không hợp lệ: %s → %s' % (a, b))
            if b:
                out[k][a] = b
    with _lock():
        _write_json('ma.json', out)
    return JsonResponse({'ok': True, **out})


def _nguoi_mac_dinh(s, pcfg):
    ph = pcfg['phong']
    gy = s.get('goi_y_nguoi') or []
    out = []
    if 'chu' in gy:
        out += _ds_chu(pcfg) + list(ph.get('QL', {}).get('truong', []))
    if 'claude' in gy:
        out += ['claude'] + list(ph.get(s.get('phong'), {}).get('truong', []))
    if not out:
        out = list(ph.get(s.get('phong'), {}).get('truong', []))
    res = []
    for u in out:
        if u and u not in res:
            res.append(u)
    return res


def nap_ke_hoach(ct=None, ap_dung=False, user='he_thong'):
    """Hợp nhất seed (đã tải lên runtime) vào viec.json theo id. Trả thống kê. Idempotent."""
    ct = ct or _ct_mac_dinh()
    seed = _seed(ct) if _seed_file(ct) else None
    if not seed:
        raise _Loi('Chưa có kế hoạch "%s" — vào Cài đặt → "Tải file kế hoạch" trước.' % (ct or '?'))
    kq = {'moi': [], 'cap_nhat': [], 'giu': 0, 'an': [], 'hien_lai': [], 'giao_mac_dinh': 0,
          'tong_seed': len(seed.get('viec') or [])}
    with _lock():
        pcfg = _load_phong()
        bd = set(pcfg.get('bi_danh') or {}) | set(BI_DANH_MAC_DINH)
        data = _load_viec()
        viec = data['viec']
        seen = set()
        for s in seed.get('viec') or []:
            vid = s.get('id')
            if not vid:
                continue
            seen.add(vid)
            cur = viec.get(vid)
            if cur is None:
                t = {k: s.get(k) for k in TRUONG_CHU}
                t.update({'id': vid, 'chuong_trinh': ct, 'bat_dau': s.get('bat_dau'), 'han': s.get('han'),
                          'han_goc': s.get('han'), 'nguoi': _nguoi_mac_dinh(s, pcfg),
                          'trang_thai': 'chua_lam', 'checklist': [], 'ghi_chu': [], 'rev': 0,
                          'an': False, 'nguon': 'seed', 'la_cong': _cong_tu_noi_dung(s)})
                if t['nguoi']:
                    kq['giao_mac_dinh'] += 1
                viec[vid] = t
                kq['moi'].append(vid)
                continue
            # Cờ Cổng: chốt từ trạng thái hiện tại (việc cũ chưa có cờ) TRƯỚC khi chép chữ mới; chỉ BẬT thêm
            # theo kế hoạch, không tự tắt (bỏ bước chủ duyệt phải qua chủ, không qua sửa tiêu đề / nạp lại).
            cong = _la_cong(cur) or _cong_tu_noi_dung(s)
            if cur.get('la_cong') is not cong:
                cur['la_cong'] = cong
            doi = [k for k in TRUONG_CHU if cur.get(k) != s.get(k)]
            if cur.get('han_goc') != s.get('han'):
                doi.append('han_goc')
            if not cur.get('han_sua_tay') and (cur.get('han') != s.get('han') or cur.get('bat_dau') != s.get('bat_dau')):
                doi.append('han')
            if cur.get('an') and cur.get('an_ly_do') == 'nap':
                cur['an'] = False
                cur.pop('an_ly_do', None)
                kq['hien_lai'].append(vid)
            # Chưa có người (hoặc chỉ có bí danh như 'claude') -> giao mặc định trưởng phòng.
            # KHÔNG bao giờ đè người đã được giao thật.
            giao = False
            ng = cur.get('nguoi') or []
            if not ng or all(u in bd for u in ng):
                nm = _nguoi_mac_dinh(s, pcfg)
                if nm and nm != ng:
                    cur['nguoi'] = nm
                    kq['giao_mac_dinh'] += 1
                    giao = True
            if doi:
                for k in TRUONG_CHU:
                    cur[k] = s.get(k)
                cur['han_goc'] = s.get('han')
                if not cur.get('han_sua_tay'):
                    cur['han'] = s.get('han')
                    cur['bat_dau'] = s.get('bat_dau')
                kq['cap_nhat'].append(vid)
            elif not giao:
                kq['giu'] += 1
            if doi or giao:
                cur['rev'] = int(cur.get('rev', 0)) + 1
        for vid, t in viec.items():
            if t.get('chuong_trinh') == ct and t.get('nguon') == 'seed' and vid not in seen and not t.get('an'):
                t['an'] = True
                t['an_ly_do'] = 'nap'
                kq['an'].append(vid)
        if ap_dung:
            data.setdefault('chuong_trinh', {})[ct] = {
                'ten': seed.get('ten', ct), 'phien_ban': seed.get('phien_ban', 1),
                'nap_luc': _now().strftime('%Y-%m-%d %H:%M'), 'nap_boi': user}
            data['phien'] = int(data.get('phien', 0)) + 1
            _write_json('viec.json', data)
            _ghi_nhat_ky([_nk(user, '', 'nap', ct, 'moi %d · cap_nhat %d · an %d' % (
                len(kq['moi']), len(kq['cap_nhat']), len(kq['an'])))])
    kq['ap_dung'] = bool(ap_dung)
    return kq


def api_nap(request, vt):
    _can_qt(vt)
    d = _body(request)
    ct = str(d.get('chuong_trinh') or _ct_mac_dinh())
    if not _seed_file(ct) or ct not in _ds_ct():
        raise _Loi('Chưa có kế hoạch "%s" — tải file kế hoạch lên trước.' % ct[:40])
    thu = str(d.get('thu', 1)) not in ('0', 'false', 'False')
    kq = nap_ke_hoach(ct, ap_dung=not thu, user=vt['username'])
    return JsonResponse({'ok': True, 'chuong_trinh': ct, **kq})


def api_tai_seed(request, vt):
    """Quản trị TẢI LÊN file kế hoạch (seed JSON) -> runtime/giaoviec/seed_<ct>.json.
    Seed chứa dữ liệu NỘI BỘ nên KHÔNG để trong git (repo công khai). Chỉ nhận JSON ≤ SEED_TOI_DA,
    kiểm cấu trúc (kiem_seed), tên file do server đặt từ mã chương trình — không theo tên người gửi.
    Tải lên KHÔNG đụng việc đang chạy: sau đó bấm "Xem trước" rồi "Áp dụng" (api/nap)."""
    _can_qt(vt)
    mb = SEED_TOI_DA // (1024 * 1024)
    try:
        dai = int(request.META.get('CONTENT_LENGTH') or 0)
    except ValueError:
        dai = 0
    if dai > SEED_TOI_DA + 64 * 1024:          # chặn sớm, chưa đọc thân request
        raise _Loi('File quá lớn (tối đa %d MB).' % mb, 413)
    f = request.FILES.get('file')
    if f is None:
        raise _Loi('Chưa chọn file kế hoạch (.json).')
    if (f.size or 0) > SEED_TOI_DA:
        raise _Loi('File quá lớn (tối đa %d MB).' % mb, 413)
    ten_goc = str(getattr(f, 'name', '') or '').lower()
    loai = str(getattr(f, 'content_type', '') or '').lower()
    if not (ten_goc.endswith('.json') or 'json' in loai):
        raise _Loi('Chỉ nhận file .json.')
    raw = f.read(SEED_TOI_DA + 1)
    if len(raw) > SEED_TOI_DA:
        raise _Loi('File quá lớn (tối đa %d MB).' % mb, 413)
    try:
        d = json.loads(raw.decode('utf-8-sig'), strict=False)
    except (UnicodeDecodeError, ValueError):
        raise _Loi('File không phải JSON hợp lệ (UTF-8).')
    seed, loi = kiem_seed(d, str(request.POST.get('chuong_trinh') or ''))
    if loi:
        raise _Loi('File kế hoạch sai cấu trúc:\n- ' + '\n- '.join(loi))
    ct = seed['chuong_trinh']
    ten_file = _seed_file(ct)                  # 'seed_<ct>.json' — ct đã qua RE_CT
    with _lock():
        co_truoc = os.path.exists(_p(ten_file))
        _write_json(ten_file, seed)
        _CACHE.pop(_p(ten_file), None)
        _ghi_nhat_ky([_nk(vt['username'], '', 'tai_ke_hoach', ct, '%d phòng · %d việc' % (
            len(seed['phong']), len(seed['viec'])))])
    return JsonResponse({'ok': True, 'chuong_trinh': ct, 'ten': seed['ten'], 'phien_ban': seed['phien_ban'],
                         'so_phong': len(seed['phong']), 'so_viec': len(seed['viec']), 'thay_the': co_truoc})


def _dong_viec(t, viec, pcfg, nguoi_ten):
    ng = ', '.join(nguoi_ten.get(u, u) for u in (t.get('nguoi') or [])) or 'CHƯA GIAO'
    return '- %s · %s · hạn %s · %s' % (t['id'], t.get('tieu_de', ''), _fmt(t.get('han')), ng)


def van_ban_zalo(phong=None, ngay=None, link=''):
    data = _load_viec(cache=True)
    viec = data['viec']
    pcfg = _load_phong(cache=True)
    ngay = ngay or _today()
    lich = _Lich()
    mai = lich.ke_tiep(ngay)
    ten_ph = {p['ma']: p['ten'] for p in _phong_ds()}
    nguoi_ten = {u: (v or {}).get('ten') or u for u, v in (pcfg.get('nguoi') or {}).items()}
    nguoi_ten.update(pcfg.get('bi_danh') or {})
    ds_ph = [phong] if phong else _phong_ma()
    out = ['📋 VIỆC NGÀY %s/%d' % (_fmt(ngay), ngay.year)]
    for m in ds_ph:
        vs = [t for t in viec.values() if t.get('phong') == m and not t.get('an') and t.get('trang_thai') not in DONG]
        tre = sorted([t for t in vs if (_d(t.get('han')) or ngay) < ngay], key=lambda t: t.get('han', ''))
        hn = [t for t in vs if _d(t.get('han')) == ngay]
        mm = [t for t in vs if ngay < (_d(t.get('han')) or ngay) <= mai]
        cd = [t for t in vs if t.get('trang_thai') == 'cho_duyet']
        if not (tre or hn or mm or cd) and not phong:
            continue
        out.append('')
        out.append('🏷 %s – %s' % (m, ten_ph.get(m, m)))
        if tre:
            out.append('⛔ Trễ:')
            out += [_dong_viec(t, viec, pcfg, nguoi_ten) for t in tre]
        if hn:
            out.append('📌 Đến hạn hôm nay:')
            out += [_dong_viec(t, viec, pcfg, nguoi_ten) for t in hn]
        if mm:
            out.append('⏭ Hạn %s:' % _fmt(mai))
            out += [_dong_viec(t, viec, pcfg, nguoi_ten) for t in mm]
        if cd:
            out.append('🔒 Chờ duyệt:')
            out += [_dong_viec(t, viec, pcfg, nguoi_ten) for t in cd]
        if not (tre or hn or mm or cd):
            out.append('✓ Không có việc trễ/đến hạn.')
    if len(out) == 1:
        out.append('✓ Không có việc trễ hay đến hạn.')
    if link:
        out += ['', 'Mở app: ' + link]
    return '\n'.join(out)


def api_zalo(request, vt):
    ph = request.GET.get('phong') or None
    if ph and ph not in _phong_ma():
        raise _Loi('Phòng không hợp lệ.')
    if not ph and not vt['xem_tong']:
        raise _Loi('Chỉ quản lý xem toàn xưởng.', 403)
    txt = van_ban_zalo(ph, _d(request.GET.get('ngay')) or _today(),
                       request.build_absolute_uri('/giao-viec'))
    return HttpResponse(txt, content_type='text/plain; charset=utf-8')


def api_tong(request, vt):
    if not vt['xem_tong']:
        raise _Loi('Chỉ quản lý / trưởng phòng xem bảng toàn xưởng.', 403)
    data = _load_viec(cache=True)
    viec = data['viec']
    pcfg = _load_phong(cache=True)
    today = _today()
    nguoc = _nguoc(viec)
    tt = [_tom_tat(t, viec, nguoc, today, vt) for t in viec.values() if not t.get('an')]
    phong = []
    for p in _phong_ds():
        vs = [x for x in tt if x['phong'] == p['ma']]
        xong = sum(1 for x in vs if x['trang_thai'] == 'xong')
        phong.append({'ma': p['ma'], 'ten': p['ten'], 'tong': len(vs), 'xong': xong,
                      'tre': sum(1 for x in vs if x['tre']),
                      'han_2_ngay': sum(1 for x in vs if not x['tre'] and x['trang_thai'] not in DONG
                                        and x['so_ngay_con'] <= 2),
                      'dang_lam': sum(1 for x in vs if x['trang_thai'] == 'dang_lam'),
                      'cho_duyet': sum(1 for x in vs if x['trang_thai'] == 'cho_duyet'),
                      'chua_giao': sum(1 for x in vs if not x['nguoi'] and x['trang_thai'] not in DONG),
                      'pct': round(100.0 * xong / len(vs)) if vs else 0,
                      'truong': pcfg['phong'].get(p['ma'], {}).get('truong', [])})
    co_push = set()
    try:
        from pha.models import PushSubscription
        co_push = set(PushSubscription.objects.values_list('username', flat=True))
    except Exception:
        pass
    trong_phong = set()
    for p in pcfg['phong'].values():
        trong_phong.update(p.get('truong', []))
        trong_phong.update(p.get('thanh_vien', []))
    for x in tt:
        trong_phong.update(x['nguoi'])
    trong_phong -= set(pcfg.get('bi_danh') or {})
    return JsonResponse({
        'ok': True, 'hom_nay': today.isoformat(), 'phong': phong,
        'tre': sorted([x for x in tt if x['tre']], key=lambda x: x['han']),
        'bi_chan': [x for x in tt if x['bi_chan']],
        'cho_duyet': [x for x in tt if x['trang_thai'] == 'cho_duyet'],
        'chua_giao': [x for x in tt if not x['nguoi'] and x['trang_thai'] not in DONG],
        'xin_lui_han': [x for x in tt if x['xin_lui_han']],
        'chua_bat_tb': sorted(u for u in trong_phong if u not in co_push),
    })


def _key():
    from pha.models import AppSetting
    k = (AppSetting.get('GIAOVIEC_KEY', '') or '').strip()
    if not k:
        import secrets
        k = 'gv_' + secrets.token_urlsafe(18)
        AppSetting.set('GIAOVIEC_KEY', k)
    return k


def api_cai_dat(request, vt):
    _can_qt(vt)
    from pha import push
    k = _key()
    base = request.build_absolute_uri('/giao-viec/nhac')
    app = str(settings.BASE_DIR)
    da_nap = _load_viec(cache=True).get('chuong_trinh') or {}
    ds = []
    for ct in reversed(_ds_ct()):               # mới tải lên trước
        s = _seed(ct) or {}
        try:
            luc = dt.datetime.fromtimestamp(os.stat(_p(_seed_file(ct))).st_mtime).strftime('%Y-%m-%d %H:%M')
        except OSError:
            luc = ''
        ds.append({'ct': ct, 'ten': s.get('ten') or ct, 'phien_ban': s.get('phien_ban', 1),
                   'so_phong': len(s.get('phong') or []), 'so_viec': len(s.get('viec') or []),
                   'tai_luc': luc, 'nap_luc': (da_nap.get(ct) or {}).get('nap_luc', '')})
    return JsonResponse({
        'ok': True, 'key': k, 'push_ok': push.is_available(), 'thu_muc': _dir(),
        'chuong_trinh_ds': ds, 'seed_toi_da': SEED_TOI_DA,
        # KHUYẾN NGHỊ: lệnh manage.py trong crontab của www-data (không có khoá trên URL/log nginx,
        # file runtime giữ đúng chủ sở hữu). Giờ theo múi giờ MÁY CHỦ (VPS để UTC thì 07:30 VN = 30 0).
        'cron': '# crontab -u www-data -e   (giờ theo múi giờ máy chủ — kiểm bằng: timedatectl)\n'
                '30 7 * * 1-6 cd %s && venv/bin/python manage.py giaoviec_nhac --dot sang >/dev/null 2>&1\n'
                '30 16 * * 1-6 cd %s && venv/bin/python manage.py giaoviec_nhac --dot chieu >/dev/null 2>&1'
                % (app, app),
        # Dự phòng (KHÔNG khuyến nghị): khoá nằm trên URL -> có thể lộ ở access log / lịch sử shell
        'cron_curl': '30 7 * * 1-6 curl -s "%s?key=%s&dot=sang" >/dev/null\n'
                     '30 16 * * 1-6 curl -s "%s?key=%s&dot=chieu" >/dev/null' % (base, k, base, k),
    })


def api_thu_push(request, vt):
    from pha import push
    if not push.is_available():
        raise _Loi('Máy chủ chưa cài pywebpush — chưa gửi được thông báo.')
    n = _push([vt['username']], '🔔 Thử thông báo Giao việc', 'Máy này đã nhận được thông báo.',
              '/giao-viec', 'gv-thu')
    if not n:
        raise _Loi('Tài khoản này chưa có máy nào bật thông báo. Bấm "Bật thông báo" trước.')
    return JsonResponse({'ok': True, 'so_may': n})


def api_nhac(request, vt):
    """Quản trị bấm trên trang (POST + CSRF): {dot, thu:1 xem trước | 0 gửi thật, force}."""
    _can_qt(vt)
    d = _body(request)
    dot = 'chieu' if d.get('dot') == 'chieu' else 'sang'
    thu = str(d.get('thu', 1)) not in ('0', 'false', 'False')
    return JsonResponse(chay_nhac(dot, gui=not thu, force=bool(d.get('force'))))


# =============================== NHẮC (cron) ===============================
def chay_nhac(dot='sang', gui=True, force=False, now=None):
    """Tính + (tuỳ chọn) gửi đợt nhắc. Dùng chung cho cron URL, management command, nút thử.
    gui=False: chỉ xem trước (không gửi, không ghi sổ)."""
    now = now or _now()
    today = now.date()
    day = today.isoformat()
    lich = _Lich()
    if not force and not lich.la_ngay_lam(today):
        return {'ok': True, 'dot': dot, 'ngay': day, 'bo_qua': 'ngày nghỉ', 'gui': False, 'so_may': 0, 'tin': []}
    data = _load_viec(cache=True)
    viec = data['viec']
    pcfg = _load_phong(cache=True)
    bd = set(pcfg.get('bi_danh') or {}) | set(BI_DANH_MAC_DINH)
    qt = _ds_quan_tri(pcfg)
    try:
        from pha.extra_views import _on_leave_today
        nghi = _on_leave_today(day)
    except Exception:
        nghi = set()
    mai = lich.ke_tiep(today)
    tin = []

    def truong(m):
        return [u for u in pcfg['phong'].get(m, {}).get('truong', []) if u not in bd]

    if dot == 'sang':
        per = defaultdict(lambda: {'hom_nay': [], 'mai': [], 'tre': [], 'ck': [], 'bao_tre': [], 'dai_han': 0})
        for t in viec.values():
            if t.get('an') or t.get('trang_thai') in DONG:
                continue
            han = _d(t.get('han'))
            if not han:
                continue
            nguoi = [u for u in (t.get('nguoi') or []) if u not in bd]
            nhan = [u for u in nguoi if u not in nghi]
            if not nhan:                       # chưa giao / người làm nghỉ phép -> trưởng phòng
                nhan = [u for u in truong(t.get('phong')) if u not in nghi]
            if han < today:
                tre_ngay = lich.so_ngay_lam_giua(han, today)
                if tre_ngay <= 3:
                    for u in nhan:
                        per[u]['tre'].append(t['id'])
                else:
                    for u in set(truong(t.get('phong')) + qt):
                        per[u]['bao_tre'].append(t['id'])
            elif han == today:
                for u in nhan:
                    per[u]['hom_nay'].append(t['id'])
            elif han <= mai:
                for u in nhan:
                    per[u]['mai'].append(t['id'])
            for c in t.get('checklist') or []:
                ch = _d(c.get('han'))
                if ch and not c.get('xong') and today <= ch <= mai:
                    for u in nhan:
                        per[u]['ck'].append(t['id'])
            if t.get('dai_han') and today.weekday() == 0 and (_d(t.get('bat_dau')) or today) <= today:
                for u in nhan:
                    per[u]['dai_han'] += 1
        for u, x in sorted(per.items()):
            phan = []
            if x['hom_nay']:
                phan.append('Hôm nay %d việc đến hạn' % len(x['hom_nay']))
            if x['mai']:
                phan.append('%s %d' % (('mai' if (mai - today).days == 1 else _fmt(mai)), len(x['mai'])))
            if x['tre']:
                phan.append('trễ %d' % len(x['tre']))
            if x['ck']:
                phan.append('mục checklist sắp hạn %d' % len(set(x['ck'])))
            if x['dai_han']:
                phan.append('tuần này %d việc dài hạn' % x['dai_han'])
            if x['bao_tre']:
                phan.append('trễ quá 3 ngày: ' + ', '.join(sorted(set(x['bao_tre']))[:6]))
            if not phan:
                continue
            ids = (x['tre'] + x['hom_nay'] + x['mai'])[:4]
            body = ' · '.join(phan) + ((' — ' + ', '.join(ids)) if ids else '')
            tin.append({'nguoi': u, 'title': '📋 Việc %s' % _fmt(today), 'body': body,
                        'url': '/giao-viec?tab=toi', 'tag': 'gv-sang'})
    else:
        tong = defaultdict(lambda: {'chua_cap_nhat': 0, 'tre': 0, 'cho_duyet': 0, 'chua_giao': 0, 'xong_hn': 0})
        for t in viec.values():
            if t.get('an'):
                continue
            m = t.get('phong')
            x = tong[m]
            if t.get('trang_thai') == 'xong' and str(t.get('cap_nhat_luc', ''))[:10] == day:
                x['xong_hn'] += 1
            if t.get('trang_thai') in DONG:
                continue
            han = _d(t.get('han'))
            if han and han < today:
                x['tre'] += 1
            if t.get('trang_thai') == 'cho_duyet':
                x['cho_duyet'] += 1
            if not t.get('nguoi'):
                x['chua_giao'] += 1
            if (_d(t.get('bat_dau')) or today) <= today and str(t.get('cap_nhat_luc', ''))[:10] != day:
                x['chua_cap_nhat'] += 1
        per = defaultdict(list)
        for m in _phong_ma():
            x = tong[m]
            line = 'Phòng %s: %d việc chưa cập nhật hôm nay · %d trễ' % (m, x['chua_cap_nhat'], x['tre'])
            if x['cho_duyet']:
                line += ' · %d chờ duyệt' % x['cho_duyet']
            for u in truong(m):
                if u not in nghi:
                    per[u].append(line)
        s = {k: sum(tong[m][k] for m in tong) for k in ('tre', 'cho_duyet', 'chua_giao', 'xong_hn')}
        tre_ph = ', '.join('%s %d' % (m, tong[m]['tre']) for m in _phong_ma() if tong[m]['tre'])
        # số phòng theo kế hoạch đã tải lên (không viết cứng)
        line_qt = '%d phòng: xong hôm nay %d · trễ %d · chờ duyệt %d · chưa giao %d' % (
            len(_phong_ma()), s['xong_hn'], s['tre'], s['cho_duyet'], s['chua_giao']) + \
            ((' — trễ: ' + tre_ph) if tre_ph else '')
        for u in qt:
            if u not in bd:
                per[u].insert(0, line_qt)
        for u, lines in sorted(per.items()):
            tin.append({'nguoi': u, 'title': '🕟 Tổng hợp 16:30 %s' % _fmt(today), 'body': ' | '.join(lines),
                        'url': '/giao-viec?tab=xuong' if u in qt else '/giao-viec?tab=phong',
                        'tag': 'gv-chieu'})

    so_may = 0
    if gui:
        han_xoa = (today - dt.timedelta(days=14)).isoformat()

        def doc_so():
            so = _read_json('da_nhac.json', {})
            return {k: v for k, v in so.items() if str(v) >= han_xoa}

        # Theo TỪNG người: kiểm sổ -> GỬI -> rồi mới ghi sổ. Tiến trình chết giữa chừng thì người chưa
        # được gửi KHÔNG bị đánh dấu (lần chạy sau gửi bù); người đã gửi không bị gửi lặp.
        # (Không giữ khoá lúc gửi mạng để không chặn thao tác của người dùng.)
        for m in tin:
            k = '%s|%s|%s' % (day, dot, m['nguoi'])
            if not force:
                with _lock():
                    da_gui = k in doc_so()
                if da_gui:
                    m['da_gui_truoc'] = True
                    continue
            so_may += _push([m['nguoi']], m['title'], m['body'], m['url'], m['tag'])
            with _lock():
                so = doc_so()
                so[k] = day
                _write_json('da_nhac.json', so)
            m['da_gui'] = True
    return {'ok': True, 'dot': dot, 'ngay': day, 'gui': bool(gui), 'so_may': so_may, 'tin': tin}


def nhac(request):
    """Cron: GET /giao-viec/nhac?key=<GIAOVIEC_KEY>&dot=sang|chieu.
    Quản trị đang đăng nhập (không cần key) chỉ được XEM TRƯỚC (&thu=1); gửi thật từ trang dùng POST api/nhac."""
    dot = 'chieu' if request.GET.get('dot') == 'chieu' else 'sang'
    key = request.GET.get('key', '')
    if key:
        try:
            ok = hmac.compare_digest(key, _key())
        except Exception:
            ok = False
        if not ok:
            return JsonResponse({'ok': False, 'error': 'Sai khoá'}, status=401)
        res = chay_nhac(dot, gui=request.GET.get('thu') != '1')
    else:
        user = getattr(request, 'user', None)
        if not (user and user.is_authenticated and _vai_tro(user)['la_quan_tri']):
            return JsonResponse({'ok': False, 'error': 'Cần khoá ?key= hoặc đăng nhập quản trị'}, status=401)
        res = chay_nhac(dot, gui=False)
    r = JsonResponse(res)
    r['Cache-Control'] = 'no-store'
    return r


_API = {
    # act: (hàm, phương thức)
    'ds': (api_ds, 'GET'), 'dem': (api_dem, 'GET'), 'viec': (api_viec, 'GET'),
    'tong': (api_tong, 'GET'), 'zalo': (api_zalo, 'GET'), 'cai-dat': (api_cai_dat, 'GET'),
    'trang-thai': (api_trang_thai, 'POST'), 'ghi-chu': (api_ghi_chu, 'POST'),
    'checklist': (api_checklist, 'POST'), 'giao': (api_giao, 'POST'), 'thong-bao': (api_thong_bao, 'POST'),
    'sua': (api_sua, 'POST'), 'tao': (api_tao, 'POST'), 'xin-lui-han': (api_xin_lui_han, 'POST'),
    'duyet-lui-han': (api_duyet_lui_han, 'POST'), 'nap': (api_nap, 'POST'), 'nhac': (api_nhac, 'POST'),
    'thu-push': (api_thu_push, 'POST'), 'tai-seed': (api_tai_seed, 'POST'),
    'phong': (api_phong, 'GET/POST'), 'ma': (api_ma, 'GET/POST'),
}


def api(request, act):
    """/giao-viec/api/<act> — JSON. Chưa đăng nhập -> 401 JSON (không redirect HTML)."""
    if not request.user.is_authenticated:
        return JsonResponse({'ok': False, 'error': 'Hết phiên đăng nhập — tải lại trang.'}, status=401)
    ent = _API.get(act)
    if not ent:
        return JsonResponse({'ok': False, 'error': 'Không có API %s' % act}, status=404)
    fn, meth = ent
    if request.method not in meth.split('/'):
        return JsonResponse({'ok': False, 'error': 'Sai phương thức (%s)' % meth}, status=405)
    try:
        vt = _vai_tro(request.user)
        resp = fn(request, vt)
    except _Loi as e:
        resp = JsonResponse({'ok': False, 'error': str(e)}, status=e.status)
    except (TypeError, ValueError, AttributeError) as e:
        # Dữ liệu gửi lên sai kiểu (vd ids là số, phong là list, rev là chữ) -> 400 JSON, không 500 HTML
        log.info('giao-viec api/%s: dữ liệu không hợp lệ: %r', act, e)
        resp = JsonResponse({'ok': False, 'error': 'Dữ liệu không hợp lệ.'}, status=400)
    resp['Cache-Control'] = 'no-store'
    return resp
