from dataclasses import dataclass, field

from models.enums import *


@dataclass
class ContractRule:
    """合约交易规则"""
    # 交易对
    symbol: str
    # 最小价格精度（计价币）
    price_prec: int = 0
    # 最小数量精度（基础币）
    amount_prec: int = 0
    # 最大下单数量（基础币）
    max_amount: float = 0
    # 最小下单数量（基础币）
    min_amount: float = 0
    # 最大杠杆
    max_leverage: int = 20
    # 交易杠杆（策略初始化时预热）
    trade_leverage: int = 20
    # 合约面值（一份合约==N个币）
    contract_size: float = 1

    def __post_init__(self):
        self.price_prec = int(self.price_prec)
        self.amount_prec = int(self.amount_prec)
        self.max_amount = float(self.max_amount)
        self.min_amount = float(self.min_amount)
        self.max_leverage = int(self.max_leverage)
        self.contract_size = float(self.contract_size)


@dataclass
class BBO:
    """最佳买卖价格"""
    # 交易对
    symbol: str
    # 买一价格
    bid: float
    # 买一数量
    bid_amount: float
    # 卖一价格
    ask: float
    # 卖一数量
    ask_amount: float
    # 时间戳(毫秒)
    time: int

    def __post_init__(self):
        self.bid = float(self.bid)
        self.bid_amount = float(self.bid_amount)
        self.ask = float(self.ask)
        self.ask_amount = float(self.ask_amount)
        self.time = int(self.time)


@dataclass
class Order:
    # 交易所
    ex_name: str
    # 交易对
    symbol: str
    # 订单id
    id: str
    # 订单状态
    status: OrderStatus
    # 订单类型
    side: Side
    # 订单开平方向
    trade_side: TradeSide
    # 下单价格
    price: float = 0
    # 下单数量
    amount: float = 0
    # 成交均价
    deal_price: float = 0
    # 成交数量
    deal_amount: float = 0
    # 下单时间
    c_time: int = 0
    

    def __post_init__(self):
        self.id = str(self.id)
        self.price = float(self.price)
        self.amount = float(self.amount)
        self.deal_price = float(self.deal_price)
        self.deal_amount = float(self.deal_amount)


@dataclass
class Position:
    """仓位信息"""
    # 交易对
    symbol: str
    # 仓位id
    id: str
    # 订单方向
    side: Side
    # 开仓均价
    price: float
    # 仓位数量
    amount: float
    # 开仓时间 没有就0 让策略自己判断
    c_time: int = 0

    def __post_init__(self):
        self.id = str(self.id)
        self.price = float(self.price)
        self.amount = float(self.amount)
        self.c_time = int(self.c_time)


@dataclass
class Account:
    # 用户id
    user_id: str = ''
    # 是否为双向持仓模式
    in_dual_mode: bool = False
    # 合约账户资金
    swap_balance: float = 0
    # 合约账户可用余额
    swap_available: float = 0

    def __post_init__(self):
        self.user_id = str(self.user_id)
        self.swap_balance = float(self.swap_balance)
        self.swap_available = float(self.swap_available)


@dataclass
class HedgeSignal:
    """信号"""
    # 信号时间
    t: int
    # 交易对
    symbol: str
    # 价差
    spread: float
    # 主所bbo延迟
    m_bbo_delay: int
    # 副所bbo延迟
    s_bbo_delay: int
    # 主所价格
    m_price: float
    # 副所价格
    s_price: float
    # 主所数量 2个数量是因为下单数量可能一样,但是戳合的仓位不一定一样
    m_amount: float
    # 副所数量
    s_amount: float
    # 订单开平方向
    trade_side: TradeSide
    # 订单类型
    type: OrderType
    # 主所订单类型
    m_side: Side | None
    # 副所订单类型
    s_side: Side | None

    def __post_init__(self):
        self.m_price = float(self.m_price)
        self.s_price = float(self.s_price)
        self.m_amount = float(self.m_amount)
        self.s_amount = float(self.s_amount)
        self.t = int(self.t)


@dataclass
class ExchangeSignal:
    # 交易所名字
    ex_name: str
    # 订单开平方向
    tside: TradeSide
    # 订单类型
    side: Side
    # 开单价格
    price: float = 0
    # 开单数量
    amount: float = 0
    # 信号时间
    time: int = 0


@dataclass
class Signal:
    # 交易对
    symbol: str
    # 订单类型
    type: OrderType
    # 价差
    spread: float = 0
    # 每个交易所各自的配置
    exchanges: list[ExchangeSignal] = field(default_factory=list)


@dataclass
class Secret:
    key: str = ''
    secret: str = ''
    api_key: str = ''
    private_key: str = ''
    public_key: str = ''
