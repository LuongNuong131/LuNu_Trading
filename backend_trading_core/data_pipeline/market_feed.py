import ccxt.async_support as ccxt
import asyncio
import pandas as pd
from core.event_engine import event_engine, Event

class MarketFeed:
    def __init__(self):
        self.exchange = ccxt.binance({'enableRateLimit': True})
        self.active = False

    async def fetch_ohlcv_loop(self, symbol="BTC/USDT"):
        self.active = True
        print(f"[MarketFeed] Mắt thần Radar Đa Khung (1m, 15m) đang quét {symbol}...")
        
        while self.active:
            try:
                # Kéo song song nến 1m và 15m
                tasks = [
                    self.exchange.fetch_ohlcv(symbol, '1m', limit=100),
                    self.exchange.fetch_ohlcv(symbol, '15m', limit=20)
                ]
                results = await asyncio.gather(*tasks)
                
                df_1m = pd.DataFrame(results[0], columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
                df_15m = pd.DataFrame(results[1], columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
                
                event = Event("NEW_BAR", data={
                    "symbol": symbol,
                    "df_1m": df_1m,
                    "df_15m": df_15m
                })
                event_engine.put(event)
                
            except Exception as e:
                print(f"[MarketFeed] Lỗi mạng Binance: {e}")
            
            await asyncio.sleep(30) # 30s quét 1 lần

    def start(self):
        asyncio.create_task(self.fetch_ohlcv_loop())
        
    async def stop(self):
        self.active = False
        await self.exchange.close()

market_feed = MarketFeed()