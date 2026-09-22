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
    for p in printers:
        p.start()
    sess = requests.Session()
    log('Agent chạy. Server:', server, '| máy:', ', '.join(p.name for p in printers))

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
                run_command(sess, server, key, by_serial, c)
        except Exception as e:
            log('vòng lặp lỗi:', e)
        time.sleep(hb)


def run_command(sess, server, key, by_serial, c):
    cid = c.get('id')
    p = by_serial.get(str(c.get('serial')))
    action = c.get('action')
    status, message = 'xong', ''
    try:
        if not p:
            status, message = 'loi', 'Agent không có máy serial này'
        elif action in ('pause', 'resume', 'stop'):
            p.cmd(action)
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
        sess.post(server + '/quan-ly-in/agent/ack',
                  json={'key': key, 'id': cid, 'status': status, 'message': message}, timeout=15)
    except Exception:
        pass


if __name__ == '__main__':
    main()
