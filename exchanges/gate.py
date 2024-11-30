import asyncio
import hashlib
import hmac
import json
import time
from urllib.parse import urlencode
import uuid

import requests
from websockets import WebSocketClientProtocol
from exchanges.conn_pool import ConnPool
from exchanges.exchange import Exchange
from exchanges.ws import WS
from models.enums import *
from models.models import *
from tool import timex
from tool.mathx import prec
from tool.timex import time_s
from config import settings

BASE_REST = 'https://api.gateio.ws'
BASE_WS = 'wss://fx-ws.gateio.ws/v4/ws/usdt'


class Gate(Exchange):

    def __init__(self, secret: Secret):
        super().__init__(secret)
        self.req = requests.Session()
        self.wss: dict[str, WS] = {}
        self.ping_interval = 10

    async def listen_public(self, symbol: str = ''):
        if symbol:
            name = f'{self.__class__.__name__} {symbol}'
            ws = WS(
                uri=BASE_WS,
                name=name,
                symbol=symbol,
                on_conn=self.pub_conn,
                on_msg=self.pub_msg,
            )
            self.wss[symbol] = ws
            await ws.loop_conn()

    async def listen_private(self):
        name = f'{self.__class__.__name__} 私有连接'
        ws = WS(
            uri=BASE_WS,
            name=name,
            on_conn=self.pri_conn,
            on_msg=self.pri_msg,
        )
        self.wss['PRIVATE'] = ws
        await ws.loop_conn()

    async def listen_ws_api(self, count: int):

        def new_ws() -> WS:
            name = f'{self.__class__.__name__} WSAPI连接'
            return WS(
                uri=BASE_WS,
                name=name,
                on_conn=self.wsapi_conn,
                on_msg=self.wsapi_msg,
            )

        self.ws_api_pool = ConnPool(self.log, new_ws)
        await self.ws_api_pool.run(count, 0.5)

    async def loop_ping(self, conn: WebSocketClientProtocol):
        while 1:
            await asyncio.sleep(self.ping_interval)
            now = time_s()
            req = {"time": now, "channel": "futures.ping"}
            await conn.send(json.dumps(req))

    async def pub_conn(
        self,
        conn: WebSocketClientProtocol,
        symbol: str,
    ) -> list[asyncio.Task]:
        """公共ws连接事件"""
        ws = self.wss[symbol]
        now = time_s()
        ex_symbol = symbol.replace(settings.quote, '_' + settings.quote)
        msg = {
            "time": now,
            "channel": "futures.book_ticker",
            "event": "subscribe",
            "payload": [ex_symbol],
        }
        await ws.send(msg)
        return [asyncio.create_task(self.loop_ping(conn))]

    async def pub_msg(
        self,
        conn: WebSocketClientProtocol,
        symbol: str,
        msg: str,
    ):
        """公共ws消息事件"""
        msg = json.loads(msg)
        if msg['channel'] == 'futures.book_ticker' and msg['event'] == 'update':
            data = msg['result']
            bbo = BBO(
                symbol=symbol,
                bid=data['b'],
                bid_amount=data['B'],
                ask=data['a'],
                ask_amount=data['A'],
                time=data['t'],
            )

            self.bbos[symbol] = bbo
            await self.emit_bbo(bbo)

        if 'request_id' in msg and ('ack' not in msg or not msg['ack']):
            return msg, msg['request_id']
        return msg, ''

    async def pri_conn(
        self,
        conn: WebSocketClientProtocol,
        symbol: str,
    ) -> list[asyncio.Task]:
        """私有ws连接事件"""
        ws = self.wss['PRIVATE']
        now = time_s()

        sign = self.get_sign('futures.orders', 'subscribe', now)
        req = {
            "time": now,
            "channel": "futures.orders",
            "event": "subscribe",
            "payload": [self.account.user_id, '!all'],
            "auth": {
                "method": "api_key",
                "KEY": self.secret.key,
                "SIGN": sign
            }
        }
        await ws.send(req)

        sign = self.get_sign('futures.positions', 'subscribe', now)
        req = {
            "time": now,
            "channel": "futures.positions",
            "event": "subscribe",
            "payload": [self.account.user_id, '!all'],
            "auth": {
                "method": "api_key",
                "KEY": self.secret.key,
                "SIGN": sign
            }
        }
        await ws.send(req)

        # 重连的时候防止丢数据
        self.orders = await self.get_orders()
        self.pos = await self.get_positions()

        return [asyncio.create_task(self.loop_ping(conn))]

    def get_sign(self, ch: str, event: str, now: int) -> str:
        """账号ws鉴权"""
        secret = self.secret.secret
        msg = f'channel={ch}&event={event}&time={now}'
        return hmac.new(
            secret.encode("utf-8"),
            msg.encode("utf-8"),
            hashlib.sha512,
        ).hexdigest()

    async def pri_msg(
        self,
        conn: WebSocketClientProtocol,
        symbol: str,
        msg: str,
    ):
        """私有ws消息事件"""
        msg = json.loads(msg)

        if 'event' in msg and msg['event'] == 'update':
            if msg['channel'] == 'futures.orders':
                await self.handle_order(msg)
            elif msg['channel'] == 'futures.positions':
                await self.handle_pos(msg)

        if 'request_id' in msg and ('ack' not in msg or not msg['ack']):
            return msg, msg['request_id']
        return msg, ''

    async def wsapi_conn(
        self,
        conn: WebSocketClientProtocol,
        symbol: str,
    ) -> list[asyncio.Task]:
        """wsapi连接事件"""
        await self.ws_login()

        return [asyncio.create_task(self.loop_ping(conn))]

    async def ws_login(self):
        """登录websocket"""
        now = timex.time_s()
        msg_id = uuid.uuid4().hex
        sign = self.ws_api_sign('futures.login', '', now)
        req = {
            "time": now,
            "channel": "futures.login",
            "event": "api",
            "payload": {
                "api_key": self.secret.key,
                "signature": sign,
                "timestamp": str(now),
                "req_id": msg_id,
            },
        }
        await self.ws_api_pool.send(req)

    async def wsapi_msg(
        self,
        conn: WebSocketClientProtocol,
        symbol: str,
        msg: str,
    ):
        """wsapi消息事件"""
        msg = json.loads(msg)

        if 'ack' in msg and msg['ack']:
            return msg, ''
        
        # 请求报错
        if 'data' in msg and 'errs' in msg['data'] and msg['data']['errs']:
            # self.log.error(f'wsapi请求报错: {str(msg['data']['errs'])}')

            # 登录失败,重连
            if 'channel' in msg['header'] and msg['header']['channel'] == 'futures.login':
                await conn.close()

        # 返回请求id
        if 'request_id' in msg:
            return msg, msg['request_id']
        return msg, ''

    def ws_api_sign(self, ch: str, query: str, now: int) -> str:
        """ws登录鉴权"""
        secret = self.secret.secret
        msg = f'api\n{ch}\n{query}\n{now}'
        return hmac.new(
            secret.encode("utf-8"),
            msg.encode("utf-8"),
            hashlib.sha512,
        ).hexdigest()

    async def handle_order(self, msg: dict):
        """更新订单"""
        for data in msg['result']:
            id = str(data['id'])
            symbol = data['contract'].replace('_', '')
            side = Side.BUY if data['size'] > 0 else Side.SELL
            amount = abs(float(data['size']))
            deal_amount = amount - float(data['left'])
            tside = TradeSide.CLOSE if data['is_close'] else TradeSide.OPEN
            if data['status'] == 'open' or data['finish_as'] == '_new':
                status = OrderStatus.NEW
            elif data['finish_as'] in [
                    'cancelled',
                    'liquidated',
                    'reduce_only',
                    'position_close',
                    'stp',
                    'reduce_out',
            ]:
                status = OrderStatus.CANCELED
            else:
                status = OrderStatus.FILLED

            order = Order(
                ex_name=self.__class__.__name__,
                symbol=symbol,
                id=id,
                price=data['price'],
                amount=amount,
                deal_price=data['fill_price'],
                deal_amount=deal_amount,
                status=status,
                side=side,
                trade_side=tside,
                c_time=data['create_time_ms'],
            )

            # 维护本地订单
            if status in self.done_staus and id in self.orders:
                del self.orders[id]
            else:
                self.orders[id] = order

            await self.emit_order(order)

            if len(self.orders) > 500:
                self.orders = dict(list(self.orders.items())[-100:])

    async def handle_pos(self, msg: dict):
        """更新仓位"""
        for data in msg['result']:
            symbol = data['contract'].replace('_', '')
            price = data['entry_price']
            size = float(data['size'])
            amount = abs(size)
            side = Side.BUY if size > 0 else Side.SELL
            id = symbol + str(side)

            # 平仓
            if amount == 0 and id in self.pos:
                del self.pos[id]
                continue

            status_str = '更新' if id in self.pos else '新增'
            pos = Position(
                symbol=symbol,
                id=id,
                side=side,
                price=price,
                amount=amount,
            )
            self.pos[id] = pos

            m = f'{status_str}仓位: {id} 方向:{side} 价格:{price} 数量:{amount}'
            self.log.info(m)

    async def init(self, symbols: list[str]):
        await self.set_position_mode()
        await self.cancel_all_order()

    def gen_sign(self, method, url, query_string=None, payload_string=None):
        key: str = self.secret.key
        secret: str = self.secret.secret

        t = time_s()
        m = hashlib.sha512()
        m.update((payload_string or "").encode('utf-8'))
        hashed_payload = m.hexdigest()
        s = '%s\n%s\n%s\n%s\n%s' % (method, url, query_string
                                    or "", hashed_payload, t)
        sign = hmac.new(
            secret.encode('utf-8'),
            s.encode('utf-8'),
            hashlib.sha512,
        ).hexdigest()
        return {'KEY': key, 'Timestamp': str(t), 'SIGN': sign}

    async def go(
        self,
        method: str,
        path: str,
        query: dict = {},
        payload: dict = {},
    ):
        url = BASE_REST + path
        args = {}

        query_str = ''
        payload_str = ''
        if query:
            query_str = urlencode(query)
            url = f'{url}?{query_str}'
        if payload:
            payload_str = json.dumps(payload)
            args['data'] = payload_str
        headers = self.gen_sign(method, path, query_str, payload_str)
        headers.update({
            'Accept': 'application/json',
            'Content-Type': 'application/json',
        })
        args['headers'] = headers

        res = self.req.request(method, url, **args)
        return res

    async def get_rules(self) -> dict[str, ContractRule]:
        res = await self.go('GET', '/api/v4/futures/usdt/contracts')
        res = res.json()
        rules = {}
        for data in res:
            symbol: str = data['name'].replace('_', '')
            if not symbol.endswith(settings.quote):
                continue

            rules[symbol] = ContractRule(
                symbol=symbol,
                price_prec=prec(data['order_price_round']),
                amount_prec=0,
                max_amount=data['order_size_max'],
                min_amount=data['order_size_min'],
                max_leverage=data['leverage_max'],
                contract_size=data['quanto_multiplier'],
            )
        return rules

    async def create_order(
        self,
        symbol: str,
        side: Side,
        trade_side: TradeSide,
        type: OrderType,
        amount: float,
        price: float = 0,
    ) -> tuple[str, str]:
        args = {}
        args['contract'] = symbol.replace(settings.quote, '_' + settings.quote)

        if trade_side == TradeSide.CLOSE:
            args['size'] = amount if side == Side.SELL else amount * -1
            args['reduce_only'] = True
        else:
            args['size'] = amount if side == Side.BUY else amount * -1

        if type == OrderType.MARKET:
            args['price'] = '0'
            args['tif'] = 'ioc'
        else:
            args['price'] = str(price)
            args['tif'] = str(type).lower()

        msg_id = uuid.uuid4().hex
        req = {
            "time": int(time.time()),
            "channel": "futures.order_place",
            "event": "api",
            "payload": {
                "req_id": msg_id,
                "req_param": args
            },
        }
        res, ok = await self.ws_api_pool.send(req, msg_id)
        if not ok:
            return '', 'ws未连接'
        if 'errs' in res['data'] and res['data']['errs']:
            return '', str(res['data']['errs'])

        return str(res['data']['result']['id']), ''

    async def cancel_order(self, id: str, symbol: str = ''):
        res = await self.go('DELETE', f'/api/v4/futures/usdt/orders/{id}')
        if res.status_code == 200:
            self.log.info(f'撤销订单{id}成功')
        else:
            self.log.info(f'撤销订单{id}失败: {res.text}')

    async def cancel_all_order(self, symbol: str = ''):
        res = await self.go(
            'DELETE',
            f'/api/v4/futures/usdt/orders',
            query={
                'contract': symbol,
            },
        )
        if res.status_code == 200:
            self.log.info(f'全部撤单成功')
        else:
            self.log.info(f'全部撤单失败: {res.text}')

    async def get_orders(self) -> dict[str, Order]:
        res = await self.go(
            'GET',
            f'/api/v4/futures/usdt/orders',
            query={'status': 'open'},
        )
        res = res.json()

        orders = {}
        for data in res:
            id = str(data['id'])
            symbol = data['contract'].replace('_', '')
            side = Side.BUY if data['size'] > 0 else Side.SELL
            amount = abs(float(data['size']))
            deal_amount = amount - float(data['left'])
            tside = TradeSide.OPEN if data['is_close'] else TradeSide.CLOSE
            if data['status'] == 'open' or data['finish_as'] == '_new':
                status = OrderStatus.NEW
            elif data['finish_as'] in [
                    'cancelled',
                    'liquidated',
                    'reduce_only',
                    'position_close',
                    'stp',
                    'reduce_out',
            ]:
                status = OrderStatus.CANCELED
            else:
                status = OrderStatus.FILLED

            orders[id] = Order(
                ex_name=self.__class__.__name__,
                symbol=symbol,
                id=id,
                price=data['price'],
                amount=amount,
                deal_price=data['fill_price'],
                deal_amount=deal_amount,
                status=status,
                side=side,
                trade_side=tside,
                c_time=int(data['create_time'] * 1000),
            )

        return orders

    async def get_positions(self) -> dict[str, Position]:
        res = await self.go(
            'GET',
            f'/api/v4/futures/usdt/positions',
            query={'holding': True},
        )
        res = res.json()

        positions = {}
        for data in res:
            symbol = data['contract'].replace('_', '')
            price = data['entry_price']
            amount = abs(data['size'])
            side = Side.BUY if data['size'] > 0 else Side.SELL
            id = symbol + str(side)

            # 平仓
            if amount == 0 and id in self.pos:
                del self.pos[id]
                continue

            positions[id] = Position(
                symbol=symbol,
                id=id,
                side=side,
                price=price,
                amount=amount,
            )
        return positions

    async def set_leverage(
        self,
        symbol: str = '',
        leverage: int = 20,
    ) -> str | None:
        ex_symbol = symbol.replace(settings.quote, '_' + settings.quote)
        res = await self.go(
            'POST',
            f'/api/v4/futures/usdt/positions/{ex_symbol}/leverage',
            query={
                'leverage': 0,  # 0就是全仓保证金
                'cross_leverage_limit': leverage,
            },
        )
        if res.status_code > 299:
            return res.text

    async def set_margin_mode(self, symbol: str = ''):
        """在设置杠杆里已经实现了"""
        pass

    async def set_position_mode(self, symbol: str = ''):
        if self.account.in_dual_mode:
            self.log.info('已设置双向持仓,无需重复设置')
            return

        res = await self.go(
            'POST',
            '/api/v4/futures/usdt/dual_mode',
            query={
                'dual_mode': True,
            },
        )
        if res.status_code == 200:
            self.log.info('设置双向持仓成功')
        else:
            self.log.info(f'设置双向持仓失败: {res.text}')

    async def update_balance(self):
        res = await self.go(
            'GET',
            '/api/v4/futures/usdt/accounts',
        )
        res = res.json()

        self.account.user_id = str(res['user'])
        self.account.in_dual_mode = res['in_dual_mode']
        self.account.swap_balance = float(res['total'])
        self.account.swap_available = float(res['available'])


if __name__ == '__main__':

    async def on_bbo(bbo: BBO):
        print(f'买一:{bbo.bid}|{bbo.bid_amount} 卖一:{bbo.ask}|{bbo.ask_amount}')

    async def on_order(order):
        pass

    s = Secret(
        key=settings.slave.key,
        secret=settings.slave.secret,
        api_key=settings.slave.api_key,
        private_key=settings.slave.private_key,
        public_key=settings.slave.public_key,
    )
    ex = Gate(s)
    ex.listen_bbo(on_bbo)
    ex.listen_order(on_order)

    # async def test():
    #     # print(await ex.get_rules())
    #     await asyncio.create_task(ex.listen_public('ARPAUSDT'))
    # asyncio.run(test())

    async def run():
        await asyncio.sleep(3)
        now1 = timex.time_ms()
        id, msg = await ex.create_order(
            'OPUSDT',
            Side.BUY,
            TradeSide.CLOSE,
            OrderType.MARKET,
            2,
            1,
        )
        now2 = timex.time_ms()
        print(f'落个烂hi单 id:{id} msg:{msg} 延迟:{now2 - now1}')
    async def main():
        asyncio.create_task(run())
        await asyncio.create_task(ex.listen_ws_api(5))
    asyncio.run(main())
