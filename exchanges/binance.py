import asyncio
import base64
import hashlib
import hmac
import json
import time
from urllib.parse import urlencode
import uuid
from cryptography.hazmat.primitives.serialization import load_pem_private_key
from cryptography.hazmat.backends import default_backend

import requests
from websockets import WebSocketClientProtocol
from exchanges.conn_pool import ConnPool
from exchanges.exchange import Exchange
from exchanges.ws import WS
from models.enums import *
from models.models import *
from tool import timex
from config import settings

BASE_REST = 'https://fapi.binance.com'
BASE_WS = 'wss://fstream.binance.com'
BASE_WS_API = 'wss://ws-fapi.binance.com/ws-fapi/v1'


def hmac_hashing(secret: str, payload: str):
    m = hmac.new(
        secret.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    )
    return m.hexdigest()


class Binance(Exchange):

    def __init__(self, secret: Secret):
        super().__init__(secret)
        self.req = requests.Session()
        self.wss: dict[str, WS] = {}

        self.private_key = load_pem_private_key(
            data=self.secret.private_key.encode('ASCII'),
            password=None,
            backend=default_backend(),
        )

    async def listen_public(self, symbol: str = ''):
        if symbol:
            name = f'{self.__class__.__name__} {symbol}'
            url = f'{BASE_WS}/ws/{symbol.lower()}@bookTicker'
            ws = WS(
                uri=url,
                name=name,
                symbol=symbol,
                on_msg=self.pub_msg,
            )
            self.wss[symbol] = ws
            await ws.loop_conn()

    async def listen_private(self):
        name = f'{self.__class__.__name__} 私有连接'
        key = await self.gen_listen_key()
        url = f'{BASE_WS}/ws/{key}'
        ws = WS(
            uri=url,
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
                uri=BASE_WS_API,
                name=name,
                on_conn=self.wsapi_conn,
                on_msg=self.wsapi_msg,
            )

        self.ws_api_pool = ConnPool(self.log, new_ws)
        await self.ws_api_pool.run(count, 0.5)

    async def pub_msg(
        self,
        conn: WebSocketClientProtocol,
        symbol: str,
        msg: str,
    ):
        """公共ws消息事件"""
        msg = json.loads(msg)
        bbo = BBO(msg['s'], msg['b'], msg['B'], msg['a'], msg['A'], msg['T'])
        self.bbos[symbol] = bbo
        await self.emit_bbo(bbo)

        return msg, ''

    async def pri_conn(
        self,
        conn: WebSocketClientProtocol,
        symbol: str,
    ) -> list[asyncio.Task]:
        """私有ws连接事件"""

        # 重连的时候防止丢数据
        self.orders = await self.get_orders()
        self.pos = await self.get_positions()

        async def listen_key():
            while 1:
                await asyncio.sleep(55 * 60)
                await self.prolong_listen_key()

        return [asyncio.create_task(listen_key())]

    async def pri_msg(
        self,
        conn: WebSocketClientProtocol,
        symbol: str,
        msg: str,
    ):
        """私有ws消息事件"""
        msg = json.loads(msg)
        if msg['e'] == 'ACCOUNT_UPDATE':
            await self.handle_account(msg)
            await self.handle_pos(msg)
        elif msg['e'] == 'ORDER_TRADE_UPDATE':
            await self.handle_order(msg)

        return msg, ''

    def wsapi_sign(self, now: int, params: dict) -> str:
        params['apiKey'] = self.secret.api_key
        params['timestamp'] = now
        payload = '&'.join([f'{k}={v}' for k, v in sorted(params.items())])
        sign = base64.b64encode(self.private_key.sign(payload.encode('ASCII')))
        return sign.decode('ASCII')

    async def wsapi_conn(
        self,
        conn: WebSocketClientProtocol,
        symbol: str,
    ) -> list[asyncio.Task]:
        """wsapi连接事件"""
        now = timex.time_ms()

        msg_id = uuid.uuid4().hex
        params = {}
        params['signature'] = self.wsapi_sign(now, params)
        req = {"id": msg_id, "method": "session.logon", "params": params}
        await self.ws_api_pool.send(req)

        return []

    async def wsapi_msg(
        self,
        conn: WebSocketClientProtocol,
        symbol: str,
        msg: str,
    ):
        """wsapi消息事件"""
        msg = json.loads(msg)

        if 'id' in msg:
            return msg, msg['id']
        return msg, ''

    async def handle_account(self, msg: dict):
        """更新账户信息"""
        for data in msg['a']['B']:
            if data['a'] == settings.quote:
                self.account.swap_balance = float(data['wb'])
                self.account.swap_available = float(data['cw'])
                self.log.info(f'可用余额:{self.account.swap_available}')

    async def handle_pos(self, msg: dict):
        """更新仓位"""
        for data in msg['a']['P']:
            symbol = data['s']
            price = data['ep']
            amount = abs(float(data['pa']))
            side = Side.BUY if data['ps'] == 'LONG' else Side.SELL
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

    async def handle_order(self, msg: dict):
        """更新订单"""
        data = msg['o']
        id = data['i']

        if data['X'] == 'PARTIALLY_FILLED':
            status = OrderStatus.PARTIALLY_FILLED
        elif data['X'] == 'FILLED':
            status = OrderStatus.FILLED
        elif data['X'] == 'CANCELED':
            status = OrderStatus.CANCELED
        elif data['X'] == 'REJECTED':
            status = OrderStatus.CANCELED
        elif data['X'] == 'EXPIRED':
            status = OrderStatus.CANCELED
        else:
            status = OrderStatus.NEW

        side = Side(data['S'])
        if side == Side.BUY:
            tside = TradeSide.OPEN if data['ps'] == 'LONG' else TradeSide.CLOSE
        else:
            tside = TradeSide.OPEN if data['ps'] == 'SHORT' else TradeSide.CLOSE

        order = Order(
            ex_name=self.__class__.__name__,
            symbol=data['s'],
            id=id,
            price=data['p'],
            amount=data['q'],
            deal_price=data['ap'],
            deal_amount=data['z'],
            status=status,
            side=side,
            trade_side=tside,
            c_time=data['T'],
        )

        # 维护本地订单
        if status in self.done_staus and id in self.orders:
            del self.orders[id]
        else:
            self.orders[id] = order

        await self.emit_order(order)

        if len(self.orders) > 500:
            self.orders = dict(list(self.orders.items())[-100:])

    async def init(self, symbols: list[str]):
        await self.set_margin_mode()
        await self.set_position_mode()

    def sign(self, now: int, payload: dict = {}) -> tuple[str, str]:
        query_string = urlencode(payload).replace('%27', '%22')
        if query_string:
            q = f'{query_string}&timestamp={now}'
        else:
            q = f'timestamp={now}'
        secret = self.secret.secret
        return q, hmac_hashing(secret, q)

    async def go(self, method: str, path: str, payload: dict = {}):
        now = timex.time_ms()
        url = BASE_REST + path
        q, signature = self.sign(now, payload)
        url = f'{url}?{q}&signature={signature}'

        headers = {
            'X-MBX-APIKEY': self.secret.key,
        }
        res = self.req.request(
            method=method,
            url=url,
            headers=headers,
        )

        return res.json()

    async def gen_listen_key(self) -> str:
        """生成ws身份认证"""
        res = await self.go('POST', '/fapi/v1/listenKey')
        return res['listenKey']

    async def prolong_listen_key(self) -> str:
        """延长ws身份认证"""
        res = await self.go('PUT', '/fapi/v1/listenKey')
        return res['listenKey']

    async def get_rules(self) -> dict[str, ContractRule]:
        """获取交易规则"""

        res = await self.go('GET', '/fapi/v1/leverageBracket')
        leverage_dict = {}
        for data in res:
            leverage = data['brackets'][0]['initialLeverage']
            leverage_dict[data['symbol']] = leverage

        res = await self.go('GET', '/fapi/v1/exchangeInfo')
        rules = {}
        for data in res['symbols']:
            symbol: str = data['symbol']
            if not symbol.endswith(settings.quote):
                continue

            leverage = leverage_dict[symbol]
            rules[symbol] = ContractRule(
                symbol=symbol,
                price_prec=data['pricePrecision'],
                amount_prec=data['quantityPrecision'],
                max_amount=data['filters'][1]['maxQty'],
                min_amount=data['filters'][1]['minQty'],
                max_leverage=leverage,
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
        now = timex.time_ms()

        args = {
            'symbol': symbol,
            'side': str(side),
            'positionSide': 'LONG' if side == Side.BUY else 'SHORT',
            'quantity': amount,
            'timestamp': now,
        }

        if trade_side == TradeSide.CLOSE:
            args['side'] = 'SELL' if side == Side.BUY else 'BUY'

        if type == OrderType.MARKET:
            args['type'] = 'MARKET'
        else:
            args['type'] = 'LIMIT'
            args['price'] = str(price)
            args['timeInForce'] = str(type)

        msg_id = uuid.uuid4().hex
        req = {
            'id': msg_id,
            'method': 'order.place',
            'params': args,
        }
        res, ok = await self.ws_api_pool.send(req, msg_id)
        if not ok:
            return '', 'ws未连接'
        if 'result' not in res or 'orderId' not in res['result']:
            return '', res

        return str(res['result']['orderId']), ''

    async def cancel_order(self, id: str, symbol: str = ''):
        res = await self.go('DELETE', '/fapi/v1/order', {
            'symbol': symbol,
            'orderId': id,
        })
        self.log.info(f'撤销订单 {id} 成功')

    async def cancel_all_order(self, symbol: str = ''):
        res = await self.go('DELETE', '/fapi/v1/allOpenOrders', {
            'symbol': symbol,
        })
        if res['code'] == 200:
            self.log.info(f'撤销全部订单成功')
        else:
            self.log.info(f'撤销全部订单失败: {res}')

    async def get_orders(self) -> dict[str, Order]:
        res = await self.go('GET', '/fapi/v1/openOrders')
        orders = {}
        for data in res:
            if data['status'] == 'PARTIALLY_FILLED':
                status = OrderStatus.PARTIALLY_FILLED
            elif data['status'] == 'FILLED':
                status = OrderStatus.FILLED
            elif data['status'] == 'CANCELED':
                status = OrderStatus.CANCELED
            elif data['status'] == 'REJECTED':
                status = OrderStatus.CANCELED
            elif data['status'] == 'EXPIRED':
                status = OrderStatus.CANCELED
            else:
                status = OrderStatus.NEW

            side = Side(data['side'])

            if side == Side.BUY:
                tside = TradeSide.OPEN if data[
                    'positionSide'] == 'LONG' else TradeSide.CLOSE
            else:
                tside = TradeSide.OPEN if data[
                    'positionSide'] == 'SHORT' else TradeSide.CLOSE

            id = data['orderId']

            orders[id] = Order(
                ex_name=self.__class__.__name__,
                symbol=data['symbol'],
                id=id,
                price=data['price'],
                amount=data['origQty'],
                deal_price=data['avgPrice'],
                deal_amount=data['executedQty'],
                status=status,
                side=side,
                trade_side=tside,
                c_time=data['time'],
            )
        return orders

    async def get_positions(self) -> dict[str, Position]:
        res = await self.go('GET', '/fapi/v3/positionRisk')
        positions = {}
        for data in res:
            symbol = data['symbol']
            amount = float(data['positionAmt'])
            side = Side.BUY if amount > 0 else Side.SELL
            id = symbol + str(side)

            positions[id] = Position(
                symbol=symbol,
                id=id,
                side=side,
                price=data['entryPrice'],
                amount=abs(amount),
                c_time=data['updateTime'],
            )
        return positions

    async def set_leverage(self,
                           symbol: str = '',
                           leverage: int = 20) -> str | None:
        res = await self.go('POST', '/fapi/v1/leverage', {
            'symbol': symbol,
            'leverage': leverage,
        })
        if 'maxNotionalValue' not in res:
            return str(res)

    async def set_margin_mode(self, symbol: str = ''):
        res = await self.go('GET', '/fapi/v1/multiAssetsMargin')
        if res['multiAssetsMargin']:
            self.log.info('已设置全仓保证金,无需重复设置')
            return

        res = await self.go('POST', '/fapi/v1/multiAssetsMargin', {
            'multiAssetsMargin': True,
        })
        if res['code'] == 200:
            self.log.info('设置全仓保证金成功')
        else:
            self.log.info(f'设置全仓保证金失败: {res}')

    async def set_position_mode(self, symbol: str = ''):
        res = await self.go('GET', '/fapi/v1/positionSide/dual')
        if res['dualSidePosition']:
            self.log.info('已设置双向持仓,无需重复设置')
            return

        res = await self.go('POST', '/fapi/v1/positionSide/dual', {
            'dualSidePosition': True,
        })
        if res['code'] == 200:
            self.log.info('设置双向持仓成功')
        else:
            self.log.info(f'设置双向持仓失败: {res}')

    async def update_balance(self):
        res = await self.go('GET', '/fapi/v3/balance')
        for data in res:
            if data['asset'] == settings.quote:
                self.account = Account(
                    swap_balance=data['balance'],
                    swap_available=data['availableBalance'],
                )


if __name__ == '__main__':

    def on_bbo(bbo: BBO):
        print(f'买一:{bbo.bid} 卖一:{bbo.ask}')

    def on_order(order):
        pass

    s = Secret(
        key=settings.master.key,
        secret=settings.master.secret,
        api_key=settings.master.api_key,
        private_key=settings.master.private_key,
        public_key=settings.master.public_key,
    )
    ex = Binance(s)
    ex.listen_bbo(on_bbo)
    ex.listen_order(on_order)

    # async def test():
    #     print(await ex.get_rules())
    # asyncio.run(test())

    # async def main():
    #     await asyncio.create_task(ex.listen_private())
    # asyncio.run(main())
    async def run():
        await asyncio.sleep(3)
        now1 = timex.time_ms()
        id, msg = await ex.create_order(
            'OPUSDT',
            Side.BUY,
            TradeSide.CLOSE,
            OrderType.MARKET,
            3,
            1,
        )
        now2 = timex.time_ms()
        ex.log.info(f'落个烂hi单 id:{id} msg:{msg} 延迟:{now2 - now1}')

    async def main():
        asyncio.create_task(run())
        await asyncio.create_task(ex.listen_ws_api(5))

    asyncio.run(main())
