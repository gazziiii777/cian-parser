class BlockedError(Exception):
    pass


class SerpParseError(Exception):
    pass


class DomclickError(Exception):
    pass


class DomclickAddressNotFound(DomclickError):
    pass


class DomclickNoPrediction(DomclickError):
    pass


class TunnelRefused(Exception):
    def __init__(self, target: str, status: str) -> None:
        super().__init__(f"upstream proxy refused tunnel to {target}: {status}")
        self.target = target
        self.status = status
