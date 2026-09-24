import { useEffect, useMemo, useRef, useState } from "react";
import { API_BASE } from "./api";
import Logo from "./Logo";
import "./Dashboard.css";

const WINDOWS = [
  { label: "7d", days: 7 },
  { label: "14d", days: 14 },
  { label: "30d", days: 30 },
  { label: "60d", days: 60 },
  { label: "90d", days: 90 },
];

const rupees = (v) =>
  v == null ? "—" : "₹" + Math.round(v).toLocaleString("en-IN");
const compact = (v) =>
  v == null ? "—" : Math.abs(v) >= 100000 ? "₹" + (v / 100000).toFixed(1) + "L" : rupees(v);
const num = (v) => (v == null ? "—" : Math.round(v).toLocaleString("en-IN"));

function StatCard({ label, value, accent, hint }) {
  return (
    <div className="rx-stat-card" style={{ "--stat-accent": accent }}>
      <div className="rx-stat-value">{value}</div>
      <div className="rx-stat-label">{label}</div>
      {hint && <div className="rx-stat-hint">{hint}</div>}
    </div>
  );
}

function BarChart({ data, valueKey, labelKey, format, accent = "var(--brand-blue)" }) {
  const max = Math.max(1, ...data.map((d) => d[valueKey] || 0));
  const step = Math.max(1, Math.ceil(data.length / 8));
  return (
    <div className="rx-chart">
      <div className="rx-bars">
        {data.map((d, i) => (
          <div
            key={i}
            className="rx-bar"
            style={{ height: `${((d[valueKey] || 0) / max) * 100}%`, background: accent }}
            title={`${d[labelKey]}: ${format(d[valueKey])}`}
          />
        ))}
      </div>
      <div className="rx-axis">
        {data.map((d, i) => (
          <span key={i}>{i % step === 0 ? d[labelKey] : ""}</span>
        ))}
      </div>
    </div>
  );
}

function MiniBars({ rows, valueKey, labelKey, format }) {
  const max = Math.max(1, ...rows.map((r) => r[valueKey] || 0));
  return (
    <div className="rx-minibars">
      {rows.map((r, i) => (
        <div key={i} className="rx-minibar-row">
          <span className="rx-minibar-label">{r[labelKey]}</span>
          <span className="rx-minibar-track">
            <span className="rx-minibar-fill" style={{ width: `${((r[valueKey] || 0) / max) * 100}%` }} />
          </span>
          <span className="rx-minibar-val">{format(r[valueKey])}</span>
        </div>
      ))}
    </div>
  );
}

function Table({ cols, rows, empty = "Nothing to show." }) {
  if (!rows || rows.length === 0) return <div className="rx-empty">{empty}</div>;
  return (
    <div className="rx-table-wrap">
      <table className="rx-table">
        <thead>
          <tr>
            {cols.map((c) => (
              <th key={c.key} className={c.num ? "num" : ""}>
                {c.head}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>
              {cols.map((c) => (
                <td key={c.key} className={c.num ? "num" : ""}>
                  {c.render ? c.render(r[c.key], r) : r[c.key] ?? "—"}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function Dashboard({ refreshSignal }) {
  const [windowDays, setWindowDays] = useState(14);
  const [search, setSearch] = useState("");
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [updated, setUpdated] = useState(null);
  const pollRef = useRef(null);

  function load() {
    fetch(`${API_BASE}/dashboard?days=${windowDays}`)
      .then((r) => {
        if (!r.ok) throw new Error(`Request failed (${r.status})`);
        return r.json();
      })
      .then((d) => {
        setData(d);
        setError(null);
        setUpdated(new Date());
      })
      .catch((e) => setError(e.message));
  }

  useEffect(() => {
    load();
    clearInterval(pollRef.current);
    pollRef.current = setInterval(load, 30000);
    return () => clearInterval(pollRef.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [windowDays]);

  useEffect(() => {
    if (refreshSignal) load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshSignal]);

  const k = data?.kpis;
  const wow = useMemo(() => {
    if (!k?.revenue_last_week) return null;
    return Math.round(((k.revenue_week - k.revenue_last_week) / k.revenue_last_week) * 100);
  }, [k]);

  const filteredBills = useMemo(() => {
    if (!data?.recent_bills) return [];
    const q = search.trim().toLowerCase();
    if (!q) return data.recent_bills;
    return data.recent_bills.filter(
      (b) =>
        String(b.bill_no).includes(q) ||
        (b.cashier || "").toLowerCase().includes(q) ||
        (b.customer || "").toLowerCase().includes(q)
    );
  }, [data, search]);

  return (
    <div className="rx-shell">
      <div className="rx-topbar">
        <Logo />
        <div className="rx-page-title">Retail Intelligence</div>
      </div>

      <div className="rx-body">
        <div className="rx-header-card">
          <div className="rx-header-left">
            <span className="rx-eyebrow">RETAIL INTELLIGENCE</span>
            <h1>{data?.store || "Hypermart — Main Branch"}</h1>
            <p>
              Live stock, billing, sales, footfall and staff performance from the
              store database. Ask the assistant (bottom-right) anything the charts
              don't already answer.
            </p>
          </div>

          <div className="rx-time-window">
            <div className="rx-time-label">Revenue trend window</div>
            <div className="rx-time-sub">last {windowDays} days</div>
            <div className="rx-preset-row">
              {WINDOWS.map((w) => (
                <button
                  key={w.label}
                  className={`rx-preset-btn ${windowDays === w.days ? "active" : ""}`}
                  onClick={() => setWindowDays(w.days)}
                >
                  {w.label}
                </button>
              ))}
            </div>
          </div>
        </div>

        {error && <div className="rx-error">Couldn't reach the dashboard API: {error}</div>}

        {data && (
          <>
            <div className="rx-stats-row">
              <StatCard
                label="Revenue Today"
                value={rupees(k.revenue_today)}
                accent="#1d5a96"
                hint={`${k.bills_today} bills`}
              />
              <StatCard
                label="Revenue This Week"
                value={compact(k.revenue_week)}
                accent="#ed7d22"
                hint={
                  wow == null ? undefined : `${wow >= 0 ? "▲" : "▼"} ${Math.abs(wow)}% vs last week`
                }
              />
              <StatCard label="Avg Bill (Month)" value={rupees(k.avg_bill_month)} accent="#7c3aed" />
              <StatCard label="Footfall Today" value={num(k.footfall_today)} accent="#0d9488" />
              <StatCard
                label="Below Threshold"
                value={k.below_threshold}
                accent="#d97706"
                hint={k.below_threshold ? "needs reorder" : "all stocked"}
              />
              <StatCard
                label="Exceptions Today"
                value={k.exceptions_today}
                accent="#dc2626"
                hint={k.exceptions_today ? "review" : "clean"}
              />
            </div>

            <div className="rx-grid-2">
              <div className="rx-panel">
                <div className="rx-panel-head">
                  <h2>Revenue — last {windowDays} days</h2>
                  <p>Daily total billed</p>
                </div>
                <BarChart
                  data={data.revenue_by_day}
                  valueKey="revenue"
                  labelKey="day"
                  format={(v) => compact(v)}
                  accent="#1d5a96"
                />
              </div>
              <div className="rx-panel">
                <div className="rx-panel-head">
                  <h2>Sales by hour — this month</h2>
                  <p>When the store actually rings up revenue</p>
                </div>
                <BarChart
                  data={data.sales_by_hour}
                  valueKey="revenue"
                  labelKey="hour"
                  format={(v) => compact(v)}
                  accent="#0d9488"
                />
              </div>
            </div>

            <div className="rx-grid-2">
              <div className="rx-panel">
                <div className="rx-panel-head">
                  <h2>Top categories — this month</h2>
                  <p>By revenue</p>
                </div>
                <MiniBars
                  rows={data.top_categories}
                  valueKey="revenue"
                  labelKey="category"
                  format={(v) => compact(v)}
                />
              </div>
              <div className="rx-panel">
                <div className="rx-panel-head">
                  <h2>Cashier leaderboard — this week</h2>
                  <p>Amount billed, bill count, avg billing time</p>
                </div>
                <Table
                  cols={[
                    { key: "cashier", head: "Cashier" },
                    { key: "billed", head: "Billed", num: true, render: (v) => compact(v) },
                    { key: "bills", head: "Bills", num: true },
                    { key: "avg_seconds", head: "Avg s", num: true },
                  ]}
                  rows={data.cashiers}
                />
              </div>
            </div>

            <div className="rx-grid-2">
              <div className="rx-panel">
                <div className="rx-panel-head">
                  <h2>Top products — this week</h2>
                  <p>By units sold</p>
                </div>
                <Table
                  cols={[
                    { key: "name", head: "Product" },
                    { key: "category", head: "Category" },
                    { key: "units", head: "Units", num: true },
                    { key: "revenue", head: "Revenue", num: true, render: (v) => compact(v) },
                  ]}
                  rows={data.top_products}
                />
              </div>
              <div className="rx-panel">
                <div className="rx-panel-head">
                  <h2>Low stock — needs reorder</h2>
                  <p>Current stock below the reorder threshold</p>
                </div>
                <Table
                  cols={[
                    { key: "name", head: "Product" },
                    { key: "current_stock", head: "Stock", num: true },
                    { key: "reorder_threshold", head: "Threshold", num: true },
                  ]}
                  rows={data.low_stock}
                  empty="Everything is above its reorder threshold."
                />
              </div>
            </div>

            <div className="rx-panel">
              <div className="rx-panel-head rx-panel-head-row">
                <div>
                  <h2>Recent bills</h2>
                  <p>Most recent transactions — every bill is fully itemised</p>
                </div>
                <input
                  className="rx-search"
                  placeholder="Filter by bill no / cashier / customer"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                />
              </div>
              <Table
                cols={[
                  { key: "bill_no", head: "Bill" },
                  {
                    key: "ts",
                    head: "Time",
                    render: (v) => new Date(v).toLocaleString(),
                  },
                  { key: "cashier", head: "Cashier" },
                  { key: "customer", head: "Customer", render: (v) => v || "walk-in" },
                  { key: "bill_seconds", head: "Secs", num: true },
                  { key: "total_amount", head: "Amount", num: true, render: (v) => rupees(v) },
                  {
                    key: "is_exception",
                    head: "",
                    render: (v) => (v ? <span className="rx-pill rx-pill-exc">exception</span> : ""),
                  },
                ]}
                rows={filteredBills}
                empty="No bills match that filter."
              />
            </div>

            {updated && (
              <div className="rx-updated">
                Updated {updated.toLocaleTimeString()} · auto-refreshes every 30s ·
                data as of {new Date(data.now).toLocaleString()}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
