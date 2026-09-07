"""Point-in-time contract specifications supplied from archived instrument masters.

No historical lot sizes or holiday calendars are inferred from today's settings.
"""
from dataclasses import dataclass
from datetime import date
from pathlib import Path
import pandas as pd


@dataclass(frozen=True)
class ContractSpec:
    underlying: str
    expiry: date
    lot_size: int
    effective_from: date
    effective_to: date
    settlement: str = "cash"


class ContractMaster:
    def __init__(self, records=()):
        self.records = tuple(records)
        for spec in self.records:
            if not isinstance(spec.lot_size, int) or spec.lot_size <= 0 or not spec.underlying or pd.isna(spec.expiry) or pd.isna(spec.effective_from) or pd.isna(spec.effective_to) or spec.effective_from > spec.effective_to:
                raise ValueError("Invalid contract specification")

    @classmethod
    def from_csv(cls, path: str | Path):
        frame = pd.read_csv(path)
        records = []
        for row in frame.to_dict("records"):
            if float(row["lot_size"]) != int(row["lot_size"]):
                raise ValueError("Lot size must be an integer")
            records.append(ContractSpec(
                underlying=str(row["underlying"]),
                expiry=pd.Timestamp(row["expiry"]).date(),
                lot_size=int(row["lot_size"]),
                effective_from=pd.Timestamp(row["effective_from"]).date(),
                effective_to=pd.Timestamp(row["effective_to"]).date(),
                settlement=str(row.get("settlement", "cash")),
            ))
        return cls(records)

    def resolve(self, underlying, expiry, asof):
        matches = [s for s in self.records if s.underlying == underlying
                   and s.expiry == expiry and s.effective_from <= asof <= s.effective_to]
        if len(matches) > 1:
            raise ValueError("Overlapping contract specifications")
        return matches[0] if matches else None

    def expiries(self, underlying, asof, min_days=2):
        return sorted({s.expiry for s in self.records if s.underlying == underlying
                       and s.effective_from <= asof <= s.effective_to
                       and (s.expiry - asof).days >= min_days})
