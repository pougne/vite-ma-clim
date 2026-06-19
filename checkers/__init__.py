from .base import Checker, make_browser_context, accept_cookies  # noqa: F401
from .boulanger import BoulangerChecker  # noqa: F401
from .castorama import CastoramaChecker  # noqa: F401
from .optimea import OptimeaChecker  # noqa: F401
from .leroymerlin import LeroyMerlinChecker  # noqa: F401

REGISTRY = {
    "boulanger": BoulangerChecker,
    "castorama": CastoramaChecker,
    "optimea": OptimeaChecker,
    "leroymerlin": LeroyMerlinChecker,
}
