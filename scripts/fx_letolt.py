"""EURHUF / USDHUF napon belüli árfolyamok gyűjtése a repóba (GitHub Actions, óránként).

Cél: napon belüli statisztikákhoz és backteszthez 15 perces / 1 órás / napi idősor, egyszer letöltve, utána csak frissítve.

Forrás: Dukascopy nyilvános historikus adatfolyam (datafeed.dukascopy.com), BID oldal.
  - lezárt napok: napi fájl 1 perces gyertyákkal (BID_candles_min_1.bi5)
  - a mai nap: órás tickfájlok (HHh_ticks.bi5) a már lezárt órákra

Kimenet (gyűjtő CSV-k, időbélyeg szerint egyedi, a régi sorok megmaradnak):
  gepi/fx/<PÁR>_15m.csv, <PÁR>_1h.csv, <PÁR>_1d.csv   oszlopok: ts_utc,open,high,low,close (a gyertya kezdete, UTC)
  gepi/fx/letoltes.txt                                 az utolsó futás naplója
A visszatöltés 2024-11-18-tól indul; futásonként legfeljebb MAX_NAP napot pótol, így pár óra alatt teljes.

Használat:  python3 scripts/fx_letolt.py [--max-nap N]
"""
import os, sys, csv, lzma, struct, time, datetime as dt
from collections import OrderedDict
import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KI = os.path.join(ROOT, "gepi", "fx")
PAROK = ["EURHUF", "USDHUF"]
KEZDET = dt.date(2024, 11, 18)
MAX_NAP = int(sys.argv[sys.argv.index("--max-nap") + 1]) if "--max-nap" in sys.argv else 120
URL = "https://datafeed.dukascopy.com/datafeed/{par}/{y:04d}/{m:02d}/{d:02d}/{fajl}"
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"}
ses = requests.Session()
ses.headers.update(UA)
log = []


def _get(par, nap, fajl):
    """A bi5 fájl kitömörítve (b'' ha nincs adat, pl. hétvége); hibánál kivétel."""
    url = URL.format(par=par, y=nap.year, m=nap.month - 1, d=nap.day, fajl=fajl)   # a hónap 0-tól számozott!
    for kiserlet in range(5):
        time.sleep(0.7)                       # udvarias tempó: a Dukascopy sűrű kérésnél 503-mal fojt
        try:
            r = ses.get(url, timeout=30)
        except requests.RequestException as e:   # időtúllépés / bontott kapcsolat: kivárás és újra
            hiba_szoveg = f"{type(e).__name__}"
            time.sleep(10 * 2 ** kiserlet)
            continue
        if r.status_code == 404:
            return b""
        if r.status_code in (429, 503):
            time.sleep(10 * 2 ** kiserlet)    # 10, 20, 40, 80, 160 s
            continue
        r.raise_for_status()
        return lzma.decompress(r.content) if r.content else b""
    raise RuntimeError(f"{url}: 5 próbálkozás után sem sikerült")


def _oszto(nyers):
    """Pont-osztó: amelyiknél a HUF-ár 150 és 700 közé esik."""
    for o in (1000, 100, 10000, 10, 100000):
        if 150 <= nyers / o <= 700:
            return o
    raise ValueError(f"ismeretlen árskála: {nyers}")


def perces_gyertyak(par, nap):
    """[(datetime_utc, o, h, l, c)] egy lezárt nap 1 perces BID-gyertyáiból (volumen 0 = nincs kötés → kihagyva)."""
    b = _get(par, nap, "BID_candles_min_1.bi5")
    ki, oszto = [], None
    for i in range(0, len(b) - len(b) % 24, 24):
        t, o, c, l, h, v = struct.unpack(">5if", b[i:i + 24])
        if v <= 0:
            continue
        oszto = oszto or _oszto(o)
        ido = dt.datetime(nap.year, nap.month, nap.day, tzinfo=dt.timezone.utc) + dt.timedelta(seconds=t)
        ki.append((ido, o / oszto, h / oszto, l / oszto, c / oszto))
    return ki


def tick_gyertyak(par, nap, orak):
    """A mai nap lezárt óráinak tickjeiből 1 perces BID-gyertyák."""
    percek = OrderedDict()
    oszto = None
    for ora in orak:
        b = _get(par, nap, f"{ora:02d}h_ticks.bi5")
        for i in range(0, len(b) - len(b) % 20, 20):
            ms, ask, bid, av, bv = struct.unpack(">3i2f", b[i:i + 20])
            oszto = oszto or _oszto(bid)
            ido = dt.datetime(nap.year, nap.month, nap.day, ora, tzinfo=dt.timezone.utc) + dt.timedelta(milliseconds=ms)
            k = ido.replace(second=0, microsecond=0)
            p = bid / oszto
            if k not in percek:
                percek[k] = [p, p, p, p]
            else:
                g = percek[k]; g[1] = max(g[1], p); g[2] = min(g[2], p); g[3] = p
    return [(k, *v) for k, v in percek.items()]


def osszevon(perces, perc):
    """1 perces gyertyákból `perc` perces (15 / 60 / 1440) gyertyák."""
    ki = OrderedDict()
    for ido, o, h, l, c in perces:
        if perc == 1440:
            k = ido.replace(hour=0, minute=0)
        else:
            m = (ido.hour * 60 + ido.minute) // perc * perc
            k = ido.replace(hour=m // 60, minute=m % 60)
        if k not in ki:
            ki[k] = [o, h, l, c]
        else:
            g = ki[k]; g[1] = max(g[1], h); g[2] = min(g[2], l); g[3] = c
    return [(k.strftime("%Y-%m-%d %H:%M"), *(round(x, 4) for x in v)) for k, v in ki.items()]


def osszefesul(utvonal, uj):
    adat = {}
    if os.path.exists(utvonal):
        with open(utvonal, newline="") as f:
            for s in csv.DictReader(f):
                adat[s["ts_utc"]] = (s["ts_utc"], s["open"], s["high"], s["low"], s["close"])
    elotte = len(adat)
    for s in uj:
        adat[s[0]] = s
    with open(utvonal, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ts_utc", "open", "high", "low", "close"])
        for k in sorted(adat):
            w.writerow(adat[k])
    return elotte, len(adat)


def utolso_teljes_nap(par):
    """Az utolsó nap, amelyre a napi fájl már be lett töltve (az állapotfájlból)."""
    p = os.path.join(KI, f"{par}_napok.txt")
    return dt.date.fromisoformat(open(p).read().strip()) if os.path.exists(p) else KEZDET - dt.timedelta(days=1)


def main():
    os.makedirs(KI, exist_ok=True)
    most = dt.datetime.now(dt.timezone.utc)
    ma = most.date()
    hiba = 0
    for par in PAROK:
        try:
            perces = []
            nap = utolso_teljes_nap(par) + dt.timedelta(days=1)
            betoltve = 0
            # A napi fájl a nap lezárása után kb. 1-2 órával jelenik meg; tegnapig próbáljuk.
            megszakadt = None
            while nap < ma and betoltve < MAX_NAP:
                try:
                    perces += perces_gyertyak(par, nap)
                except Exception as e:          # a már letöltött napok megmaradnak, a következő futás innen folytatja
                    megszakadt = f"{nap}: {type(e).__name__}: {e}"
                    break
                utolso = nap
                nap += dt.timedelta(days=1)
                betoltve += 1
            if megszakadt:
                hiba += 0.5
                log.append(f"FÉLBE {par}: megszakadt ({megszakadt}); {betoltve} nap megvan, a következő futás folytatja")
            if betoltve:
                open(os.path.join(KI, f"{par}_napok.txt"), "w").write(utolso.isoformat() + "\n")
            # A mai nap: a már lezárt órák tickjei (ha a visszatöltés utolérte a mai napot).
            mai = []
            if nap >= ma and not megszakadt:
                mai = tick_gyertyak(par, ma, range(0, most.hour))
                # A tegnapi napi fájl lehet, hogy még nincs kint: akkor a tegnapot is tickből pótoljuk.
                if utolso_teljes_nap(par) < ma - dt.timedelta(days=1):
                    mai = tick_gyertyak(par, ma - dt.timedelta(days=1), range(24)) + mai
            osszes = perces + mai
            for nev, perc in (("15m", 15), ("1h", 60), ("1d", 1440)):
                elotte, utana = osszefesul(os.path.join(KI, f"{par}_{nev}.csv"), osszevon(osszes, perc))
                log.append(f"OK   {par} {nev}: fájl {elotte}→{utana} sor")
            if osszes:
                u = osszes[-1]
                log.append(f"     {par}: {betoltve} lezárt nap + {len(mai)} mai perc; utolsó {u[0]:%Y-%m-%d %H:%M} UTC close {u[4]:.3f}; "
                           f"visszatöltve eddig: {utolso_teljes_nap(par)}")
            else:
                log.append(f"     {par}: nincs új adat")
        except Exception as e:
            hiba += 1
            log.append(f"HIBA {par}: {type(e).__name__}: {e}")
    with open(os.path.join(KI, "letoltes.txt"), "w") as f:
        f.write(f"Utolsó futás: {most:%Y-%m-%d %H:%M} UTC (forrás: Dukascopy BID)\n" + "\n".join(log) + "\n")
    print("\n".join(log))
    sys.exit(1 if hiba >= len(PAROK) else 0)


if __name__ == "__main__":
    main()
