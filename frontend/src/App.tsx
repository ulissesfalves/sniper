// =============================================================================
// DESTINO: frontend/src/App.tsx
// Dashboard institucional SNIPER v10.10
// Tema: terminal quant — preto profundo, âmbar, verde/vermelho nítidos
// Fonte: JetBrains Mono (mono) + Syne (display)
// Dados: REST polling 10s + WebSocket tempo real
// =============================================================================
import { useState, useEffect, useRef, useCallback } from "react";
import {
  LineChart, Line, AreaChart, Area,
  XAxis, YAxis, Tooltip, ResponsiveContainer,
  ReferenceLine, ScatterChart, Scatter,
} from "recharts";

// ─── Types ────────────────────────────────────────────────────────────────────
interface PortfolioSummary {
  capital_total_usdt: number;
  capital_hwm_usdt:   number;
  drawdown_pct:       number;
  n_open_positions:   number;
  pnl_total_pct:      number;
  last_updated:       string;
}

interface RiskDashboard {
  cvar_stress:      number;
  cvar_limit:       number;
  cvar_ok:          boolean;
  drawdown_scalar:  number;
  global_alarm:     string;
  n_assets_bear:    number;
  n_assets_blocked: number;
}

interface AlarmEvent {
  timestamp:       string;
  symbol:          string;
  level:           number;
  action:          string;
  reasons:         string[];
  c2st_severity?:  string;
  cs_zscore?:      number;
  cvar_stress?:    number;
}

interface RealtimeEvent {
  channel: string;
  data:    Record<string, unknown>;
}

interface CalibrationBin {
  bin_low:   number;
  bin_high:  number;
  mean_conf: number;
  mean_acc:  number;
  n:         number;
}

// ─── Constants ────────────────────────────────────────────────────────────────
const API = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";
const WS  = import.meta.env.VITE_WS_URL       ?? "ws://localhost:8000/ws";

const ALARM_COLORS: Record<string, string> = {
  CLEAR: "#22c55e",
  WARN:  "#f59e0b",
  BLOCK: "#f97316",
  HALT:  "#ef4444",
};
const ALARM_BG: Record<string, string> = {
  CLEAR: "rgba(34,197,94,.08)",
  WARN:  "rgba(245,158,11,.10)",
  BLOCK: "rgba(249,115,22,.12)",
  HALT:  "rgba(239,68,68,.15)",
};

// ─── Hooks ────────────────────────────────────────────────────────────────────
function useApi<T>(url: string, interval = 10_000): T | null {
  const [data, setData] = useState<T | null>(null);
  const fetch_ = useCallback(async () => {
    try {
      const r = await fetch(`${API}${url}`);
      setData(await r.json());
    } catch { /* silencioso */ }
  }, [url]);

  useEffect(() => {
    fetch_();
    const id = setInterval(fetch_, interval);
    return () => clearInterval(id);
  }, [fetch_, interval]);

  return data;
}

function useWebSocket(): RealtimeEvent[] {
  const [events, setEvents] = useState<RealtimeEvent[]>([]);
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    const connect = () => {
      const ws = new WebSocket(WS);
      wsRef.current = ws;

      ws.onmessage = (e) => {
        try {
          const evt = JSON.parse(e.data) as RealtimeEvent;
          setEvents(prev => [evt, ...prev].slice(0, 100));
        } catch { /* ignora */ }
      };
      ws.onclose = () => setTimeout(connect, 3000);
    };
    connect();
    return () => wsRef.current?.close();
  }, []);

  return events;
}

// ─── Sub-components ───────────────────────────────────────────────────────────

function MetricCard({ label, value, sub, color = "#f59e0b", alert = false }: {
  label: string; value: string; sub?: string; color?: string; alert?: boolean;
}) {
  return (
    <div style={{
      background:   alert ? "rgba(239,68,68,.08)" : "rgba(255,255,255,.03)",
      border:       `1px solid ${alert ? "rgba(239,68,68,.4)" : "rgba(255,255,255,.07)"}`,
      borderRadius: 6,
      padding:      "16px 20px",
      minWidth:     160,
    }}>
      <div style={{ fontSize: 10, letterSpacing: "0.12em", color: "#6b7280", textTransform: "uppercase", marginBottom: 6 }}>
        {label}
      </div>
      <div style={{ fontSize: 26, fontWeight: 700, color, fontFamily: "JetBrains Mono, monospace", lineHeight: 1 }}>
        {value}
      </div>
      {sub && <div style={{ fontSize: 11, color: "#9ca3af", marginTop: 5 }}>{sub}</div>}
    </div>
  );
}

function SectionHeader({ title, badge }: { title: string; badge?: string }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 12 }}>
      <div style={{ width: 3, height: 16, background: "#f59e0b", borderRadius: 2 }} />
      <span style={{ fontSize: 11, fontWeight: 600, letterSpacing: "0.14em", color: "#d1d5db", textTransform: "uppercase" }}>
        {title}
      </span>
      {badge && (
        <span style={{ fontSize: 10, background: "rgba(245,158,11,.15)", color: "#f59e0b",
                       border: "1px solid rgba(245,158,11,.3)", borderRadius: 10,
                       padding: "1px 8px", letterSpacing: "0.06em" }}>
          {badge}
        </span>
      )}
    </div>
  );
}

function CalibrationPlot({ bins }: { bins: CalibrationBin[] }) {
  const data = bins.map(b => ({
    conf: +((b.bin_low + b.bin_high) / 2).toFixed(2),
    acc:  +(b.mean_acc).toFixed(3),
    n:    b.n,
  }));
  const diagonal = [{ conf: 0, acc: 0 }, { conf: 1, acc: 1 }];

  return (
    <ResponsiveContainer width="100%" height={180}>
      <ScatterChart margin={{ top: 8, right: 8, left: -20, bottom: 0 }}>
        <XAxis dataKey="conf" type="number" domain={[0,1]} tick={{ fontSize: 9, fill: "#6b7280" }}
               tickCount={6} label={{ value: "Score bruto", position: "insideBottom", offset: -2, fontSize: 9, fill: "#6b7280" }} />
        <YAxis dataKey="acc"  type="number" domain={[0,1]} tick={{ fontSize: 9, fill: "#6b7280" }} tickCount={6} />
        <Tooltip
          contentStyle={{ background: "#0f1117", border: "1px solid #374151", borderRadius: 4, fontSize: 10 }}
          formatter={(v: number, n: string) => [v.toFixed(3), n === "acc" ? "P(real)" : "score"]}
        />
        {/* Linha perfeita */}
        <Line data={diagonal} type="linear" dataKey="acc" dot={false}
              stroke="rgba(255,255,255,.18)" strokeWidth={1} strokeDasharray="4 4" />
        <Scatter data={data} fill="#f59e0b" opacity={0.85} />
      </ScatterChart>
    </ResponsiveContainer>
  );
}

function AlarmBadge({ level, action }: { level: number; action: string }) {
  const color = ALARM_COLORS[action] ?? "#6b7280";
  return (
    <span style={{
      display: "inline-block",
      fontSize: 9, fontWeight: 700,
      letterSpacing: "0.12em",
      color,
      border: `1px solid ${color}40`,
      background: `${color}12`,
      borderRadius: 4,
      padding: "2px 7px",
    }}>
      L{level} {action}
    </span>
  );
}

function AlarmFeed({ alarms }: { alarms: AlarmEvent[] }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4, maxHeight: 280, overflowY: "auto" }}>
      {alarms.length === 0 && (
        <div style={{ color: "#4b5563", fontSize: 11, textAlign: "center", padding: "20px 0" }}>
          Sem alarmes registrados
        </div>
      )}
      {alarms.map((a, i) => (
        <div key={i} style={{
          background: ALARM_BG[a.action] ?? "transparent",
          border:     `1px solid ${ALARM_COLORS[a.action] ?? "#374151"}25`,
          borderRadius: 5,
          padding:    "8px 12px",
          display:    "flex",
          alignItems: "flex-start",
          gap:        10,
        }}>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 3 }}>
              <AlarmBadge level={a.level} action={a.action} />
              <span style={{ fontSize: 10, fontFamily: "JetBrains Mono, monospace",
                             color: "#e5e7eb", fontWeight: 600 }}>
                {a.symbol}
              </span>
              <span style={{ fontSize: 9, color: "#6b7280", marginLeft: "auto" }}>
                {new Date(a.timestamp).toLocaleTimeString("pt-BR")}
              </span>
            </div>
            {a.reasons?.slice(0, 2).map((r, j) => (
              <div key={j} style={{ fontSize: 9.5, color: "#9ca3af", lineHeight: 1.4 }}>
                {r}
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

function RealtimeFeed({ events }: { events: RealtimeEvent[] }) {
  const executions = events.filter(e => e.channel === "sniper:executions").slice(0, 15);

  return (
    <div style={{ maxHeight: 200, overflowY: "auto" }}>
      {executions.length === 0 && (
        <div style={{ color: "#374151", fontSize: 10, fontStyle: "italic",
                      textAlign: "center", padding: "16px 0" }}>
          Aguardando execuções…
        </div>
      )}
      {executions.map((e, i) => {
        const d = e.data as Record<string, unknown>;
        const isBuy  = d.side === "BUY";
        const isFail = d.status === "FAILED" || d.type === "rejection";
        return (
          <div key={i} style={{
            display:       "flex",
            alignItems:    "center",
            gap:           8,
            padding:       "5px 0",
            borderBottom:  "1px solid rgba(255,255,255,.04)",
            fontFamily:    "JetBrains Mono, monospace",
            fontSize:      10,
          }}>
            <span style={{ color: isFail ? "#ef4444" : isBuy ? "#22c55e" : "#f97316",
                           fontWeight: 700, minWidth: 30 }}>
              {isFail ? "FAIL" : isBuy ? "BUY " : "SELL"}
            </span>
            <span style={{ color: "#e5e7eb", minWidth: 80 }}>
              {String(d.symbol ?? d.type ?? "—")}
            </span>
            <span style={{ color: "#9ca3af" }}>
              {d.notional_usdt ? `$${Number(d.notional_usdt).toFixed(0)}` : ""}
            </span>
            <span style={{ color: "#6b7280", marginLeft: "auto" }}>
              slip {d.slippage_real ? `${(Number(d.slippage_real)*100).toFixed(2)}%` : "—"}
            </span>
          </div>
        );
      })}
    </div>
  );
}

function EquityCurve({ data }: { data: { date: string; equity: number; benchmark: number }[] }) {
  if (data.length < 2) return (
    <div style={{ height: 200, display: "flex", alignItems: "center",
                  justifyContent: "center", color: "#374151", fontSize: 11 }}>
      Aguardando dados de equity…
    </div>
  );
  return (
    <ResponsiveContainer width="100%" height={200}>
      <AreaChart data={data} margin={{ top: 4, right: 4, left: -28, bottom: 0 }}>
        <defs>
          <linearGradient id="equityGrad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%"  stopColor="#f59e0b" stopOpacity={0.25} />
            <stop offset="95%" stopColor="#f59e0b" stopOpacity={0} />
          </linearGradient>
        </defs>
        <XAxis dataKey="date" tick={{ fontSize: 9, fill: "#6b7280" }} tickLine={false}
               tickFormatter={(v: string) => v.slice(5)} />
        <YAxis tick={{ fontSize: 9, fill: "#6b7280" }} tickLine={false}
               tickFormatter={(v: number) => `$${(v/1000).toFixed(0)}k`} />
        <Tooltip
          contentStyle={{ background: "#0f1117", border: "1px solid #374151",
                          borderRadius: 4, fontSize: 10 }}
          formatter={(v: number, n: string) => [`$${v.toLocaleString()}`, n === "equity" ? "Equity" : "Benchmark"]}
        />
        <ReferenceLine y={200_000} stroke="rgba(255,255,255,.12)" strokeDasharray="3 3" />
        <Area type="monotone" dataKey="benchmark" stroke="#374151" strokeWidth={1}
              fill="transparent" dot={false} />
        <Area type="monotone" dataKey="equity" stroke="#f59e0b" strokeWidth={2}
              fill="url(#equityGrad)" dot={false} />
      </AreaChart>
    </ResponsiveContainer>
  );
}

function RegimeChart({ prob }: { prob: { date: string; p_bull: number }[] }) {
  if (prob.length < 2) return (
    <div style={{ height: 100, display: "flex", alignItems: "center",
                  justifyContent: "center", color: "#374151", fontSize: 11 }}>
      Aguardando dados de regime…
    </div>
  );
  return (
    <ResponsiveContainer width="100%" height={100}>
      <AreaChart data={prob} margin={{ top: 4, right: 4, left: -28, bottom: 0 }}>
        <defs>
          <linearGradient id="bullGrad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%"  stopColor="#22c55e" stopOpacity={0.3} />
            <stop offset="95%" stopColor="#22c55e" stopOpacity={0} />
          </linearGradient>
        </defs>
        <XAxis dataKey="date" hide />
        <YAxis domain={[0,1]} tick={{ fontSize: 9, fill: "#6b7280" }} tickLine={false} tickCount={3} />
        <Tooltip
          contentStyle={{ background: "#0f1117", border: "1px solid #374151",
                          borderRadius: 4, fontSize: 10 }}
          formatter={(v: number) => [`${(v*100).toFixed(1)}%`, "P(bull)"]}
        />
        <ReferenceLine y={0.5} stroke="rgba(255,255,255,.15)" strokeDasharray="3 3" />
        <Area type="monotone" dataKey="p_bull" stroke="#22c55e" strokeWidth={1.5}
              fill="url(#bullGrad)" dot={false} />
      </AreaChart>
    </ResponsiveContainer>
  );
}

// ─── Main Dashboard ───────────────────────────────────────────────────────────
export default function App() {
  const portfolio  = useApi<PortfolioSummary>("/api/portfolio/summary");
  const riskData   = useApi<RiskDashboard>("/api/risk/dashboard");
  const alarmRes   = useApi<{ alarms: AlarmEvent[] }>("/api/risk/alarms");
  const equityRes  = useApi<{ dates: string[]; equity: number[]; benchmark: number[] }>("/api/portfolio/equity_curve");
  const wsEvents   = useWebSocket();

  // Formata equity para recharts
  const equityData = (equityRes?.dates ?? []).map((d, i) => ({
    date:      d,
    equity:    equityRes!.equity[i],
    benchmark: equityRes!.benchmark[i],
  }));

  // Calibration placeholder bins
  const calibBins: CalibrationBin[] = [0.05,0.15,0.25,0.35,0.45,0.55,0.65,0.75,0.85,0.95].map((c, i) => ({
    bin_low:   c - 0.05,
    bin_high:  c + 0.05,
    mean_conf: c,
    mean_acc:  c + (Math.random() - 0.5) * 0.08,
    n:         20 + Math.floor(Math.random() * 60),
  }));

  const globalAlarm = riskData?.global_alarm ?? "CLEAR";
  const alarmColor  = ALARM_COLORS[globalAlarm] ?? "#6b7280";

  return (
    <div style={{
      minHeight:  "100vh",
      background: "#080b10",
      color:      "#e5e7eb",
      fontFamily: "JetBrains Mono, monospace",
      padding:    "20px 24px",
    }}>
      {/* Google Fonts */}
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@300;400;500;600;700&family=Syne:wght@600;700;800&display=swap');
        * { box-sizing: border-box; }
        ::-webkit-scrollbar { width: 4px; } ::-webkit-scrollbar-track { background: transparent; }
        ::-webkit-scrollbar-thumb { background: #374151; border-radius: 2px; }
        @keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: .4; } }
      `}</style>

      {/* ── Header ─────────────────────────────────────────────────────── */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between",
                    marginBottom: 24, borderBottom: "1px solid rgba(255,255,255,.06)", paddingBottom: 16 }}>
        <div style={{ display: "flex", alignItems: "baseline", gap: 14 }}>
          <span style={{ fontFamily: "Syne, sans-serif", fontSize: 22, fontWeight: 800,
                         color: "#f59e0b", letterSpacing: "-0.01em" }}>
            SNIPER
          </span>
          <span style={{ fontSize: 10, color: "#4b5563", letterSpacing: "0.15em" }}>v10.10</span>
          <span style={{ fontSize: 10, color: "#6b7280" }}>
            {new Date().toLocaleString("pt-BR", { timeZone: "America/Sao_Paulo" })}
          </span>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          {/* Status global */}
          <div style={{ display: "flex", alignItems: "center", gap: 6,
                        background: `${alarmColor}12`, border: `1px solid ${alarmColor}30`,
                        borderRadius: 6, padding: "5px 12px" }}>
            <div style={{ width: 6, height: 6, borderRadius: "50%", background: alarmColor,
                          animation: globalAlarm !== "CLEAR" ? "pulse 1.5s infinite" : "none" }} />
            <span style={{ fontSize: 10, fontWeight: 700, color: alarmColor, letterSpacing: "0.1em" }}>
              {globalAlarm}
            </span>
          </div>
          {/* Testnet badge */}
          <div style={{ fontSize: 9, background: "rgba(139,92,246,.15)", color: "#a78bfa",
                        border: "1px solid rgba(139,92,246,.3)", borderRadius: 4, padding: "3px 8px",
                        letterSpacing: "0.1em" }}>
            TESTNET
          </div>
        </div>
      </div>

      {/* ── KPI Row ─────────────────────────────────────────────────────── */}
      <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginBottom: 24 }}>
        <MetricCard
          label="Equity"
          value={`$${((portfolio?.capital_total_usdt ?? 200_000)/1000).toFixed(1)}k`}
          sub={`HWM $${((portfolio?.capital_hwm_usdt ?? 200_000)/1000).toFixed(1)}k`}
          color="#f59e0b"
        />
        <MetricCard
          label="P&L Total"
          value={`${portfolio?.pnl_total_pct?.toFixed(2) ?? "0.00"}%`}
          color={(portfolio?.pnl_total_pct ?? 0) >= 0 ? "#22c55e" : "#ef4444"}
        />
        <MetricCard
          label="Drawdown"
          value={`${(portfolio?.drawdown_pct ?? 0).toFixed(2)}%`}
          sub="Limite: 18%"
          color={(portfolio?.drawdown_pct ?? 0) > 12 ? "#ef4444" : "#f59e0b"}
          alert={(portfolio?.drawdown_pct ?? 0) > 12}
        />
        <MetricCard
          label="CVaR_stress ρ=1"
          value={`${((riskData?.cvar_stress ?? 0)*100).toFixed(2)}%`}
          sub="Limite: 15%"
          color={riskData?.cvar_ok !== false ? "#22c55e" : "#ef4444"}
          alert={riskData?.cvar_ok === false}
        />
        <MetricCard
          label="DD Scalar"
          value={`${((riskData?.drawdown_scalar ?? 1)*100).toFixed(0)}%`}
          sub="Kelly × κ × scalar"
          color={(riskData?.drawdown_scalar ?? 1) < 0.5 ? "#ef4444" : "#f59e0b"}
        />
        <MetricCard
          label="Posições Abertas"
          value={String(portfolio?.n_open_positions ?? 0)}
          sub={`${riskData?.n_assets_bear ?? 0} em BEAR`}
        />
      </div>

      {/* ── Grid principal ───────────────────────────────────────────────── */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 14 }}>

        {/* ── Equity Curve ─────────────────────────────────────────────── */}
        <div style={{ gridColumn: "1 / 3", background: "rgba(255,255,255,.025)",
                      border: "1px solid rgba(255,255,255,.07)", borderRadius: 8, padding: "16px 18px" }}>
          <SectionHeader title="Equity Curve" badge="CPCV N=6 k=2" />
          <EquityCurve data={equityData} />
        </div>

        {/* ── Alarmes ──────────────────────────────────────────────────── */}
        <div style={{ background: "rgba(255,255,255,.025)",
                      border: "1px solid rgba(255,255,255,.07)", borderRadius: 8, padding: "16px 18px" }}>
          <SectionHeader title="Alarmes" badge={`${alarmRes?.alarms?.length ?? 0} eventos`} />
          <AlarmFeed alarms={(alarmRes?.alarms ?? []).slice(0, 20) as AlarmEvent[]} />
        </div>

        {/* ── Calibração Isotônica ─────────────────────────────────────── */}
        <div style={{ background: "rgba(255,255,255,.025)",
                      border: "1px solid rgba(255,255,255,.07)", borderRadius: 8, padding: "16px 18px" }}>
          <SectionHeader title="Calibração Isotônica" badge="halflife=180d" />
          <div style={{ fontSize: 9, color: "#6b7280", marginBottom: 8 }}>
            Reliability diagram — ideal: pontos na diagonal
          </div>
          <CalibrationPlot bins={calibBins} />
          <div style={{ display: "flex", gap: 16, marginTop: 8 }}>
            {[["ECE raw", "—", "#6b7280"], ["ECE cal.", "—", "#22c55e"]].map(([l, v, c]) => (
              <div key={l}>
                <div style={{ fontSize: 9, color: "#6b7280" }}>{l}</div>
                <div style={{ fontSize: 13, fontWeight: 700, color: String(c) }}>{v}</div>
              </div>
            ))}
          </div>
        </div>

        {/* ── HMM Regime ───────────────────────────────────────────────── */}
        <div style={{ background: "rgba(255,255,255,.025)",
                      border: "1px solid rgba(255,255,255,.07)", borderRadius: 8, padding: "16px 18px" }}>
          <SectionHeader title="Regime HMM" badge="P(bull) 90d" />
          <RegimeChart prob={[]} />
          <div style={{ display: "flex", gap: 16, marginTop: 8 }}>
            {[
              ["Bear",  riskData?.n_assets_bear    ?? 0, "#ef4444"],
              ["Block", riskData?.n_assets_blocked ?? 0, "#f97316"],
            ].map(([l, v, c]) => (
              <div key={String(l)}>
                <div style={{ fontSize: 9, color: "#6b7280" }}>{l}</div>
                <div style={{ fontSize: 18, fontWeight: 700, color: String(c) }}>{v}</div>
              </div>
            ))}
          </div>
        </div>

        {/* ── Execuções RT ─────────────────────────────────────────────── */}
        <div style={{ gridColumn: "1 / 3", background: "rgba(255,255,255,.025)",
                      border: "1px solid rgba(255,255,255,.07)", borderRadius: 8, padding: "16px 18px" }}>
          <SectionHeader title="Execuções" badge="tempo real" />
          <RealtimeFeed events={wsEvents} />
        </div>

      </div>

      {/* ── Footer ─────────────────────────────────────────────────────── */}
      <div style={{ marginTop: 20, paddingTop: 12, borderTop: "1px solid rgba(255,255,255,.05)",
                    display: "flex", justifyContent: "space-between", fontSize: 9, color: "#374151" }}>
        <span>SNIPER v10.10 — Capital R$200k — Horizonte 3-5 anos — Target 15-25% a.a.</span>
        <span>CVaR limit 15% · MaxDD 18% · Kelly κ=0.25 · ρ=1.0 stress · τ=1e-5</span>
      </div>
    </div>
  );
}
