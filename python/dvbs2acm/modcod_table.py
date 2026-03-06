"""
modcod_table.py

Python-side DVB-S2 MODCOD table for use in GRC flowgraphs,
post-processing scripts, and Jupyter notebooks.

Usage:
    from dvbs2acm.modcod_table import MODCOD_TABLE, get_modcod, snr_to_modcod

    mc = get_modcod(14)           # 8PSK 3/4
    print(mc['name'])             # "8PSK 3/4"
    print(mc['spectral_eff'])     # 2.794
    modcod_id = snr_to_modcod(10.0)  # Returns best MODCOD for 10 dB SNR
"""

from typing import Optional, List

MODCOD_TABLE: List[dict] = [
    dict(id= 1, modulation="QPSK",   code_rate="1/4",  bits_per_sym=2,
         spectral_eff=0.490, min_snr_db=-2.35, threshold_db=-1.85,
         kbch=16008, nbch=16200, kldpc=16200, nldpc=64800,
         name="QPSK 1/4"),
    dict(id= 2, modulation="QPSK",   code_rate="1/3",  bits_per_sym=2,
         spectral_eff=0.656, min_snr_db=-1.24, threshold_db=-0.74,
         kbch=21408, nbch=21600, kldpc=21600, nldpc=64800,
         name="QPSK 1/3"),
    dict(id= 3, modulation="QPSK",   code_rate="2/5",  bits_per_sym=2,
         spectral_eff=0.789, min_snr_db=-0.30, threshold_db=0.20,
         kbch=25728, nbch=25920, kldpc=25920, nldpc=64800,
         name="QPSK 2/5"),
    dict(id= 4, modulation="QPSK",   code_rate="1/2",  bits_per_sym=2,
         spectral_eff=0.988, min_snr_db=1.00, threshold_db=1.50,
         kbch=32208, nbch=32400, kldpc=32400, nldpc=64800,
         name="QPSK 1/2"),
    dict(id= 5, modulation="QPSK",   code_rate="3/5",  bits_per_sym=2,
         spectral_eff=1.188, min_snr_db=2.23, threshold_db=2.73,
         kbch=38688, nbch=38880, kldpc=38880, nldpc=64800,
         name="QPSK 3/5"),
    dict(id= 6, modulation="QPSK",   code_rate="2/3",  bits_per_sym=2,
         spectral_eff=1.322, min_snr_db=3.10, threshold_db=3.60,
         kbch=43040, nbch=43200, kldpc=43200, nldpc=64800,
         name="QPSK 2/3"),
    dict(id= 7, modulation="QPSK",   code_rate="3/4",  bits_per_sym=2,
         spectral_eff=1.487, min_snr_db=4.03, threshold_db=4.53,
         kbch=48408, nbch=48600, kldpc=48600, nldpc=64800,
         name="QPSK 3/4"),
    dict(id= 8, modulation="QPSK",   code_rate="4/5",  bits_per_sym=2,
         spectral_eff=1.587, min_snr_db=4.68, threshold_db=5.18,
         kbch=51648, nbch=51840, kldpc=51840, nldpc=64800,
         name="QPSK 4/5"),
    dict(id= 9, modulation="QPSK",   code_rate="5/6",  bits_per_sym=2,
         spectral_eff=1.655, min_snr_db=5.18, threshold_db=5.68,
         kbch=53840, nbch=54000, kldpc=54000, nldpc=64800,
         name="QPSK 5/6"),
    dict(id=10, modulation="QPSK",   code_rate="8/9",  bits_per_sym=2,
         spectral_eff=1.766, min_snr_db=6.20, threshold_db=6.70,
         kbch=57472, nbch=57600, kldpc=57600, nldpc=64800,
         name="QPSK 8/9"),
    dict(id=11, modulation="QPSK",   code_rate="9/10", bits_per_sym=2,
         spectral_eff=1.789, min_snr_db=6.42, threshold_db=6.92,
         kbch=58192, nbch=58320, kldpc=58320, nldpc=64800,
         name="QPSK 9/10"),
    dict(id=12, modulation="8PSK",   code_rate="3/5",  bits_per_sym=3,
         spectral_eff=2.228, min_snr_db=5.50, threshold_db=6.00,
         kbch=38688, nbch=38880, kldpc=38880, nldpc=64800,
         name="8PSK 3/5"),
    dict(id=13, modulation="8PSK",   code_rate="2/3",  bits_per_sym=3,
         spectral_eff=2.479, min_snr_db=6.62, threshold_db=7.12,
         kbch=43040, nbch=43200, kldpc=43200, nldpc=64800,
         name="8PSK 2/3"),
    dict(id=14, modulation="8PSK",   code_rate="3/4",  bits_per_sym=3,
         spectral_eff=2.794, min_snr_db=7.91, threshold_db=8.41,
         kbch=48408, nbch=48600, kldpc=48600, nldpc=64800,
         name="8PSK 3/4"),
    dict(id=15, modulation="8PSK",   code_rate="5/6",  bits_per_sym=3,
         spectral_eff=3.093, min_snr_db=9.35, threshold_db=9.85,
         kbch=53840, nbch=54000, kldpc=54000, nldpc=64800,
         name="8PSK 5/6"),
    dict(id=16, modulation="8PSK",   code_rate="8/9",  bits_per_sym=3,
         spectral_eff=3.318, min_snr_db=10.69, threshold_db=11.19,
         kbch=57472, nbch=57600, kldpc=57600, nldpc=64800,
         name="8PSK 8/9"),
    dict(id=17, modulation="8PSK",   code_rate="9/10", bits_per_sym=3,
         spectral_eff=3.348, min_snr_db=10.98, threshold_db=11.48,
         kbch=58192, nbch=58320, kldpc=58320, nldpc=64800,
         name="8PSK 9/10"),
    dict(id=18, modulation="16APSK", code_rate="2/3",  bits_per_sym=4,
         spectral_eff=3.522, min_snr_db=8.97, threshold_db=9.47,
         kbch=43040, nbch=43200, kldpc=43200, nldpc=64800,
         name="16APSK 2/3"),
    dict(id=19, modulation="16APSK", code_rate="3/4",  bits_per_sym=4,
         spectral_eff=3.973, min_snr_db=10.21, threshold_db=10.71,
         kbch=48408, nbch=48600, kldpc=48600, nldpc=64800,
         name="16APSK 3/4"),
    dict(id=20, modulation="16APSK", code_rate="4/5",  bits_per_sym=4,
         spectral_eff=4.220, min_snr_db=11.03, threshold_db=11.53,
         kbch=51648, nbch=51840, kldpc=51840, nldpc=64800,
         name="16APSK 4/5"),
    dict(id=21, modulation="16APSK", code_rate="5/6",  bits_per_sym=4,
         spectral_eff=4.397, min_snr_db=11.61, threshold_db=12.11,
         kbch=53840, nbch=54000, kldpc=54000, nldpc=64800,
         name="16APSK 5/6"),
    dict(id=22, modulation="16APSK", code_rate="8/9",  bits_per_sym=4,
         spectral_eff=4.701, min_snr_db=12.89, threshold_db=13.39,
         kbch=57472, nbch=57600, kldpc=57600, nldpc=64800,
         name="16APSK 8/9"),
    dict(id=23, modulation="16APSK", code_rate="9/10", bits_per_sym=4,
         spectral_eff=4.748, min_snr_db=13.13, threshold_db=13.63,
         kbch=58192, nbch=58320, kldpc=58320, nldpc=64800,
         name="16APSK 9/10"),
    dict(id=24, modulation="32APSK", code_rate="3/4",  bits_per_sym=5,
         spectral_eff=4.875, min_snr_db=12.73, threshold_db=13.23,
         kbch=48408, nbch=48600, kldpc=48600, nldpc=64800,
         name="32APSK 3/4"),
    dict(id=25, modulation="32APSK", code_rate="4/5",  bits_per_sym=5,
         spectral_eff=5.195, min_snr_db=13.64, threshold_db=14.14,
         kbch=51648, nbch=51840, kldpc=51840, nldpc=64800,
         name="32APSK 4/5"),
    dict(id=26, modulation="32APSK", code_rate="5/6",  bits_per_sym=5,
         spectral_eff=5.405, min_snr_db=14.28, threshold_db=14.78,
         kbch=53840, nbch=54000, kldpc=54000, nldpc=64800,
         name="32APSK 5/6"),
    dict(id=27, modulation="32APSK", code_rate="8/9",  bits_per_sym=5,
         spectral_eff=5.784, min_snr_db=15.69, threshold_db=16.19,
         kbch=57472, nbch=57600, kldpc=57600, nldpc=64800,
         name="32APSK 8/9"),
    dict(id=28, modulation="32APSK", code_rate="9/10", bits_per_sym=5,
         spectral_eff=5.848, min_snr_db=16.05, threshold_db=16.55,
         kbch=58192, nbch=58320, kldpc=58320, nldpc=64800,
         name="32APSK 9/10"),
]

def get_modcod(modcod_id: int) -> dict:
    """Get MODCOD entry by 1-indexed ID (1-28)."""
    if modcod_id < 1 or modcod_id > 28:
        raise ValueError(f"MODCOD ID must be 1-28, got {modcod_id}")
    return MODCOD_TABLE[modcod_id - 1]

def snr_to_modcod(snr_db: float, margin_db: float = 0.5) -> int:
    """
    Select optimal MODCOD ID for a given SNR.
    Returns highest spectral efficiency MODCOD that can operate at snr_db.
    """
    best_id  = 1
    best_eff = 0.0
    for mc in MODCOD_TABLE:
        if (snr_db - margin_db) >= mc['threshold_db'] and mc['spectral_eff'] > best_eff:
            best_eff = mc['spectral_eff']
            best_id  = mc['id']
    return best_id

def get_snr_range() -> tuple:
    """Return the (min, max) SNR range covered by all MODCODs."""
    return (MODCOD_TABLE[0]['min_snr_db'], MODCOD_TABLE[-1]['threshold_db'])

def print_modcod_table():
    """Pretty-print the MODCOD table."""
    print(f"{'ID':>3}  {'Name':<14}  {'eff(b/s/Hz)':>11}  "
          f"{'Min SNR (dB)':>12}  {'Threshold (dB)':>14}")
    print("-" * 60)
    for mc in MODCOD_TABLE:
        print(f"{mc['id']:>3}  {mc['name']:<14}  {mc['spectral_eff']:>10.3f}  "
              f"{mc['min_snr_db']:>12.2f}  {mc['threshold_db']:>14.2f}")

if __name__ == "__main__":
    print_modcod_table()
    print(f"\nSNR range: {get_snr_range()[0]:.2f} to {get_snr_range()[1]:.2f} dB")
    print(f"Best MODCOD at 12 dB SNR: {get_modcod(snr_to_modcod(12.0))['name']}")
