# fx-arfolyamok

EURHUF és USDHUF árfolyam-idősorok (nyilvános piaci adat, Dukascopy BID), óránként frissítve GitHub Actionsszel.

| Fájl | Tartalom |
|---|---|
| `gepi/fx/<PÁR>_15m.csv` | 15 perces gyertyák |
| `gepi/fx/<PÁR>_1h.csv` | 1 órás gyertyák |
| `gepi/fx/<PÁR>_1d.csv` | napi gyertyák |
| `gepi/fx/letoltes.txt` | az utolsó futás naplója |

Oszlopok: `ts_utc,open,high,low,close` (a gyertya kezdete, UTC). A visszatöltés 2024-11-18-tól indul.
Letöltő: `scripts/fx_letolt.py`, munkafolyamat: `.github/workflows/fx_orankent.yml`.
