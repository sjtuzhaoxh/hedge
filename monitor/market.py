import asyncio
import copy
import os
import sys
import pandas as pd

from config import settings
from models.models import *
from exchanges.exchange import Exchange
from exchanges.binance import Binance
from exchanges.gate import Gate
from tool.mathx import *
from tool.timex import time_ms
from tool import logger

if __name__ == "__main__":
    sys.path.append(
        os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

QUOTE: str = settings.quote  # 计价币
SYMBOL_RANG: list[int] = settings.symbol_rang  # 监控的交易对的范围
SPREAD: float = settings.spread  # 开仓价差
MAX_DELAY: int = settings.max_delay  # 行情最大延迟


class Market:

    def __init__(self):
        self.log = logger.get_logger(self.__class__.__name__)

        s1 = Secret(key=settings.master.key, secret=settings.master.secret)
        bnb = Binance(s1)
        bnb.listen_bbo(self.on_bbo)

        s2 = Secret(key=settings.slave.key, secret=settings.slave.secret)
        gate = Gate(s2)
        gate.listen_bbo(self.on_bbo)

        self.exchanges: list[Exchange] = [bnb, gate]

        self.last_open_spread = None
        self.last_close_spread = None
        self.pos: dict[str, int] = {}

    def add_exchagne(self, ex: Exchange):
        ex.listen_bbo(self.on_bbo)
        self.exchanges.append(ex)

    def on_bbo(self, bbo: BBO):
        symbol = bbo.symbol
        now = time_ms()

        m_ex = self.exchanges[0]
        s_ex = self.exchanges[1]

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

        # 开 > SPREAD
        if m_bbo.bid > s_bbo.ask:
            open_spread = calc_spread(m_bbo.bid, s_bbo.ask)  # SELL_BUY
        else:
            open_spread = calc_spread(s_bbo.bid, m_bbo.ask)  # BUY_SELL
        open_spread = floor(open_spread, 4)

        # 平 <= 0
        if m_bbo.bid > s_bbo.ask:
            close_spread = calc_spread(m_bbo.ask, s_bbo.bid)
        else:
            close_spread = calc_spread(s_bbo.ask, m_bbo.bid)
        close_spread = floor(close_spread, 4)

        # 数据没变化，不记录
        if open_spread == self.last_open_spread and close_spread == self.last_close_spread:
            return

        # 储存上一条数据
        self.last_open_spread = open_spread
        self.last_close_spread = close_spread

        data = {}
        if symbol in self.pos:
            if close_spread <= 0:
                data['action'] = ['平']
                data['spread'] = [close_spread]
                del self.pos[symbol]
            else:
                return
        elif open_spread > SPREAD:
            data['action'] = ['开']
            data['spread'] = [open_spread]
            self.pos[symbol] = now
        else:
            return

        data['m_delay'] = [now - m_bbo.time]
        data['s_delay'] = [now - s_bbo.time]
        data['t'] = [now]

        df = pd.DataFrame(data)
        file_path = f'./cache/{symbol}.csv'
        if os.path.exists(file_path):
            df.to_csv(file_path, mode='a', index=False, header=False)
        else:
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            df.to_csv(file_path, index=False)
        # self.log.info(f'记录数据: {symbol} {data['action']} {data['spread']}')

    def fetch_pos(
        self,
        symbol: str,
        positions: dict[str, Position],
    ) -> Position:
        """查找仓位"""
        for s, p in positions.items():
            if s == symbol:
                if p.amount == 0: break
                return p
        return

    def match_symbols(self) -> list[str]:
        symbols = []

        ex_len = len(self.exchanges)
        if ex_len == 1:
            for r in self.exchanges[0].rules:
                symbols.append(r)
        elif ex_len > 1:
            master = self.exchanges[0]
            slaves = self.exchanges[1:]

            for r in master.rules:
                # 过滤掉不符合的币种
                if not r.endswith(QUOTE):
                    continue

                match = False
                for ex in slaves:
                    if r in ex.rules or "1000" + r in ex.rules or r.replace(
                            "1000", "") in ex.rules:
                        match = True
                    else:
                        match = False
                        break
                if match:
                    symbols.append(r)

        # 允许自由配置
        if len(SYMBOL_RANG) >= 2:
            start = SYMBOL_RANG[0]
            end = SYMBOL_RANG[1]
            if start == 0:
                if end != 0: return symbols[:end]
            else:
                if end != 0:
                    return symbols[start:end]
                else:
                    return symbols[start:]

        return symbols

    async def run(self, symbols: list[str] = []):
        # 加载交易规则
        for ex in self.exchanges:
            ex.rules = ex.get_rules()

        # 匹配交易对
        self.symbols = symbols if symbols else self.match_symbols()
        if not self.symbols:
            self.log.error("主副所中没有匹配的交易对")
            return
        self.log.info(f"找到 {len(self.symbols)} 个匹配的交易对")

        # 启动ws监听
        tasks = []
        # 监听行情ws
        for symbol in self.symbols:
            await asyncio.sleep(0.1)
            for ex in self.exchanges:
                tasks.append(asyncio.create_task(ex.listen_public(symbol)))
        await asyncio.gather(*tasks)


if __name__ == '__main__':
    market = Market()
    asyncio.run(market.run([]))
