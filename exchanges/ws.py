import asyncio
import json
import traceback
from typing import Awaitable, Callable, List

import websockets
from websockets.client import WebSocketClientProtocol

from tool import logger


class WS:

    def __init__(
        self,
        uri: str,
        name: str,
        symbol: str = '',
        on_conn: Callable[
            [WebSocketClientProtocol, str],
            Awaitable[List[asyncio.Task]],
        ] | None = None,
        on_msg: Callable[
            [WebSocketClientProtocol, str, websockets.Data],
            Awaitable[tuple[dict, str | None]],
        ] | None = None,
        send_timeout: int = 5,
    ):
        self.uri = uri
        self.name = name
        self.symbol = symbol
        self.on_conn = on_conn
        self.on_msg = on_msg
        self.send_timeout = send_timeout

        self.log = logger.get_logger(name)
        self.ws: WebSocketClientProtocol = None
        self.response_futures: dict[str, asyncio.Future] = {}

    async def loop_conn(self):
        while 1:
            try:
                await self.conn()
                self.ws = None
            except websockets.InvalidHandshake as e:
                self.log.error(f"连接失败(无效握手) {e} 开始重连...")
            except asyncio.exceptions.TimeoutError as e:
                self.log.error(f"连接失败(连接超时) 开始重连...")
            except websockets.ConnectionClosed as e:
                self.log.error(f"连接已关闭 {e} 开始重连...")
            except Exception as e:
                self.log.error(f'连接失败(未知错误) 开始重连...')
                traceback.print_exc()
            await asyncio.sleep(0.2)

    async def conn(self):
        if not self.ok():
            self.ws = await websockets.connect(self.uri, ping_interval=None)
            # self.log.info('连上ws')

            tasks: list[asyncio.Task] = []
            if self.on_conn:
                tasks = await self.on_conn(self.ws, self.symbol)

            try:
                while self.ok():
                    res = await self.ws.recv()
                    if self.on_msg:
                        data, id = await self.on_msg(self.ws, self.symbol, res)
                        if id and id in self.response_futures:
                            self.response_futures[id].set_result(data)
            except Exception:
                raise
            finally:
                for task in tasks:
                    task.cancel()
                    
    async def close(self):
        await self.ws.close()

    def ok(self) -> bool:
        return self.ws is not None and not self.ws.closed

    async def send(self, data: dict, id: str = '') -> tuple[dict, bool]:
        if not self.ok():
            self.log.error(f'ws还没准备好就发送消息: {data}')
            return None, False

        fut: asyncio.Future = None
        if id:
            loop = asyncio.get_running_loop()
            fut = loop.create_future()
            self.response_futures[id] = fut

        await self.ws.send(json.dumps(data))

        if fut and id:
            result = await asyncio.wait_for(fut, self.send_timeout)
            del self.response_futures[id]
            return result, True

        return None, True
