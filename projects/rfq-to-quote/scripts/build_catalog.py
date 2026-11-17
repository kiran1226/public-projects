"""Generate data/catalog.csv: a synthetic catalog of industrial electrical materials.

Product families and naming follow real conventions (NYY-J, H07RN-F, kVA ratings)
so the matching problem is realistic. Prices are invented from simple formulas.

    uv run python scripts/build_catalog.py
"""

from __future__ import annotations

import csv
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "data" / "catalog.csv"
FIELDS = "sku category description keywords unit unit_cost list_price moq lead_time_days".split()
# Margin on selling price per category: cables and transformers are thin-margin goods.
MARGIN = {"cable": 0.16, "transformer": 0.14, "solar": 0.28, "accessory": 0.35}

rows: list[dict] = []


def add(sku, category, description, keywords, unit, cost, moq, lead):
    rows.append(
        dict(
            sku=sku,
            category=category,
            description=description,
            keywords=keywords,
            unit=unit,
            unit_cost=round(cost, 2),
            list_price=round(cost / (1 - MARGIN[category]), 2),
            moq=moq,
            lead_time_days=lead,
        )
    )


def size(cores, cs, sep="x"):
    return f"{cores}{sep}{cs:g}"


# --- cables (sold per metre) ---------------------------------------------------
NYY = [(3, 1.5), (3, 2.5), (5, 1.5), (5, 2.5), (5, 4), (5, 6), (5, 10), (5, 16), (5, 25),
       (4, 25), (4, 35), (4, 50), (4, 70), (4, 95), (4, 120), (4, 150), (4, 185), (4, 240)]  # fmt: skip
for cores, cs in NYY:
    add(f"CAB-NYY-{size(cores, cs).upper()}", "cable",
        f"NYY-J {size(cores, cs)} mm² power cable 0.6/1 kV, copper, PVC, black",
        "NYY NYYJ Erdkabel Starkstromkabel Kupfer underground power cable",
        "m", cores * cs * 0.095 + 0.35, 100, 15 if cs >= 95 else 5)  # fmt: skip

for cores, cs in [(3, 1.5), (3, 2.5), (5, 1.5), (5, 2.5), (5, 4), (5, 6), (5, 10), (5, 16)]:
    add(f"CAB-NYM-{size(cores, cs).upper()}", "cable",
        f"NYM-J {size(cores, cs)} mm² installation cable 300/500 V, copper, PVC, grey",
        "NYM NYMJ Mantelleitung Installationsleitung indoor sheathed cable",
        "m", cores * cs * 0.09 + 0.25, 100, 5)  # fmt: skip

for cores, cs in [(3, 1.5), (3, 2.5), (5, 1.5), (5, 2.5), (5, 4), (5, 6), (5, 10), (5, 16)]:
    add(f"CAB-H07-{size(cores, cs, 'G').upper()}", "cable",
        f"H07RN-F {size(cores, cs, 'G')} mm² heavy rubber flexible cable 450/750 V",
        "H07RNF Gummischlauchleitung Gummileitung rubber flex cable site cable",
        "m", cores * cs * 0.115 + 0.6, 100, 5)  # fmt: skip

for cs in [35, 50, 70, 95, 120, 150, 185, 240]:
    add(f"CAB-NAYY-4X{cs}", "cable",
        f"NAYY-J 4x{cs} mm² power cable 0.6/1 kV, aluminium, PVC, black",
        "NAYY NAYYJ Aluminium Alu Erdkabel aluminium underground power cable",
        "m", 4 * cs * 0.021 + 0.8, 100, 15 if cs >= 95 else 5)  # fmt: skip

for cs in [50, 95, 150, 240, 300, 500]:
    for kv, rating, factor in [(10, "6/10 kV", 1.0), (20, "12/20 kV", 1.18)]:
        add(f"CAB-MV-1X{cs}-{kv}KV", "cable",
            f"NA2XS(F)2Y 1x{cs} mm² medium-voltage cable {rating}, aluminium, XLPE",
            "NA2XSF2Y NA2XS2Y Mittelspannungskabel MV cable VPE single core",
            "m", (4.0 + cs * 0.034) * factor, 100, 20)  # fmt: skip

for cs in [4, 6, 10]:
    for code, colour, de in [("BK", "black", "schwarz"), ("RD", "red", "rot")]:
        add(f"CAB-PV-{cs}-{code}", "cable",
            f"H1Z2Z2-K 1x{cs} mm² solar cable 1.5 kV DC, tinned copper, {colour}",
            f"H1Z2Z2K Solarkabel Solarleitung PV cable photovoltaic DC string cable {de}",
            "m", cs * 0.11 + 0.12, 100, 5)  # fmt: skip

# --- distribution transformers (sold per piece) -------------------------------
KVA = [100, 160, 250, 400, 630, 800, 1000, 1250, 1600, 2000, 2500]
for kva in KVA:
    for kv, factor in [(10, 1.0), (20, 1.05)]:
        add(f"TRF-OIL-{kva}-{kv}", "transformer",
            f"Oil-immersed distribution transformer {kva} kVA, {kv}/0.4 kV, Dyn5, ONAN",
            "Trafo Transformator Öltransformator Oeltransformator Verteiltransformator "
            "oil filled hermetically sealed",
            "pcs", (3000 + kva * 14) * factor, 1, 70)  # fmt: skip
for kva in KVA[2:]:
    for kv, factor in [(10, 1.0), (20, 1.05)]:
        add(f"TRF-DRY-{kva}-{kv}", "transformer",
            f"Cast-resin dry-type transformer {kva} kVA, {kv}/0.4 kV, Dyn5, AN",
            "Trafo Transformator Gießharztransformator Giessharz Trockentransformator "
            "cast resin dry type",
            "pcs", (3000 + kva * 14) * 1.35 * factor, 1, 80)  # fmt: skip

# --- solar mounting hardware --------------------------------------------------
for profile, base in [("40x40", 3.4), ("40x60", 4.6)]:
    for mm in [2200, 3300, 4400, 6000]:
        add(f"PV-RAIL-{profile.upper()}-{mm}", "solar",
            f"Aluminium mounting rail {profile} mm, length {mm / 1000:g} m",
            f"Montageschiene Profilschiene Modulschiene Tragschiene mounting rail {mm} mm",
            "pcs", base * mm / 1000, 10, 7)  # fmt: skip
    add(
        f"PV-RAILCON-{profile.upper()}",
        "solar",
        f"Rail connector (splice) for mounting rail {profile} mm",
        "Schienenverbinder Verbinder rail splice joiner",
        "pcs",
        1.9,
        10,
        7,
    )
for frame in [30, 35, 40]:
    for code, colour, de in [("SIL", "silver", "silber"), ("BLK", "black", "schwarz")]:
        add(f"PV-MID-{frame}-{code}", "solar",
            f"Module mid clamp for {frame} mm frame, {colour}",
            f"Mittelklemme Modulklemme mid clamp middle clamp {de}", "pcs",
            0.85 + (0.1 if code == "BLK" else 0), 10, 7)  # fmt: skip
        add(f"PV-END-{frame}-{code}", "solar",
            f"Module end clamp for {frame} mm frame, {colour}",
            f"Endklemme Modulendklemme end clamp {de}", "pcs",
            0.95 + (0.1 if code == "BLK" else 0), 10, 7)  # fmt: skip
for code, name, kw, cost in [
    ("TILE", "Roof hook for pantile roofs, stainless steel", "Dachhaken Pfanne Ziegel tile", 4.2),
    ("SLATE", "Roof hook for slate roofs, stainless steel", "Dachhaken Schiefer slate", 5.1),
    ("FLAT", "Roof hook for plain tile roofs, stainless steel",
     "Dachhaken Biberschwanz plain tile flat tile", 4.8),
    ("ADJ", "Roof hook, height adjustable, stainless steel",
     "Dachhaken verstellbar höhenverstellbar adjustable", 6.3),
]:  # fmt: skip
    add(f"PV-HOOK-{code}", "solar", name, kw, "pcs", cost, 10, 7)
for dim, cost in [("M10x200", 1.6), ("M10x250", 1.8), ("M12x300", 2.6)]:
    add(f"PV-BOLT-{dim.upper()}", "solar",
        f"Hanger bolt {dim} mm with EPDM seal, stainless steel",
        "Stockschraube hanger bolt double threaded screw", "pcs", cost, 10, 7)  # fmt: skip
for mm in [1200, 1600, 2000]:
    add(f"PV-GSCREW-{mm}", "solar",
        f"Ground screw {mm} mm for ground-mount systems, galvanised",
        "Erdschraube Schraubfundament Bodenschraube ground screw foundation",
        "pcs", 9 + mm * 0.012, 10, 10)  # fmt: skip
add("PV-TRAP-CLAMP", "solar", "Trapezoidal sheet metal roof clamp with EPDM",
    "Trapezblech Trapezblechklemme Blechdach trapezoidal sheet clamp", "pcs", 2.3, 10, 7)  # fmt: skip
add("PV-EARTH-CLIP", "solar", "Module earthing clip, stainless steel",
    "Erdungsklemme Erdungsclip Potentialausgleich grounding clip bonding", "pcs", 0.45, 10, 7)  # fmt: skip
add("PV-MC4-PAIR", "solar", "MC4 connector pair (male + female), 4-6 mm², 1500 V DC",
    "MC4 Stecker Steckverbinder Paar Set connector pair", "pcs", 1.1, 10, 5)  # fmt: skip
add("PV-MC4-M", "solar", "MC4 connector male, 4-6 mm², 1500 V DC",
    "MC4 Stecker male plug", "pcs", 0.6, 10, 5)  # fmt: skip
add("PV-MC4-F", "solar", "MC4 connector female, 4-6 mm², 1500 V DC",
    "MC4 Buchse female socket", "pcs", 0.6, 10, 5)  # fmt: skip
add("PV-CLIP-CABLE", "solar", "Cable clip for module frame, stainless steel, 2 cables",
    "Kabelclip Kabelklemme Edelstahl cable clip", "pcs", 0.18, 100, 5)  # fmt: skip

# --- switchgear and installation accessories ---------------------------------
for amp in [100, 160, 250, 400, 630, 800, 1000, 1250, 1600]:
    add(f"ACC-MCCB-{amp}", "accessory",
        f"Moulded case circuit breaker (MCCB) {amp} A, 3-pole, 50 kA",
        "Leistungsschalter Kompaktleistungsschalter MCCB circuit breaker 3P",
        "pcs", 60 + amp * 0.85, 1, 10)  # fmt: skip
for cs in [16, 25, 35, 50, 70, 95, 120, 150, 185, 240]:
    add(f"ACC-LUG-CU-{cs}", "accessory",
        f"Compression cable lug {cs} mm², copper, tinned, M12",
        "Kabelschuh Presskabelschuh Rohrkabelschuh crimp lug", "pcs", 0.3 + cs * 0.022, 10, 3)  # fmt: skip
for m in [16, 20, 25, 32, 40, 50, 63]:
    add(f"ACC-GLAND-M{m}", "accessory",
        f"Cable gland M{m}, polyamide, IP68, with locknut",
        "Kabelverschraubung Verschraubung cable gland", "pcs", 0.25 + m * 0.03, 10, 3)  # fmt: skip
for width in [100, 200, 300, 400, 500, 600]:
    add(f"ACC-TRAY-{width}", "accessory",
        f"Cable tray {width} mm wide, 60 mm high, length 3 m, galvanised, perforated",
        "Kabelrinne Kabelkanal Rinne cable tray", "pcs", 8 + width * 0.045, 1, 7)  # fmt: skip
for width in [200, 300, 400, 600]:
    add(f"ACC-LADDER-{width}", "accessory",
        f"Cable ladder {width} mm wide, length 3 m, hot-dip galvanised",
        "Kabelleiter Kabelbahn Leiter cable ladder", "pcs", 22 + width * 0.07, 1, 7)  # fmt: skip
add("ACC-SPD-T12", "accessory", "Surge protection device type 1+2, 3-phase, TN-S",
    "Überspannungsschutz Überspannungsableiter Blitzstromableiter Kombiableiter SPD", "pcs",
    142, 1, 5)  # fmt: skip
add("ACC-SPD-T2", "accessory", "Surge protection device type 2, 3-phase, TN-S",
    "Überspannungsschutz Überspannungsableiter SPD surge arrester", "pcs", 58, 1, 5)  # fmt: skip
add("ACC-EROD-1500", "accessory", "Earth rod 1500 mm, 20 mm diameter, galvanised steel",
    "Erdungsstab Tiefenerder Staberder Erder earth rod ground rod", "pcs", 11.5, 1, 5)  # fmt: skip
add("ACC-ETAPE-30X3.5", "accessory", "Earthing tape 30x3.5 mm, galvanised steel, per metre",
    "Bandstahl Erdungsband Flachband Bandeisen earthing strip tape", "m", 1.35, 50, 5)  # fmt: skip
for rng, fits, cost in [("16-50", "16 25 35 50", 14), ("70-150", "70 95 120 150", 27),
                        ("185-240", "185 240", 41)]:  # fmt: skip
    add(f"ACC-JOINT-LV-{rng}", "accessory",
        f"Low-voltage heat-shrink straight joint for 4-core cable {rng} mm², 1 kV",
        f"Verbindungsmuffe Muffe Schrumpfmuffe Kabelmuffe cable joint fits {fits} mm²",
        "pcs", cost, 1, 5)  # fmt: skip

if __name__ == "__main__":
    ids = [r["sku"] for r in rows]
    assert len(ids) == len(set(ids)), "duplicate SKU ids"
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {len(rows)} SKUs to {OUT}")
