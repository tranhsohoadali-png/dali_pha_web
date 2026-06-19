# -*- coding: utf-8 -*-
"""DALI Print Agent — cau noi tu phan mem ghep kho (web) sang FlexiPRINT (RIP).

Cach hoat dong: theo doi vai thu muc tren may Windows nay; khi co file PDF MOI,
copy vao HOT FOLDER cua Flexi -> Flexi tu nhap vao hang doi in (kem ICC/dither cua setup).

Mac dinh theo doi:
  - <Downloads>            chi file ten 'ghep_*.pdf'  (tai tu web ghep-in ve)
  - C:\\DALI_DROP           moi file '*.pdf'           (keo-tha tay de in bat ky)

Chay nen (khong cua so): dung dali_print_agent.bat.  Dung stdlib, khong can cai them.
"""
import os
import sys
import time
import glob
import json
import shutil

# ------- CAU HINH (sua o day neu can) -------
HOTFOLDER = r"C:\Program Files (x86)\SAi\FlexiPRINT 19 RIPControl Edition\Jobs and Settings\Jobs\RIPControl\PRINTTYPE-SC"
DROP_DIR = r"C:\DALI_DROP"
DOWNLOADS = os.path.join(os.path.expanduser("~"), "Downloads")
# (thu_muc, mau_ten): theo doi nhung file khop mau trong thu muc
WATCH = [
    (DOWNLOADS, "ghep_*.pdf"),
    (DROP_DIR, "*.pdf"),
]
POLL_SECONDS = 2.0
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dali_agent_seen.json")
LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dali_agent.log")
# --------------------------------------------


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


def _load_seen():
    try:
        return set(json.load(open(STATE_FILE, encoding="utf-8")))
    except Exception:
        return set()


def _save_seen(seen):
    try:
        json.dump(sorted(seen), open(STATE_FILE, "w", encoding="utf-8"))
    except Exception as e:
        log("Khong luu duoc state: %s" % e)


def _stable(path, wait=1.0):
    """File da ghi xong chua (kich thuoc on dinh + > 0 + mo doc duoc)."""
    try:
        s1 = os.path.getsize(path)
        if s1 <= 0:
            return False
        time.sleep(wait)
        if os.path.getsize(path) != s1:
            return False
        with open(path, "rb"):       # khong con bi khoa boi trinh duyet
            pass
        return True
    except OSError:
        return False


def _unique_dest(hotfolder, name):
    """Tranh ghi de neu trung ten trong hotfolder."""
    dst = os.path.join(hotfolder, name)
    if not os.path.exists(dst):
        return dst
    base, ext = os.path.splitext(name)
    i = 2
    while os.path.exists(os.path.join(hotfolder, "%s_%d%s" % (base, i, ext))):
        i += 1
    return os.path.join(hotfolder, "%s_%d%s" % (base, i, ext))


def find_candidates(watch):
    out = []
    for folder, pattern in watch:
        try:
            if not os.path.isdir(folder):
                continue
            for p in glob.glob(os.path.join(folder, pattern)):
                if os.path.isfile(p):
                    out.append(p)
        except OSError:
            pass
    return out


def process_once(watch, hotfolder, seen, stable_wait=1.0):
    """Quet 1 luot; copy file moi vao hotfolder. Tra danh sach ten da day."""
    pushed = []
    for p in find_candidates(watch):
        key = os.path.normcase(os.path.abspath(p))
        if key in seen:
            continue
        if not _stable(p, wait=stable_wait):
            continue   # con dang tai -> de luot sau
        try:
            dst = _unique_dest(hotfolder, os.path.basename(p))
            shutil.copy2(p, dst)
            seen.add(key)
            pushed.append(os.path.basename(dst))
            log("Da day sang Flexi: %s" % os.path.basename(dst))
        except Exception as e:
            log("Loi copy %s: %s" % (p, e))
    return pushed


def main():
    os.makedirs(DROP_DIR, exist_ok=True)
    if not os.path.isdir(HOTFOLDER):
        log("CANH BAO: khong thay hotfolder Flexi: %s" % HOTFOLDER)
    seen = _load_seen()
    first_run = not os.path.exists(STATE_FILE)
    if first_run:
        # Lan dau: danh dau cac file dang co la 'da xem' de KHONG in lai backlog cu.
        for p in find_candidates(WATCH):
            seen.add(os.path.normcase(os.path.abspath(p)))
        _save_seen(seen)
        log("Khoi dong lan dau — bo qua %d file cu." % len(seen))
    log("DALI Print Agent dang chay. Theo doi: %s" % ", ".join(f for f, _ in WATCH))
    log("Hotfolder Flexi: %s" % HOTFOLDER)
    while True:
        try:
            if process_once(WATCH, HOTFOLDER, seen):
                _save_seen(seen)
        except Exception as e:
            log("Loi vong lap: %s" % e)
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
