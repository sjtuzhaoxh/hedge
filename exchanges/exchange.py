from abc import ABC, abstractmethod
import copy
from typing import Awaitable, Callable

from models.enums import *
from models.models import *
from tool import logger


class Exchange(ABC):

    def __init__(self, secret: Secret):
        self.secret = secret

        self.log = logger.get_logger(self.__class__.__name__)
        self.rules: dict[str, ContractRule] = {}
        self.bbos: dict[str, BBO] = {}
        self.orders: dict[str, Order] = {}
        self.pos: dict[str, Position] = {}
        self.taker_fee_rate = 0.0005
        self.account: Account = Account()

        self.emit_bbo: Callable[[BBO], Awaitable[None]] = None
        self.emit_order: Callable[[Order], Awaitable[None]] = None

        self.done_staus = [
            OrderStatus.PARTIALLY_FILLED,
            OrderStatus.FILLED,
            OrderStatus.CANCELED,
        ]

    def listen_bbo(self, handler: Callable[[BBO], Awaitable[None]]):
        self.emit_bbo = handler

    def listen_order(self, handler: Callable[[Order], Awaitable[None]]):
        self.emit_order = handler

    @abstractmethod
    async def init(self, symbols: list[str]):
        """
        初始化设置
        根据交易所的不同规则来自行处理
        包括:设置杠杆、设置保证金模式、设置仓位模式
        """
        pass

    @abstractmethod
    async def listen_public(self, symbol: str):
        """公共频道监听"""
        pass

    @abstractmethod
    async def listen_private(self):
        """私有频道监听"""
        pass

    @abstractmethod
    async def listen_ws_api(self, count: int):
        """
        websocket api
        某些交易所上需要独立一条ws来执行ws的api
        """
        pass

    @abstractmethod
    async def get_rules(self) -> dict[str, ContractRule]:
        """获取交易规则"""
        pass

    def get_rule(self, symbol: str) -> ContractRule | None:
        """获取交易对的交易规则"""
        if symbol in self.rules:
            return self.rules[symbol]
        elif "1000" + symbol in self.rules:
            return self.rules["1000" + symbol]
        elif symbol.replace("1000", "") in self.rules:
            return self.rules[symbol.replace("1000", "")]
        else:
            return None

    def get_last_bbo(self, symbol: str) -> BBO | None:
        """获取最新的bbo"""
        bbo = None
        if symbol in self.bbos:
            bbo = self.bbos[symbol]
        elif "1000" + symbol in self.bbos:
            bbo = self.bbos["1000" + symbol]
        elif symbol.replace("1000", "") in self.bbos:
            bbo = self.bbos[symbol.replace("1000", "")]

        if not bbo: return bbo

        if bbo.symbol.startswith('1000'):
            bbo = copy.copy(bbo)
            bbo.bid = bbo.bid / 1000
            bbo.ask = bbo.ask / 1000
            bbo.bid_amount = bbo.bid_amount * 1000
            bbo.ask_amount = bbo.ask_amount * 1000
        return bbo

    @abstractmethod
    async def create_order(
        self,
        symbol: str,
        side: Side,
        trade_side: TradeSide,
        type: OrderType,
        amount: float,
        price: float = 0,
    ) -> tuple[str, str]:
        """
        创建订单
        return: 订单id, 错误日志
        """
        pass

    @abstractmethod
    async def cancel_order(self, id: str, symbol: str = ''):
        """取消订单"""
        pass

    @abstractmethod
    async def cancel_all_order(self, symbol: str = ''):
        """取消所有订单"""
        pass

    @abstractmethod
    async def get_orders(self) -> dict[str, Order]:
        """获取挂单列表"""
        pass

    @abstractmethod
    async def get_positions(self) -> dict[str, Position]:
        """获取仓位列表"""
        pass

    @abstractmethod
    async def set_leverage(self, symbol: str = '', leverage: int = 20) -> str | None:
        """设置杠杆"""
        pass

    @abstractmethod
    async def set_margin_mode(self, symbol: str = ''):
        """设置保证金模式为全仓"""
        pass

    @abstractmethod
    async def set_position_mode(self, symbol: str = ''):
        """设置持仓模式为双向持仓"""
        pass

    @abstractmethod
    async def update_balance(self):
        """
        更新余额
        合约余额、合约可用余额等
        """
        pass
