"""
Fishao Auto-Angler v6 (haelt den blauen Marker in der Mitte der gruenen Zone)

Installation:  pip install mss numpy pyautogui

Ablauf:
    1. Fishao oeffnen, Skript starten, im Spiel die Angel auswerfen (Spielfenster hat Fokus).
    2. Das Skript sucht den Farbbalken auf allen Monitoren. Sobald er da ist,
       macht es einmal einen kurzen Tastentest (links/rechts), misst, welche Taste
       den Fehler (Zonenmitte - Marker) verkleinert, und regelt dann.
    Abbruch: Strg+C oder Maus in die obere linke Bildschirmecke.

Neu in v6:
    - Tasten per SendInput (Scancode / Extended-Key) statt pyautogui
    - Automatische Richtungserkennung + Selbstkorrektur, falls die Regelung wegläuft
    - Mitte der gruenen Zone wird pro Bild neu aus dem Gruen-Verlauf bestimmt
      (egal wie gross die Zone oder die anderen Farbbereiche gerade sind)
    - Fehler wird auf die Balkenbreite normiert (keine staendige Saettigung)
    - Protokoll in fishao_log.csv (x, mitte, fehler, u, Taste)
"""
import sys
import time
import ctypes
from collections import deque
import numpy as np
import mss
import mss.tools
import pyautogui

# neuere mss-Versionen: mss.MSS, ältere: mss.mss
MSS = getattr(mss, "MSS", None) or mss.mss

pyautogui.PAUSE = 0
pyautogui.FAILSAFE = True

# ---------------- Einstellungen ----------------
AUTO_DETECT = True    # False = manuelle Kalibrierung per Maus
MONITOR = 0           # 0 = ALLE Monitore durchsuchen, 1 = Monitor 1, 2 = Monitor 2 ...
SCAN_INTERVAL_S = 0.2
MIN_BAR_W = 80
MAX_GAP = 30
MIN_BAR_H = 8
MARGIN_Y = 6
AUTO_CLOSE_CATCH = True   # Fang-Fenster ("... GEFANGEN!") automatisch mit ESC schliessen
CATCH_COOLDOWN_S = 1.2    # Wartezeit zwischen zwei ESC-Druecken
CATCH_MAX_TRIES = 4       # maximal so viele ESC-Druecke pro Fenster (Schutz vor Dauerfeuer)
STRICT = True         # strenge Pruefung: Balken muss im beigen Dialog liegen und beide Marker haben
PANEL_MIN = 0.6       # Mindestanteil Dialog-Hintergrund ober-/unterhalb des Balkens

# Tastenmodus: "scancode" (Standard), "vk" (virtuelle Tastencodes), "pyautogui" (alt)
KEY_MODE = "scancode"
INVERT_KEYS = False   # True, falls die Tasten im Spiel verkehrt herum wirken
DO_PROBE = False      # Tastentest (stoert im laufenden Spiel, daher aus)
PROBE_TAP_S = 0.10
PROBE_WAIT_S = 0.10

# Regler (Fehler und Geschwindigkeit sind auf die halbe Balkenbreite normiert).
# Die Taste wird DURCHGEHEND GEHALTEN, solange die Regelgroesse u in dieselbe
# Richtung zeigt (wie ein Mensch). Der D-Anteil bremst vor dem Ueberschiessen.
KP = 4.0
LEAD_S = 0.30         # Vorausschau: der Regler arbeitet mit der Position, an der der Marker
                      # in LEAD_S Sekunden waere. Geht der Marker nach links, wird also
                      # rechts gedrueckt, geht er nach rechts, links (frueh gegenhalten).
VEL_WINDOW_S = 0.08   # Zeitfenster fuer die Geschwindigkeitsschaetzung
MIN_U = 0.10          # Taste wird gedrueckt, wenn |u| ueber MIN_U steigt
RELEASE_U = 0.05      # ... und erst losgelassen, wenn |u| unter RELEASE_U faellt (Hysterese)
LOOP_MIN_S = 0.004    # kurze Pause pro Durchlauf, um die CPU zu schonen
GONE_FRAMES = 12

DEBUG = False
DIAG = True
LOG = True            # fishao_log.csv schreiben

PROFILES = [
    dict(name="streng", bmax=110, mxmin=140, sat=120, red=100,
         g_min=140, g_dr=30, g_db=60, r_min=180, r_gmax=120, r_bmax=120, blue_min=150),
    dict(name="locker", bmax=150, mxmin=105, sat=70, red=60,
         g_min=105, g_dr=20, g_db=40, r_min=140, r_gmax=150, r_bmax=150, blue_min=110),
]
# ------------------------------------------------

IS_WIN = sys.platform == "win32"

# ---------- Tastatur-Eingabe ----------
if IS_WIN:
    from ctypes import wintypes

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                    ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                    ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                    ("dwExtraInfo", ctypes.c_size_t)]

    class _INPUTUNION(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT)]

    class INPUT(ctypes.Structure):
        _anonymous_ = ("u",)
        _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]

    KEYEVENTF_EXTENDEDKEY = 0x0001
    KEYEVENTF_KEYUP = 0x0002
    KEYEVENTF_SCANCODE = 0x0008
    KEYCODES = {"left": (0x25, 0x4B), "right": (0x27, 0x4D),
                "esc": (0x1B, 0x01)}  # (vk, scancode)

    try:
        ctypes.windll.winmm.timeBeginPeriod(1)  # genauere Timer (1 ms)
    except Exception:
        pass


def _send(key, down):
    if KEY_MODE == "pyautogui" or not IS_WIN:
        (pyautogui.keyDown if down else pyautogui.keyUp)(key)
        return
    vk, sc = KEYCODES[key]
    ext = KEYEVENTF_EXTENDEDKEY if key in ("left", "right") else 0  # nur Pfeiltasten sind "extended"
    flags = ext | (0 if down else KEYEVENTF_KEYUP)
    if KEY_MODE == "scancode":
        inp = INPUT(type=1, ki=KEYBDINPUT(0, sc, flags | KEYEVENTF_SCANCODE, 0, 0))
    else:
        inp = INPUT(type=1, ki=KEYBDINPUT(vk, sc, flags, 0, 0))
    ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


def release_all():
    """Laesst nur eine Taste los, die das Skript selbst gerade haelt.
    Ohne gehaltene Taste wird NICHTS gesendet, damit die eigene Steuerung im Spiel
    ausserhalb des Minispiels nicht beeinflusst wird."""
    h = globals().get("holder")
    if h is None or h.key is None:
        return
    _send(h.key, False)
    h.key = None


def key_for(direction):
    """direction +1 = Fehler soll groesser werden -> 'rechts' (normal)."""
    key = "right" if direction > 0 else "left"
    if INVERT_KEYS:
        key = "left" if key == "right" else "right"
    return key


def tap(key, seconds):
    if key in ("left", "right"):
        _send("left" if key == "right" else "right", False)
    _send(key, True)
    time.sleep(seconds)
    _send(key, False)


class Hold:
    """Haelt hoechstens eine Pfeiltaste gedrueckt und sendet nur bei Aenderungen."""

    def __init__(self):
        self.key = None

    def set(self, key):
        if key == self.key:
            return
        if self.key is not None:
            _send(self.key, False)
        if key is not None:
            _send(key, True)
        self.key = key

    def refresh(self):
        """Taste erneut druecken (Auto-Repeat), falls das Spiel Key-Events braucht."""
        if self.key is not None:
            _send(self.key, True)


holder = Hold()


# ---------- Balken suchen ----------
def channels(img):
    b = img[:, :, 0].astype(np.int16)  # mss liefert BGRA
    g = img[:, :, 1].astype(np.int16)
    r = img[:, :, 2].astype(np.int16)
    return r, g, b


def _mono(a, sign):
    dd = np.diff(a[::4])
    return float((sign * dd >= -3).mean()) if dd.size else 0.0


def _beige(r, g, b):
    """Farbe des Dialog-Hintergrunds (hellbeige, auf dem Bildschirm ca. 241/234/188)."""
    return ((r > 205) & (g > 195) & (b > 130) & (b < 240) &
            (r - b > 18) & (r - b < 100) & (g - b > 14) & (g - b < 90))


def _frac(m):
    return float(m.mean()) if m.size else 0.0


def validate_box(r, g, b, box, prof):
    """Strenge Pruefung eines Kandidaten. Gibt None zurueck, wenn er ein echter Balken im
    Dialog ist, sonst den Ablehnungsgrund als Text."""
    x0, y0, x1, y1 = box
    H, W = r.shape
    w, h = x1 - x0, y1 - y0 + 1
    if not (150 <= w <= 520):
        return f"Breite {w}px unplausibel"
    if not (3.5 <= w / max(h, 1) <= 12):
        return f"Seitenverhaeltnis {w / max(h, 1):.1f} unplausibel"
    m = max(MARGIN_Y - 2, 2)
    if y0 < 1 or y1 > H - 2 or x0 < 8 or x1 > W - 8:
        return "Balken liegt am Bildrand"
    beige = _beige(r, g, b)
    # Dialog-Hintergrund ober- und unterhalb sowie links und rechts neben dem Balken
    top = _frac(beige[y0:y0 + m, x0:x1])
    bot = _frac(beige[y1 - m + 1:y1 + 1, x0:x1])
    ym0, ym1 = y0 + h // 3, y1 - h // 3
    left = _frac(beige[ym0:ym1, x0 - 6:x0 - 2])
    right = _frac(beige[ym0:ym1, x1 + 2:x1 + 6])
    if top < PANEL_MIN or bot < PANEL_MIN:
        return f"kein Dialog-Hintergrund ober-/unterhalb (oben {top:.0%}, unten {bot:.0%})"
    if left < 0.5 or right < 0.5:
        return f"kein Dialog-Hintergrund links/rechts (links {left:.0%}, rechts {right:.0%})"
    # zwei blaue Marker-Dreiecke: eines in der oberen, eines in der unteren Haelfte
    sub = (slice(y0, y1 + 1), slice(x0, x1))
    bl = ((b[sub] > prof["blue_min"]) & (b[sub] > r[sub] + 60) & (b[sub] > g[sub] + 30))
    hh = bl.shape[0] // 2
    both = bl[:hh].any(axis=0) & bl[hh:].any(axis=0)
    if int(both.sum()) < 2:
        return "keine zwei blauen Marker-Dreiecke"
    return None


def _search(r, g, b, prof, stats):
    mx = np.maximum(r, g)
    mask = (b < prof["bmax"]) & (mx > prof["mxmin"]) & (mx - b > prof["sat"])
    h = mask.shape[0]

    row_counts = mask.sum(axis=1)
    if stats is not None and "max_zeile" not in stats:
        stats["farbpixel"] = int(mask.sum())
        stats["max_zeile"] = int(row_counts.max())
    rows = np.where(row_counts >= MIN_BAR_W)[0]
    if stats is not None and "zeilen_ok" not in stats:
        stats["zeilen_ok"] = int(rows.size)
    if rows.size == 0:
        return None

    # Pro Zeile ALLE Farbabschnitte behalten (nicht nur den laengsten: Sand neben dem
    # Dialog ist oft laenger als der Balken). Luecken werden nur ueberbrueckt, wenn sie
    # ueberwiegend blau sind (= Marker-Dreiecke), nicht den beigen Dialograhmen.
    bluish = b > r + 20
    blocks, open_blocks = [], []
    for y in map(int, rows):
        padded = np.concatenate(([0], mask[y].astype(np.int8), [0]))
        d = np.diff(padded)
        starts = np.where(d == 1)[0]
        ends = np.where(d == -1)[0]
        ms, me = [int(starts[0])], [int(ends[0])]
        for s_, e_ in zip(starts[1:], ends[1:]):
            gap = int(s_) - me[-1]
            if gap <= MAX_GAP and float(bluish[y, me[-1]:int(s_)].mean()) >= 0.5:
                me[-1] = int(e_)
            else:
                ms.append(int(s_))
                me.append(int(e_))
        for s, e in zip(ms, me):
            if e - s < MIN_BAR_W:
                continue
            for blk in open_blocks:
                py = blk[-1][0]
                _, s0, e0 = blk[0]
                if y - py <= 3 and abs(s - s0) <= 12 and abs(e - e0) <= 12:
                    blk.append((y, s, e))
                    break
            else:
                blk = [(y, s, e)]
                blocks.append(blk)
                open_blocks.append(blk)
        open_blocks = [blk for blk in open_blocks if y - blk[-1][0] <= 3]
    if not blocks:
        return None

    accepted = []
    for blk in blocks:
        if len(blk) < MIN_BAR_H:
            continue
        ys = [t[0] for t in blk]
        x0 = int(np.median([t[1] for t in blk]))
        x1 = int(np.median([t[2] for t in blk]))
        n = x1 - x0
        pr = r[ys, x0:x1].mean(axis=0)
        pg = g[ys, x0:x1].mean(axis=0)
        k = max(int(n * 0.12), 3)
        left_v = float((pr[:k] - pg[:k]).mean())
        right_v = float((pr[-k:] - pg[-k:]).mean())
        dr = g[ys, x0:x1] - r[ys, x0:x1]
        diff = np.percentile(dr, 75, axis=0)
        sm = np.convolve(diff, np.ones(9) / 9, mode="valid")
        ai = int(np.argmax(sm))
        pk = ai + 4
        peak_ok = (0.3 * n < pk < 0.7 * n) and float(sm.max()) > 30
        mono_ok = _mono(sm[:ai + 1], 1) > 0.9 and _mono(sm[ai:], -1) > 0.9
        mid_ok = bool(peak_ok and mono_ok)
        if stats is not None:
            stats.setdefault("kandidaten", []).append(
                f"[{prof['name']}] x={x0}-{x1} y={min(ys)}-{max(ys)} "
                f"links_rot={left_v:.0f} rechts_rot={right_v:.0f} gruen_mitte={mid_ok}")
        if left_v > prof["red"] and right_v > prof["red"] and mid_ok:
            accepted.append((n, x0, min(ys), x1, max(ys)))
    # breiteste Kandidaten zuerst; jeder muss die strenge Pruefung bestehen
    accepted.sort(reverse=True)
    for n, x0, y0, x1, y1 in accepted:
        cov = mask[:, x0:x1].mean(axis=1)
        while y0 > 0 and cov[y0 - 1] > 0.5:
            y0 -= 1
        while y1 < h - 1 and cov[y1 + 1] > 0.5:
            y1 += 1
        box = (x0, max(y0 - MARGIN_Y, 0), x1, min(y1 + MARGIN_Y, h - 1))
        reason = validate_box(r, g, b, box, prof) if STRICT else None
        if stats is not None and reason:
            stats.setdefault("kandidaten", []).append(
                f"[{prof['name']}] x={box[0]}-{box[2]} y={box[1]}-{box[3]} VERWORFEN: {reason}")
        if reason is None:
            return box
    return None


def find_bar(img, stats=None):
    r, g, b = channels(img)
    if stats is not None:
        stats["groesse"] = f"{img.shape[1]}x{img.shape[0]}"
        stats["helligkeit"] = round(float(img[:, :, :3].mean()), 1)
    for prof in PROFILES:
        res = _search(r, g, b, prof, stats)
        if res is not None:
            return (*res, prof)
    return None


# ---------- Fang-Fenster erkennen ("<Fisch> GEFANGEN!") ----------
def find_catch_window(img, stats=None):
    """Sucht das Fang-Fenster am gruenen Button 'ZEIGE FISCHBUCH'. Der Fischname und das
    Fischbild sind egal. Gepruefte Merkmale: gruener Button (ca. 157x35 px), beiger
    Dialog-Hintergrund rund um den Button und das gelbe Lineal links ueber dem Button.
    Gibt (x0, y0, x1, y1) des Buttons oder None zurueck."""
    r, g, b = channels(img)
    gm = (g > 110) & (g - r > 40) & (g - b > 90) & (r < 140) & (b < 70)
    rows = np.where(gm.sum(axis=1) >= 40)[0]
    if rows.size == 0:
        return None
    H, W = gm.shape
    blocks, open_blocks = [], []
    for y in map(int, rows):
        padded = np.concatenate(([0], gm[y].astype(np.int8), [0]))
        d = np.diff(padded)
        starts = np.where(d == 1)[0]
        ends = np.where(d == -1)[0]
        ms, me = [int(starts[0])], [int(ends[0])]
        for s_, e_ in zip(starts[1:], ends[1:]):
            if int(s_) - me[-1] <= 30:        # Luecken durch die weisse Schrift ueberbruecken
                me[-1] = int(e_)
            else:
                ms.append(int(s_))
                me.append(int(e_))
        for s, e in zip(ms, me):
            if not (135 <= e - s <= 185):
                continue
            for blk in open_blocks:
                _, s0, e0 = blk[0]
                if y - blk[-1][0] <= 5 and abs(s - s0) <= 8 and abs(e - e0) <= 8:
                    blk.append((y, s, e))
                    break
            else:
                blk = [(y, s, e)]
                blocks.append(blk)
                open_blocks.append(blk)
        open_blocks = [blk for blk in open_blocks if y - blk[-1][0] <= 5]

    beige = _beige(r, g, b)
    yel = (r > 225) & (g > 195) & (b < 150) & (r - b > 80)
    for blk in blocks:
        if not (20 <= len(blk) <= 60):
            continue
        x0 = int(np.median([t[1] for t in blk]))
        x1 = int(np.median([t[2] for t in blk]))
        y0, y1 = blk[0][0], blk[-1][0]
        bw, bh = x1 - x0, y1 - y0 + 1
        if x0 < 270 or x1 > W - 20 or y0 < 100 or y1 > H - 20:
            continue
        fill = float(gm[y0:y1 + 1, x0:x1].mean())
        if not (0.45 <= fill <= 0.95):
            continue
        ctx = {
            "unten": _frac(beige[y1 + 6:y1 + 14, x0:x1]),
            "oben": _frac(beige[y0 - 12:y0 - 4, x0:x1]),
            "links": _frac(beige[y0:y1, x0 - 12:x0 - 4]),
            "rechts": _frac(beige[y0:y1, x1 + 4:x1 + 12]),
        }
        ruler = _frac(yel[y0 - 85:y0 - 19, x0 - 255:x0 - 15])
        if stats is not None:
            stats.setdefault("catch_kandidaten", []).append(
                f"x={x0}-{x1} y={y0}-{y1} fuellung={fill:.2f} "
                + " ".join(f"{k}={v:.2f}" for k, v in ctx.items()) + f" lineal={ruler:.2f}")
        if min(ctx.values()) < 0.6 or ruler < 0.3:
            continue
        return x0, y0, x1, y1
    return None


# ---------- Messung waehrend des Minispiels ----------
def analyze(img, prof):
    """None, wenn kein Balken sichtbar ist, sonst (marker_x, zonen_mitte, halbbreite_zone)."""
    r, g, b = channels(img)
    h, w = r.shape

    blue = (b > prof["blue_min"]) & (b > r + 60) & (b > g + 30)
    if blue.mean() > 0.05:
        return None
    cnt = blue.sum(axis=0)
    cols = np.where(cnt >= 2)[0]
    if cols.size < 2:
        cols = np.where(cnt >= 1)[0]
    if cols.size < 2:
        return None
    marker_x = float(cols.mean())

    # Gruen-Verlauf entlang des Balkens (Marker-Dreiecke ausgeblendet)
    dr = np.where(blue, -255, g - r).astype(np.float32)
    diff = np.percentile(dr, 75, axis=0)
    sm = np.convolve(diff, np.ones(5, dtype=np.float32) / 5, mode="same")
    peak = float(sm.max())
    if peak < 25:
        return None
    gc = np.where(sm > 0.7 * peak)[0]
    wts = sm[gc]
    center = float((wts * gc).sum() / wts.sum())
    half_w = max((gc.max() - gc.min()) / 2, 4.0)
    return marker_x, center, half_w


def measure(sct, region, prof):
    res = analyze(np.array(sct.grab(region)), prof)
    if res is None:
        return None
    return res[1] - res[0]  # Fehler in Pixeln (Mitte - Marker)


def probe_keys(sct, region, prof):
    """Misst, welche Taste den Fehler (Mitte - Marker) verkleinert. Setzt INVERT_KEYS."""
    global INVERT_KEYS
    e0 = measure(sct, region, prof)
    tap("right", PROBE_TAP_S)
    time.sleep(PROBE_WAIT_S)
    e1 = measure(sct, region, prof)
    tap("left", PROBE_TAP_S)
    time.sleep(PROBE_WAIT_S)
    e2 = measure(sct, region, prof)
    if None in (e0, e1, e2):
        print("Tastentest: Messung unvollstaendig, behalte INVERT_KEYS =", INVERT_KEYS)
        return
    # Wirkung von 'rechts' auf den Fehler minus Wirkung von 'links' (Eigenbewegung faellt weg)
    effect = (e1 - e0) - (e2 - e1)
    print(f"Tastentest: Fehler {e0:.1f} -> {e1:.1f} (rechts) -> {e2:.1f} (links), Wirkung {effect:+.1f}px")
    if effect < -2:
        INVERT_KEYS = False
        print("  'rechts' verkleinert den Fehler bei positivem Fehler: normale Richtung.")
    elif effect > 2:
        INVERT_KEYS = True
        print("  Richtung ist umgekehrt: INVERT_KEYS = True gesetzt.")
    else:
        print("  Kein klarer Effekt gemessen (kommen die Tasten im Spiel an?). Behalte", INVERT_KEYS)


def calibrate():
    input("Maus auf OBERE LINKE Ecke des Balkens (inkl. Dreiecke) -> ENTER ")
    x1, y1 = pyautogui.position()
    input("Maus auf UNTERE RECHTE Ecke des Balkens (inkl. Dreiecke) -> ENTER ")
    x2, y2 = pyautogui.position()
    return {"left": min(x1, x2), "top": min(y1, y2),
            "width": abs(x2 - x1), "height": abs(y2 - y1)}


def save_png(bgra, path):
    rgb = np.ascontiguousarray(bgra[:, :, [2, 1, 0]])
    mss.tools.to_png(rgb.tobytes(), (rgb.shape[1], rgb.shape[0]), output=path)


def main():
    region = None
    prof = PROFILES[0]
    if not AUTO_DETECT:
        region = calibrate()
        print("Region:", region)
    print("Suche Balken auf",
          "allen Monitoren" if MONITOR == 0 else f"Monitor {MONITOR}",
          f"... Tastenmodus: {KEY_MODE} (Strg+C zum Beenden)")

    log = open("fishao_log.csv", "w", encoding="utf-8") if LOG else None
    if log:
        log.write("t;x;mitte;halbzone;fehler_px;vel_n;u;taste\n")

    err_hist = deque()
    vel = 0.0
    missing = 0
    probed = not DO_PROBE
    t_game = 0.0
    last_esc = 0.0
    catch_tries = 0

    with MSS() as sct:
        mon = sct.monitors[MONITOR]
        try:
            first = sct.grab(mon)
            mss.tools.to_png(first.rgb, first.size, output="debug_screen.png")
            print(f"Erfasster Bereich: {first.width}x{first.height} "
                  f"-> Kontrollbild: debug_screen.png")
        except Exception as e:
            print("Konnte Kontrollbild nicht speichern:", e)
        last_hb = last_diag = time.time()
        best_score = 0

        while True:
            # ---- Suchphase ----
            if region is None:
                release_all()
                shot = np.array(sct.grab(mon))
                stats = {}
                found = find_bar(shot, stats)
                if found is None and AUTO_CLOSE_CATCH:
                    # Fang-Fenster ("... GEFANGEN!") mit ESC schliessen
                    if find_catch_window(shot) is not None:
                        now_c = time.time()
                        if catch_tries < CATCH_MAX_TRIES and now_c - last_esc >= CATCH_COOLDOWN_S:
                            tap("esc", 0.05)
                            last_esc = time.time()
                            catch_tries += 1
                            print(f"Fang-Fenster erkannt -> ESC gedrueckt ({catch_tries}/{CATCH_MAX_TRIES})")
                        time.sleep(0.15)
                        continue
                    catch_tries = 0   # Fenster weg: Zaehler zuruecksetzen
                if found is None:
                    if DIAG:
                        if stats["max_zeile"] > best_score + 20:
                            best_score = stats["max_zeile"]
                            try:
                                save_png(shot, "debug_beste_zeile.png")
                                print(f"[DIAG] neues Suchbild gespeichert "
                                      f"(max. {best_score} Balkenfarb-Pixel in einer Zeile)")
                            except Exception as e:
                                print("[DIAG] Speichern fehlgeschlagen:", e)
                        if time.time() - last_diag > 3:
                            last_diag = time.time()
                            print("[DIAG]", {k: v for k, v in stats.items() if k != "kandidaten"})
                            for c in stats.get("kandidaten", []):
                                print("[DIAG]   Kandidat abgelehnt:", c)
                    elif time.time() - last_hb > 5:
                        print("... suche weiter (noch kein Balken sichtbar)")
                        last_hb = time.time()
                    time.sleep(SCAN_INTERVAL_S)
                    continue
                x0, y0, x1, y1, prof = found
                region = {"left": mon["left"] + x0, "top": mon["top"] + y0,
                          "width": x1 - x0, "height": y1 - y0}
                print(f"Balken gefunden (Profil {prof['name']}):", region)
                err_hist.clear()
                vel = 0.0
                missing = 0
                t_game = time.time()
                if not probed:
                    probe_keys(sct, region, prof)
                    probed = True

            # ---- Steuerphase ----
            t0 = time.perf_counter()
            img = np.array(sct.grab(region))
            res = analyze(img, prof)
            now = time.time()

            if res is None:
                release_all()
                missing += 1
                if missing >= GONE_FRAMES:
                    print("Minispiel beendet.")
                    if AUTO_DETECT:
                        region = None
                time.sleep(0.01)
                continue

            missing = 0
            x, center, half_w = res
            half_bar = max(region["width"] / 2.0, 1.0)

            err_px = center - x
            # Geschwindigkeit der Fehleraenderung ueber ein kurzes Zeitfenster
            # (das Spiel zeichnet nicht jedes Bild neu, Einzelbild-Differenzen waeren zu grob)
            err_hist.append((now, err_px))
            while len(err_hist) > 2 and now - err_hist[1][0] >= VEL_WINDOW_S:
                err_hist.popleft()
            t_old, e_old = err_hist[0]
            vel = (err_px - e_old) / (now - t_old) if now - t_old > 0.02 else 0.0
            vel_n = vel / half_bar
            # vel = Aenderung des Fehlers (Mitte - Marker): wandert der Marker nach links,
            # waechst der Fehler (vel > 0) -> u > 0 -> rechts druecken; nach rechts -> links.
            err_pred = err_px + vel * LEAD_S
            u = KP * err_pred / half_bar

            if DEBUG:
                print(f"x={x:6.1f} mitte={center:6.1f} f={err_px:+6.1f}px "
                      f"vorhersage={err_pred:+6.1f}px u={u:+.2f}")

            # Taste halten, solange u in dieselbe Richtung zeigt (mit Hysterese)
            in_zone = abs(err_pred) < max(0.25 * half_w, 2.0)
            if in_zone:
                want = None
            else:
                cur = holder.key
                cur_dir = 0 if cur is None else (1 if (cur == "right") != INVERT_KEYS else -1)
                if cur_dir != 0 and abs(u) > RELEASE_U and (u > 0) == (cur_dir > 0):
                    want = cur                      # weiter halten
                elif abs(u) >= MIN_U:
                    want = key_for(1 if u > 0 else -1)
                else:
                    want = None
            holder.set(want)
            key = want or ""

            if log:
                log.write(f"{now - t_game:.3f};{x:.1f};{center:.1f};{half_w:.1f};"
                          f"{err_px:.1f};{vel_n:.2f};{u:.2f};{key}\n")

            spent = time.perf_counter() - t0
            if spent < LOOP_MIN_S:
                time.sleep(LOOP_MIN_S - spent)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    finally:
        release_all()
        print("Beendet, Tasten losgelassen.")
