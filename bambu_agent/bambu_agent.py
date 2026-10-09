# -*- coding: utf-8 -*-
"""DALI Bambu Agent — cầu nối máy in Bambu A1 (LAN) <-> web mau.tranhdali.vn.

Chạy trên 1 PC Ở XƯỞNG (cùng WiFi/LAN với máy in). VPS ở xa không với tới máy in
trong LAN, nên agent này:
  1) Kết nối từng máy A1 qua MQTT (port 8883, TLS, user 'bblp', pass = Access Code),
     đọc trạng thái (tiến độ %, nhiệt vòi/bàn, số lớp, thời gian còn lại, lỗi).
  2) Cứ vài giây POST trạng thái tất cả máy về web (heartbeat).
  3) POLL lệnh từ web (in / tạm dừng / tiếp tục / dừng) rồi thực thi:
       - in: tải .3mf từ web -> FTPS (port 990) nạp vào thẻ SD máy -> lệnh project_file.
       - dừng/tạm dừng/tiếp tục: publish lệnh MQTT.
     Báo kết quả lại (ack).

Access Code CHỈ nằm trong config.json trên PC xưởng — KHÔNG gửi lên web.

Cài: pip install -r requirements.txt   (paho-mqtt, requests)
Chạy: python bambu_agent.py            (hoặc bấm run.bat trên Windows)
"""
import ftplib
import json
import os
import ssl
import subprocess
import sys
import threading
import time

try:
    import requests
except ImportError:
    sys.exit("Thiếu thư viện 'requests'. Chạy: pip install -r requirements.txt")
try:
    import paho.mqtt.client as mqtt
except ImportError:
    sys.exit("Thiếu thư viện 'paho-mqtt'. Chạy: pip install -r requirements.txt")

HERE = os.path.dirname(os.path.abspath(__file__))
CFG_PATH = os.path.join(HERE, 'config.json')

PUSHALL = {"pushing": {"sequence_id": "0", "command": "pushall"}}
PUSHALL_EVERY = 50          # xin full trạng thái lại mỗi 50s (report thường là delta)
REPORT_FRESH_S = 30         # còn coi là "online" nếu có report trong 30s gần đây


def log(*a):
    print(time.strftime('[%H:%M:%S]'), *a, flush=True)


# ---------------- FTPS ngầm (implicit TLS, port 990) cho Bambu ----------------
class ImplicitFTP_TLS(ftplib.FTP_TLS):
    """FTP_TLS hỗ trợ implicit FTPS (bọc TLS ngay từ socket điều khiển)."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._sock = None

    @property
    def sock(self):
        return self._sock

    @sock.setter
    def sock(self, value):
        if value is not None and not isinstance(value, ssl.SSLSocket):
            value = self.context.wrap_socket(value)
        self._sock = value

    def ntransfercmd(self, cmd, rest=None):
        conn, size = ftplib.FTP.ntransfercmd(self, cmd, rest)
        conn = self.sock.context.wrap_socket(
            conn, server_hostname=self.host, session=self.sock.session)
        return conn, size


def ftps_upload(ip, access_code, local_path, remote_name):
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    ftp = ImplicitFTP_TLS(context=ctx)
    ftp.connect(host=ip, port=990, timeout=60)
    ftp.login(user='bblp', passwd=access_code)
    ftp.prot_p()
    with open(local_path, 'rb') as f:
        ftp.storbinary('STOR ' + remote_name, f)
    try:
        ftp.quit()
    except Exception:
        ftp.close()


# ---------------- 1 máy in ----------------
class Printer(object):
    def __init__(self, conf):
        self.name = conf.get('name') or conf.get('serial') or conf.get('ip')
        self.ip = conf['ip']
        self.serial = str(conf['serial'])
        self.access = str(conf['access_code'])
        self.state = {}                # print object gần nhất (đã gộp delta)
        self.last_report = 0.0
        self.lock = threading.Lock()
        self._seq = 0
        self._last_pushall = 0.0
        self.topic_req = 'device/%s/request' % self.serial
        self.topic_rep = 'device/%s/report' % self.serial
        self.cl = self._mk_client()

    def _mk_client(self):
        try:
            cl = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1,
                             client_id='dali-%s' % self.serial)   # paho 2.x
        except (AttributeError, TypeError):
            cl = mqtt.Client(client_id='dali-%s' % self.serial)   # paho 1.x
        cl.username_pw_set('bblp', self.access)
        cl.tls_set(cert_reqs=ssl.CERT_NONE)
        cl.tls_insecure_set(True)
        cl.reconnect_delay_set(min_delay=1, max_delay=30)
        cl.on_connect = self._on_connect
        cl.on_message = self._on_message
        return cl

    def start(self):
        try:
            self.cl.connect_async(self.ip, 8883, keepalive=60)
            self.cl.loop_start()
            log('MQTT nối', self.name, self.ip)
        except Exception as e:
            log('LỖI nối', self.name, e)

    def _on_connect(self, cl, u, flags, rc, *a):
        if rc == 0:
            cl.subscribe(self.topic_rep)
            self.pushall()
            log(self.name, 'đã kết nối + subscribe')
        else:
            log(self.name, 'connect rc=', rc, '(sai Access Code? bật LAN Mode chưa?)')

    def _on_message(self, cl, u, msg):
        try:
            data = json.loads(msg.payload.decode('utf-8'))
        except Exception:
            return
        pr = data.get('print')
        if isinstance(pr, dict):
            with self.lock:
                self.state.update(pr)      # gộp delta
                self.last_report = time.time()

    def pushall(self):
        try:
            self.cl.publish(self.topic_req, json.dumps(PUSHALL))
            self._last_pushall = time.time()
        except Exception:
            pass

    def tick(self):
        if time.time() - self._last_pushall >= PUSHALL_EVERY:
            self.pushall()

    def _seqid(self):
        self._seq += 1
        return str(self._seq)

    def cmd(self, command):
        """pause/resume/stop."""
        payload = {"print": {"sequence_id": self._seqid(), "command": command, "param": ""}}
        self.cl.publish(self.topic_req, json.dumps(payload))

    def print_file(self, remote_name, use_ams=False):
        subtask = os.path.splitext(remote_name)[0]
        payload = {"print": {
            "sequence_id": self._seqid(), "command": "project_file",
            "param": "Metadata/plate_1.gcode",
            "url": "file:///sdcard/%s" % remote_name,
            "bed_type": "auto", "timelapse": False, "bed_leveling": True,
            "flow_cali": True, "vibration_cali": True, "layer_inspect": True,
            "use_ams": bool(use_ams),
            "ams_mapping": [0, 1, 2, 3] if use_ams else [0],
            "subtask_name": subtask,
            "profile_id": "0", "project_id": "0", "subtask_id": "0", "task_id": "0",
        }}
        self.cl.publish(self.topic_req, json.dumps(payload))

    def snapshot(self):
        with self.lock:
            s = dict(self.state)
            fresh = (time.time() - self.last_report) <= REPORT_FRESH_S
        err = s.get('mc_print_error_code') or s.get('print_error') or 0
        return {
            'serial': self.serial, 'ip': self.ip,
            'online': bool(self.cl.is_connected()) and fresh,
            'gcode_state': s.get('gcode_state') or '',
            'percent': s.get('mc_percent') or 0,
            'remain_min': s.get('mc_remaining_time') or 0,
            'layer': s.get('layer_num') or 0, 'total_layer': s.get('total_layer_num') or 0,
            'nozzle': s.get('nozzle_temper') or 0, 'nozzle_t': s.get('nozzle_target_temper') or 0,
            'bed': s.get('bed_temper') or 0, 'bed_t': s.get('bed_target_temper') or 0,
            'stage': str(s.get('stg_cur', '')),
            'error': str(err),
            'file': s.get('subtask_name') or s.get('gcode_file') or '',
        }


# ---------------- CẮT FILE bằng Bambu Studio CLI ----------------
DEFAULT_BAMBU = r'C:\Program Files\Bambu Studio\bambu-studio.exe'


def _dl(sess, url, dest):
    with sess.get(url, stream=True, timeout=180) as r:
        r.raise_for_status()
        with open(dest, 'wb') as f:
            for chunk in r.iter_content(65536):
                f.write(chunk)
    return dest


def _read_result(workdir):
    """Đọc result.json (nguồn tin cậy; KHÔNG dùng exit code/stdout). Trả (rc, plates)."""
    rj = os.path.join(workdir, 'result.json')
    if not os.path.exists(rj):
        return None, []
    with open(rj, encoding='utf-8') as f:
        d = json.load(f)
    return d.get('return_code'), (d.get('sliced_plates') or [])


def _plate_info(plates):
    """Tổng hợp khay 1: số vật thể, thời gian (phút), nhựa (g)."""
    if not plates:
        return 0, 0, 0.0
    p = plates[0]
    objs = len(p.get('objects') or [])
    secs = p.get('total_predication') or 0
    g = sum(f.get('total_used_g', 0) for f in (p.get('filaments') or []))
    return objs, int(round(secs / 60.0)), round(float(g), 1)


def _run_slice(exe, inputs, workdir, nozzle_t=0, bed_t=0, timeout=600):
    """Chạy Bambu CLI: inputs (template.3mf + các STL lặp theo SL) -> out.gcode.3mf.
    Dùng list args (không qua shell) nên không bị lỗi path. Đọc result.json để biết kết quả."""
    os.makedirs(workdir, exist_ok=True)
    args = [exe] + list(inputs) + ['--arrange', '1', '--slice', '1']
    if nozzle_t:
        args += ['--nozzle-temperature', str(int(nozzle_t)),
                 '--nozzle-temperature-initial-layer', str(int(nozzle_t))]
    if bed_t:
        args += ['--hot-plate-temp', str(int(bed_t)), '--hot-plate-temp-initial-layer', str(int(bed_t))]
    args += ['--outputdir', workdir, '--export-3mf', 'out.gcode.3mf']   # tên PHẢI tương đối khi có --outputdir
    subprocess.run(args, capture_output=True, timeout=timeout)
    rc, plates = _read_result(workdir)
    out3mf = os.path.join(workdir, 'out.gcode.3mf')
    if rc != 0 or not os.path.exists(out3mf):
        raise RuntimeError('Cắt lỗi (Bambu rc=%s). Kiểm tra template/preset.' % rc)
    return out3mf, plates


def slice_tags(sl, cmd, sess, workdir):
    """Cắt 1 lệnh slice_print. Trả (out_gcode_3mf, info{so_the,actual_phut,nhua_g})."""
    exe = sl.get('bambu_exe') or DEFAULT_BAMBU
    if not os.path.exists(exe):
        raise RuntimeError('Không thấy Bambu Studio: %s' % exe)
    dau = str(cmd.get('dau_phun') or '0.4')
    tpl = (sl.get('templates') or {}).get(dau)
    if not tpl or not os.path.exists(tpl):
        raise RuntimeError('Chưa cấu hình template .3mf cho đầu phun %s (sửa config.json).' % dau)
    items = cmd.get('items') or []
    if not items:
        raise RuntimeError('Lệnh không có thẻ nào.')
    nozzle_t, bed_t = cmd.get('nhiet_do') or 0, cmd.get('bed_nhiet') or 0
    os.makedirs(workdir, exist_ok=True)
    # tải STL từng loại 1 lần
    for it in items:
        it['_local'] = _dl(sess, it['stl_url'], os.path.join(workdir, os.path.basename(it['ten_file']).replace(' ', '_')))

    target = int(cmd.get('target_phut') or 0)
    if target and len(items) == 1:
        # CHẾ ĐỘ THỜI GIAN: tính số lượng cho vừa thời gian (1 loại thẻ)
        it = items[0]
        per = it.get('phut_moi_the') or 0
        if per <= 0:                       # chưa khai phút/thẻ -> cắt thử 1 cái đo
            _, pl = _run_slice(exe, [tpl, it['_local']], os.path.join(workdir, 'probe'), nozzle_t, bed_t)
            _, per1, _ = _plate_info(pl)
            per = max(1, per1)
        qty = max(1, int(round(target / float(per))))
        it['_qty'] = qty
    else:
        for it in items:
            it['_qty'] = int(it.get('so_luong') or 0)
        if not any(it['_qty'] for it in items):
            raise RuntimeError('Không có số lượng.')

    requested = sum(it['_qty'] for it in items)
    inputs = [tpl]
    for it in items:
        inputs += [it['_local']] * it['_qty']
    out3mf, plates = _run_slice(exe, inputs, workdir, nozzle_t, bed_t)
    objs, mins, g = _plate_info(plates)
    if len(plates) > 1 or objs < requested:
        raise RuntimeError('Quá 1 khay: chỉ vừa %d/%d thẻ trên 1 khay. Giảm số lượng/thời gian rồi thử lại.'
                           % (objs, requested))
    return out3mf, {'so_the': objs, 'actual_phut': mins, 'nhua_g': g}


# ---------------- Agent ----------------
def load_cfg():
    if not os.path.exists(CFG_PATH):
        sys.exit("Chưa có config.json. Chép config.example.json -> config.json rồi điền thông tin.")
    with open(CFG_PATH, encoding='utf-8') as f:
        return json.load(f)


def main():
    cfg = load_cfg()
    server = (cfg.get('server') or '').rstrip('/')
    key = cfg.get('key') or ''
    hb = int(cfg.get('heartbeat_s') or 5)
    if not server or not key:
        sys.exit("config.json thiếu 'server' hoặc 'key'.")
    printers = [Printer(c) for c in (cfg.get('printers') or []) if c.get('ip') and c.get('serial')]
    if not printers:
        sys.exit("config.json chưa khai báo máy in nào (printers).")
    by_serial = {p.serial: p for p in printers}
    slicer = cfg.get('slicer') or {}
    for p in printers:
        p.start()
    sess = requests.Session()
    log('Agent chạy. Server:', server, '| máy:', ', '.join(p.name for p in printers),
        '| cắt thẻ:', 'BẬT' if slicer.get('templates') else 'tắt (chưa cấu hình slicer)')

    while True:
        try:
            for p in printers:
                p.tick()
            # 1) heartbeat
            machines = [p.snapshot() for p in printers]
            try:
                sess.post(server + '/quan-ly-in/agent/heartbeat',
                          json={'key': key, 'machines': machines}, timeout=15)
            except Exception as e:
                log('heartbeat lỗi:', e)
            # 2) lấy lệnh
            try:
                r = sess.get(server + '/quan-ly-in/agent/pull',
                             headers={'X-API-Key': key}, timeout=15)
                cmds = (r.json() or {}).get('commands') or []
            except Exception:
                cmds = []
            for c in cmds:
                run_command(sess, server, key, by_serial, c, slicer)
        except Exception as e:
            log('vòng lặp lỗi:', e)
        time.sleep(hb)


def run_command(sess, server, key, by_serial, c, slicer):
    cid = c.get('id')
    p = by_serial.get(str(c.get('serial')))
    action = c.get('action')
    status, message = 'xong', ''
    extra = {}
    try:
        if not p:
            status, message = 'loi', 'Agent không có máy serial này'
        elif action in ('pause', 'resume', 'stop'):
            p.cmd(action)
        elif action == 'slice_print':
            workdir = os.path.join(HERE, '_slice', str(cid))
            log(p.name, 'cắt thẻ (Bambu Studio)…')
            out3mf, info = slice_tags(slicer, c, sess, workdir)
            remote = 'dali_%s.gcode.3mf' % str(cid)
            log(p.name, 'cắt xong %d thẻ (~%d phút, %.0f g) — nạp FTPS…'
                % (info['so_the'], info['actual_phut'], info['nhua_g']))
            ftps_upload(p.ip, p.access, out3mf, remote)
            p.print_file(remote, use_ams=False)
            extra = info
            message = 'Đã cắt %d thẻ (~%d phút, %.0f g) + phát lệnh in' % (
                info['so_the'], info['actual_phut'], info['nhua_g'])
        elif action == 'print':
            url, fname = c.get('file_url'), (c.get('file_name') or 'print.3mf')
            fname = os.path.basename(fname).replace(' ', '_')
            tmp = os.path.join(HERE, '_dl_' + fname)
            log(p.name, 'tải file in:', url)
            with sess.get(url, stream=True, timeout=120) as resp:
                resp.raise_for_status()
                with open(tmp, 'wb') as f:
                    for chunk in resp.iter_content(65536):
                        f.write(chunk)
            log(p.name, 'nạp FTPS lên máy…')
            ftps_upload(p.ip, p.access, tmp, fname)
            p.print_file(fname, use_ams=bool(c.get('use_ams')))
            try:
                os.remove(tmp)
            except Exception:
                pass
            message = 'Đã nạp %s + phát lệnh in' % fname
        else:
            status, message = 'loi', 'Lệnh lạ: %s' % action
    except Exception as e:
        status, message = 'loi', str(e)[:180]
    log('lệnh', action, '->', status, message)
    try:
        payload = {'key': key, 'id': cid, 'status': status, 'message': message}
        payload.update(extra)      # slice_print: so_the / actual_phut / nhua_g
        sess.post(server + '/quan-ly-in/agent/ack', json=payload, timeout=15)
    except Exception:
        pass


if __name__ == '__main__':
    main()
