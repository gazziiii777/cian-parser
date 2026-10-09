from dataclasses import dataclass


@dataclass
class RentEstimate:
    offer_id: int
    scope: str
    sample_size: int
    median_rent: int
    min_rent: int
    max_rent: int


@dataclass
class RentedStats:
    raion_name: str
    days: int
    count: int
    median_rent: int
    median_exposure_days: int
