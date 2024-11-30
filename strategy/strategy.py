from abc import ABC, abstractmethod
import copy
from exchanges.exchange import Exchange
from models.models import *
from tool import logger


class Strategy(ABC):

    def __init__(self):
        self.log = logger.get_logger(self.__class__.__name__)

    @abstractmethod
    def gen_signal(
        self,
        now: int,
        symbol: str,
        exchanges: list[Exchange],
    ) -> Signal | None:
        pass

