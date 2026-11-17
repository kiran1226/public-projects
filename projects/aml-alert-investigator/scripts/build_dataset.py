"""Generate the synthetic bank dataset. Everything is invented; same seed, same files.

uv run python scripts/build_dataset.py
"""

from __future__ import annotations

import csv
import json
import random
import shutil
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / "data"
SEED = 20261115
MONTHS = [(2026, m) for m in range(4, 10)]
HIGH_RISK = ("VD", "KV")  # fictional jurisdictions, so no real country is labelled

FIRST = "Anna Jonas Lea Felix Mira Tobias Clara Emil Nora Lukas Ida Oskar Greta Paul Marie Henrik Sofia Jan Elif Marek".split()
LAST = "Becker Wolff Hartmann Lindqvist Moreau Castellano Nowak Petrov Yilmaz Haddad Fischer Jansen Kovacs Silva Demir Aaltonen Marchetti Dubois Keller Sorensen".split()
WL_FIRST = "Dmitar Zoran Ilya Rashid Octavian Bogdan Teodor Kasim Vadim Anton".split()
WL_LAST = (
    "Kovalenko Draganov Mirzoyan Baltaru Vasilenko Tomescu Orlovic Zhakupov Stanciu Hadjiev".split()
)
THIRD = [
    "Marta Vesely",
    "Rudi Almstedt",
    "Pia Laurenz",
    "Tomas Brevik",
    "Lotte Oberkirch",
    "Sven Halloran",
    "Mina Varga",
    "Kai Rennert",
]
CITIES = ["Hamburg", "Kiel", "Bremen", "Rostock", "Hannover", "Leipzig"]
STREETS = [
    "Lindenweg",
    "Hafenstrasse",
    "Birkenallee",
    "Marktgasse",
    "Am Deich",
    "Schulstrasse",
    "Kastanienring",
]
SUFFIXES = [
    "Handels GmbH",
    "Bau GmbH",
    "Logistik GmbH",
    "Gastro GmbH",
    "Service GmbH",
    "Technik GmbH",
]
SHELLS = [
    "Orvane Trading Ltd",
    "Kestova Consulting LLC",
    "Mirelle Holdings SA",
    "Tarkon Global Services Ltd",
    "Velmora Advisory LLC",
    "Zandrik Partners Ltd",
    "Halcyon Reef Trading Ltd",
    "Brixen Vale Consulting SA",
]
UNIS = [
    "University of Westmark",
    "Delfthaven Institute of Technology",
    "Kilmore College Dublin",
    "Universidad de Costa Clara",
    "Lakeland School of Design",
]
HOTLINE, BILLING = "+00 555 0100 200", "billing@lumen-strom.example"


def iso(d: date) -> str:
    return d.isoformat()


class Builder:
    def __init__(self) -> None:
        self.rng = random.Random(SEED)
        self.t: dict[str, list[dict]] = {
            k: []
            for k in (
                "customers",
                "accounts",
                "counterparties",
                "transactions",
                "watchlist",
                "media",
                "prior_alerts",
                "links",
            )
        }
        self.alerts: list[dict] = []
        self.gold: list[dict] = []
        self.planted: set[str] = set()
        self.by_id: dict[str, dict] = {}
        self.names: set[str] = set()
        self.shells = list(SHELLS)
        self.unis = list(UNIS)

    def _id(self, table: str, prefix: str, width: int) -> str:
        return f"{prefix}{len(self.t[table]) + 1:0{width}d}"

    def unique(self, make) -> str:
        while True:
            name = make()
            if name not in self.names:
                self.names.add(name)
                return name

    def person(self, first=FIRST, last=LAST) -> str:
        return self.unique(lambda: f"{self.rng.choice(first)} {self.rng.choice(last)}")

    def iban(self, country: str) -> str:
        return f"{country}{self.rng.randrange(10, 99)}HARB{self.rng.randrange(10**9, 10**10)}"

    def customer(
        self, kind: str, occupation: str, turnover: float, *, risk="low", notes=""
    ) -> tuple[dict, dict]:
        r = self.rng
        name = (
            self.person()
            if kind == "retail"
            else self.unique(lambda: f"{r.choice(LAST)} {r.choice(SUFFIXES)}")
        )
        dob = (
            iso(date(r.randrange(1955, 2004), r.randrange(1, 13), r.randrange(1, 28)))
            if kind == "retail"
            else ""
        )
        onboarded = iso(date(r.randrange(2012, 2025), r.randrange(1, 13), 1))
        row = dict(
            customer_id=self._id("customers", "C", 4),
            kind=kind,
            name=name,
            dob=dob,
            nationality="DE",
            country="DE",
            city=r.choice(CITIES),
            address=f"{r.choice(STREETS)} {r.randrange(1, 120)}",
            email=f"{name.lower().replace(' ', '.')}@example.com",
            phone=f"+00 555 {r.randrange(1000, 9999)} {r.randrange(100, 999)}",
            occupation=occupation,
            risk_rating=risk,
            pep=0,
            expected_monthly_turnover=turnover,
            onboarded=onboarded,
            kyc_notes=notes,
        )
        self.t["customers"].append(row)
        acct = dict(
            account_id=self._id("accounts", "A", 4),
            customer_id=row["customer_id"],
            iban=self.iban("DE"),
            opened=onboarded,
        )
        self.t["accounts"].append(acct)
        return row, acct

    def cp(
        self,
        name: str,
        kind: str,
        business_type: str,
        *,
        country="DE",
        sensitive="",
        dob="",
        customer_id="",
        iban="",
        first_seen=date(2026, 4, 1),
    ) -> dict:
        row = dict(
            counterparty_id=self._id("counterparties", "P", 4),
            name=name,
            kind=kind,
            country=country,
            iban=iban or self.iban(country),
            customer_id=customer_id,
            sensitive_category=sensitive,
            business_type=business_type,
            dob=dob,
            first_seen=iso(first_seen),
        )
        self.t["counterparties"].append(row)
        return row

    def txn(
        self,
        acct: dict,
        when: datetime,
        direction: str,
        amount: float,
        channel: str,
        cp: dict | None = None,
        desc: str = "",
    ) -> str:
        row = dict(
            txn_id=self._id("transactions", "T", 6),
            account_id=acct["account_id"],
            booked_at=when.isoformat(timespec="minutes"),
            direction=direction,
            amount=round(amount, 2),
            channel=channel,
            counterparty_id=cp["counterparty_id"] if cp else "",
            description=desc,
        )
        self.t["transactions"].append(row)
        self.by_id[row["txn_id"]] = row
        return row["txn_id"]

    def prior(self, cust: dict, raised: date, rule: str, outcome: str, note: str) -> str:
        row = dict(
            prior_id=self._id("prior_alerts", "H", 3),
            customer_id=cust["customer_id"],
            raised=iso(raised),
            rule=rule,
            outcome=outcome,
            note=note,
        )
        self.t["prior_alerts"].append(row)
        return row["prior_id"]

    def watch(
        self, name: str, aliases: list[str], dob: str, country: str, list_: str = "sanctions"
    ) -> str:
        row = dict(
            entry_id=self._id("watchlist", "W", 3),
            name=name,
            aliases=";".join(aliases),
            kind="person",
            dob=dob,
            country=country,
            list=list_,
        )
        self.t["watchlist"].append(row)
        return row["entry_id"]

    def news(self, subject: str, published: date, headline: str, snippet: str) -> None:
        self.t["media"].append(
            dict(
                media_id=self._id("media", "M", 3),
                subject_name=subject,
                published=iso(published),
                headline=headline,
                snippet=snippet,
            )
        )

    def when(self, year: int, month: int, hour_range=(8, 19)) -> datetime:
        return datetime(year, month, self.rng.randrange(1, 28), self.rng.randrange(*hour_range), 0)

    def background(self, cust: dict, acct: dict, sh: dict) -> dict:
        """Six months of routine activity. Returns the parties used, for scenarios."""
        r = self.rng
        index = len(self.t["customers"])
        if cust["kind"] == "retail":
            salary = round(cust["expected_monthly_turnover"] / 1.1, -1)
            employer, landlord = r.choice(sh["employers"]), r.choice(sh["landlords"])
            for y, m in MONTHS:
                mon = date(y, m, 1).strftime("%B")
                self.txn(
                    acct,
                    datetime(y, m, 27, 9, 0),
                    "in",
                    salary,
                    "transfer",
                    employer,
                    f"Salary {mon}",
                )
                self.txn(
                    acct,
                    datetime(y, m, 1, 8, 0),
                    "out",
                    round(salary * 0.3, -1),
                    "transfer",
                    landlord,
                    f"Rent {mon} {cust['name']} IBAN {landlord['iban']}",
                )
                extra = f" hotline {HOTLINE} or {BILLING}" if m == 4 else ""
                self.txn(
                    acct,
                    datetime(y, m, 5, 8, 0),
                    "out",
                    r.uniform(60, 180),
                    "transfer",
                    r.choice(sh["utilities"]),
                    f"Utilities {mon} contract {r.randrange(10000, 99999)}{extra}",
                )
                for _ in range(6):
                    self.txn(
                        acct,
                        self.when(y, m),
                        "out",
                        r.uniform(15, 140),
                        "card",
                        r.choice(sh["shops"]),
                        "Card payment",
                    )
                for _ in range(2):
                    self.txn(
                        acct,
                        self.when(y, m),
                        "out",
                        r.choice([50, 100, 150, 200]),
                        "cash",
                        None,
                        "ATM withdrawal",
                    )
                if index % 7 == 0:
                    self.txn(
                        acct,
                        datetime(y, m, 3, 8, 0),
                        "out",
                        28,
                        "transfer",
                        sh["union"],
                        "Membership dues",
                    )
                if index % 9 == 0:
                    self.txn(
                        acct,
                        datetime(y, m, 12, 8, 0),
                        "out",
                        40,
                        "transfer",
                        sh["church"],
                        "Donation",
                    )
            if index % 4 == 0:
                third = THIRD[index % len(THIRD)]
                self.planted.add(third)
                self.txn(
                    acct,
                    self.when(2026, 5),
                    "in",
                    r.choice([100, 200, 500]),
                    "transfer",
                    r.choice(sh["relatives"]),
                    f"Birthday gift, regards to {third}",
                )
            return {"employer": employer, "landlord": landlord}
        scale = cust["expected_monthly_turnover"] / 26000
        for y, m in MONTHS:
            mon = date(y, m, 1).strftime("%B")
            for _ in range(8):
                self.txn(
                    acct,
                    self.when(y, m),
                    "in",
                    r.uniform(400, 6000) * scale,
                    "transfer",
                    r.choice(sh["clients"]),
                    f"Invoice {r.randrange(1000, 9999)}",
                )
            for _ in range(4):
                self.txn(
                    acct,
                    self.when(y, m),
                    "out",
                    r.uniform(800, 3500) * scale,
                    "transfer",
                    r.choice(sh["suppliers"]),
                    f"Supplier invoice {r.randrange(1000, 9999)}",
                )
            self.txn(
                acct,
                datetime(y, m, 28, 9, 0),
                "out",
                6000 * scale,
                "transfer",
                sh["payroll"],
                f"Payroll {mon}",
            )
        return {}

    def raise_alert(
        self,
        cust: dict,
        acct: dict,
        rule: str,
        txn_ids: list[str],
        cps: list[dict],
        *,
        outcome: str,
        scenario: str,
        split: str,
        difficulty="clear",
        typology=None,
        reason=None,
        key=None,
        sensitive=False,
    ) -> None:
        txns = [self.by_id[t] for t in txn_ids]
        days = sorted(datetime.fromisoformat(t["booked_at"]).date() for t in txns)
        alert_id = f"AL-{len(self.alerts) + 1:03d}"
        raised = iso(days[-1] + timedelta(days=1))
        self.alerts.append(
            dict(
                alert_id=alert_id,
                customer_id=cust["customer_id"],
                account_id=acct["account_id"],
                rule=rule,
                raised=raised,
                as_of=raised,
                window_start=iso(days[0]),
                window_end=iso(days[-1]),
                amount=round(sum(t["amount"] for t in txns), 2),
                txn_ids=txn_ids,
                counterparty_ids=sorted({c["counterparty_id"] for c in cps}),
                split=split,
            )
        )
        self.gold.append(
            dict(
                alert_id=alert_id,
                outcome=outcome,
                typology=typology,
                reason_code=reason,
                difficulty=difficulty,
                key_evidence=key if key is not None else txn_ids,
                sensitive_relevant=sensitive,
                scenario=scenario,
            )
        )


def shared_parties(b: Builder) -> dict:
    org = lambda name, kind, **kw: b.cp(name, "org", kind, **kw)  # noqa: E731
    return {
        "employers": [
            org(n, "employer")
            for n in (
                "Nordhafen Logistik GmbH",
                "Elbtal Verkehrsbetriebe",
                "Stadtverwaltung Lindhafen",
                "Brennwerk Energie AG",
                "Tallinger Maschinenbau GmbH",
                "Kornfeld Verlag GmbH",
            )
        ],
        "landlords": [b.cp(b.person(), "person", "landlord") for _ in range(8)],
        "utilities": [org(n, "utility") for n in ("Stadtwerke Elbtal", "Lumen Strom GmbH")],
        "shops": [org(n, "supermarket") for n in ("Frischmarkt", "Kaufhalle Nord", "Biokorb")],
        "clients": [
            org(n, "business customer")
            for n in (
                "Hansekontor GmbH",
                "Wattwerk Solar GmbH",
                "Deichblick Hotel GmbH",
                "Fehmarn Reederei GmbH",
                "Lindhafen Immobilien GmbH",
                "Polarstern Catering GmbH",
            )
        ]
        + [b.cp(b.person(), "person", "private customer") for _ in range(4)],
        "suppliers": [
            org(n, "supplier", country=c)
            for n, c in (
                ("Baltica Hurt Sp. z o.o.", "PL"),
                ("Fornitura Adriatica S.r.l.", "IT"),
                ("Anadolu Tekstil A.S.", "TR"),
                ("Rheinland Baustoffe GmbH", "DE"),
                ("Nordsee Frischfisch GmbH", "DE"),
                ("Alpen Verpackung GmbH", "AT"),
            )
        ],
        "payroll": org("Lohnwerk Abrechnung GmbH", "payroll provider"),
        "relatives": [b.cp(b.person(), "person", "private individual") for _ in range(6)],
        "union": org(
            "Gewerkschaft Metall Bezirk Nord", "membership organisation", sensitive="trade_union"
        ),
        "church": org(
            "Evangelische Gemeinde Lindhafen", "religious community", sensitive="religion"
        ),
        "clinics": [
            org(n, "medical practice", sensitive="health")
            for n in ("Praxis Sonnental", "Onkologie Zentrum Elbtal")
        ],
    }


# --- suspicious scenarios -------------------------------------------------------------


def structuring(b: Builder, sh: dict, split: str) -> None:
    cust, acct = b.customer("retail", "taxi driver", 2600)
    b.background(cust, acct, sh)
    start = date(2026, b.rng.randrange(5, 9), b.rng.randrange(2, 12))
    offsets = sorted(b.rng.sample(range(12), 7))
    deposits = [
        b.txn(
            acct,
            datetime.combine(start + timedelta(days=o), datetime.min.time()).replace(hour=10),
            "in",
            round(b.rng.uniform(8600, 9800), -1),
            "cash",
            None,
            "Cash deposit",
        )
        for o in offsets
    ]
    recipient = b.cp(b.person(), "person", "private individual", country="TR", first_seen=start)
    for extra in (13, 14):
        b.txn(
            acct,
            datetime.combine(start + timedelta(days=extra), datetime.min.time()).replace(hour=11),
            "out",
            30000,
            "transfer",
            recipient,
            "Family support",
        )
    b.raise_alert(
        cust,
        acct,
        "STRUCTURING",
        deposits,
        [recipient],
        outcome="escalate",
        typology="structuring",
        difficulty="needs_investigation",
        scenario="structuring",
        split=split,
    )


def pass_through(b: Builder, sh: dict, split: str) -> None:
    cust, acct = b.customer("retail", b.rng.choice(["warehouse worker", "student"]), 2200)
    b.background(cust, acct, sh)
    start = datetime(2026, b.rng.randrange(5, 9), b.rng.randrange(3, 15), 9, 0)
    target = b.cp(b.person(), "person", "private individual", country="LT", first_seen=start.date())
    ins, outs, senders = [], [], []
    for i in range(9):
        sender = b.cp(b.person(), "person", "private individual", first_seen=start.date())
        senders.append(sender)
        at = start + timedelta(hours=16 * i)
        amount = round(b.rng.uniform(1200, 4800), -1)
        ins.append(b.txn(acct, at, "in", amount, "transfer", sender, "Payment"))
        outs.append(
            b.txn(
                acct,
                at + timedelta(hours=b.rng.randrange(2, 12)),
                "out",
                round(amount * 0.95, -1),
                "transfer",
                target,
                "Transfer",
            )
        )
    b.raise_alert(
        cust,
        acct,
        "RAPID_MOVEMENT",
        ins + outs,
        [target, *senders],
        outcome="escalate",
        typology="pass_through",
        difficulty="needs_investigation",
        scenario="pass_through",
        split=split,
    )


def layering(b: Builder, sh: dict, split: str) -> None:
    firms = [
        b.customer("business", occ, 30000)
        for occ in ("management consulting", "holding company", "real estate services")
    ]
    for cust, acct in firms:
        b.background(cust, acct, sh)
    (a, a_acct), (h, h_acct), (c, c_acct) = firms
    for left, right in ((a, h), (a, c), (h, c)):
        b.t["links"].append(
            dict(
                customer_id=left["customer_id"],
                related_customer_id=right["customer_id"],
                link_type="shared_director",
            )
        )
    as_cp = {
        x["customer_id"]: b.cp(
            x["name"], "org", x["occupation"], customer_id=x["customer_id"], iban=acc["iban"]
        )
        for x, acc in firms
    }
    flagged = []
    for rnd in range(2):
        at, amount = datetime(2026, 6 + rnd, 10, 10, 0), 40000 + rnd * 5000
        flagged.append(
            b.txn(a_acct, at, "out", amount, "transfer", as_cp[h["customer_id"]], "Loan agreement")
        )
        b.txn(h_acct, at, "in", amount, "transfer", as_cp[a["customer_id"]], "Loan agreement")
        b.txn(
            h_acct,
            at + timedelta(days=1),
            "out",
            amount - 500,
            "transfer",
            as_cp[c["customer_id"]],
            "Consulting fee",
        )
        b.txn(
            c_acct,
            at + timedelta(days=1),
            "in",
            amount - 500,
            "transfer",
            as_cp[h["customer_id"]],
            "Consulting fee",
        )
        b.txn(
            c_acct,
            at + timedelta(days=2),
            "out",
            amount - 1000,
            "transfer",
            as_cp[a["customer_id"]],
            "Repayment",
        )
        flagged.append(
            b.txn(
                a_acct,
                at + timedelta(days=2),
                "in",
                amount - 1000,
                "transfer",
                as_cp[c["customer_id"]],
                "Repayment",
            )
        )
    b.raise_alert(
        a,
        a_acct,
        "RAPID_MOVEMENT",
        flagged,
        [as_cp[h["customer_id"]], as_cp[c["customer_id"]]],
        outcome="escalate",
        typology="layering_related_parties",
        difficulty="needs_investigation",
        scenario="layering",
        split=split,
    )


def profile_mismatch(b: Builder, sh: dict, split: str) -> None:
    cust, acct = b.customer("retail", "student", 900)
    b.background(cust, acct, sh)
    prior = b.prior(
        cust,
        date(2026, 3, 2),
        "PROFILE_DEVIATION",
        "escalated",
        "Third-party business payments into a student account; referred to level 2.",
    )
    orgs = [
        b.cp(b.shells.pop(), "org", "trading company", country="CY", first_seen=date(2026, 7, 1))
        for _ in range(2)
    ]
    start = datetime(2026, 7, 6, 11, 0)
    ins = [
        b.txn(
            acct,
            start + timedelta(days=2 * i),
            "in",
            round(b.rng.uniform(6000, 14000), -2),
            "transfer",
            orgs[i % 2],
            f"Invoice {b.rng.randrange(1000, 9999)}",
        )
        for i in range(5)
    ]
    for i in range(5):
        b.txn(
            acct,
            start + timedelta(days=2 * i, hours=5),
            "out",
            5000,
            "cash",
            None,
            "Cash withdrawal",
        )
    b.raise_alert(
        cust,
        acct,
        "PROFILE_DEVIATION",
        ins,
        orgs,
        outcome="escalate",
        typology="profile_mismatch",
        difficulty="needs_investigation",
        key=[*ins, prior],
        scenario="profile_mismatch",
        split=split,
    )


def high_risk(b: Builder, sh: dict, split: str) -> None:
    cust, acct = b.customer("business", "import and export of machine parts", 60000, risk="high")
    b.background(cust, acct, sh)
    orgs = [
        b.cp(
            b.shells.pop(),
            "org",
            "consulting firm",
            country=HIGH_RISK[i],
            first_seen=date(2026, 8, 3),
        )
        for i in range(2)
    ]
    outs = [
        b.txn(
            acct,
            datetime(2026, 8, 4 + 3 * i, 14, 0),
            "out",
            round(b.rng.uniform(18000, 45000), -2),
            "transfer",
            orgs[i % 2],
            "Consulting services",
        )
        for i in range(4)
    ]
    name = orgs[0]["name"]
    b.news(
        name,
        date(2026, 6, 20),
        f"{name} named in invoice fraud inquiry",
        f"Prosecutors are examining payments routed through {name} for services that were never delivered.",
    )
    b.raise_alert(
        cust,
        acct,
        "HIGH_RISK_COUNTRY",
        outs,
        orgs,
        outcome="escalate",
        typology="high_risk_jurisdiction",
        difficulty="needs_investigation",
        scenario="high_risk",
        split=split,
    )


def sanctions(b: Builder, sh: dict, split: str) -> None:
    cust, acct = b.customer("business", "freight forwarding", 45000)
    b.background(cust, acct, sh)
    name = b.person(WL_FIRST, WL_LAST)
    dob = iso(date(b.rng.randrange(1960, 1985), b.rng.randrange(1, 13), b.rng.randrange(1, 28)))
    first, last = name.split()
    entry = b.watch(name, [f"{first[0]}. {last}"], dob, "KV")
    party = b.cp(
        name, "person", "private individual", country="KV", dob=dob, first_seen=date(2026, 7, 1)
    )
    outs = [
        b.txn(
            acct,
            datetime(2026, 7, 8 + 6 * i, 13, 0),
            "out",
            round(b.rng.uniform(12000, 25000), -2),
            "transfer",
            party,
            "Commission",
        )
        for i in range(2)
    ]
    b.news(
        name,
        date(2026, 2, 11),
        f"{name} added to sanctions list",
        f"Authorities listed {name} for facilitating trade in restricted goods.",
    )
    b.raise_alert(
        cust,
        acct,
        "WATCHLIST",
        outs,
        [party],
        outcome="escalate",
        typology="sanctions_match",
        difficulty="needs_investigation",
        key=[*outs, entry],
        scenario="sanctions_match",
        split=split,
    )


# --- legitimate scenarios -------------------------------------------------------------


def property_sale(b: Builder, sh: dict, split: str) -> None:
    cust, acct = b.customer(
        "retail",
        "teacher",
        3800,
        notes="Customer informed the branch in advance about the sale of an inherited flat. Notarised purchase contract is on file.",
    )
    b.background(cust, acct, sh)
    notary = b.cp("Notariat Feldkamp", "org", "notary")
    paid = b.txn(
        acct,
        datetime(2026, 7, 14, 10, 0),
        "in",
        240000,
        "transfer",
        notary,
        "Purchase price property sale file 118",
    )
    b.raise_alert(
        cust,
        acct,
        "HIGH_VALUE",
        [paid],
        [notary],
        outcome="close",
        reason="documented_expected_activity",
        difficulty="needs_investigation",
        scenario="property_sale",
        split=split,
    )


def vehicle_sale(b: Builder, sh: dict, split: str) -> None:
    cust, acct = b.customer(
        "retail",
        "electrician",
        3400,
        notes="Customer informed the branch about the private sale of a vehicle. Sales contract is on file.",
    )
    b.background(cust, acct, sh)
    buyer = b.cp(b.person(), "person", "private individual")
    paid = b.txn(
        acct,
        b.when(2026, b.rng.randrange(5, 9)),
        "in",
        28000,
        "transfer",
        buyer,
        "Purchase price vehicle",
    )
    b.raise_alert(
        cust,
        acct,
        "PROFILE_DEVIATION",
        [paid],
        [buyer],
        outcome="close",
        reason="documented_expected_activity",
        scenario="vehicle_sale",
        split=split,
    )


def seasonal(b: Builder, sh: dict, split: str) -> None:
    trade = b.rng.choice(
        ["beach kiosk", "ice cream parlour", "garden centre", "boat rental", "campsite"]
    )
    cust, acct = b.customer(
        "business",
        trade,
        18000,
        notes=(
            "Seasonal business: most takings arrive in cash between June and August. "
            "The declared turnover is the yearly average; summer months run at three to four "
            "times that."
        ),
    )
    b.background(cust, acct, sh)
    # The season is under way when the alert fires, so June shows the same cash pattern.
    # A separate generator keeps the values of every other scenario unchanged.
    season = random.Random(f"{SEED}-season-{acct['account_id']}")
    for i in range(12):
        b.txn(
            acct,
            datetime(2026, 6, 3 + 2 * i, 17, 0),
            "in",
            round(season.uniform(2500, 7500), -1),
            "cash",
            None,
            "Cash deposit daily takings",
        )
    prior = b.prior(
        cust,
        date(2025, 7, 21),
        "CASH_FREQUENCY",
        "closed_false_positive",
        "Seasonal cash pattern confirmed against last year's accounts.",
    )
    deposits = [
        b.txn(
            acct,
            datetime(2026, 7, 2 + 2 * i, 17, 0),
            "in",
            round(b.rng.uniform(2500, 7500), -1),
            "cash",
            None,
            "Cash deposit daily takings",
        )
        for i in range(10)
    ]
    b.raise_alert(
        cust,
        acct,
        "CASH_FREQUENCY",
        deposits,
        [],
        outcome="close",
        reason="consistent_with_profile",
        key=[prior],
        scenario="seasonal",
        split=split,
    )


def bonus(b: Builder, sh: dict, split: str) -> None:
    cust, acct = b.customer(
        "retail",
        b.rng.choice(["engineer", "sales manager", "pharmacist"]),
        b.rng.choice([4200, 5200, 6100]),
    )
    used = b.background(cust, acct, sh)
    month = b.rng.choice([6, 7])
    paid = b.txn(
        acct,
        datetime(2026, month, 27, 9, 30),
        "in",
        round(cust["expected_monthly_turnover"] * b.rng.uniform(2.5, 3.2), -2),
        "transfer",
        used["employer"],
        "Annual bonus 2026",
    )
    b.raise_alert(
        cust,
        acct,
        "PROFILE_DEVIATION",
        [paid],
        [used["employer"]],
        outcome="close",
        reason="explained_one_off",
        scenario="bonus",
        split=split,
    )


def supplier(b: Builder, sh: dict, split: str) -> None:
    cust, acct = b.customer(
        "business",
        b.rng.choice(["furniture retail", "textile wholesale", "restaurant supplies"]),
        50000,
    )
    b.background(cust, acct, sh)
    vendor = b.rng.choice(sh["suppliers"][:3])
    paid = [
        b.txn(
            acct,
            datetime(y, m, 15, 11, 0),
            "out",
            round(b.rng.uniform(15000, 35000), -2),
            "transfer",
            vendor,
            f"Supplier invoice {b.rng.randrange(1000, 9999)}",
        )
        for y, m in MONTHS
    ]
    b.raise_alert(
        cust,
        acct,
        "HIGH_VALUE",
        [paid[-1]],
        [vendor],
        outcome="close",
        reason="recurring_known_counterparty",
        scenario="supplier",
        split=split,
    )


def tuition(b: Builder, sh: dict, split: str) -> None:
    cust, acct = b.customer(
        "retail",
        "physician",
        6000,
        notes="Customer's child studies abroad; tuition is paid twice a year.",
    )
    b.background(cust, acct, sh)
    school = b.cp(b.unis.pop(), "org", "university", country=b.rng.choice(["NL", "IE", "ES"]))
    child = THIRD[len(b.alerts) % len(THIRD)]
    b.planted.add(child)
    b.txn(
        acct,
        datetime(2026, 4, 8, 10, 0),
        "out",
        9000,
        "transfer",
        school,
        f"Tuition fee spring term {child}",
    )
    paid = b.txn(
        acct,
        datetime(2026, 9, 8, 10, 0),
        "out",
        9000,
        "transfer",
        school,
        f"Tuition fee autumn term {child}",
    )
    b.raise_alert(
        cust,
        acct,
        "PROFILE_DEVIATION",
        [paid],
        [school],
        outcome="close",
        reason="documented_expected_activity",
        scenario="tuition",
        split=split,
    )


def false_match(b: Builder, sh: dict, split: str) -> None:
    cust, acct = b.customer("retail", "architect", 5200)
    b.background(cust, acct, sh)
    name = b.person(WL_FIRST, WL_LAST)
    entry = b.watch(name, [], iso(date(b.rng.randrange(1950, 1962), 3, 9)), "KV")
    party = b.cp(
        name,
        "person",
        "private individual",
        country="DE",
        dob=iso(date(b.rng.randrange(1990, 1999), 8, 21)),
    )
    paid = b.txn(
        acct,
        b.when(2026, b.rng.randrange(5, 9)),
        "out",
        1800,
        "transfer",
        party,
        "Apartment renovation",
    )
    b.raise_alert(
        cust,
        acct,
        "WATCHLIST",
        [paid],
        [party],
        outcome="close",
        reason="screening_false_match",
        difficulty="needs_investigation",
        key=[entry],
        scenario="false_match",
        split=split,
    )


def club_treasurer(b: Builder, sh: dict, split: str) -> None:
    cust, acct = b.customer(
        "retail",
        "bank clerk",
        3000,
        notes="Customer is the volunteer treasurer of a local football club and collects membership fees on this account.",
    )
    b.background(cust, acct, sh)
    start = datetime(2026, 8, 3, 9, 0)
    ins, total = [], 0.0
    for i in range(24):
        amount = b.rng.choice([40, 60, 80, 120])
        total += amount
        member = b.cp(b.person(), "person", "private individual")
        ins.append(
            b.txn(
                acct,
                start + timedelta(hours=5 * i),
                "in",
                amount,
                "transfer",
                member,
                "Membership fee",
            )
        )
    league = b.cp("Kreissportbund Elbtal", "org", "sports association")
    out = b.txn(
        acct,
        start + timedelta(days=6),
        "out",
        round(total * 0.97, 2),
        "transfer",
        league,
        "Membership fees season",
    )
    b.raise_alert(
        cust,
        acct,
        "RAPID_MOVEMENT",
        [*ins, out],
        [league],
        outcome="close",
        reason="documented_expected_activity",
        difficulty="needs_investigation",
        key=[out],
        scenario="club_treasurer",
        split=split,
    )


def clinic(b: Builder, sh: dict, split: str) -> None:
    cust, acct = b.customer("retail", "accountant", 4200)
    b.background(cust, acct, sh)
    practice = sh["clinics"][1]
    paid = [
        b.txn(
            acct,
            datetime(2026, m, 18, 10, 0),
            "out",
            4800,
            "transfer",
            practice,
            f"Treatment invoice {b.rng.randrange(1000, 9999)}",
        )
        for m in (6, 7, 8, 9)
    ]
    b.raise_alert(
        cust,
        acct,
        "PROFILE_DEVIATION",
        paid,
        [practice],
        outcome="close",
        reason="explained_one_off",
        difficulty="needs_investigation",
        sensitive=True,
        scenario="clinic",
        split=split,
    )


PLAN = (
    [
        (structuring, "dev"),
        (structuring, "heldout"),
        (pass_through, "dev"),
        (pass_through, "heldout"),
        (layering, "dev"),
        (profile_mismatch, "dev"),
        (high_risk, "dev"),
        (sanctions, "dev"),
        (sanctions, "heldout"),
        (property_sale, "dev"),
        (vehicle_sale, "dev"),
        (vehicle_sale, "heldout"),
        (false_match, "dev"),
        (false_match, "heldout"),
        (club_treasurer, "dev"),
        (clinic, "dev"),
    ]
    + [(seasonal, s) for s in ["dev"] * 4 + ["heldout"] * 2]
    + [(bonus, s) for s in ["dev"] * 5 + ["heldout"] * 2]
    + [(supplier, s) for s in ["dev"] * 4 + ["heldout"] * 2]
    + [(tuition, s) for s in ["dev"] * 4 + ["heldout"]]
)


def main(out: Path = DATA) -> None:
    b = Builder()
    sh = shared_parties(b)
    plan = list(PLAN)
    b.rng.shuffle(plan)
    for scenario, split in plan:
        scenario(b, sh, split)
    for _ in range(30):  # filler watchlist entries
        b.watch(
            b.person(WL_FIRST, WL_LAST),
            [],
            iso(date(b.rng.randrange(1950, 1990), b.rng.randrange(1, 13), b.rng.randrange(1, 28))),
            b.rng.choice([*HIGH_RISK, "CY", "LT"]),
            b.rng.choice(["sanctions", "pep"]),
        )
    b.planted.update({HOTLINE, BILLING})
    b.t["transactions"].sort(key=lambda t: (t["booked_at"], t["txn_id"]))

    for sub in ("core", "alerts", "gold"):
        shutil.rmtree(out / sub, ignore_errors=True)
        (out / sub).mkdir(parents=True)
    for table, rows in b.t.items():
        with (out / "core" / f"{table}.csv").open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(rows[0]), lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
    for alert, gold in zip(b.alerts, b.gold, strict=True):
        name = f"alert-{alert['alert_id'][3:]}.json"
        (out / "alerts" / name).write_text(json.dumps(alert, indent=1) + "\n", encoding="utf-8")
        (out / "gold" / name).write_text(json.dumps(gold, indent=1) + "\n", encoding="utf-8")
    (out / "gold" / "planted_identifiers.json").write_text(
        json.dumps(sorted(b.planted), indent=1) + "\n", encoding="utf-8"
    )
    print(
        f"{len(b.t['customers'])} customers, {len(b.t['transactions'])} transactions, {len(b.alerts)} alerts -> {out}"
    )


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else DATA)
