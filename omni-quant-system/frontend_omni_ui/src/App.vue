<template>
  <div class="quant-dashboard">
    <header class="glass-header">
      <div class="logo">
        <span class="glitch" data-text="OMNI-QUANT">OMNI-QUANT</span>
        <span class="version">Phase 5</span>
      </div>
      
      <!-- BẢNG KÉT SẮT TÀI SẢN -->
      <div class="stats-panel">
        <div class="stat-box">
          <span class="stat-label">VỐN (USD)</span>
          <span class="stat-value" :class="{'profit': capital > 10000, 'loss': capital < 10000}">
            ${{ Number(capital ?? 0).toFixed(2) }}
          </span>
        </div>
        <div class="stat-box">
          <span class="stat-label">LỆNH MỞ</span>
          <span class="stat-value accent">{{ openPositions }}</span>
        </div>
      </div>

      <div class="status-bar">
        <div class="status-item"><span class="dot binance"></span> Binance WS</div>
        <div class="status-item"><span class="dot local"></span> Local: 8000</div>
      </div>
    </header>

    <main class="dashboard-grid">
      <section class="panel chart-panel">
        <div class="panel-header"><h2>BTC/USDT - Radar Đa Khung</h2></div>
        <div id="tv-chart" class="chart-container" ref="chartContainerRef"></div>
      </section>

      <section class="panel ai-panel">
        <div class="panel-header"><h2>🧠 Neural Debate Room</h2><span class="refresh-rate">Ping: 5s</span></div>
        <div class="logs-container">
          <div v-if="errorMessage" class="error-msg">{{ errorMessage }}</div>
                        <div v-for="log in filteredLogs" :key="log.id || `${log.timestamp}-${log.agent_name}-${log.decision}`" class="log-card" :class="getLogClass(log)">

            <div class="log-header">
                              <span class="agent-name">{{ getAgentIcon(log?.agent_name) }} {{ log?.agent_name || 'System' }}</span>

              <span class="timestamp">{{ formatTime(log.timestamp) }}</span>
            </div>
            <div class="log-body">
                              <div class="decision-badge" :class="String(log?.decision || 'HOLD').toLowerCase()">{{ log?.decision || 'HOLD' }}</div>
                <p class="reasoning">"{{ log?.reasoning || 'No reasoning recorded.' }}"</p>

            </div>
          </div>
        </div>
      </section>

      <section class="panel execution-panel">
        <div class="panel-header"><h2>⚖️ Risk Manager</h2></div>
        <div class="execution-container">
          <div v-for="log in riskLogs" :key="log.id || `${log.timestamp}-${log.agent_name}-${log.decision}`" class="risk-card" :class="String(log?.decision || 'HOLD').toLowerCase()">
            <div class="risk-title">LỆNH DUYỆT</div>
            <div class="risk-decision">{{ log?.decision || 'HOLD' }}</div>
            <div class="risk-time">{{ formatTime(log.timestamp) }}</div>
          </div>
        </div>
      </section>
    </main>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue';
import { createChart, CandlestickSeries } from 'lightweight-charts';

const allLogs = ref([]);
const filteredLogs = ref([]);
const riskLogs = ref([]);
const errorMessage = ref("");
const capital = ref(10000.00);
const openPositions = ref(0);
let dataInterval = null;

const chartContainerRef = ref(null);
let chart = null;
let candlestickSeries = null;
let binanceSocket = null;

const initChart = async () => {
  try {
    const w = chartContainerRef.value.clientWidth || 600;
    const h = chartContainerRef.value.clientHeight || 400;
    chart = createChart(chartContainerRef.value, {
      width: w, height: h, autoSize: true, 
      layout: { background: { type: 'solid', color: 'transparent' }, textColor: '#94a3b8' },
      grid: { vertLines: { color: 'rgba(255,255,255,0.05)' }, horzLines: { color: 'rgba(255,255,255,0.05)' } },
      timeScale: { timeVisible: true, secondsVisible: false },
    });

    candlestickSeries = chart.addSeries(CandlestickSeries, {
      upColor: '#10b981', downColor: '#ef4444', borderVisible: false, wickUpColor: '#10b981', wickDownColor: '#ef4444'
    });

    const res = await fetch('https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1m&limit=60');
    const data = await res.json();
    candlestickSeries.setData(data.map(d => ({ time: Math.floor(d[0]/1000), open: parseFloat(d[1]), high: parseFloat(d[2]), low: parseFloat(d[3]), close: parseFloat(d[4]) })));

    binanceSocket = new WebSocket('wss://stream.binance.com:9443/ws/btcusdt@kline_1m');
    binanceSocket.onmessage = (event) => {
      const kline = JSON.parse(event.data).k;
      candlestickSeries.update({ time: Math.floor(kline.t/1000), open: parseFloat(kline.o), high: parseFloat(kline.h), low: parseFloat(kline.l), close: parseFloat(kline.c) });
    };
  } catch (e) { console.error("Lỗi Chart:", e); }
};

const fetchData = async () => {
  try {
    const logRes = await fetch('http://127.0.0.1:8000/api/logs?limit=30');
    const logResult = await logRes.json();
    if (logResult.success) {
      allLogs.value = logResult.data;
      filteredLogs.value = allLogs.value.filter(log => log.agent_name !== 'Risk_Manager');
      riskLogs.value = allLogs.value.filter(log => log.agent_name === 'Risk_Manager');
      errorMessage.value = ""; 
    }
    
    // Kéo số dư Két Sắt
    const statRes = await fetch('http://127.0.0.1:8000/api/stats');
    const statResult = await statRes.json();
    if (statResult.success) {
      const stats = statResult.data || {};
      capital.value = Number(stats.capital ?? 0);
      const positions = Array.isArray(stats.open_positions) ? stats.open_positions : [];
      openPositions.value = positions.length;
    }
  } catch (error) {
    errorMessage.value = "⚠️ Mất kết nối tới Backend 8000.";
  }
};

const formatTime = (isoString) => {
  const parsed = new Date(isoString || Date.now());
  return Number.isNaN(parsed.getTime()) ? '--:--:--' : parsed.toLocaleTimeString('vi-VN');
};
const getAgentIcon = (name = '') => {
  const safeName = String(name || '');
  return safeName.includes('Bull') ? '🐂' : safeName.includes('Bear') ? '🐻' : '🤖';
};
const getLogClass = (log = {}) => {
  const decision = String(log?.decision || 'HOLD');
  return decision === 'BUY' ? 'bull-card' : decision === 'SELL' ? 'bear-card' : 'hold-card';
};

onMounted(() => {
  setTimeout(initChart, 100);
  setTimeout(fetchData, 200);
  dataInterval = setInterval(fetchData, 5000);
});

onUnmounted(() => {
  if (dataInterval) clearInterval(dataInterval);
  if (binanceSocket) binanceSocket.close();
  if (chart) chart.remove();
});
</script>

<style>
/* Kế thừa CSS cũ và thêm Bảng Thống Kê */
:root { --bg-color: #050b14; --glass-bg: rgba(16, 25, 40, 0.6); --glass-border: rgba(56, 189, 248, 0.2); --accent: #38bdf8; --bull: #10b981; --bear: #ef4444; --hold: #fbbf24; --text-main: #f8fafc; --text-sub: #94a3b8; }
body { margin: 0; padding: 0; background-color: var(--bg-color); color: var(--text-main); font-family: 'Consolas', 'Courier New', monospace; height: 100vh; overflow: hidden; }
.quant-dashboard { display: flex; flex-direction: column; height: 100vh; padding: 16px; box-sizing: border-box; gap: 16px; }

.glass-header { display: flex; justify-content: space-between; align-items: center; padding: 0 24px; height: 70px; background: var(--glass-bg); backdrop-filter: blur(10px); border: 1px solid var(--glass-border); border-radius: 12px; }
.logo .glitch { font-size: 1.5rem; font-weight: bold; color: var(--accent); }
.logo .version { font-size: 0.8rem; color: var(--hold); border: 1px solid var(--hold); padding: 2px 6px; border-radius: 4px; margin-left: 10px;}

/* CSS Mới cho Bảng Thống kê */
.stats-panel { display: flex; gap: 30px; background: rgba(0,0,0,0.3); padding: 10px 20px; border-radius: 8px; border: 1px solid rgba(255,255,255,0.05); }
.stat-box { display: flex; flex-direction: column; align-items: center; }
.stat-label { font-size: 0.7rem; color: var(--text-sub); letter-spacing: 1px; }
.stat-value { font-size: 1.2rem; font-weight: bold; text-shadow: 0 0 10px currentColor; }
.stat-value.profit { color: var(--bull); }
.stat-value.loss { color: var(--bear); }
.stat-value.accent { color: var(--accent); }

.status-bar { display: flex; gap: 20px; font-size: 0.85rem; color: var(--text-sub); }
.status-item { display: flex; align-items: center; gap: 8px; }
.dot { width: 8px; height: 8px; border-radius: 50%; }
.dot.binance { background-color: #f59e0b; box-shadow: 0 0 8px #f59e0b; animation: blink 1s infinite; }
.dot.local { background-color: var(--bull); box-shadow: 0 0 8px var(--bull); }
@keyframes blink { 0%, 100% { opacity: 1; } 50% { opacity: 0.4; } }

.dashboard-grid { display: grid; grid-template-columns: 2fr 1.5fr 1fr; gap: 16px; flex: 1; min-height: 0; }
.panel { background: var(--glass-bg); backdrop-filter: blur(10px); border: 1px solid var(--glass-border); border-radius: 12px; display: flex; flex-direction: column; overflow: hidden; }
.panel-header { padding: 12px 16px; border-bottom: 1px solid rgba(255,255,255,0.05); display: flex; justify-content: space-between; align-items: center; background: rgba(0,0,0,0.2); }
.panel-header h2 { margin: 0; font-size: 1rem; color: var(--text-main); text-transform: uppercase; letter-spacing: 1px; }
.chart-container { flex: 1; width: 100%; min-height: 300px; }
.logs-container { flex: 1; overflow-y: auto; padding: 16px; display: flex; flex-direction: column; gap: 12px; }
.logs-container::-webkit-scrollbar { width: 4px; }
.logs-container::-webkit-scrollbar-thumb { background: rgba(56, 189, 248, 0.3); }
.error-msg { background: rgba(239, 68, 68, 0.2); border: 1px solid var(--bear); color: #fff; padding: 10px; border-radius: 6px; text-align: center; font-size: 0.9rem; }
.log-card { background: rgba(0, 0, 0, 0.3); border-left: 3px solid; border-radius: 6px; padding: 12px; font-size: 0.85rem; }
.bull-card { border-color: var(--bull); } .bear-card { border-color: var(--bear); } .hold-card { border-color: var(--hold); }
.log-header { display: flex; justify-content: space-between; margin-bottom: 8px; color: var(--accent); }
.log-body { display: flex; gap: 12px; align-items: flex-start; }
.decision-badge { padding: 4px 8px; border-radius: 4px; font-weight: bold; }
.decision-badge.buy { background: rgba(16, 185, 129, 0.1); color: var(--bull); }
.decision-badge.sell { background: rgba(239, 68, 68, 0.1); color: var(--bear); }
.decision-badge.hold { background: rgba(251, 191, 36, 0.1); color: var(--hold); }
.reasoning { margin: 0; color: var(--text-sub); line-height: 1.4; flex: 1; }
.execution-container { padding: 16px; display: flex; flex-direction: column; gap: 16px; overflow-y: auto; }
.risk-card { background: rgba(0, 0, 0, 0.4); border: 1px solid; border-radius: 8px; padding: 20px; text-align: center; position: relative; overflow: hidden; }
.risk-card::before { content: ''; position: absolute; top: 0; left: 0; right: 0; height: 2px; }
.risk-card.buy { border-color: rgba(16, 185, 129, 0.3); } .risk-card.buy::before { background: var(--bull); box-shadow: 0 0 10px var(--bull); } .risk-card.buy .risk-decision { color: var(--bull); }
.risk-card.sell { border-color: rgba(239, 68, 68, 0.3); } .risk-card.sell::before { background: var(--bear); box-shadow: 0 0 10px var(--bear); } .risk-card.sell .risk-decision { color: var(--bear); }
.risk-card.hold { border-color: rgba(251, 191, 36, 0.3); } .risk-card.hold::before { background: var(--hold); box-shadow: 0 0 10px var(--hold); } .risk-card.hold .risk-decision { color: var(--hold); }
.risk-title { font-size: 0.75rem; color: var(--text-sub); letter-spacing: 2px; margin-bottom: 8px; }
.risk-decision { font-size: 2rem; font-weight: bold; text-shadow: 0 0 20px currentColor; margin-bottom: 8px; }
.risk-time { font-size: 0.8rem; color: var(--text-sub); }
.loading-matrix { text-align: center; color: var(--accent); animation: blink 1.5s infinite; }
</style>