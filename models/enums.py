from enum import Enum


class OrderStatus(Enum):
    """订单状态"""
    # 新订单
    NEW = 'NEW'
    # 部分交易
    PARTIALLY_FILLED = 'PARTIALLY_FILLED'
    # 完全成交
    FILLED = 'FILLED'
    # 已取消
    CANCELED = 'CANCELED'

    def __str__(self):
        return self.value


class Side(Enum):
    """交易操作"""
    # 买入
    BUY = 'BUY'
    # 卖出
    SELL = 'SELL'

    def __str__(self):
        return self.value


class PositionSide(Enum):
    """仓位方向"""
    # 多头
    LONG = 'LONG'
    # 空头
    SHORT = 'SHORT'

    def __str__(self):
        return self.value


class TradeSide(Enum):
    """交易方向"""
    # 开仓
    OPEN = 'OPEN'
    # 平仓
    CLOSE = 'CLOSE'

    def __str__(self):
        return self.value


class OrderType(Enum):
    """订单类型"""
    # 市价单
    MARKET = 'MARKET'
    # 不能部分成交就撤单
    IOC = 'IOC'
    # 不能全部成功就撤单
    FOK = 'FOK'
    # 成交为止
    GTC = 'GTC'

    def __str__(self):
        return self.value
