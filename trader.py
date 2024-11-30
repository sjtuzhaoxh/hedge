import asyncio
import signal
from exchanges.binance import Binance
from exchanges.gate import Gate
from models.models import *
from strategy.hedge import HedgeStrategy
from strategy.strategy import Strategy
from exchanges.exchange import Exchange
from tool.mathx import *
from tool.timex import *
from config import settings

QUOTE: str = settings.quote  # 计价币
SYMBOL_RANG: list[int] = settings.symbol_rang  # 监控的交易对的范围
SYMBOLS_BLACKLIST: list[str] = settings.symbols_blacklist  # 交易对黑名单
LEVERAGE: int = settings.leverage  # 开仓杠杆


class Trader:

    def __init__(self, strategy: Strategy):
        self.strategy = strategy
        self.exchanges: dict[str, Exchange] = {}

        self.orders: dict[str, Order] = {}

        # 下单锁
        self.order_lock: dict = {}

    def add_exchagne(self, ex: Exchange):
        ex.listen_bbo(self.on_bbo)
        ex.listen_order(self.on_order)
        self.exchanges[ex.__class__.__name__] = ex

    async def on_bbo(self, bbo: BBO):
        symbol = bbo.symbol
        now = time_ms()

        # 拦截锁
        if symbol in self.order_lock:
            return

        signal = self.strategy.gen_signal(
            now,
            symbol,
            list(self.exchanges.values()),
        )
        if signal:
            self.order_lock[symbol] = now
            await self.trade(now, signal)

            for ex in self.exchanges.values():
                await ex.update_balance()
                ex.pos = await ex.get_positions()

            # todo 暂时用这种方式解决高频下仓位更新不及时的问题
            await asyncio.sleep(2)
            del self.order_lock[symbol]

    async def on_order(self, order: Order):
        return

        order_key = order.ex_name + order.id
        price = order.price
        deal_price = order.deal_price

        # 市价单一般开仓价格都是0，这里用下单时记录的行情替代
        if price == 0:
            price = self.orders[order_key].price

        if order.status == OrderStatus.FILLED:
            if price == 0 or deal_price == 0:
                self.strategy.log.error(f"回调中的成交订单价格异常: {order}")
                return

            symbol = order.symbol
            side = order.side
            tside = order.trade_side

            # 计算滑点
            slip = floor(deal_price - price / price, 4) * 100

            msg = f'{symbol} '
            msg += '开' if tside == TradeSide.OPEN else '平'
            msg += '多' if side == Side.BUY else '空'
            msg += f'仓滑点:{slip}%'
            self.strategy.log.info(msg)

        # 订单结束,清除订单记录
        if order.status in [
                OrderStatus.PARTIALLY_FILLED,
                OrderStatus.FILLED,
                OrderStatus.CANCELED,
        ]:
            if order_key in self.orders:
                del self.orders[order_key]
        elif len(self.orders) > 500:
            self.orders = dict(list(self.orders.items())[-100:])

    async def trade(self, market_time: int, signal: Signal):
        symbol = signal.symbol
        ex_len = len(signal.exchanges)
        for ex_signal in signal.exchanges:
            delay = market_time - ex_signal.time
            msg = f'{symbol} 开单信号 行情延迟:{delay}'
            msg += f' 价差:{floor(signal.spread * 100, 2)}% 方向:{ex_signal.side},{ex_signal.tside} 类型:{signal.type} 价格:{ex_signal.price} 数量:{ex_signal.amount}'
            ex = self.exchanges[ex_signal.ex_name]
            ex.log.info(msg)

        # 并发下单
        ids: dict[str, str] = {}
        tasks = []
        for ex_signal in signal.exchanges:
            ex = self.exchanges[ex_signal.ex_name]
            t = self.create_order(market_time, ex, signal, ex_signal)
            f = asyncio.create_task(t)
            tasks.append(f)
        for task in tasks:
            ex_name, id = await task
            if id:
                ids[ex_name] = id

        if len(ids) != ex_len:
            print('有交易所下单失败,请排查原因')
            for ex_signal in signal.exchanges:
                ex = self.exchanges[ex_signal.ex_name]
                if ex_signal.ex_name in ids:
                    # await ex.cancel_order(id, symbol)
                    # todo 下单失败 研发阶段:记录失败原因，停止策略，等待排查
                    pass

    async def create_order(
        self,
        market_time: int,
        ex: Exchange,
        signal: Signal,
        ex_signal: ExchangeSignal,
    ) -> tuple[str, str]:
        id, text = await ex.create_order(
            signal.symbol,
            ex_signal.side,
            ex_signal.tside,
            signal.type,
            ex_signal.amount,
        )

        now = time_ms()
        delay = now - market_time
        msg = f'{signal.symbol} 下单延迟:{delay}'
        msg += f' 下单成功:{id}' if id else f' 下单失败:{text}'
        msg += f' 价差:{floor(signal.spread * 100, 2)}% 方向:{ex_signal.side},{ex_signal.tside} 类型:{signal.type} 价格:{ex_signal.price} 数量:{ex_signal.amount}'
        ex.log.info(msg)

        ex_name = ex.__class__.__name__

        # order = Order(
        #     ex_name=ex_name,
        #     symbol=signal.symbol,
        #     id=id,
        #     status=OrderStatus.NEW,
        #     side=ex_signal.side,
        #     trade_side=ex_signal.tside,
        #     price=ex_signal.price,
        #     amount=ex_signal.amount,
        #     c_time=now,
        # )
        # self.orders[ex_name + id] = order

        return ex_name, id

    def match_symbols(self) -> list[str]:
        symbols: list[str] = []

        exchanges = list(self.exchanges.values())
        master = exchanges[0]
        slaves = exchanges[1:]
        ex_len = len(self.exchanges)
        if ex_len == 1:
            for r in master.rules:
                symbols.append(r)
        elif ex_len > 1:
            for r in master.rules:
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

        # 过滤
        filter_symbols = []
        for symbol in symbols:
            # 过滤报价币
            if not symbol.endswith(QUOTE):
                continue

            # 过滤黑名单
            if symbol in SYMBOLS_BLACKLIST:
                continue

            filter_symbols.append(symbol)
        symbols = filter_symbols

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
        try:
            # 加载交易规则
            for ex in self.exchanges.values():
                ex.rules = await ex.get_rules()

            # 匹配交易对
            self.symbols = symbols if symbols else self.match_symbols()
            if not self.symbols:
                self.strategy.log.error("主副所中没有匹配的交易对")
                return
            self.strategy.log.info(f"找到 {len(self.symbols)} 个匹配的交易对")

            # 更新余额
            balance_total = 0
            msg = ''
            for ex in self.exchanges.values():
                await ex.update_balance()
                ex_name = ex.__class__.__name__
                balance = ex.account.swap_balance
                balance_total += balance
                msg += f' {ex_name}余额:{balance}'
            self.strategy.log.info(f"资金总额:{balance_total} {msg}")

            # 计算公共杠杆
            leverages: dict[str, int] = {}
            for ex in self.exchanges.values():
                for symbol in self.symbols:
                    rule = ex.get_rule(symbol)
                    if symbol in leverages:
                        l = leverages[symbol]
                        leverages[symbol] = min(rule.max_leverage, l)
                    else:
                        leverages[symbol] = min(rule.max_leverage, LEVERAGE)

            # 设置公共杠杆
            for symbol, leverage in leverages.items():
                for ex in self.exchanges.values():
                    time.sleep(0.1)
                    rule = ex.get_rule(symbol)
                    rule.trade_leverage = leverage

                    # continue # 测试时打开，提高debug速度
                    err = await ex.set_leverage(rule.symbol, leverage)
                    if err:
                        ex.log.error(f'{rule.symbol} 设置杠杆失败: {err}')

            # 启动ws监听
            tasks = []
            # 监听账号ws
            for ex in self.exchanges.values():
                tasks.append(asyncio.create_task(ex.listen_private()))
                tasks.append(asyncio.create_task(ex.listen_ws_api(5)))
            # 监听行情ws
            for symbol in self.symbols:
                await asyncio.sleep(0.1)
                for ex in self.exchanges.values():
                    tasks.append(asyncio.create_task(ex.listen_public(symbol)))
            await asyncio.gather(*tasks)
            print('任务完成')
        except asyncio.CancelledError:
            print("main: 任务被取消")
        except Exception as e:
            print(f"main: 报错 {e}")


async def shutdown(loop, signal=None):
    """取消所有任务并等待它们完成"""
    if signal:
        print(f"收到信号 {signal.name}，准备关闭事件循环...")

    # 获取所有正在运行的任务
    tasks = [
        task for task in asyncio.all_tasks(loop)
        if task is not asyncio.current_task(loop)
    ]
    if not tasks:
        return

    for task in tasks:
        task.cancel()

    # 等待所有任务完成取消
    await asyncio.gather(*tasks, return_exceptions=True)


if __name__ == '__main__':
    hedge = HedgeStrategy()
    trader = Trader(hedge)

    s1 = Secret(
        key=settings.master.key,
        secret=settings.master.secret,
        api_key=settings.master.api_key,
        private_key=settings.master.private_key,
        public_key=settings.master.public_key,
    )
    trader.add_exchagne(Binance(s1))

    s2 = Secret(
        key=settings.slave.key,
        secret=settings.slave.secret,
        api_key=settings.slave.api_key,
        private_key=settings.slave.private_key,
        public_key=settings.slave.public_key,
    )
    trader.add_exchagne(Gate(s2))

    loop = asyncio.new_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(
            sig, lambda s=sig: asyncio.create_task(shutdown(loop, signal=s)))

    try:
        loop.run_until_complete(trader.run([]))
    except KeyboardInterrupt:
        print("\n程序被手动中断")
    except Exception as e:
        print(f"\程序报错: {e}")
    finally:
        # 确保事件循环关闭之前所有任务都已完成
        pending = asyncio.all_tasks(loop)
        if pending:
            loop.run_until_complete(
                asyncio.gather(*pending, return_exceptions=True))
        loop.close()
        print("事件循环已关闭")
