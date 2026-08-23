import asyncio
from collections import defaultdict

class Event:
    """Lớp chứa dữ liệu cho mọi sự kiện (TICK, BAR, ORDER, AI_SIGNAL)"""
    def __init__(self, type_: str, data: dict = None):
        self.type = type_
        self.data = data or {}

class EventEngine:
    def __init__(self):
        self._handlers = defaultdict(list)
        self._queue = asyncio.Queue()
        self._active = False

    def register(self, event_type: str, handler: callable):
        """Đăng ký một hàm (async) lắng nghe sự kiện"""
        if handler not in self._handlers[event_type]:
            self._handlers[event_type].append(handler)
            print(f"[EventEngine] Đã đăng ký {handler.__name__} cho sự kiện {event_type}")

    def unregister(self, event_type: str, handler: callable):
        """Hủy đăng ký lắng nghe sự kiện"""
        if handler in self._handlers[event_type]:
            self._handlers[event_type].remove(handler)

    def put(self, event: Event):
        """Bắn sự kiện vào hàng đợi (Queue)"""
        self._queue.put_nowait(event)

    async def _run(self):
        """Vòng lặp vĩnh cửu xử lý sự kiện siêu tốc"""
        self._active = True
        print("[EventEngine] Động cơ sự kiện đã khởi chạy...")
        while self._active:
            event = await self._queue.get()
            handlers = self._handlers.get(event.type, [])
            
            # Kích hoạt tất cả các Agent/Handler liên quan chạy song song (Concurrent)
            for handler in handlers:
                asyncio.create_task(handler(event))
                
            self._queue.task_done()

    def start(self):
        """Khởi động Động cơ Sự kiện"""
        asyncio.create_task(self._run())

    def stop(self):
        """Dừng Động cơ"""
        self._active = False
        print("[EventEngine] Đã dừng động cơ.")

# Khởi tạo Singleton
event_engine = EventEngine()