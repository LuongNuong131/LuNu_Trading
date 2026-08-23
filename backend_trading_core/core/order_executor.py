from db.duckdb_client import db_client
import uuid

class PaperExecutor:
    def __init__(self):
        self.positions = {}
        self.capital = 10000.0  # Két sắt khởi điểm 10,000$
        
    def update_price(self, symbol: str, current_price: float):
        """Mắt thần canh chừng SL/TP liên tục cho lệnh đang mở"""
        if symbol not in self.positions:
            return
            
        pos = self.positions[symbol]
        p_type = pos['type']
        entry = pos['price']
        sl = pos['sl']
        tp = pos['tp']
        amount = pos['amount']
        
        closed = False
        reason = ""
        
        # Logic check chạm SL hoặc TP
        if p_type == "BUY":
            if current_price <= sl: closed, reason = True, "Chạm SL 🔴"
            elif current_price >= tp: closed, reason = True, "Chạm TP 🟢"
        else: # SELL
            if current_price >= sl: closed, reason = True, "Chạm SL 🔴"
            elif current_price <= tp: closed, reason = True, "Chạm TP 🟢"
                
        if closed:
            profit_usd = (current_price - entry) / entry * (entry * amount) if p_type == "BUY" else (entry - current_price) / entry * (entry * amount)
            self.capital += profit_usd
            print(f"\n[KÉT SẮT] ⚡ TỰ ĐỘNG ĐÓNG {p_type} do {reason}! PnL: {profit_usd:.2f}$ | VỐN MỚI: {self.capital:.2f}$")
            
            db_client.insert_trade(str(uuid.uuid4())[:8], symbol, f"CLOSE_{p_type}", current_price, amount, reason)
            del self.positions[symbol]
            
    def execute(self, symbol: str, signal: str, price: float, atr: float):
        if signal not in ["BUY", "SELL"]: return
            
        if symbol in self.positions and self.positions[symbol]['type'] == signal:
            return # Tránh nhồi lệnh

        # Chốt lệnh cũ nếu có tín hiệu đảo chiều
        if symbol in self.positions:
            old_type = self.positions[symbol]['type']
            entry = self.positions[symbol]['price']
            amount = self.positions[symbol]['amount']
            profit_usd = (price - entry) / entry * (entry * amount) if old_type == "BUY" else (entry - price) / entry * (entry * amount)
            self.capital += profit_usd
            icon = "🟢" if profit_usd > 0 else "🔴"
            print(f"\n[KÉT SẮT] {icon} ĐẢO CHIỀU CẮT {old_type}! PnL: {profit_usd:.2f}$ | VỐN MỚI: {self.capital:.2f}$")
            del self.positions[symbol]

        # QUẢN TRỊ VỐN: Chỉ rủi ro 1% tài khoản
        risk_amount = self.capital * 0.01 
        sl_dist = atr * 1.5 if atr > 0 else price * 0.005
        
        # Tính Volume: Đi bao nhiêu coin để nếu dính SL chỉ mất đúng 1% vốn
        amount = risk_amount / sl_dist
        usd_size = amount * price
        
        sl = price - sl_dist if signal == "BUY" else price + sl_dist
        tp = price + (atr * 3.0) if signal == "BUY" else price - (atr * 3.0)
        
        order_id = str(uuid.uuid4())[:8].upper()
        self.positions[symbol] = {"type": signal, "price": price, "amount": amount, "sl": sl, "tp": tp}
        
        print(f"💰 [EXECUTOR] >> VÀO {signal} {symbol} | Vol: {usd_size:.2f}$ | SL: {sl:.1f} | TP: {tp:.1f} (Risk 1%)")
        db_client.insert_trade(order_id, symbol, signal, price, amount, "FILLED")

executor = PaperExecutor()