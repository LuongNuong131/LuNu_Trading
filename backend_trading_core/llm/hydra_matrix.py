import asyncio
import os
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()

class HydraMatrix:
    def __init__(self):
        keys_env = os.getenv("GEMINI_KEYS", "")
        self.api_keys = [k.strip() for k in keys_env.split(",") if k.strip() and k.strip() != "AIzaSy_Key1_xxx"]
        
        if not self.api_keys:
            print("[HydraMatrix CẢNH BÁO] Chưa nạp API Key Gemini thật vào file .env!")
            self.api_keys = ["DUMMY_KEY"]
            
        self.key_status = {key: "ACTIVE" for key in self.api_keys}
        self.cooling_time = 65  
        self.current_index = 0
        self.lock = asyncio.Lock()

    async def _get_next_key(self) -> str:
        async with self.lock:
            for _ in range(len(self.api_keys)):
                key = self.api_keys[self.current_index]
                self.current_index = (self.current_index + 1) % len(self.api_keys)
                if self.key_status[key] == "ACTIVE":
                    return key
            raise Exception("ALL_KEYS_COOLING")

    async def _cool_down_key(self, key: str):
        self.key_status[key] = "COOLING"
        print(f"[HydraMatrix] ❄️ Key {key[:6]}... bị Rate Limit. Bắt buộc nghỉ mát {self.cooling_time}s.")
        await asyncio.sleep(self.cooling_time)
        self.key_status[key] = "ACTIVE"
        print(f"[HydraMatrix] 🔥 Key {key[:6]}... đã HỒI SINH!")

    def _call_gemini_sync(self, key: str, prompt: str, system_instruction: str = None) -> str:
        client = genai.Client(api_key=key)
        config = None
        if system_instruction:
            config = types.GenerateContentConfig(system_instruction=system_instruction)
        
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
            config=config
        )
        return response.text

    async def generate_response(self, prompt: str, system_instruction: str = None) -> str:
        if self.api_keys[0] == "DUMMY_KEY":
            return "DUMMY_RESPONSE: Vui lòng nhập API key thật."

        max_retries = 3
        
        for attempt in range(max_retries):
            key = None 
            try:
                key = await self._get_next_key()
                text = await asyncio.to_thread(
                    self._call_gemini_sync,
                    key,
                    prompt,
                    system_instruction
                )
                return text
                
            except Exception as e:
                error_msg = str(e).lower()
                
                # Bắt lỗi tất cả key đang nghỉ mát
                if "all_keys_cooling" in error_msg:
                    print("[HydraMatrix] ⏳ Đang chờ Key phục hồi. Bỏ qua nhịp này...")
                    return "HOLD - Đang kẹt Rate Limit."
                
                # Bắt lỗi quá tải từ Google
                if "429" in error_msg or "quota" in error_msg or "rate limit" in error_msg or "exhausted" in error_msg:
                    if key:
                        asyncio.create_task(self._cool_down_key(key))
                else:
                    safe_key = key[:6] if key else "Unknown"
                    print(f"[HydraMatrix] Lỗi với key {safe_key}...: {e}")
                    
        return "HOLD - Đứt kết nối AI."

    async def debate_concurrently(self, agents_prompts: dict) -> dict:
        """
        Bắn đa luồng có chèn nhịp nghỉ (Staggered Concurrency)
        Giúp Google không hiểu lầm mình đang spam DDoS.
        """
        tasks = []
        agent_names = list(agents_prompts.keys())
        
        for i, (name, prompt) in enumerate(agents_prompts.items()):
            # Thằng số 1 bắn ngay, thằng số 2 chờ 1.5s, thằng số 3 chờ 3.0s...
            delay = i * 1.5 
            
            async def delayed_generate(p, d):
                if d > 0:
                    await asyncio.sleep(d)
                return await self.generate_response(p)
                
            tasks.append(delayed_generate(prompt, delay))
            
        print(f"[HydraMatrix] 🚀 Kích hoạt phân tích (giãn cách nhịp): {', '.join(agent_names)}")
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        return dict(zip(agent_names, results))

hydra_matrix = HydraMatrix()