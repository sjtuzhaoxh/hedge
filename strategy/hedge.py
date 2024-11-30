import asyncio
from exchanges.binance import Binance
from exchanges.gate import Gate
from strategy.strategy import Strategy
from exchanges.exchange import Exchange
from models.models import *
from config import settings
from tool.mathx import *
from tool.timex import time_ms

RESERVE_MARGIN: float = settings.reserve_margin  # 预留保证金
POS_RATE: float = settings.pos_rate  # 分仓占比
BBO_VOLUME_RATE: float = settings.bbo_volume_rate  # bbo容量占比
MAX_DELAY: int = settings.max_delay  # 行情最大延迟
SPREAD: float = settings.spread  # 开仓价差
LEVERAGE: int = settings.leverage  # 开仓杠杆
MIN_NOMINAL: float = settings.min_nominal  # 开仓最小名义价值


class HedgeStrategy(Strategy):

    def __init__(self):
        super().__init__()

    def gen_signal(
        self,
        now: int,
        symbol: str,
        exchanges: list[Exchange],
    ) -> Signal | None:
        if len(exchanges) != 2:
            self.log.error('策略只支持2个交易所')
            return

        m_ex = exchanges[0]
        s_ex = exchanges[1]

        # 获取最新的bbo
        m_bbo = m_ex.get_last_bbo(symbol)
        s_bbo = s_ex.get_last_bbo(symbol)
        if not m_bbo or not s_bbo:
            return

        # 过滤延迟太大的行情
        m_delay = now - m_bbo.time
        s_delay = now - s_bbo.time
        if MAX_DELAY < m_delay or MAX_DELAY < s_delay:
            return

        # todo 追加仓位,现在是有仓位就不追加
        # 找出仓位
        m_pos = self.fetch_pos(symbol, m_ex.pos)
        s_pos = self.fetch_pos(symbol, s_ex.pos)

        # 判断平仓
        if m_pos and s_pos:
            return self.gen_close_pos_sign(
                symbol,
                m_bbo,
                s_bbo,
                m_ex,
                s_ex,
                m_pos,
                s_pos,
            )

        # 判断开仓
        elif not m_pos and not s_pos:
            return self.gen_open_pos_sign(
                now,
                symbol,
                m_bbo,
                s_bbo,
                m_ex,
                s_ex,
            )

        return

    def fetch_pos(
        self,
        symbol: str,
        positions: dict[str, Position],
    ) -> Position:
        """查找仓位"""
        for p in positions.values():
            if p.symbol == symbol:
                if p.amount == 0: break
                return p
        return

    def gen_close_pos_sign(
        self,
        symbol: str,
        m_bbo: BBO,
        s_bbo: BBO,
        m: Exchange,
        s: Exchange,
        m_pos: Position,
        s_pos: Position,
    ) -> HedgeSignal | None:
        """产生平仓信号"""
        m_data = None
        s_data = None

        # 开仓时是 主空 副多
        if m_pos.side == Side.SELL and s_pos.side == Side.BUY:
            s1 = calc_spread(m_bbo.ask, s_bbo.bid)
            if s1 <= 0:
                spread = s1
                m_data = (m_bbo.ask, m_bbo.ask_amount)
                s_data = (s_bbo.bid, s_bbo.bid_amount)
        # 开仓时是 主多 副空
        elif m_pos.side == Side.BUY and s_pos.side == Side.SELL:
            s2 = calc_spread(s_bbo.ask, m_bbo.bid)
            if s2 <= 0:
                spread = s2
                m_data = (m_bbo.bid, m_bbo.bid_amount)
                s_data = (s_bbo.ask, s_bbo.ask_amount)

        if m_data and s_data:
            m_bbo_price, m_bbo_contract_count = m_data
            s_bbo_price, s_bbo_contract_count = s_data

            # 查询规则
            m_rule = m.get_rule(symbol)
            s_rule = s.get_rule(symbol)

            # 计算是否值得平 开仓没赌对,平仓时再赌一次
            # 手续费
            m_fee = (m_bbo_price + m_pos.price) * m.taker_fee_rate
            s_fee = (s_bbo_price + s_pos.price) * s.taker_fee_rate
            fee = m_fee + s_fee
            # 盈亏
            if m_pos.side == Side.SELL:
                m_pnl = m_pos.price - m_bbo_price
                s_pnl = s_bbo_price - s_pos.price
            else:
                m_pnl = m_bbo_price - m_pos.price
                s_pnl = s_pos.price - s_bbo_price
            pnl = m_pnl + s_pnl
            if pnl < 0: 
                self.log.info(f'{symbol} 价差回归,但是不盈利')
                return
            # 利润 = 盈亏 - 手续费
            profit = pnl - fee
            if profit < 0: 
                self.log.info(f'{symbol} 价差回归,但是还不够交手续费')
                return
            # 回报率 = 利润 / 开仓成本
            profit_rate = profit / (m_pos.price + s_pos.price)
            # 盈利大于0.2%，平
            if profit_rate < 0.002:
                self.log.info(f'{symbol} 价差回归,但是盈利不足 回报率:{profit_rate}')
                return

            # 计算应平币数
            coin_count = min(
                m_bbo_contract_count * m_rule.contract_size *
                BBO_VOLUME_RATE,  # 盘口币数
                s_bbo_contract_count * s_rule.contract_size *
                BBO_VOLUME_RATE,  # 盘口币数
                m_pos.amount * m_rule.contract_size,  # 仓位币数
                s_pos.amount * s_rule.contract_size,  # 仓位币数
            )

            # 币数还原成合约数
            m_contract_count = coin_count / m_rule.contract_size
            s_contract_count = coin_count / s_rule.contract_size

            # 规范数量精度 主张 * 主面值 = 副张 * 副面值
            # 合约面值不一样时，需要约束成相同的币数
            amount_prec = min(m_rule.amount_prec, s_rule.amount_prec)
            if m_rule.contract_size == s_rule.contract_size:
                m_contract_count = floor(m_contract_count, amount_prec)
                s_contract_count = floor(s_contract_count, amount_prec)
            elif m_rule.contract_size < s_rule.contract_size:
                s_contract_count = floor(s_contract_count, amount_prec)
                m_contract_count = (s_contract_count * s_rule.contract_size
                                    ) / m_rule.contract_size
            elif m_rule.contract_size > s_rule.contract_size:
                m_contract_count = floor(m_contract_count, amount_prec)
                s_contract_count = (m_contract_count * m_rule.contract_size
                                    ) / s_rule.contract_size

            # 拦截错误的数量
            if m_contract_count == 0 or s_contract_count == 0:
                return

            return Signal(
                symbol=symbol,
                spread=spread,
                type=OrderType.MARKET,
                exchanges=[
                    ExchangeSignal(
                        ex_name=m.__class__.__name__,
                        tside=TradeSide.CLOSE,
                        side=m_pos.side,
                        price=m_bbo_price,
                        amount=m_contract_count,
                        time=m_bbo.time,
                    ),
                    ExchangeSignal(
                        ex_name=s.__class__.__name__,
                        tside=TradeSide.CLOSE,
                        side=s_pos.side,
                        price=s_bbo_price,
                        amount=s_contract_count,
                        time=s_bbo.time,
                    ),
                ],
            )

    def gen_open_pos_sign(
        self,
        now: int,
        symbol: str,
        m_bbo: BBO,
        s_bbo: BBO,
        m: Exchange,
        s: Exchange,
    ) -> HedgeSignal:
        """产生开仓信号"""
        # 可用余额
        available = self.get_available(m, s)
        if available <= 0:
            return

        # 计算价差
        s1 = calc_spread(m_bbo.bid, s_bbo.ask)
        s2 = calc_spread(s_bbo.bid, m_bbo.ask)
        if s1 > SPREAD:
            # 主空 副多
            spread = s1
            m_data = (m_bbo.bid, m_bbo.bid_amount)
            s_data = (s_bbo.ask, s_bbo.ask_amount)
            m_side = Side.SELL
            s_side = Side.BUY
        elif s2 > SPREAD:
            # 主多 副空
            spread = s2
            m_data = (m_bbo.ask, m_bbo.ask_amount)
            s_data = (s_bbo.bid, s_bbo.bid_amount)
            m_side = Side.BUY
            s_side = Side.SELL
        else:
            m_data = None
            s_data = None

        # 满足价差
        if m_data and s_data:
            m_bbo_price, m_bbo_contract_count = m_data
            s_bbo_price, s_bbo_contract_count = s_data

            # 查询规则
            m_rule = m.get_rule(symbol)
            s_rule = s.get_rule(symbol)

            # bbo最小币数库存
            min_bbo_coin_count = min(
                m_bbo_contract_count * m_rule.contract_size,
                s_bbo_contract_count * s_rule.contract_size,
            )
            # 可开合约价值
            order_value = available * m_rule.trade_leverage
            # 计算最低可开币数
            coin_count = min(
                min_bbo_coin_count * BBO_VOLUME_RATE,  # 较小盘口的分仓币数
                (order_value / m_bbo_price),  # 余额的最大可开币数
                (order_value / s_bbo_price),  # 余额的最大可开币数
                m_rule.max_amount * m_rule.contract_size,  # 最大下单币数
                s_rule.max_amount * s_rule.contract_size,  # 最大下单币数
            )
            # 还原合约面值成张数
            m_contract_count = coin_count / m_rule.contract_size
            s_contract_count = coin_count / s_rule.contract_size

            # 规范数量精度 主张 * 主面值 = 副张 * 副面值
            # 合约面值不一样时，需要约束成相同的币数
            amount_prec = min(m_rule.amount_prec, s_rule.amount_prec)
            if m_rule.contract_size == s_rule.contract_size:
                m_contract_count = floor(m_contract_count, amount_prec)
                s_contract_count = floor(s_contract_count, amount_prec)
            elif m_rule.contract_size < s_rule.contract_size:
                s_contract_count = floor(s_contract_count, amount_prec)
                m_contract_count = (s_contract_count * s_rule.contract_size
                                    ) / m_rule.contract_size
            elif m_rule.contract_size > s_rule.contract_size:
                m_contract_count = floor(m_contract_count, amount_prec)
                s_contract_count = (m_contract_count * m_rule.contract_size
                                    ) / s_rule.contract_size

            # 验证是否符合最小下单量
            if m_contract_count < m_rule.min_amount:
                self.log.warning(
                    f'[{symbol}] 主所 {m_contract_count} < 最小下单量 {m_rule.min_amount}'
                )
                return
            elif s_contract_count < s_rule.min_amount:
                self.log.warning(
                    f'[{symbol}] 副所 {s_contract_count} < 最小下单量 {s_rule.min_amount}'
                )
                return

            # 判断符合最小名义价值
            if m_bbo_price * m_contract_count * m_rule.contract_size < MIN_NOMINAL:
                self.log.warning(f'[{symbol}] 主所下单不足最小名义价值')
                return
            elif s_bbo_price * s_contract_count * s_rule.contract_size < MIN_NOMINAL:
                self.log.warning(f'[{symbol}] 副所下单不足最小名义价值')
                return

            return Signal(
                symbol=symbol,
                spread=spread,
                type=OrderType.MARKET,
                exchanges=[
                    ExchangeSignal(
                        ex_name=m.__class__.__name__,
                        tside=TradeSide.OPEN,
                        side=m_side,
                        price=m_bbo_price,
                        amount=m_contract_count,
                        time=m_bbo.time,
                    ),
                    ExchangeSignal(
                        ex_name=s.__class__.__name__,
                        tside=TradeSide.OPEN,
                        side=s_side,
                        price=s_bbo_price,
                        amount=s_contract_count,
                        time=s_bbo.time,
                    ),
                ],
            )

        return

    def get_available(self, m: Exchange, s: Exchange) -> float:
        """获取可用余额"""
        # 计算主所分仓后的余额
        m_swap = m.account.swap_balance
        m_swap_ava = m.account.swap_available
        m_pos_rate_balance = m_swap * POS_RATE
        m_reserve_balance = m_swap * RESERVE_MARGIN
        if m_swap_ava <= 0:
            # msg = f'主所余额不足 可用余额:{m_swap_ava} 分仓:{m_pos_rate_balance}'
            # m.log.warning(msg)
            return 0
        if m_swap_ava - m_pos_rate_balance < m_reserve_balance:
            # msg = f'主所余额不足 可用余额:{m_swap_ava} 分仓:{m_pos_rate_balance}'
            # m.log.warning(msg)
            return 0

        # 计算副所分仓后的余额
        s_swap = s.account.swap_balance
        s_swap_ava = s.account.swap_available
        s_pos_rate_balance = s_swap * POS_RATE
        s_reserve_balance = s_swap * RESERVE_MARGIN
        if s_swap_ava <= 0:
            # msg = f'副所余额不足 可用余额:{s_swap_ava} 分仓:{s_pos_rate_balance}'
            # s.log.warning(msg)
            return 0
        if s_swap_ava - s_pos_rate_balance < s_reserve_balance:
            # msg = f'副所余额不足 可用余额:{s_swap_ava} 分仓:{s_pos_rate_balance}'
            # s.log.warning(msg)
            return 0

        return min(m_pos_rate_balance, s_pos_rate_balance)


if __name__ == '__main__':

    async def test():
        s = HedgeStrategy()
        now = time_ms()

        bnb = Binance(
            Secret(
                key=settings.master.key,
                secret=settings.master.secret,
                api_key=settings.master.api_key,
                private_key=settings.master.private_key,
                public_key=settings.master.public_key,
            ))
        gate = Gate(
            Secret(
                key=settings.slave.key,
                secret=settings.slave.secret,
                api_key=settings.slave.api_key,
                private_key=settings.slave.private_key,
                public_key=settings.slave.public_key,
            ))

        bnb.account.swap_balance = 100
        bnb.account.swap_available = 100
        gate.account.swap_balance = 100
        gate.account.swap_available = 100

        bnb.rules = await bnb.get_rules()
        gate.rules = await gate.get_rules()

        bnb.bbos = {
            'ARPAUSDT': BBO('ARPAUSDT', 0.04610, 10000, 0.04620, 10000, now),
        }
        gate.bbos = {
            'ARPAUSDT': BBO('ARPAUSDT', 0.04651, 10000, 0.04700, 10000, now),
        }

        signal = s.gen_signal(now, 'ARPAUSDT', [bnb, gate])
        print(signal)

    asyncio.run(test())
