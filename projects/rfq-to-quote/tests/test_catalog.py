import pytest

from rfq2quote.catalog import Catalog, Sku, normalize, query_tokens


def test_normalize_makes_size_one_token_across_spellings():
    assert normalize("NYY-J 5x2,5 mm²") == normalize("nyy j 5 X 2.5mm2") == "nyy j 5x2p5 mm2"
    assert normalize("H07RN-F 5G2.5") == "h07rn f 5x2p5"


def test_plural_and_singular_share_a_stem():
    assert normalize("Mittelklemmen") == normalize("Mittelklemme")
    assert normalize("rails") == normalize("rail")


def test_count_times_rating_keeps_rating_searchable():
    assert "630" in query_tokens("2 x 630 kVA")


@pytest.mark.parametrize(
    "text, sku",
    [
        ("NYY-J 5x2,5 mm²", "CAB-NYY-5X2.5"),
        ("NYY-J 5x25 mm²", "CAB-NYY-5X25"),
        ("Mittelklemmen 35mm silber", "PV-MID-35-SIL"),
        ("Solarkabel 6mm2 schwarz", "CAB-PV-6-BK"),
        ("1000 kVA dry-type cast resin transformer 10 kV", "TRF-DRY-1000-10"),
        ("Erdungsband 30x3,5", "ACC-ETAPE-30X3.5"),
    ],
)
def test_search_ranks_the_right_sku_first(catalog, text, sku):
    assert catalog.search(text, 3)[0][0].sku == sku


def test_search_returns_all_plausible_cables_for_an_ambiguous_request(catalog):
    found = {s.sku for s, _ in catalog.search("5x2.5 cable", 5)}
    assert {"CAB-NYY-5X2.5", "CAB-NYM-5X2.5", "CAB-H07-5G2.5"} <= found


def test_catalog_size_and_lookup(catalog):
    assert len(catalog) > 150
    assert catalog.get("TRF-OIL-630-20").unit == "pcs"
    assert catalog.get("NOPE") is None


def test_catalog_can_be_built_in_memory_from_skus():
    def sku(code: str, description: str) -> Sku:
        return Sku(sku=code, category="", description=description, keywords="", unit="pcs",
                   unit_cost=0, list_price=0, moq=1, lead_time_days=0)  # fmt: skip

    small = Catalog.from_skus([sku("A", "Akku-Bohrschrauber 18V"), sku("B", "Winterreifen 205/55")])
    assert len(small) == 2
    assert [s.sku for s, _ in small.search("Bohrschrauber 18 V", 8)] == ["A"]


def test_a_catalog_file_with_a_price_of_zero_is_refused_and_names_the_product(tmp_path):
    path = tmp_path / "catalog.csv"
    path.write_text(
        "sku,category,description,keywords,unit,unit_cost,list_price,moq,lead_time_days\n"
        "CAB-OK,cable,NYY-J 5x2.5,,m,1.0,2.0,1,3\n"
        "CAB-FREE,cable,NYY-J 5x4,,m,1.0,0,1,3\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="CAB-FREE"):
        Catalog.from_csv(path)
