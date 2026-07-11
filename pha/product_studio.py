# -*- coding: utf-8 -*-
"""XƯỞNG ẢNH SẢN PHẨM — đóng khung tranh vào ảnh mockup (port từ agent.tranhdali.vn).

Cách hoạt động (giống hệt bản trên agent):
  1. KHO KHUNG (nạp 1 lần): upload ảnh mockup phòng/giá vẽ có Ô MÀN XANH (#00FF00)
     đánh dấu chỗ đặt tranh -> tự dò vùng xanh -> lưu spec {rect, 4 góc, ratio}.
  2. GHÉP: chọn 1 tranh (bản THIẾT KẾ từ /xu-ly-anh hoặc upload) + nhiều khung
     + Mã/KT/Số màu -> mỗi khung ra 2 ảnh: WEB (cỡ gốc mockup) + SHOPEE 1:1 (1200²).
     Banner trên cùng: logo DALI (trái) + chữ 'MÃ: … – KT: …' / 'Số lượng màu: … (color)'.

LƯU BẰNG FILE dưới MEDIA_ROOT/xuong_anh/ (KHÔNG model/migration — deploy chỉ cần
git pull + restart, theo pattern in_a3.py; media/* nằm ngoài git nên pull không đụng):
  xuong_anh/khung/tpl-<uuid>.png + .json (spec)   — kho khung
  xuong_anh/out/out-<uuid>_web.jpg / _shopee.jpg + .json — ảnh đã ghép
"""
import json
import os
import re
import uuid
from datetime import datetime

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import render

from pha.views import staff_required

_KHUNG_SUB = os.path.join('xuong_anh', 'khung')
_OUT_SUB = os.path.join('xuong_anh', 'out')

# Banner: chữ NHÃN xanh DALI + GIÁ TRỊ đậm (đúng bản gốc trên agent)
_LABEL_RGB = (118, 184, 42)
_VALUE_RGB = (55, 62, 55)
_SHOPEE_SIZE = 1200          # ảnh Shopee vuông 1:1
_WEB_MAX = 1400              # trần cạnh dài ảnh web (mockup thường 760-1024)

_FONT_PATHS = [
    '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',   # VPS Ubuntu
    '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
    'C:/Windows/Fonts/arialbd.ttf',                            # máy dev Windows
    'C:/Windows/Fonts/arial.ttf',
]


def _dir(sub):
    p = os.path.join(settings.MEDIA_ROOT, sub)
    os.makedirs(p, exist_ok=True)
    return p


def _font(px):
    for p in _FONT_PATHS:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, px)
            except Exception:
                pass
    return ImageFont.load_default()


# ---------------------------------------------------------------- dò màn xanh
def _green_mask(bgr):
    """Mask ô màn xanh neon (#00FF00). Ngưỡng S/V >=170 để KHÔNG dính cây cảnh:
    lá thật S~120-160/V~100-150, màn xanh chuẩn S=V=255 — đo bằng mockup test có
    3 bụi cây (60,140,70): ngưỡng 130 dính 6487px lá, ngưỡng 170 dính 0px."""
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    m = cv2.inRange(hsv, (45, 170, 170), (78, 255, 255))
    return cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))


def _detect_spec(bgr):
    """Ảnh khung -> spec {mode, width, height, rect, corners, ratio}. Trả (spec, '')
    hoặc (None, 'lý do'). corners cho phép khung CHỤP NGHIÊNG (warp phối cảnh)."""
    H, W = bgr.shape[:2]
    m = _green_mask(bgr)
    n, lbl, stats, _ = cv2.connectedComponentsWithStats(m, 8)
    if n < 2:
        return None, 'Không tìm thấy ô màn xanh trong ảnh'
    k = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    if stats[k, cv2.CC_STAT_AREA] < 0.02 * W * H:
        return None, 'Ô màn xanh quá nhỏ (<2% ảnh) — kiểm tra lại mockup'
    comp = (lbl == k).astype(np.uint8)
    x, y = int(stats[k, cv2.CC_STAT_LEFT]), int(stats[k, cv2.CC_STAT_TOP])
    w, h = int(stats[k, cv2.CC_STAT_WIDTH]), int(stats[k, cv2.CC_STAT_HEIGHT])
    corners = {'tl': [x, y], 'tr': [x + w, y], 'bl': [x, y + h], 'br': [x + w, y + h]}
    mode = 'rect'
    cnts, _ = cv2.findContours(comp, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if cnts:
        c = max(cnts, key=cv2.contourArea)
        ap = cv2.approxPolyDP(c, 0.02 * cv2.arcLength(c, True), True)
        if len(ap) == 4:
            pts = ap.reshape(-1, 2).astype(float)
            s = pts.sum(1)
            d = pts[:, 0] - pts[:, 1]
            tl, br = pts[np.argmin(s)], pts[np.argmax(s)]
            tr, bl = pts[np.argmax(d)], pts[np.argmin(d)]
            # lệch >3px so với chữ nhật thẳng -> khung chụp nghiêng (quad phối cảnh)
            if max(abs(tl[1] - tr[1]), abs(bl[1] - br[1]),
                   abs(tl[0] - bl[0]), abs(tr[0] - br[0])) > 3:
                mode = 'quad'
            corners = {'tl': tl.tolist(), 'tr': tr.tolist(),
                       'bl': bl.tolist(), 'br': br.tolist()}
    panels = int(sum(1 for i in range(1, n)
                     if stats[i, cv2.CC_STAT_AREA] >= 0.01 * W * H))
    return {'mode': mode, 'width': W, 'height': H,
            'rect': {'left': x, 'top': y, 'width': w, 'height': h},
            'corners': corners, 'ratio': round(w / float(h), 3),
            'panels': panels, 'label_pos': 'top'}, ''


# ------------------------------------------------------------------- ghép ảnh
def _component_quad(comp):
    """Component mask 0/1 -> 4 góc (tl,tr,br,bl) float32. Thử quad từ contour
    (khung chụp nghiêng); không ra 4 điểm thì dùng bounding rect."""
    x, y, w, h = cv2.boundingRect(comp)
    quad = np.float32([[x, y], [x + w, y], [x + w, y + h], [x, y + h]])
    cnts, _ = cv2.findContours(comp, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if cnts:
        c = max(cnts, key=cv2.contourArea)
        ap = cv2.approxPolyDP(c, 0.02 * cv2.arcLength(c, True), True)
        if len(ap) == 4:
            pts = ap.reshape(-1, 2).astype(np.float32)
            s = pts.sum(1)
            d = pts[:, 0] - pts[:, 1]
            quad = np.float32([pts[np.argmin(s)], pts[np.argmax(d)],
                               pts[np.argmax(s)], pts[np.argmin(d)]])
    return quad, (w, h)


def _compose_scene(tpl_bgr, spec, art_bgr):
    """Dán tranh vào TỪNG ô màn xanh (mockup BỘ 2-3 TRANH có nhiều ô -> mỗi ô
    một bản tranh): ép tranh đúng cỡ ô (khách thấy TOÀN BỘ tranh), warp theo
    4 góc (khung nghiêng vẫn đúng), alpha = mask xanh nở 1px (hết viền xanh sót)
    + feather (mép mượt). Vật che phía trước ô (lá cây…) tự giữ nguyên vì chỗ đó
    không phải màu xanh. spec chỉ dùng cho ratio/UI — ô dò LẠI trực tiếp."""
    H, W = tpl_bgr.shape[:2]
    mask = _green_mask(tpl_bgr)
    n, lbl, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    warped = np.zeros_like(tpl_bgr)
    for k in range(1, n):
        if stats[k, cv2.CC_STAT_AREA] < 0.01 * W * H:
            continue                                 # đốm xanh lạc: bỏ, không dán
        comp = (lbl == k).astype(np.uint8)
        quad, (rw, rh) = _component_quad(comp)
        art = cv2.resize(art_bgr, (max(2, rw), max(2, rh)),
                         interpolation=cv2.INTER_AREA)
        M = cv2.getPerspectiveTransform(
            np.float32([[0, 0], [rw, 0], [rw, rh], [0, rh]]), quad)
        piece = cv2.warpPerspective(art, M, (W, H), flags=cv2.INTER_LINEAR)
        sel = comp > 0
        warped[sel] = piece[sel]
    mask = cv2.dilate(mask, np.ones((3, 3), np.uint8))
    # nở mask kéo theo mép: lấp phần nở bằng chính warped gần nhất (inpaint mép 1px)
    ring = (mask > 0) & (warped.sum(2) == 0)
    if ring.any():
        warped[ring] = cv2.dilate(warped, np.ones((5, 5), np.uint8))[ring]
    alpha = (cv2.GaussianBlur(mask, (3, 3), 0).astype(np.float32) / 255.0)[..., None]
    return (tpl_bgr.astype(np.float32) * (1 - alpha)
            + warped.astype(np.float32) * alpha).astype(np.uint8)


_logo_cache = [None]


def _logo_img():
    if _logo_cache[0] is None:
        p = os.path.join(settings.MEDIA_ROOT, 'icon-512.png')
        try:
            _logo_cache[0] = Image.open(p).convert('RGBA')
        except Exception:
            _logo_cache[0] = False
    return _logo_cache[0] or None


def _draw_rich_center(dr, W, y, segs, font):
    """Vẽ 1 dòng nhiều màu (nhãn xanh + giá trị đậm) CĂN GIỮA, viền trắng mỏng
    cho dễ đọc trên mọi nền mockup."""
    widths = [dr.textlength(t, font=font) for t, _c in segs]
    x = (W - sum(widths)) / 2.0
    for (t, col), w in zip(segs, widths):
        dr.text((x, y), t, font=font, fill=col,
                stroke_width=max(1, font.size // 14), stroke_fill=(255, 255, 255, 210))
        x += w
    return y + font.size * 1.3


def _draw_banner(pil, ma, kt, colors, logo=True):
    """Banner trên cùng đúng bản agent: logo DALI góc trái + 2 dòng chữ giữa."""
    W, H = pil.size
    dr = ImageDraw.Draw(pil, 'RGBA')
    pad = max(8, int(W * 0.022))
    if logo:
        lg = _logo_img()
        if lg is not None:
            lh = max(30, int(H * 0.085))
            lgi = lg.resize((int(lg.width * lh / lg.height), lh), Image.LANCZOS)
            pil.paste(lgi, (pad, pad), lgi)
    f1 = _font(max(15, int(H * 0.036)))
    y = pad * 1.1
    line1 = []
    if ma:
        line1 += [('MÃ: ', _LABEL_RGB), (str(ma), _VALUE_RGB)]
    if kt:
        line1 += ([('  –  ', _LABEL_RGB)] if line1 else []) + \
                 [('KT: ', _LABEL_RGB), (str(kt), _VALUE_RGB)]
    if line1:
        y = _draw_rich_center(dr, W, y, line1, f1)
    if colors:
        _draw_rich_center(dr, W, y, [('Số lượng màu: ', _LABEL_RGB),
                                     (f'{colors} (color)', _VALUE_RGB)], f1)


def _finish(scene_bgr, ma, kt, colors, logo, square=False):
    """Scene đã ghép -> ảnh thành phẩm PIL. square=True: SHOPEE 1:1 1200² (cắt
    vuông quanh TÂM Ô TRANH — không phải tâm ảnh — để tranh không bị lệch/cụt)."""
    img = scene_bgr
    H, W = img.shape[:2]
    if square:
        s = min(H, W)
        cx, cy = W // 2, H // 2
        x0 = min(max(cx - s // 2, 0), W - s)
        y0 = min(max(cy - s // 2, 0), H - s)
        img = img[y0:y0 + s, x0:x0 + s]
        interp = cv2.INTER_AREA if s > _SHOPEE_SIZE else cv2.INTER_CUBIC
        img = cv2.resize(img, (_SHOPEE_SIZE, _SHOPEE_SIZE), interpolation=interp)
    elif max(H, W) > _WEB_MAX:
        sc = _WEB_MAX / float(max(H, W))
        img = cv2.resize(img, (int(W * sc), int(H * sc)), interpolation=cv2.INTER_AREA)
    pil = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
    _draw_banner(pil, ma, kt, colors, logo)
    return pil


# ------------------------------------------------------------------ kho / list
def _read_json(path):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None                      # đọc chịu lỗi: file hỏng thì bỏ qua


def _khung_list():
    d = _dir(_KHUNG_SUB)
    items = []
    for fn in os.listdir(d):
        if not fn.endswith('.json'):
            continue
        j = _read_json(os.path.join(d, fn))
        if not j:
            continue
        stem = fn[:-5]
        j['id'] = stem
        j['url'] = f'/media/xuong_anh/khung/{stem}.png'
        items.append(j)
    items.sort(key=lambda x: x.get('created', ''), reverse=True)
    return items


def _out_list():
    d = _dir(_OUT_SUB)
    items = []
    for fn in os.listdir(d):
        if not fn.endswith('.json'):
            continue
        j = _read_json(os.path.join(d, fn))
        if not j:
            continue
        j['id'] = fn[:-5]
        items.append(j)
    items.sort(key=lambda x: x.get('created', ''), reverse=True)
    return items


_ID_RE = re.compile(r'^(tpl|out)-[0-9a-f]{8,16}$')


def _decode_upload(f):
    """Đọc ảnh upload qua imdecode (cv2.imread chết với tên file tiếng Việt)."""
    buf = np.frombuffer(f.read(), np.uint8)
    return cv2.imdecode(buf, cv2.IMREAD_COLOR)


def _resolve_media(rel):
    """'/media/x/y.png' -> đường dẫn tuyệt đối AN TOÀN trong MEDIA_ROOT (chặn ../)."""
    if not rel or not rel.startswith('/media/'):
        return None
    p = os.path.normpath(os.path.join(settings.MEDIA_ROOT, rel[len('/media/'):]))
    root = os.path.normpath(str(settings.MEDIA_ROOT))
    if not p.startswith(root + os.sep) or not os.path.isfile(p):
        return None
    return p


# ----------------------------------------------------------------------- views
@staff_required
def xuong_anh(request):
    """Trang Xưởng ảnh sản phẩm. Nhận prefill từ nút 'Đóng khung' của /xu-ly-anh:
    ?art=/media/..._design.png&ma=C102&kt=40x50&mau=23"""
    from django.middleware.csrf import get_token
    get_token(request)                 # ép phát cookie csrftoken cho các fetch POST
    art = request.GET.get('art', '')
    if not _resolve_media(art):
        art = ''
    ctx = {
        'khung_json': json.dumps(_khung_list(), ensure_ascii=False),
        'outs_json': json.dumps(_out_list(), ensure_ascii=False),
        'pre_art': art,
        'pre_ma': request.GET.get('ma', ''),
        'pre_kt': request.GET.get('kt', ''),
        'pre_mau': request.GET.get('mau', ''),
    }
    return render(request, 'xuong_anh.html', ctx)


@staff_required
def khung_upload(request):
    """Nạp khung mockup (nhiều file). Tự dò ô màn xanh -> lưu PNG + spec JSON."""
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST only'})
    files = request.FILES.getlist('files')
    if not files:
        return JsonResponse({'ok': False, 'error': 'Chưa chọn file'})
    d = _dir(_KHUNG_SUB)
    added, errors = [], []
    for f in files:
        img = _decode_upload(f)
        if img is None:
            errors.append(f'{f.name}: không đọc được ảnh')
            continue
        spec, err = _detect_spec(img)
        if spec is None:
            errors.append(f'{f.name}: {err}')
            continue
        stem = f'tpl-{uuid.uuid4().hex[:12]}'
        cv2.imwrite(os.path.join(d, stem + '.png'), img)
        name = os.path.splitext(f.name)[0][:80]
        meta = {'name': name, 'spec': spec,
                'created': datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
        with open(os.path.join(d, stem + '.json'), 'w', encoding='utf-8') as fh:
            json.dump(meta, fh, ensure_ascii=False)
        meta['id'] = stem
        meta['url'] = f'/media/xuong_anh/khung/{stem}.png'
        added.append(meta)
    return JsonResponse({'ok': bool(added), 'added': added, 'errors': errors})


@staff_required
def khung_xoa(request):
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST only'})
    kid = (request.POST.get('id') or '').strip()
    if not _ID_RE.match(kid):
        return JsonResponse({'ok': False, 'error': 'id sai'})
    d = _dir(_KHUNG_SUB)
    for ext in ('.png', '.json'):
        try:
            os.remove(os.path.join(d, kid + ext))
        except OSError:
            pass
    return JsonResponse({'ok': True})


@staff_required
def ghep(request):
    """Ghép 1 tranh vào NHIỀU khung -> mỗi khung 2 ảnh (web + shopee 1:1).
    Tranh lấy từ upload ('art') hoặc từ media ('art_path' — bản thiết kế đã số hoá).
    Ghép nhanh (<1s/khung) nên chạy đồng bộ, không cần job nền."""
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST only'})
    ma = (request.POST.get('ma') or '').strip()[:40]
    kt = (request.POST.get('kt') or '').strip()[:40]
    colors = (request.POST.get('colors') or '').strip()[:10]
    logo = request.POST.get('logo', '1') == '1'
    ids = [i for i in request.POST.getlist('khung') if _ID_RE.match(i)]
    if not ids:
        return JsonResponse({'ok': False, 'error': 'Chưa chọn khung nào'})

    art = None
    up = request.FILES.get('art')
    if up is not None:
        art = _decode_upload(up)
    else:
        p = _resolve_media(request.POST.get('art_path', ''))
        if p:
            art = cv2.imdecode(np.fromfile(p, np.uint8), cv2.IMREAD_COLOR)
    if art is None:
        return JsonResponse({'ok': False, 'error': 'Chưa có ảnh tranh (upload hoặc art_path)'})

    kd, od = _dir(_KHUNG_SUB), _dir(_OUT_SUB)
    results, errors = [], []
    for kid in ids:
        meta = _read_json(os.path.join(kd, kid + '.json'))
        tpl = cv2.imread(os.path.join(kd, kid + '.png'))
        if not meta or tpl is None:
            errors.append(f'{kid}: khung hỏng/thiếu file')
            continue
        try:
            scene = _compose_scene(tpl, meta.get('spec') or {}, art)
            web = _finish(scene, ma, kt, colors, logo, square=False)
            shp = _finish(scene, ma, kt, colors, logo, square=True)
            stem = f'out-{uuid.uuid4().hex[:12]}'
            web.save(os.path.join(od, stem + '_web.jpg'), quality=92)
            shp.save(os.path.join(od, stem + '_shopee.jpg'), quality=92)
            rec = {'ma': ma, 'kt': kt, 'colors': colors,
                   'khung_name': meta.get('name', ''),
                   'web': f'/media/xuong_anh/out/{stem}_web.jpg',
                   'shopee': f'/media/xuong_anh/out/{stem}_shopee.jpg',
                   'created': datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
            with open(os.path.join(od, stem + '.json'), 'w', encoding='utf-8') as fh:
                json.dump(rec, fh, ensure_ascii=False)
            rec['id'] = stem
            results.append(rec)
        except Exception as e:                       # 1 khung hỏng không chặn cả bộ
            errors.append(f'{meta.get("name", kid)}: {e}')
    return JsonResponse({'ok': bool(results), 'results': results, 'errors': errors})


@staff_required
def out_xoa(request):
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST only'})
    oid = (request.POST.get('id') or '').strip()
    if not _ID_RE.match(oid):
        return JsonResponse({'ok': False, 'error': 'id sai'})
    d = _dir(_OUT_SUB)
    for suf in ('_web.jpg', '_shopee.jpg', '.json'):
        try:
            os.remove(os.path.join(d, oid + suf))
        except OSError:
            pass
    return JsonResponse({'ok': True})
