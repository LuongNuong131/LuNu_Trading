import pandas as pd

def calculate_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def get_technical_context(df: pd.DataFrame) -> dict:
    """Hệ thống lõi phân tích Động lượng & Biến động"""
    if len(df) < 35:
        return {}
        
    try:
        # 1. Tự tính RSI (14)
        df['RSI_14'] = calculate_rsi(df['close'], period=14)
        rsi_val = df['RSI_14'].iloc[-1]
        if pd.isna(rsi_val): rsi_val = 50.0 
        
        # 2. Tự tính MACD (12, 26, 9)
        ema_12 = df['close'].ewm(span=12, adjust=False).mean()
        ema_26 = df['close'].ewm(span=26, adjust=False).mean()
        macd = ema_12 - ema_26
        signal = macd.ewm(span=9, adjust=False).mean()
        macd_hist = (macd - signal).iloc[-1] # Lấy mốc Histogram cuối
        
        # 3. Tự tính ATR (14) - Đo lường biến động
        high_low = df['high'] - df['low']
        high_close = (df['high'] - df['close'].shift()).abs()
        low_close = (df['low'] - df['close'].shift()).abs()
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        atr_val = tr.rolling(14).mean().iloc[-1]
            
        # 4. SMC: Quét Thanh Khoản
        recent_low = df['low'].rolling(window=10).min().iloc[-2] 
        current_low = df['low'].iloc[-1]
        current_close = df['close'].iloc[-1]
        liq_sweep_bullish = (current_low < recent_low) and (current_close > recent_low)
        
        return {
            "rsi": round(rsi_val, 2),
            "macd_hist": round(macd_hist, 2),
            "atr": round(atr_val, 2),
            "liquidity_sweep_bullish": liq_sweep_bullish,
            "current_price": current_close
        }
    except Exception as e:
        print(f"[FactorZoo] Lỗi tính toán kỹ thuật: {e}")
        return {}