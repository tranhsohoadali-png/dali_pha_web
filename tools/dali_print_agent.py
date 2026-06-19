# -*- coding: utf-8 -*-
"""DALI Print Agent — cau noi tu phan mem GHEP IN (web) sang FlexiPRINT (RIP).

Web KHONG tu RIP duoc (.prt la du lieu rieng cua may in EPS3200, chi Flexi tao ra).
Agent nay chay tren MAY IN, lam cau noi:

  1) HANG DOI WEB: poll https://mau.tranhdali.vn/api/rip-queue -> tai PDF moi ve ->
     tha vao HOT FOLDER cua Flexi -> bao trang thai nguoc lai (sent / done).
  2) THEO DOI THU MUC (du phong): Downloads (ghep_*.pdf) + C:\\DALI_DROP (*.pdf)
     -> cung copy vao hot folder Flexi (de in tay nhanh, khong qua web).
  3) (Tuy chon) THEO DOI OUTPUT_DIR: khi Flexi RIP xong sinh file .prt -> bao 'RIP xong'.

Flexi tu nhap file trong hot folder vao hang doi (kem ICC Skycolor + dither cua setup).
Chay nen: dali_print_agent.bat. Chi dung thu vien chuan Python.
"""
import os
import time
import glob
import json
import ssl
import shutil
import urllib.request
import urllib.parse
from collections import deque

# ===================== CAU HINH (sua o day) =====================
WEB_BASE = "https://mau.tranhdali.vn"          # dia chi web Ghep in
RIP_KEY = ""                                    # DAN "Khoa Agent" tu trang Ghep in vao day
HOTFOLDER = r"C:\Program Files (x86)\SAi\FlexiPRINT 19 RIPControl Edition\Jobs and Settings\Jobs\RIPControl\PRINTTYPE-SC"
OUTPUT_DIR = r""                                # (tuy chon) thu muc Flexi xuat .prt -> de bao 'RIP xong'. Trong = bo qua.
DROP_DIR = r"C:\DALI_DROP"
DOWNLOADS = os.path.join(os.path.expanduser("~"), "Downloads")
WATCH = [(DOWNLOADS, "ghep_*.pdf"), (DROP_DIR, "*.pdf")]
POLL_SECONDS = 3.0
# ================================================================

_HERE = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(_HERE, "dali_agent_seen.json")
LOG_FILE = os.path.join(_HERE, "dali_agent.log")
_CTX = ssl.create_default_context()
_SENT_AWAIT = deque()       # (job_id, filename) cho cac job da gui, cho .prt


def log(msg):
    line = time.strftime("%Y-%m-%d %H:%M:%S") + "  " + str(msg)
    try:
        print(line)
    except Exception:
        pass
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _load_state():
    try:
        d = json.load(open(STATE_FILE, encoding="utf-8"))
        return set(d.get("files", [])), set(d.get("jobs", []))
    except Exception:
        return set(), set()


def _save_state(files, jobs):
    try:
        json.dump({"files": sorted(files), "jobs": sorted(jobs)},
                  open(STATE_FILE, "w", encoding="utf-8"))
    except Exception as e:
        log("Khong luu duoc state: %s" % e)


def _stable(path, wait=1.0):
    try:
        s1 = os.path.getsize(path)
        if s1 <= 0:
            return False
        time.sleep(wait)
        if os.path.getsize(path) != s1:
            return False
        with open(path, "rb"):
            pass
        return True
    except OSError:
        return False


def _unique_dest(folder, name):
    dst = os.path.join(folder, name)
    if not os.path.exists(dst):
        return dst
    base, ext = os.path.splitext(name)
    i = 2
    while os.path.exists(os.path.join(folder, "%s_%d%s" % (base, i, ext))):
        i += 1
    return os.path.join(folder, "%s_%d%s" % (base, i, ext))


# ---------------- HTTP (web hang doi) ----------------
def _http_json(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": "DALI-Agent"})
    with urllib.request.urlopen(req, timeout=timeout, context=_CTX) as r:
        return json.loads(r.read().decode("utf-8"))


def _http_post(url, data, timeout=20):
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(url, data=body, headers={"User-Agent": "DALI-Agent"})
    with urllib.request.urlopen(req, timeout=timeout, context=_CTX) as r:
        return r.read().decode("utf-8")


def _http_download(url, dst, timeout=300):
    req = urllib.request.Request(url, headers={"User-Agent": "DALI-Agent"})
    tmp = dst + ".part"
    with urllib.request.urlopen(req, timeout=timeout, context=_CTX) as r, open(tmp, "wb") as f:
        shutil.copyfileobj(r, f)
    os.replace(tmp, dst)


def _report(job_id, status, message="", prt_mb=None):
    if not (RIP_KEY and WEB_BASE):
        return
    data = {"key": RIP_KEY, "id": job_id, "status": status, "message": message}
    if prt_mb is not None:
        data["prt_mb"] = prt_mb
    try:
        _http_post(WEB_BASE.rstrip("/") + "/api/rip-status", data)
    except Exception as e:
        log("Loi bao trang thai #%s: %s" % (job_id, e))


def poll_web(job_seen):
    """Lay job PENDING tu web -> tai PDF -> tha vao hot folder -> bao 'sent'."""
    if not (RIP_KEY and WEB_BASE):
        return False
    try:
        d = _http_json(WEB_BASE.rstrip("/") + "/api/rip-queue?key=" + urllib.parse.quote(RIP_KEY))
    except Exception as e:
        log("Khong goi duoc web (kiem tra mang/khoa): %s" % e)
        return False
    changed = False
    for job in d.get("jobs", []):
        jid = job.get("id")
        if jid in job_seen:
            continue
        job_seen.add(jid)
        changed = True
        url = job.get("pdf_url", "")
        name = os.path.basename(urllib.parse.urlparse(url).path) or ("job%s.pdf" % jid)
        try:
            dst = _unique_dest(HOTFOLDER, name)
            _http_download(url, dst)
            log("Web job #%s -> Flexi: %s" % (jid, os.path.basename(dst)))
            _report(jid, "sent", "Da day vao hot folder Flexi")
            _SENT_AWAIT.append((jid, os.path.basename(dst)))
        except Exception as e:
            log("Loi tai job #%s: %s" % (jid, e))
            _report(jid, "error", "Agent tai/copy loi: " + str(e)[:100])
    return changed


def watch_output(prt_seen):
    """(Tuy chon) Khi Flexi RIP xong sinh .prt trong OUTPUT_DIR -> bao 'RIP xong'."""
    if not (OUTPUT_DIR and os.path.isdir(OUTPUT_DIR) and RIP_KEY):
        return False
    changed = False
    for p in glob.glob(os.path.join(OUTPUT_DIR, "*.prt")):
        if p in prt_seen:
            continue
        if not _stable(p, wait=2.0):
            continue
        prt_seen.add(p)
        changed = True
        mb = round(os.path.getsize(p) / 1048576.0, 1)
        if _SENT_AWAIT:
            jid, _ = _SENT_AWAIT.popleft()
            _report(jid, "done", "RIP xong (.prt %s MB)" % mb, prt_mb=mb)
            log("RIP xong job #%s: %s (%s MB)" % (jid, os.path.basename(p), mb))
    return changed


# ---------------- Theo doi thu muc (du phong, in tay) ----------------
def _find_files(watch):
    out = []
    for folder, pattern in watch:
        try:
            if os.path.isdir(folder):
                out += [p for p in glob.glob(os.path.join(folder, pattern)) if os.path.isfile(p)]
        except OSError:
            pass
    return out


def process_folders(watch, hotfolder, file_seen, stable_wait=1.0):
    pushed = []
    for p in _find_files(watch):
        key = os.path.normcase(os.path.abspath(p))
        if key in file_seen:
            continue
        if not _stable(p, wait=stable_wait):
            continue
        try:
            dst = _unique_dest(hotfolder, os.path.basename(p))
            shutil.copy2(p, dst)
            file_seen.add(key)
            pushed.append(os.path.basename(dst))
            log("Tha vao Flexi (thu muc): %s" % os.path.basename(dst))
        except Exception as e:
            log("Loi copy %s: %s" % (p, e))
    return pushed


def main():
    os.makedirs(DROP_DIR, exist_ok=True)
    if not os.path.isdir(HOTFOLDER):
        log("CANH BAO: khong thay hot folder Flexi: %s" % HOTFOLDER)
    file_seen, job_seen = _load_state()
    first_run = not os.path.exists(STATE_FILE)
    if first_run:
        for p in _find_files(WATCH):
            file_seen.add(os.path.normcase(os.path.abspath(p)))
        _save_state(file_seen, job_seen)
        log("Khoi dong lan dau — bo qua %d file cu trong thu muc." % len(file_seen))
    log("DALI Print Agent dang chay.")
    log("  Hang doi web: %s" % (WEB_BASE if RIP_KEY else "(chua dat RIP_KEY -> bo qua)"))
    log("  Theo doi: %s" % ", ".join(f for f, _ in WATCH))
    log("  Hot folder Flexi: %s" % HOTFOLDER)
    prt_seen = set()
    while True:
        try:
            c1 = poll_web(job_seen)
            c2 = process_folders(WATCH, HOTFOLDER, file_seen)
            c3 = watch_output(prt_seen)
            if c1 or c2 or c3:
                _save_state(file_seen, job_seen)
        except Exception as e:
            log("Loi vong lap: %s" % e)
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
