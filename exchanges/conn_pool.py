import asyncio
import logging
from typing import Callable

from exchanges.ws import WS


class ConnPool:

    def __init__(
        self,
        log: logging.Logger,
        new_ws: Callable[[], WS],
    ):
        self.log = log
        self.new_ws = new_ws
        self.wss: list[WS] = []
        self.next_conn = 0
        
    async def run(self, count: int, sleep: int = 0):
        futures = []
        for i in range(count):
            ws = self.new_ws()
            self.wss.append(ws)
            futures.append(self._conn(ws, i * sleep))
        await asyncio.gather(*futures)

    async def _conn(self, ws: WS, sleep: int = 0):
        if sleep:
            await asyncio.sleep(sleep)
        await ws.loop_conn()
        
    async def send(self, msg: dict, id: str = '') -> tuple[dict, bool]:
        length = len(self.wss)
        try_count = 0
        ws: WS | None = None
        
        while try_count < length:
            try_count += 1
            
            ws = self.wss[self.next_conn % length]
            self.next_conn = (self.next_conn + 1) % length
            if ws.ok():
                break
        
        if not ws:
            self.log.error('没有能用的wsapi')
            return '', False
        
        return await ws.send(msg, id)
    
    async def close_all(self):
        for ws in self.wss:
            await ws.close()
        self.wss.clear()