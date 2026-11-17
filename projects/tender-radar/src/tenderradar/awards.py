"""Who won what: link awards to tenders and follow one supplier across awards.

The feed has no clean supplier key. Buyers type the winner's name and registration
number by hand, so the same company appears as 'NIP5271011366' and '5271011366', as
'Sp. z o.o.' and 'spółka z ograniczoną odpowiedzialnością', and now and then a buyer
enters one number for every winner of a notice. This module groups those spellings
into suppliers, and refuses to group through a number that unrelated names share.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date

from .models import Award, Outcome, PastWin, Winner

# Words that say what kind of company it is, not which company.
_LEGAL_FORMS = frozenset(
    """
    gmbh ag kg co mbh ug ohg ek gbr se kgaa
    sp z o oo sa sk spk spolka ograniczona odpowiedzialnoscia akcyjna komandytowa jawna sj
    sro as spol ks vos
    srl sa sas sarl sasu eurl snc spa sl slu sau sc scs
    ltd limited plc llc llp inc corp corporation company
    bv nv vof ab aps oy oyj asa hf ehf
    uab sia ou as ood eood ad ead doo dd dooel kft zrt bt rt nyrt
    lda unipessoal epe ae ike ee
    """.split()
)
# Enough of them, on their own, to say "this is a company, not a person".
_COMPANY_MARKERS = re.compile(
    r"\b(gmbh|ag|kg|ug|se|s\.?\s?a\.?|s\.?\s?r\.?\s?o\.?|a\.?\s?s\.?|s\.?\s?r\.?\s?l\.?|"
    r"s\.?\s?p\.?\s?a\.?|s\.?\s?a\.?\s?s\.?|sarl|ltd|limited|plc|llc|inc|b\.?\s?v\.?|"
    r"n\.?\s?v\.?|ab|aps|a/s|oy|oyj|uab|sia|o[üu]|ood|eood|ead|d\.?\s?o\.?\s?o\.?|d\.?\s?d\.?|"
    r"kft|zrt|lda|s\.?\s?l\.?\s?u?\.?|sp\.?\s?z\s?o\.?\s?o\.?|sp[óo][łl]ka|sp\.?\s?k\.?|"
    r"sp\.?\s?j\.?|a\.?\s?e\.?|e\.?\s?p\.?\s?e\.?|i\.?\s?k\.?\s?e\.?)(?=\W|$)",
    re.IGNORECASE,
)
_PREFIXED_NUMBER = re.compile(r"^[A-Z]{1,8}(\d{6,})$")
_NON_ALNUM = re.compile(r"[^0-9A-Z]")
_NON_WORD = re.compile(r"[^0-9a-z]+")
MIN_IDENTIFIER_LENGTH = 6


def normalise_identifier(identifier: str | None) -> str | None:
    """A registration number without spaces, punctuation or a registry prefix."""
    if not identifier:
        return None
    compact = _NON_ALNUM.sub("", identifier.upper())
    match = _PREFIXED_NUMBER.match(compact)
    if match:
        compact = match.group(1)
    compact = compact.lstrip("0")
    return compact if len(compact) >= MIN_IDENTIFIER_LENGTH else None


def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.lower().replace("ł", "l").replace("ß", "ss"))
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def name_tokens(name: str | None) -> list[str]:
    """The words that tell one company from another: lower case, no accents, no legal form."""
    if not name:
        return []
    words = _NON_WORD.sub(" ", _fold(name)).split()
    return [w for w in words if w not in _LEGAL_FORMS and (len(w) > 1 or w.isdigit())]


def normalise_name(name: str | None) -> str | None:
    tokens = name_tokens(name)
    return " ".join(tokens) if tokens else None


def looks_like_company(name: str | None) -> bool:
    """True if the name carries a legal form. Used to keep sole traders out of the panel."""
    return bool(name and _COMPANY_MARKERS.search(name))


def names_related(a: str, b: str) -> bool:
    """Two normalised names that plausibly belong to one company."""
    tokens_a, tokens_b = a.split(), b.split()
    if not tokens_a or not tokens_b:
        return False
    if tokens_a[0] == tokens_b[0]:
        return True
    set_a, set_b = set(tokens_a), set(tokens_b)
    return len(set_a & set_b) / len(set_a | set_b) >= 0.5


def supplier_hash(key: str) -> str:
    """The id used in everything that is committed. The key itself never leaves the machine."""
    return "s_" + hashlib.sha256(f"tender-radar:{key}".encode()).hexdigest()[:12]


def buyer_key(name: str | None, country: str | None) -> str | None:
    """A hash of a buying organisation: its name without accents or case, and its country."""
    tokens = name_tokens(name)
    if not tokens:
        return None
    payload = f"tender-radar-buyer:{country or '?'}:{' '.join(tokens)}"
    return "b_" + hashlib.sha256(payload.encode()).hexdigest()[:12]


class _UnionFind:
    def __init__(self) -> None:
        self._parent: dict[str, str] = {}

    def find(self, node: str) -> str:
        self._parent.setdefault(node, node)
        root = node
        while self._parent[root] != root:
            root = self._parent[root]
        while self._parent[node] != root:
            self._parent[node], node = root, self._parent[node]
        return root

    def union(self, a: str, b: str) -> None:
        root_a, root_b = self.find(a), self.find(b)
        if root_a != root_b:
            # The smaller string becomes the root, so the result does not depend on order.
            low, high = sorted((root_a, root_b))
            self._parent[high] = low


def _name_node(winner: Winner, fallback_country: str | None) -> str | None:
    name = normalise_name(winner.name)
    if not name:
        return None
    return f"N:{winner.country or fallback_country or '?'}:{name}"


def _id_node(winner: Winner) -> str | None:
    identifier = normalise_identifier(winner.identifier)
    return f"I:{identifier}" if identifier else None


@dataclass
class SupplierIndex:
    """Maps every spelling of a winner to one supplier key."""

    _uf: _UnionFind = field(default_factory=_UnionFind)
    _company: dict[str, bool] = field(default_factory=dict)
    shared_identifiers: int = 0

    @classmethod
    def build(cls, awards: Iterable[Award]) -> SupplierIndex:
        index = cls()
        names_of_id: dict[str, set[str]] = defaultdict(set)
        nodes_of_id: dict[str, set[str]] = defaultdict(set)
        company_nodes: set[str] = set()
        all_nodes: set[str] = set()
        for award in awards:
            for winner in award.winners:
                name_node, id_node = _name_node(winner, award.buyer_country), _id_node(winner)
                for node in (name_node, id_node):
                    if node:
                        all_nodes.add(node)
                if name_node and looks_like_company(winner.name):
                    company_nodes.add(name_node)
                if name_node and id_node:
                    names_of_id[id_node].add(name_node.split(":", 2)[2])
                    nodes_of_id[id_node].add(name_node)
        for node in all_nodes:
            index._uf.find(node)
        for id_node, names in names_of_id.items():
            ordered = sorted(names)
            if all(names_related(ordered[0], other) for other in ordered[1:]):
                for name_node in nodes_of_id[id_node]:
                    index._uf.union(id_node, name_node)
            else:
                # One number under unrelated names: a typing error or a shared number.
                # It links nothing; each name stands on its own.
                index.shared_identifiers += 1
        for node in company_nodes:
            index._company[index._uf.find(node)] = True
        return index

    def key(self, winner: Winner, buyer_country: str | None = None) -> str | None:
        """The supplier key for a winner, or None if the record names nobody."""
        name_node, id_node = _name_node(winner, buyer_country), _id_node(winner)
        for node in (name_node, id_node):
            if node and node in self._uf._parent:
                return self._uf.find(node)
        return None

    def is_company(self, key: str) -> bool:
        return self._company.get(key, False)

    def suppliers(self) -> int:
        return len({self._uf.find(node) for node in list(self._uf._parent)})


def wins_by_supplier(
    awards: Iterable[Award], index: SupplierIndex
) -> dict[str, dict[str, PastWin]]:
    """For each supplier key, the procedures it won, each with the earliest award that says so."""
    wins: dict[str, dict[str, PastWin]] = defaultdict(dict)
    for award in awards:
        for winner in award.winners:
            key = index.key(winner, award.buyer_country)
            if key is None:
                continue
            known = wins[key].get(award.procedure_id)
            if known is None or award.publication_date < known.publication_date:
                wins[key][award.procedure_id] = PastWin(
                    procedure_id=award.procedure_id,
                    publication_date=award.publication_date,
                    buyer_country=award.buyer_country,
                    buyer=buyer_key(award.buyer_name, award.buyer_country),
                    cpv=award.cpv,
                    text=award.text,
                )
    return wins


def outcomes_for(
    wins: dict[str, dict[str, PastWin]], supplier_keys: Iterable[str], since: date
) -> list[Outcome]:
    """Outcomes that arrived on or after `since`, for the given suppliers, as hashed ids."""
    out = []
    for key in supplier_keys:
        for win in wins.get(key, {}).values():
            if win.publication_date >= since:
                out.append(
                    Outcome(
                        profile_id=supplier_hash(key),
                        procedure_id=win.procedure_id,
                        arrived=win.publication_date,
                    )
                )
    return sorted(out, key=lambda o: (o.arrived, o.profile_id, o.procedure_id))
