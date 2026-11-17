from __future__ import annotations

from datetime import date

import pytest

from tenderradar import cpv
from tenderradar.awards import (
    SupplierIndex,
    looks_like_company,
    names_related,
    normalise_identifier,
    normalise_name,
    outcomes_for,
    supplier_hash,
    wins_by_supplier,
)
from tenderradar.filters import DEADLINE, PROCEDURE, TERRITORY, VALUE, apply_filters, value_in_eur
from tenderradar.models import PastWin, Profile
from tenderradar.notices import parse_award
from tenderradar.profile import build_profile, load_client_profile, load_profiles, save_profiles

from conftest import make_notice, raw_award


def award(number, procedure, names, ids=None, countries=None, day="2026-08-03+02:00", buyer="POL"):
    over = {
        "publication-number": number,
        "procedure-identifier": procedure,
        "publication-date": day,
        "winner-name": {"pol": names},
        "buyer-country": [buyer],
    }
    over["winner-identifier"] = ids if ids is not None else []
    over["winner-country"] = countries if countries is not None else [buyer] * len(names)
    over["winner-size"] = []
    return parse_award(raw_award(**over))


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("NIP5271011366", "5271011366"),
        ("5271011366", "5271011366"),
        ("REGON 390044713", "390044713"),
        ("DE129314192", "129314192"),
        ("CZ04628527", "4628527"),
        ("04628527", "4628527"),
        ("PL 527-10-11-366", "5271011366"),
        ("NL001234567B01", "NL001234567B01"),
        ("123", None),
        ("n/a", None),
        (None, None),
    ],
)
def test_identifier_normalisation(raw, expected):
    assert normalise_identifier(raw) == expected


@pytest.mark.parametrize(
    "a, b",
    [
        ("Biokom Sp. z o.o. Sp. k.", "BIOKOM spółka z ograniczoną odpowiedzialnością sp.k."),
        ("4JTech s.r.o.", "4jtech sro"),
        ("Norad A/S", "NORAD"),
        ("Müller Labortechnik GmbH & Co. KG", "Muller Labortechnik GmbH"),
    ],
)
def test_legal_form_and_accents_do_not_split_a_company(a, b):
    assert normalise_name(a) == normalise_name(b)


def test_names_related():
    assert names_related("biokom", "biokom cz 13")
    assert names_related("antisel ro", "antisel")
    assert not names_related("kawa ska", "abchem agnieszka busler")


def test_company_marker():
    assert looks_like_company("Musterlabor Technik GmbH")
    assert looks_like_company("Alfa Lab sp. z o.o.")
    assert looks_like_company("BARIA s.r.o.")
    assert not looks_like_company("Jan Kowalski")
    assert not looks_like_company(None)


def test_prefix_variants_of_one_number_are_one_supplier():
    index = SupplierIndex.build(
        [
            award("1", "p1", ["Biokom Sp. z o.o."], ["NIP5271011366"]),
            award("2", "p2", ["BIOKOM spółka z ograniczoną odpowiedzialnością"], ["5271011366"]),
        ]
    )
    wins = wins_by_supplier(
        [
            award("1", "p1", ["Biokom Sp. z o.o."], ["NIP5271011366"]),
            award("2", "p2", ["BIOKOM spółka z ograniczoną odpowiedzialnością"], ["5271011366"]),
        ],
        index,
    )
    assert len(wins) == 1
    assert set(next(iter(wins.values()))) == {"p1", "p2"}


def test_a_name_seen_without_a_number_joins_the_supplier_known_by_number():
    awards = [
        award("1", "p1", ["Alfa Lab sp. z o.o."], ["5270000001"]),
        # Two winners: the names are kept, the number is not paired with either.
        award("2", "p2", ["Alfa Lab sp. z o.o.", "Beta Med S.A."], ["5270000001"]),
    ]
    wins = wins_by_supplier(awards, SupplierIndex.build(awards))
    by_size = sorted(wins.values(), key=len, reverse=True)
    assert set(by_size[0]) == {"p1", "p2"}
    assert set(by_size[1]) == {"p2"}


def test_one_number_under_unrelated_names_links_nothing():
    awards = [
        award("1", "p1", ["Kawa.Ska Sp. z o.o."], ["1231021965"]),
        award("2", "p2", ["ABChem Agnieszka Busler"], ["1231021965"]),
        award("3", "p3", ["Cyto-Logic Sp. z o.o."], ["1231021965"]),
    ]
    index = SupplierIndex.build(awards)
    assert index.shared_identifiers == 1
    wins = wins_by_supplier(awards, index)
    assert sorted(len(v) for v in wins.values()) == [1, 1, 1]


def test_the_same_name_in_two_countries_is_two_suppliers():
    awards = [
        award("1", "p1", ["Roche s.r.o."], countries=["CZE"], buyer="CZE"),
        award("2", "p2", ["Roche GmbH"], countries=["DEU"], buyer="DEU"),
    ]
    assert len(wins_by_supplier(awards, SupplierIndex.build(awards))) == 2


def test_company_flag_follows_the_cluster():
    awards = [
        award("1", "p1", ["Jan Kowalski"], ["5270000009"]),
        award("2", "p2", ["Alfa Lab sp. z o.o."], ["5270000001"]),
    ]
    index = SupplierIndex.build(awards)
    keys = {w.winners[0].name: index.key(w.winners[0], "POL") for w in awards}
    assert not index.is_company(keys["Jan Kowalski"])
    assert index.is_company(keys["Alfa Lab sp. z o.o."])


def test_the_earliest_award_dates_a_win_and_outcomes_are_hashed():
    awards = [
        award("2", "p1", ["Alfa Lab sp. z o.o."], ["5270000001"], day="2026-09-01+02:00"),
        award("1", "p1", ["Alfa Lab sp. z o.o."], ["5270000001"], day="2026-08-03+02:00"),
        award("3", "p0", ["Alfa Lab sp. z o.o."], ["5270000001"], day="2025-05-05+02:00"),
    ]
    index = SupplierIndex.build(awards)
    wins = wins_by_supplier(awards, index)
    (key,) = wins
    assert wins[key]["p1"].publication_date == date(2026, 8, 3)
    outcomes = outcomes_for(wins, [key], since=date(2026, 4, 1))
    assert [(o.procedure_id, o.arrived) for o in outcomes] == [("p1", date(2026, 8, 3))]
    assert outcomes[0].profile_id == supplier_hash(key)


def test_supplier_hash_is_stable_and_hides_the_key():
    key = "I:5271011366"
    assert supplier_hash(key) == supplier_hash(key)
    assert supplier_hash(key).startswith("s_") and len(supplier_hash(key)) == 14
    assert "5271011366" not in supplier_hash(key)
    assert supplier_hash(key) != supplier_hash("I:5271011367")


def test_cpv_depth():
    assert cpv.stem("38434000") == "38434"
    assert cpv.stem("38000000") == "38"
    assert cpv.shared_depth("38434000", "38434540") == 5
    assert cpv.shared_depth("38434000", "38430000") == 4
    assert cpv.shared_depth("38434000", "38000000") == 2
    assert cpv.shared_depth("38434000", "33696500") == 0
    assert cpv.shared_depth("38434000", "bad") == 0
    assert cpv.in_divisions(["45000000", "38434000"], ("38",))
    assert not cpv.in_divisions(["45000000"], ("38",))


def win(procedure, day, country, codes, text):
    return PastWin(
        procedure_id=procedure, publication_date=day, buyer_country=country, cpv=codes, text=text
    )


def test_profile_counts_each_win_once_however_many_codes_it_lists():
    profile = build_profile(
        "s_x",
        [
            win("p1", date(2025, 3, 1), "POL", ["38434000"], "Analizator"),
            win("p2", date(2025, 6, 1), "POL", ["38434000", "33696500"], "Odczynniki"),
            win("p3", date(2025, 9, 1), "CZE", ["33696500", "33696500"], "Činidla"),
        ],
    )
    assert profile.past_wins == 3
    assert profile.countries == ["POL", "CZE"]
    assert profile.cpv_weights == {"33696500": 1.5, "38434000": 1.5}
    assert profile.texts == ["Činidla", "Odczynniki", "Analizator"]


def test_profiles_round_trip_through_the_export_file(tmp_path):
    profiles = [build_profile("s_b", [win("p1", date(2025, 3, 1), "POL", ["38434000"], "x")])]
    profiles.append(build_profile("s_a", []))
    path = tmp_path / "panel.jsonl.gz"
    assert save_profiles(profiles, path) == 2
    loaded = load_profiles(path)
    assert [p.profile_id for p in loaded] == ["s_a", "s_b"]
    assert loaded[1].cpv_weights == {"38434000": 1.0}
    first = path.read_bytes()
    save_profiles(profiles, path)
    assert path.read_bytes() == first
    assert load_profiles(tmp_path / "missing.gz") == []


def test_client_profile_from_yaml(tmp_path):
    path = tmp_path / "client.yaml"
    path.write_text(
        """
id: client
name: Example Lab Supply
description: Distributor of laboratory instruments.
countries: [DEU, AUT]
categories: {"38434000": 3, "38000000": 1}
sells: ["Spectrometers", "Analysers"]
min_value_eur: 20000
max_value_eur: 2000000
min_days_to_deadline: 10
excluded_natures: [works]
capabilities: ["ISO 9001"]
""",
        encoding="utf-8",
    )
    profile = load_client_profile(path)
    assert profile.kind == "client" and profile.countries == ["DEU", "AUT"]
    assert profile.cpv_weights == {"38434000": 3.0, "38000000": 1.0}
    assert profile.min_days_to_deadline == 10 and profile.excluded_natures == ["works"]
    path.write_text("categories: {'384': 1}", encoding="utf-8")
    with pytest.raises(ValueError, match="eight-digit"):
        load_client_profile(path)


def rules(profile, notice, today=date(2026, 4, 14)):
    return [hit.rule for hit in apply_filters(profile, notice, today)]


def test_a_matching_notice_breaks_no_rule(profile, notice):
    assert rules(profile, notice) == []


def test_territory_rule(profile):
    assert rules(profile, make_notice(**{"buyer-country": ["POL"]})) == [TERRITORY]
    open_profile = profile.model_copy(update={"countries": []})
    assert rules(open_profile, make_notice(**{"buyer-country": ["POL"]})) == []


def test_deadline_rule(profile, notice):
    assert rules(profile, notice, today=date(2026, 5, 19)) == [DEADLINE]
    careful = profile.model_copy(update={"min_days_to_deadline": 40})
    hits = apply_filters(careful, notice, date(2026, 4, 14))
    assert [h.rule for h in hits] == [DEADLINE]
    assert "34 days" in hits[0].detail


def test_value_rule_converts_currency(profile):
    banded = profile.model_copy(update={"min_value_eur": 50_000, "max_value_eur": 1_000_000})
    assert rules(banded, make_notice()) == []
    small = make_notice(**{"estimated-value-proc": "100000", "estimated-value-cur-proc": "PLN"})
    assert value_in_eur(small) == pytest.approx(23_000)
    assert rules(banded, small) == [VALUE]
    large = make_notice(**{"estimated-value-proc": "5000000"})
    assert rules(banded, large) == [VALUE]


def test_procedure_rule(profile):
    picky = profile.model_copy(
        update={"excluded_procedure_types": ["restricted"], "excluded_natures": ["works"]}
    )
    assert rules(picky, make_notice(**{"procedure-type": "restricted"})) == [PROCEDURE]
    assert rules(picky, make_notice(**{"contract-nature-main-proc": "works"})) == [PROCEDURE]


def test_missing_fields_never_remove_a_tender():
    strict = Profile(
        profile_id="s",
        countries=["DEU"],
        min_value_eur=1,
        max_value_eur=2,
        min_days_to_deadline=30,
        excluded_procedure_types=["open"],
        excluded_natures=["supplies"],
    )
    bare = make_notice(
        **{
            "buyer-country": None,
            "deadline-receipt-tender-date-lot": None,
            "estimated-value-proc": None,
            "procedure-type": None,
            "contract-nature-main-proc": None,
        }
    )
    assert rules(strict, bare) == []
    unknown_currency = make_notice(
        **{
            "estimated-value-cur-proc": "XYZ",
            "buyer-country": ["DEU"],
            "deadline-receipt-tender-date-lot": None,
            "procedure-type": None,
            "contract-nature-main-proc": None,
        }
    )
    assert rules(strict, unknown_currency) == []
