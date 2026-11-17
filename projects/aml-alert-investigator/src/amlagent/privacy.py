"""The privacy gateway. Nothing reaches a model except through this module.

Three lines of defence:
1. Views build model-facing data from allow-listed fields and case-scoped tokens.
2. `scrub` cleans free text: known identifiers become tokens, and any capitalised word
   that is not ordinary vocabulary is redacted, so an unknown name fails closed.
3. `OutboundScanner` checks every request just before it is sent.

The vault that maps tokens to real values is also the evidence ledger: a finding may
cite only tokens issued in its own case.
"""

from __future__ import annotations

import json
import re
import threading
from collections import Counter, defaultdict
from collections.abc import Iterable
from datetime import date

from .bankdb import BankDB, name_parts
from .models import Alert

TOKEN = r"\b[A-Z]{3,6}_\d{2,4}\b"
TOKEN_RE = re.compile(TOKEN)
SEP = r"[ \-.\u00a0]{0,2}"  # how people break up an account number
# Two letters, two check digits, an optional four-letter bank code, then groups of digits.
# Any case, any of the usual separators.
IBAN_RE = re.compile(
    rf"(?<![A-Za-z0-9])[A-Za-z]{{2}}\d{{2}}(?:{SEP}[A-Za-z]{{4}})?(?:{SEP}\d{{1,4}}){{2,8}}(?![A-Za-z0-9])"
)
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# Nine or more digits with the usual separators: phone numbers, card numbers, references.
# ISO dates and timestamps have eight digits per run, amounts fewer, so they do not match.
PHONE_RE = re.compile(r"(?<![\w.])\+?\d(?:[ \-.\u00a0()]{0,2}\d){8,}(?!\w)")
# An ISO date standing on its own. Removed before the phone test, because an amount
# followed by a date ("30,200 (2026-08-15)") has enough digits to look like a phone number.
DATE_RE = re.compile(
    r"(?<![\d-])(?:19|20)\d{2}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])"
    r"(?:T\d{2}:\d{2}(?::\d{2})?)?(?![\d-])"
)
ID_RE = re.compile(r"(?<![A-Za-z0-9_])[ACPTHWM]\d{3,6}(?![A-Za-z0-9_])")  # the bank's own ids
LONG_NUMBER_RE = re.compile(r"\d{8,}")
PIECE_RE = re.compile(rf"(?P<keep>{TOKEN}|\[(?:redacted|withheld|number)\])|(?P<word>[^\W\d_]+)")
REDACTED_RUN_RE = re.compile(r"\[redacted\](?:[\s,]+\[redacted\])+")

# Ordinary words that may appear capitalised in bank free text. Anything else that
# contains a capital letter is treated as a possible name and redacted.
SAFE_WORDS = frozenset(
    """
    january february march april may june july august september october november december
    salary rent utilities card payment payments atm withdrawal cash deposit deposits daily
    takings transfer invoice supplier payroll membership dues fee fees donation birthday
    gift regards aunt uncle family support loan agreement repayment consulting services
    commission purchase price property sale file vehicle annual bonus tuition spring autumn
    term apartment renovation treatment season iban ref contract hotline mail paid
    customer customers notarised sales seasonal third party authorities prosecutors
    business account branch bank the a an and or of to from for in on with by is are was
    """.split()
)


class UnknownToken(Exception):
    """A token that was never issued in this case, or is of the wrong kind."""


class Vault:
    """Case-scoped token mapping. Lives on the bank's side; no tool reads it."""

    def __init__(self, case_id: str):
        self.case_id = case_id
        self._lock = threading.Lock()
        self._tokens: dict[str, dict] = {}
        self._index: dict[tuple[str, str], str] = {}
        self._count: Counter = Counter()

    def token(self, kind: str, key: str, display: str | None = None) -> str:
        with self._lock:
            found = self._index.get((kind, key))
            if found:
                return found
            self._count[kind] += 1
            token = f"{kind}_{self._count[kind]:0{4 if kind == 'TXN' else 2}d}"
            self._tokens[token] = {"kind": kind, "key": key, "display": display or key}
            self._index[(kind, key)] = token
            return token

    def lookup(self, token: str, *kinds: str) -> str:
        entry = self._tokens.get(token)
        if entry is None:
            raise UnknownToken(f"{token!r} is not a token issued in this case.")
        if kinds and entry["kind"] not in kinds:
            raise UnknownToken(f"{token} is not a {' or '.join(kinds)} token.")
        return entry["key"]

    def kind(self, token: str) -> str | None:
        entry = self._tokens.get(token)
        return entry["kind"] if entry else None

    def resolve(self, token: str) -> str | None:
        entry = self._tokens.get(token)
        return entry["key"] if entry else None

    def issued(self) -> set[str]:
        return set(self._tokens)

    def reidentify(self, text: str) -> str:
        """Restore real values for the analyst. Never call this on text bound for a model."""
        return TOKEN_RE.sub(
            lambda m: self._tokens.get(m.group(0), {}).get("display", m.group(0)), text
        )

    def dump(self) -> dict:
        return {"case_id": self.case_id, "tokens": self._tokens}

    @classmethod
    def load(cls, data: dict) -> Vault:
        vault = cls(data["case_id"])
        for token, entry in data["tokens"].items():
            vault._tokens[token] = entry
            vault._index[(entry["kind"], entry["key"])] = token
            number = int(token.rsplit("_", 1)[1])
            vault._count[entry["kind"]] = max(vault._count[entry["kind"]], number)
        return vault


def _digits(text: str) -> str:
    return re.sub(r"\D", "", text)


def _compact(text: str) -> str:
    return re.sub(r"[ \-.\u00a0]", "", text).upper()


def _fold(text: str) -> str:
    return " ".join(text.lower().split())


def _alternation(values: Iterable[str]) -> str:
    """Longest first, spaces flexible, so 'Anna  Becker' and 'ANNA BECKER' both match."""
    ordered = sorted({v for v in values if len(v) >= 4}, key=len, reverse=True)
    return "|".join(r"\s+".join(re.escape(w) for w in v.split()) for v in ordered)


class PartyIndex:
    """Every identifier that points at a known party. Built once per database."""

    def __init__(self, db: BankDB):
        self.lookup: dict[str, tuple[str, str]] = {}
        self.ids: dict[str, tuple[str, str]] = {}  # the bank's own ids, e.g. C0001
        names = []
        parts: dict[str, set[tuple[str, str]]] = defaultdict(set)
        for p in db.parties():
            target = (p["kind"], p["key"])
            self.ids[p["key"]] = target
            if p["name"]:
                self.lookup.setdefault(_fold(p["name"]), target)
                names.append(p["name"])
                if p["person"]:
                    for word in name_parts(p["name"]):
                        parts[word.lower()].add(target)
            if p["iban"]:
                self.lookup.setdefault(p["iban"].upper(), target)
            if p["email"]:
                self.lookup.setdefault(p["email"].lower(), target)
            if p["phone"]:
                self.lookup.setdefault("tel:" + _digits(p["phone"]), target)
        self.names = re.compile(rf"(?<!\w)(?:{_alternation(names)})(?!\w)", re.IGNORECASE)
        # A first name or surname on its own: the party's token when it is unambiguous.
        self.parts = {w: (min(t) if len(t) == 1 else None) for w, t in parts.items()}
        words = "|".join(sorted(map(re.escape, self.parts), key=len, reverse=True))
        self.part_re = re.compile(rf"(?<!\w)(?:{words})(?!\w)", re.IGNORECASE) if words else None


def age_band(dob: str, as_of: date) -> str:
    if not dob:
        return "n/a"
    born = date.fromisoformat(dob)
    age = as_of.year - born.year - ((as_of.month, as_of.day) < (born.month, born.day))
    for top, label in (
        (17, "under 18"),
        (24, "18-24"),
        (34, "25-34"),
        (44, "35-44"),
        (54, "45-54"),
        (64, "55-64"),
    ):
        if age <= top:
            return label
    return "65+"


def _allow(match: re.Match) -> str:
    if match.group("keep"):
        return match.group("keep")
    word = match.group("word")
    return word if word.islower() or word.lower() in SAFE_WORDS else "[redacted]"


# Free text in the bank's data is data, never an instruction. These patterns recognise text
# written to the model instead of about the customer: talk of instructions and prompts, of
# what the reader is, and this system's own tool names and output vocabulary. A case in
# which any appears goes to a person (policy rule INSTRUCTION_IN_DATA). It is a heuristic:
# the same request in other words passes.
INSTRUCTION_RE = re.compile(
    r"""(?ix)
      \b(?:ignore|disregard)\b [^.\n]{0,40} \b(?:instructions?|prompts?|rules|guidance|guidelines)\b
    | \b(?:new|updated|additional)\s+instructions?\b
    | \bsystem\s+prompt\b
    | ^\s*(?:system|assistant)\s*:
    | \byou\s+are\s+(?:now\s+)?(?:an?\s+)?
        (?:ai|assistant|language\s+model|llm|chatbot|reviewer|investigator|helpful)\b
    | \bas\s+an?\s+(?:ai|assistant|language\s+model|llm)\b
    | \b(?:get|list|search|screen|request|submit)_[a-z]+(?:_[a-z]+)*\b
    | \b(?:handoff_notes|reason_code|evidence_ids|close_false_positive|escalate_l2|needs_info)\b
    | \b(?:set|with|at)\s+(?:the\s+)?confidence\s+(?:to\s+)?(?:high|medium|low)\b
    | \bconfidence\s*[:=]?\s*high\b
    """
)


def addresses_model(text: str) -> bool:
    """Does this free text read like an instruction to the model that will be shown it?"""
    return bool(text) and INSTRUCTION_RE.search(text) is not None


class Gateway:
    def __init__(self, db: BankDB, vault: Vault, as_of: date, index: PartyIndex | None = None):
        self.db, self.vault, self.as_of = db, vault, as_of
        self.index = index or PartyIndex(db)
        self.instructions_seen: list[str] = []  # scrubbed excerpts, for the analyst

    # --- tokens -------------------------------------------------------------------

    def tok(self, kind: str, key: str) -> str:
        if kind == "CUST":
            display = self.db.customer(key)["name"]
        elif kind == "ACCT":
            display = self.db.account(key)["iban"]
        elif kind == "CPTY":
            display = self.db.counterparty(key)["name"]
        else:
            display = key
        return self.vault.token(kind, key, display)

    def _known(self, value: str, fallback: str, raw: str) -> str:
        target = self.index.lookup.get(value)
        return self.tok(*target) if target else self.vault.token(fallback, value, raw)

    # --- free text ----------------------------------------------------------------

    def _number(self, match: re.Match) -> str:
        raw = match.group(0)
        target = self.index.lookup.get("tel:" + _digits(raw))
        if target:
            return self.tok(*target)
        return self.vault.token("PHONE", _digits(raw), raw) if raw.startswith("+") else "[number]"

    def _part(self, match: re.Match) -> str:
        target = self.index.parts[match.group(0).lower()]
        return self.tok(*target) if target else "[redacted]"

    def _own_id(self, match: re.Match) -> str:
        target = self.index.ids.get(match.group(0))
        return self.tok(*target) if target else "[redacted]"

    def scrub(self, text: str) -> str:
        if not text:
            return text
        clean = self._scrub(text)
        if addresses_model(text):
            self.instructions_seen.append(clean[:200])
        return clean

    def _scrub(self, text: str) -> str:
        text = EMAIL_RE.sub(lambda m: self._known(m.group(0).lower(), "EMAIL", m.group(0)), text)
        text = IBAN_RE.sub(lambda m: self._known(_compact(m.group(0)), "IBAN", m.group(0)), text)
        text = PHONE_RE.sub(self._number, text)
        text = self.index.names.sub(
            lambda m: self._known(_fold(m.group(0)), "NAME", m.group(0)), text
        )
        if self.index.part_re:
            text = self.index.part_re.sub(self._part, text)
        text = ID_RE.sub(self._own_id, text)
        text = LONG_NUMBER_RE.sub("[number]", text)
        text = PIECE_RE.sub(_allow, text)
        return REDACTED_RUN_RE.sub("[redacted]", text)

    # --- views --------------------------------------------------------------------

    def alert_view(self, alert: Alert) -> dict:
        return {
            "alert": self.tok("ALERT", alert.alert_id),
            "rule": alert.rule,
            "raised": alert.raised.isoformat(),
            "window_start": alert.window_start.isoformat(),
            "window_end": alert.window_end.isoformat(),
            "amount": alert.amount,
            "customer": self.tok("CUST", alert.customer_id),
            "account": self.tok("ACCT", alert.account_id),
            "transactions": [self.tok("TXN", t) for t in alert.txn_ids],
            "counterparties": [self.tok("CPTY", c) for c in alert.counterparty_ids],
        }

    def profile_view(self, customer: dict, role: str) -> dict:
        view = {
            "customer": self.tok("CUST", customer["customer_id"]),
            "kind": customer["kind"],
            "age_band": age_band(customer["dob"], self.as_of),
            "occupation": self.scrub(customer["occupation"]),
            "country": customer["country"],
            "risk_rating": customer["risk_rating"],
            "pep": bool(customer["pep"]),
            "expected_monthly_turnover": customer["expected_monthly_turnover"],
            "onboarded_year": int(customer["onboarded"][:4]),
            "kyc_notes": self.scrub(customer["kyc_notes"]),
        }
        if role != "triage":  # need-to-know: the lead sees a little more
            view |= {"city": customer["city"], "nationality": customer["nationality"]}
        return view

    def txn_view(self, txn: dict) -> dict:
        cp = self.db.counterparty(txn["counterparty_id"]) if txn["counterparty_id"] else None
        hidden = bool(cp and cp["sensitive_category"])
        return {
            "id": self.tok("TXN", txn["txn_id"]),
            "booked_at": txn["booked_at"],
            "direction": txn["direction"],
            "amount": txn["amount"],
            "channel": txn["channel"],
            "counterparty": self.tok("CPTY", cp["counterparty_id"]) if cp else None,
            "description": "[withheld]" if hidden else self.scrub(txn["description"]),
        }

    def counterparty_view(self, cp: dict, reveal: bool = False) -> dict:
        hidden = bool(cp["sensitive_category"]) and not reveal
        view = {
            "counterparty": self.tok("CPTY", cp["counterparty_id"]),
            "kind": cp["kind"],
            "country": cp["country"],
            "first_seen": cp["first_seen"],
            "is_customer": bool(cp["customer_id"]),
            "business_type": "withheld" if hidden else cp["business_type"],
            "sensitive": "withheld" if hidden else (cp["sensitive_category"] or "none"),
        }
        if cp["customer_id"]:
            view["customer"] = self.tok("CUST", cp["customer_id"])
        if cp["dob"]:
            view["age_band"] = age_band(cp["dob"], self.as_of)
        return view

    def prior_view(self, prior: dict) -> dict:
        return {
            "id": self.tok("PRIOR", prior["prior_id"]),
            "raised": prior["raised"],
            "rule": prior["rule"],
            "outcome": prior["outcome"],
            "note": self.scrub(prior["note"]),
        }


class OutboundScanner:
    """Second line: looks for real identifiers in text that is about to leave."""

    def __init__(self, identifiers: Iterable[str]):
        pattern = _alternation(identifiers)
        self._known = re.compile(rf"(?<!\w)(?:{pattern})(?!\w)", re.IGNORECASE) if pattern else None

    def scan(self, text: str) -> list[str]:
        kinds = set()
        if self._known and self._known.search(text):
            kinds.add("known_identifier")
        if IBAN_RE.search(text):
            kinds.add("iban_pattern")
        if EMAIL_RE.search(text):
            kinds.add("email_pattern")
        # An IBAN's digits and a date's digits are not a phone number.
        if PHONE_RE.search(DATE_RE.sub("|", IBAN_RE.sub("|", text))):
            kinds.add("phone_pattern")
        if ID_RE.search(text):
            kinds.add("internal_id_pattern")
        return sorted(kinds)


def outbound_text(system: str, messages: list[dict]) -> str:
    """Everything our side contributes to a request.

    Assistant turns are skipped: a model cannot emit an identifier it never received,
    and replayed thinking signatures are random strings that would trip the patterns.
    """
    parts = [system]
    for message in messages:
        if message["role"] != "user":
            continue
        content = message["content"]
        if isinstance(content, str):
            parts.append(content)
            continue
        for block in content:
            if block.get("type") == "text":
                parts.append(block["text"])
            elif block.get("type") == "tool_result":
                inner = block.get("content", "")
                parts.append(inner if isinstance(inner, str) else json.dumps(inner))
    return "\n".join(parts)
