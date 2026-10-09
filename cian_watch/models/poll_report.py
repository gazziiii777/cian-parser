from dataclasses import dataclass, field

from cian_watch.models.domclick import DomclickEstimate
from cian_watch.models.offer import Offer


@dataclass
class PriceChange:
    offer: Offer
    old_price: int

    def drop_percent(self) -> float:
        return (self.old_price - self.offer.price) / self.old_price * 100


@dataclass
class PollReport:
    seeded: bool = False
    new_offers: list[Offer] = field(default_factory=list)
    price_changes: list[PriceChange] = field(default_factory=list)
    reappeared: list[Offer] = field(default_factory=list)


@dataclass
class EstimationOutcome:
    estimates: dict[int, DomclickEstimate] = field(default_factory=dict)
    not_found: set[int] = field(default_factory=set)
