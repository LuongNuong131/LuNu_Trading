import asyncio
import uvicorn
from core.event_engine import event_engine, Event
from db.duckdb_client import db_client
from llm.hydra_matrix import hydra_matrix
from data_pipeline.market_feed import market_feed
from factor_zoo.technical import get_technical_context
from core.order_executor import executor

async def handle_new_bar(event: Event):
    data = event.data
    df_1m = data['df_1m']
    df_15m = data['df_15m']
    symbol = data['symbol']
    
    price = df_1m['close'].iloc[-1]
    trend_15m = "TĂNG" if price > df_15m['open'].iloc[-1] else "GIẢM"
    
    # 0. Mắt thần kiểm tra xem có cắn SL/TP chưa
    executor.update_price(symbol, price)
    
    # 1. Kỹ thuật SMC & ATR
    tech_data = get_technical_context(df_1m)
    atr = tech_data.get('atr', 0)
    
    print(f"\n[Core] {symbol} | Giá: {price}$ | RSI: {tech_data.get('rsi')} | MACD: {tech_data.get('macd_hist')} | ATR: {atr}$")
    
    tech_str = f"RSI: {tech_data.get('rsi')}. Giá: {price}. Lực MACD: {tech_data.get('macd_hist')}. ATR: {atr}$. Trend 15m đang {trend_15m}."
    if tech_data.get('liquidity_sweep_bullish'): tech_str += " Vừa quét thanh khoản đáy."
        
    prompts = {
        "Bull_Analyst": f"Data: {tech_str}. Hãy đưa ra 1 lý do cực ngắn thuyết phục MUA.",
        "Bear_Analyst": f"Data: {tech_str}. Hãy bẻ lại phe Bò, đưa ra 1 lý do cực ngắn thuyết phục BÁN."
    }
    
    results = await hydra_matrix.debate_concurrently(prompts)
    bull_res = results.get("Bull_Analyst", "HOLD")
    bear_res = results.get("Bear_Analyst", "HOLD")
    
    db_client.insert_ai_log("Bull_Analyst", symbol, "BUY", str(bull_res))
    db_client.insert_ai_log("Bear_Analyst", symbol, "SELL", str(bear_res))

    if "HOLD" in bull_res or "HOLD" in bear_res:
        final_decision = "HOLD"
    else:
        risk_prompt = f"Bò: {bull_res}\nGấu: {bear_res}\nData: {tech_str}\nLà Giám đốc Rủi ro, quyết định là gì? (BUY/SELL/HOLD)"
        final_decision = await hydra_matrix.generate_response(risk_prompt, "Bạn là AI tàn nhẫn. Trả lời đúng 1 từ.")
        final_decision = final_decision.strip().upper()
        if "BUY" in final_decision: final_decision = "BUY"
        elif "SELL" in final_decision: final_decision = "SELL"
        else: final_decision = "HOLD"

    db_client.insert_ai_log("Risk_Manager", symbol, final_decision, f"Dựa trên Trend 15m và ATR={atr}$")
    
    if final_decision != "HOLD":
        print(f"⚖️ [Giám đốc Rủi ro] Phán quyết: {final_decision}")
        executor.execute(symbol, final_decision, price, atr)

async def main():
    print("=== KHỞI ĐỘNG OMNI-QUANT (PHASE 5: RISK & STATS) ===")
    event_engine.register("NEW_BAR", handle_new_bar)
    event_engine.start()
    market_feed.start()
    
    config = uvicorn.Config("api.fastapi_gateway:app", host="127.0.0.1", port=8000, log_level="error")
    server = uvicorn.Server(config)
    await server.serve()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        event_engine.stop()
        asyncio.run(market_feed.stop())