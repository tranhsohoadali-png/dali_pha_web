# -*- coding: utf-8 -*-
"""Chuyển bản kế hoạch (plan_result.json: depts[] -> tasks[]) -> file kế hoạch (seed) cho /giao-viec.

!!! Seed chứa dữ liệu NỘI BỘ — repo GitHub CÔNG KHAI: KHÔNG BAO GIỜ ghi/commit seed vào repo.
    Ghi RA NGOÀI repo, rồi quản trị tải lên ở /giao-viec -> Cài đặt -> "Tải file kế hoạch"
    (server kiểm cấu trúc và lưu ở runtime/giaoviec/seed_<chuong_trinh>.json).

Chạy (ví dụ):
    python tools/tao_seed_giaoviec.py <plan_result.json> <thu_muc_ngoai_repo>/seed.json \
        --ct tet2027 --ten "Mẫu Tết 2027" --nam 2026 [--ma bang_ma.json]

- Ngày dd/mm -> ISO: tháng 11–12 thuộc năm --nam, tháng 1–10 thuộc năm --nam + 1.
- Giữ NGUYÊN chữ (kể cả mã mẫu tạm), tách danh sách ma_tam để trang hiển thị kèm mã chính thức.
- Bảng mã (mã tạm -> mã chính thức) KHÔNG viết cứng ở đây: truyền --ma <file.json>
  {"ma_map": {...}, "ma_du_kien": {...}} hoặc nhập trên trang (Cài đặt -> Bảng mã mẫu).
- Kiểm: phụ thuộc trỏ đúng id, bắt đầu <= hạn, hạn không rơi Chủ nhật.
"""
import argparse
import datetime as dt
import json
import os
import re
import sys

RE_RANGE = re.compile(r'\b(CZ|HO|TP)(\d{2})\s*[–-]\s*(?:\1)?(\d{2})\b')
RE_ONE = re.compile(r'\b(?:CZ|HO|TP)\d{2}\b|\bPC\d{1,2}\b')
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def iso(ddmm, nam):
    d, m = (int(x) for x in ddmm.split('/'))
    return dt.date(nam if m >= 11 else nam + 1, m, d)


def codes(text):
    out = set(RE_ONE.findall(text))
    for pre, a, b in RE_RANGE.findall(text):
        a, b = int(a), int(b)
        if 0 < a <= b <= a + 12:
            out.update('%s%02d' % (pre, i) for i in range(a, b + 1))
    return sorted(out)


def goi_y(t):
    txt = t['title'] + ' ' + t['detail']
    g = []
    if 'Trợ lý Claude' in txt:
        g.append('claude')
    if t['title'].startswith(('Cổng', 'Chủ ')) or 'Chủ duyệt' in t['title']:
        g.append('chu')
    return g


def trong_repo(path):
    """True nếu path nằm trong repo nhưng KHÔNG ở runtime/ (runtime/ đã gitignore)."""
    p = os.path.normcase(os.path.abspath(path))
    r = os.path.normcase(REPO)
    rt = os.path.join(r, 'runtime')
    try:
        if os.path.commonpath([p, r]) != r:
            return False
        return os.path.commonpath([p, rt]) != rt
    except ValueError:          # Windows: khác ổ đĩa -> chắc chắn ngoài repo
        return False


def tao_seed(d, ct, ten, nam, bang_ma=None):
    ids = {t['id'] for dp in d['depts'] for t in dp['tasks']}
    phong, viec, loi = [], [], []
    for dp in d['depts']:
        phong.append({'ma': dp['prefix'], 'ten': dp['name'],
                      'nhiem_vu': dp.get('mission', ''), 'vai_tro': dp.get('roles', '')})
        for t in dp['tasks']:
            s, e = iso(t['start'], nam), iso(t['due'], nam)
            if s > e:
                loi.append('%s: bắt đầu sau hạn' % t['id'])
            if e.weekday() == 6:
                loi.append('%s: hạn rơi Chủ nhật' % t['id'])
            for x in t.get('depends_on', []):
                if x not in ids:
                    loi.append('%s: phụ thuộc %s không tồn tại' % (t['id'], x))
            viec.append({
                'id': t['id'], 'phong': dp['prefix'],
                'tieu_de': t['title'], 'chi_tiet': t['detail'],
                'bat_dau': s.isoformat(), 'han': e.isoformat(),
                'phu_thuoc': list(t.get('depends_on', [])),
                'san_pham': t.get('deliverable', ''), 'cong_cu': t.get('tool', ''),
                'dung_chatgpt': bool(t.get('uses_chatgpt')),
                'ma_tam': codes(t['title'] + ' ' + t['detail'] + ' ' + t.get('deliverable', '')),
                'goi_y_nguoi': goi_y(t),
                'dai_han': (e - s).days > 14,
            })
    bang_ma = bang_ma or {}
    plan = d.get('plan') or {}
    seed = {
        'chuong_trinh': ct, 'ten': ten, 'phien_ban': 1,
        'phong': phong, 'viec': viec,
        'ma_map': dict(bang_ma.get('ma_map') or {}), 'ma_du_kien': dict(bang_ma.get('ma_du_kien') or {}),
        'dot': plan.get('waves', []), 'moc': plan.get('milestones', []),
        'lich_mau': plan.get('design_schedule', []),
    }
    return seed, loi


def main(argv=None):
    ap = argparse.ArgumentParser(description='plan_result.json -> seed Giao việc (ghi NGOÀI repo)')
    ap.add_argument('src')
    ap.add_argument('out')
    ap.add_argument('--ct', required=True, help='mã chương trình: chữ thường/số/_ (vd tet2027)')
    ap.add_argument('--ten', default='', help='tên hiển thị của kế hoạch')
    ap.add_argument('--nam', type=int, default=dt.date.today().year, help='năm của tháng 11–12')
    ap.add_argument('--ma', default='', help='file JSON bảng mã {"ma_map":{}, "ma_du_kien":{}} (tuỳ chọn)')
    a = ap.parse_args(argv)
    if not re.match(r'^[a-z0-9_]{1,40}$', a.ct):
        ap.error('--ct chỉ gồm chữ thường/số/_ (1–40 ký tự)')
    if trong_repo(a.out):
        ap.error('KHÔNG ghi seed vào trong repo (repo công khai) — chọn thư mục ngoài repo: %s' % a.out)
    with open(a.src, encoding='utf-8') as f:
        d = json.load(f)
    bang_ma = None
    if a.ma:
        with open(a.ma, encoding='utf-8') as f:
            bang_ma = json.load(f)
    seed, loi = tao_seed(d, a.ct, a.ten or a.ct, a.nam, bang_ma)
    with open(a.out, 'w', encoding='utf-8') as f:
        json.dump(seed, f, ensure_ascii=False, indent=1)
    viec = seed['viec']
    print('phong', len(seed['phong']), 'viec', len(viec), 'loi', len(loi))
    for x in loi:
        print('  ', x)
    print('dai_han', sum(v['dai_han'] for v in viec),
          'co_ma', sum(1 for v in viec if v['ma_tam']),
          'goi_y_claude', [v['id'] for v in viec if 'claude' in v['goi_y_nguoi']],
          'goi_y_chu', [v['id'] for v in viec if 'chu' in v['goi_y_nguoi']])
    return 0


if __name__ == '__main__':
    sys.exit(main())
