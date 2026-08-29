/* app.js — maimai DX Rating Clipper GUI
 * React + QWebChannel bridge.
 */

const { useState, useEffect, useRef } = React;

// ── Icons ────────────────────────────────────────────────────────────────────

const Icon = ({ d, s = 16, sw = 1.6 }) => (
  <svg width={s} height={s} viewBox="0 0 16 16" fill="none" stroke="currentColor"
    strokeWidth={sw} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d={d} />
  </svg>
);

const I = {
  play:    <Icon d="M5 3.5v9l7-4.5z" sw={1.4} />,
  stop:    <Icon d="M4.5 4.5h7v7h-7z" />,
  scan:    <Icon d="M2.5 5V3h2M11.5 3h2v2M13.5 11v2h-2M4.5 13h-2v-2M2.5 8h11" />,
  folder:  <Icon d="M2.5 4.5h4l1.5 1.5h5.5v7h-11z" />,
  film:    <Icon d="M2.5 3.5h11v9h-11zM2.5 6.5h11M2.5 9.5h11M5 3.5v9M11 3.5v9" />,
  check:   <Icon d="M3.5 8.5L6.5 11.5 12.5 5" sw={1.8} />,
  retry:   <Icon d="M13 8a5 5 0 11-1.5-3.5M13 2v3h-3" />,
  list:    <Icon d="M2.5 4h11M2.5 8h11M2.5 12h11" />,
  trash:   <Icon d="M3 4.5h10M5.5 4.5V3h5v1.5M6 7v5M10 7v5M4 4.5l.5 8h7l.5-8" />,
  warn:    <Icon d="M8 2.5L1.5 13.5h13zM8 6.5v3.5M8 11.5v.5" sw={1.5} />,
  gear:    <Icon d="M8 10a2 2 0 100-4 2 2 0 000 4zM8 1v1.5M8 13.5V15M1 8h1.5M13.5 8H15M3.1 3.1l1.1 1.1M11.8 11.8l1.1 1.1M3.1 12.9l1.1-1.1M11.8 4.2l1.1-1.1" sw={1.4} />,
  book:    <Icon d="M3 2.5h7a1.5 1.5 0 011.5 1.5v9.5h-8A1.5 1.5 0 012 15V4a1.5 1.5 0 011-1.5zM5 5.5h4.5M5 7.5h4.5M5 9.5h3" />,
  help:    <Icon d="M8 1.5a6.5 6.5 0 100 13 6.5 6.5 0 000-13zM6.5 6.2a1.5 1.5 0 113 0c0 .75-.6 1-1.2 1.4-.4.3-.3.7-.3 1.1M8 11.5v.4" />,
};

// ── Constants ────────────────────────────────────────────────────────────────

const RATING_MIN = 0;
const RATING_MAX = 17000;

const VOD_PHASES   = ["fetch", "scan", "download", "ocr", "cut"];
const LIVE_PHASES  = ["fetch", "monitor"];
const PHASE_LABELS = {
  fetch:    "연결",
  scan:     "스캔",
  download: "다운로드",
  ocr:      "OCR 분석",
  cut:      "클립 추출",
  monitor:  "실시간 감시",
};

const ENV_CHECK_INITIAL = [
  { id: "ffmpeg",          label: "ffmpeg",           status: "loading", desc: "클립 커팅 및 라이브 녹화에 사용" },
  { id: "tesseract",       label: "Tesseract OCR",     status: "loading", desc: "영상에서 레이팅 숫자를 읽는 OCR 엔진" },
  { id: "yolo",            label: "YOLO 모델",          status: "loading", desc: "영상에서 게임 화면 영역을 감지하는 AI 모델" },
  { id: "firefox_youtube", label: "YouTube 로그인",     status: "loading", desc: "영상 다운로드 및 스트림 접근에 사용" },
  { id: "client_secret",   label: "Google 인증 파일",   status: "loading", desc: "YouTube 자동 업로드 인증에만 사용" },
  { id: "jacket_index",    label: "자켓 인덱스",         status: "loading", desc: "곡 자켓 이미지로 곡명을 식별 (최초 1회 다운로드, 신곡만 추가)" },
  { id: "song_db",         label: "maimai DB",          status: "loading", desc: "곡명·레벨 정보 (gekichumai/dxrating, 7일 캐시)" },
  { id: "cuda",            label: "GPU(CUDA)",          status: "loading", desc: "AI 분석 속도 향상 (없으면 CPU로 동작)" },
];

// "required" | "optional" | "conditional" | "conditional_ocr"
// conditional     = autoUpload ON일 때만 required
// conditional_ocr = songOcr ON일 때만 required
const ITEM_ROLE = {
  ffmpeg:          "required",
  tesseract:       "required",
  yolo:            "required",
  firefox_youtube: "required",
  client_secret:   "conditional",
  jacket_index:    "conditional_ocr",
  song_db:         "conditional_ocr",
  cuda:            "optional",
};

function getEffectiveRole(itemId, autoUpload, songOcr) {
  const base = ITEM_ROLE[itemId] ?? "required";
  if (base === "conditional")     return autoUpload ? "required" : "unused";
  if (base === "conditional_ocr") return songOcr    ? "required" : "unused";
  return base;
}

function getEnvBlockReason(items, autoUpload, songOcr) {
  const blocked = items.filter(
    item => getEffectiveRole(item.id, autoUpload, songOcr) === "required"
           && item.status !== "ok" && item.status !== "loading"
  );
  if (blocked.length === 0) return null;
  const names = blocked.map(i => i.label).join(", ");
  return `시작 불가 — 필수 항목 오류: ${names}`;
}

// Mirrors core/scanner_parallel.py parse_time. Returns seconds (float) or null on error.
function parseTimeJS(s) {
  if (!s || !s.trim()) return null;
  const parts = s.trim().split(":");
  if (parts.length === 3) {
    const v = parseInt(parts[0]) * 3600 + parseInt(parts[1]) * 60 + parseFloat(parts[2]);
    return isNaN(v) ? null : v;
  }
  if (parts.length === 2) {
    const v = parseInt(parts[0]) * 60 + parseFloat(parts[1]);
    return isNaN(v) ? null : v;
  }
  const n = parseFloat(s);
  return isNaN(n) ? null : n;
}

function fmtSec(s) {
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = Math.floor(s % 60);
  return h > 0
    ? `${h}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`
    : `${m}:${String(sec).padStart(2, "0")}`;
}

// ── Mode helpers ─────────────────────────────────────────────────────────────

const MODES = {
  caleidoscope:      { short: "칼레이도", cls: "kaleid"  },
  normal:            { short: "일반",     cls: "normal"  },
  perfect_challenge: { short: "퍼펙트",   cls: "perfect" },
  grade:             { short: "단위인정", cls: "grade"   },
};
const modeClass = (m) => MODES[m]?.cls || "normal";
const modeLabel = (m) => MODES[m]?.short || (m || "미확인");

// ── PhaseBar — shows current pipeline phase + step progress ──────────────────

function PhaseBar({ phaseInfo, isLive }) {
  if (!phaseInfo || phaseInfo.phase === "idle") return null;
  const phases  = isLive ? LIVE_PHASES : VOD_PHASES;
  const curIdx  = phases.indexOf(phaseInfo.phase);
  const pct     = phaseInfo.total > 0
    ? Math.round((phaseInfo.step / phaseInfo.total) * 100) : 0;

  return (
    <div className="card card-pad col gap-10">
      <div className="row gap-8" style={{ flexWrap: "wrap" }}>
        {phases.map((p, i) => {
          const done    = i < curIdx;
          const current = i === curIdx;
          return (
            <React.Fragment key={p}>
              <span style={{
                display: "inline-flex", alignItems: "center",
                height: 24, padding: "0 10px", borderRadius: 999,
                fontSize: 11.5, fontWeight: 700, whiteSpace: "nowrap",
                background: current ? "var(--fg)"
                          : done    ? "var(--success-soft)"
                          :           "var(--surface-3)",
                color:      current ? "var(--accent-fg)"
                          : done    ? "var(--success)"
                          :           "var(--muted)",
                transition: "background .2s, color .2s",
              }}>
                {done ? "✓ " : ""}{PHASE_LABELS[p]}
              </span>
              {i < phases.length - 1 && (
                <span style={{ color: "var(--muted-2)", fontSize: 12, lineHeight: "24px" }}>›</span>
              )}
            </React.Fragment>
          );
        })}
      </div>
      {phaseInfo.total > 0 && (
        <div className="col gap-4">
          <div className="pbar">
            <i style={{ width: `${pct}%`, transition: "width .3s" }} />
          </div>
          <div className="muted num" style={{ fontSize: 11 }}>
            {phaseInfo.step} / {phaseInfo.total}
          </div>
        </div>
      )}
      {phaseInfo.msg && (
        <div className="mono" style={{
          fontSize: 11, color: "var(--muted-2)",
          overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
        }}>
          {phaseInfo.msg}
        </div>
      )}
    </div>
  );
}

// ── VodThumb placeholder ──────────────────────────────────────────────────────

function VodThumb({ w = 140, h = 80, radius = 6 }) {
  const id = `vt-${w}-${h}`;
  return (
    <svg width={w} height={h} style={{ borderRadius: radius, display: "block", flexShrink: 0 }}>
      <defs>
        <pattern id={id} width="14" height="14" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
          <rect width="14" height="14" fill="#eaebef" />
          <rect width="7" height="14" fill="#f4f5f7" />
        </pattern>
      </defs>
      <rect width={w} height={h} fill={`url(#${id})`} rx={radius} />
    </svg>
  );
}

// ── Shared components ─────────────────────────────────────────────────────────

function Stat({ label, val, hint, successVal = false }) {
  return (
    <div className="card col gap-10" style={{ padding: "16px 20px", minHeight: 92 }}>
      <div style={{ fontSize: 11, fontWeight: 700, color: "var(--muted)", letterSpacing: "0.06em" }}>
        {String(label).toUpperCase()}
      </div>
      <div className="num" style={{
        fontSize: 28, fontWeight: 800, letterSpacing: "-0.03em", lineHeight: 1,
        color: successVal ? "var(--success)" : "var(--fg)",
      }}>
        {val}
      </div>
      <div style={{ fontSize: 12, color: "var(--muted-2)" }}>{hint}</div>
    </div>
  );
}

function FormField({ label, children }) {
  return (
    <div className="col gap-6">
      <label style={{ fontSize: 11.5, fontWeight: 700, color: "var(--muted)", letterSpacing: "0.04em" }}>
        {String(label).toUpperCase()}
      </label>
      {children}
    </div>
  );
}

function FieldError({ msg }) {
  if (!msg) return null;
  return <div style={{ fontSize: 11, color: "var(--danger)", marginTop: 2 }}>{msg}</div>;
}

function Toggle({ checked, onChange, label, desc, disabled }) {
  return (
    <label className="row gap-8" style={{ cursor: disabled ? "default" : "pointer", userSelect: "none", opacity: disabled ? 0.45 : 1, alignItems: "center" }}>
      <button onClick={() => !disabled && onChange(!checked)} type="button" style={{
        border: 0, padding: 0, cursor: disabled ? "default" : "pointer",
        width: 32, height: 18, borderRadius: 999,
        background: checked ? "var(--accent)" : "var(--surface-3)",
        position: "relative", transition: "background .15s", flexShrink: 0,
      }}>
        <span style={{
          position: "absolute", top: 2, left: checked ? 16 : 2,
          width: 14, height: 14, borderRadius: "50%", background: "#fff",
          boxShadow: "0 1px 2px rgba(0,0,0,0.2)", transition: "left .15s",
        }} />
      </button>
      <div className="col" style={{ gap: 1 }}>
        <span style={{ fontSize: 12.5, fontWeight: 600 }}>{label}</span>
        {desc && <span style={{ fontSize: 11, color: "var(--muted)" }}>{desc}</span>}
      </div>
    </label>
  );
}

function UploadChip({ status }) {
  if (status === "uploaded")  return <span className="chip success">{I.check}완료</span>;
  if (status === "uploading") return <span className="chip accent">업로드 중</span>;
  if (status === "queued")    return <span className="chip">대기</span>;
  if (status === "failed")    return <span className="chip danger">실패</span>;
  return null;
}

// ── EnvCheck ──────────────────────────────────────────────────────────────────

function EnvCheckItem({ item, role, onAction, actionLabel }) {
  const descEl = item.desc
    ? <span style={{ fontSize: 11, color: "var(--muted-2, var(--muted))", marginTop: 1 }}>{item.desc}</span>
    : null;

  if (item.status === "loading") {
    return (
      <div className="row gap-8" style={{ fontSize: 12.5, padding: "3px 0", alignItems: "flex-start" }}>
        <span style={{ flexShrink: 0 }}>⏳</span>
        <div className="col" style={{ gap: 1 }}>
          <div className="row gap-6" style={{ alignItems: "center" }}>
            <span style={{ fontWeight: 600, minWidth: 110 }}>{item.label}</span>
            <span style={{ color: "var(--muted)", fontSize: 11 }}>확인 중...</span>
          </div>
          {descEl}
        </div>
      </div>
    );
  }

  if (role === "unused") {
    return (
      <div className="row gap-8" style={{ fontSize: 12.5, padding: "3px 0", alignItems: "flex-start", opacity: 0.38 }}>
        <span style={{ flexShrink: 0 }}>—</span>
        <div className="col" style={{ gap: 1 }}>
          <div className="row gap-6" style={{ alignItems: "center" }}>
            <span style={{ fontWeight: 600, minWidth: 110, flexShrink: 0 }}>{item.label}</span>
            <span style={{ fontSize: 10, color: "var(--muted)" }}>자동 업로드 꺼짐</span>
          </div>
          {descEl}
        </div>
      </div>
    );
  }

  const icon     = item.status === "ok" ? "✅" : item.status === "warning" ? "⚠️" : "❌";
  const msgColor = item.status === "warning" ? "#f0a500"
                 : item.status === "error"   ? "var(--danger)"
                 :                             "var(--muted)";
  const chip = role === "optional"
    ? <span style={{ fontSize: 10, color: "var(--muted)" }}>선택</span>
    : null;

  return (
    <div className="row gap-8" style={{ fontSize: 12.5, padding: "3px 0", alignItems: "flex-start" }}>
      <span style={{ flexShrink: 0 }}>{icon}</span>
      <div className="col" style={{ gap: 1 }}>
        <div className="row gap-6" style={{ alignItems: "center", flexWrap: "wrap" }}>
          <span style={{ fontWeight: 600, minWidth: 110, flexShrink: 0 }}>{item.label}</span>
          {chip}
          <span style={{ color: msgColor, fontSize: item.status === "ok" ? 12 : 11, lineHeight: 1.4 }}>
            {item.status === "ok" ? "정상" : item.message}
          </span>
          {onAction && item.status !== "ok" && item.status !== "loading" && (
            <button className="btn ghost sm" onClick={onAction} style={{ padding: "1px 8px", fontSize: 11 }}>
              {actionLabel || "🔑 재인증"}
            </button>
          )}
        </div>
        {descEl}
      </div>
    </div>
  );
}

function EnvCheck({ items, onRecheck, onYoutubeReauth, onJacketUpdate, autoUpload, songOcr }) {
  return (
    <div className="card col" style={{ padding: "16px 22px", gap: 10, marginBottom: 18 }}>
      <div className="row between">
        <span style={{ fontSize: 11.5, fontWeight: 700, color: "var(--muted)", letterSpacing: "0.06em" }}>
          환경 점검
        </span>
        <button className="btn ghost sm" onClick={onRecheck}>🔄 다시 점검</button>
      </div>
      <div className="col" style={{ gap: 2 }}>
        {items.map(item => (
          <EnvCheckItem
            key={item.id}
            item={item}
            role={getEffectiveRole(item.id, autoUpload, songOcr)}
            onAction={item.id === "client_secret" ? onYoutubeReauth
                    : item.id === "jacket_index"  ? onJacketUpdate : undefined}
            actionLabel={item.id === "jacket_index" ? "⬇️ 업데이트" : undefined}
          />
        ))}
      </div>
    </div>
  );
}

// ── NavItem — defined outside Sidebar to avoid remounting on every render ─────

function NavItem({ id, label, icon, badge, activeScreen, setScreen }) {
  const active = activeScreen === id;
  return (
    <button onClick={() => setScreen(id)} className="row gap-8" style={{
      width: "100%", height: 40, padding: "0 14px",
      border: 0, background: active ? "var(--surface-2)" : "transparent",
      color: active ? "var(--fg)" : "var(--fg-2)",
      borderRadius: "var(--r-md)", cursor: "pointer",
      fontFamily: "inherit", fontSize: 13, fontWeight: active ? 700 : 500,
      textAlign: "left",
    }}>
      <span style={{ color: active ? "var(--accent)" : "var(--muted)" }}>{icon}</span>
      <span className="grow">{label}</span>
      {badge != null && badge > 0 && (
        <span className="chip" style={{
          height: 18, padding: "0 6px", fontSize: 10.5,
          background: active ? "var(--accent)" : "var(--surface-3)",
          color: active ? "var(--accent-fg)" : "var(--muted)",
          border: 0,
        }}>{badge}</span>
      )}
    </button>
  );
}

// ── Sidebar ───────────────────────────────────────────────────────────────────

function Sidebar({ screen, setScreen, scanStatus, detections, highlights, phaseInfo }) {
  const failedCount = highlights.filter(h => h.status === "failed").length;
  const dotCls = scanStatus === "running" ? "live" : scanStatus === "error" ? "err" : scanStatus === "done" ? "ok" : "idle";
  const statusLabel = scanStatus === "running" ? "스캔 중" : scanStatus === "error" ? "오류" : scanStatus === "done" ? "성공" : "대기";

  // 상세 상태 텍스트 — 실행 중에는 phase 진행 정보 표시
  let statusDetail;
  if (scanStatus === "running" && phaseInfo) {
    const pct = phaseInfo.msg?.match(/(\d+(?:\.\d+)?)\s*%/)?.[1];
    if (phaseInfo.phase === "scan" && pct) {
      statusDetail = `워커 ${pct}% · 큐 ${detections.length}`;
    } else if (phaseInfo.phase === "download" && phaseInfo.total > 0) {
      statusDetail = `다운로드 ${phaseInfo.step}/${phaseInfo.total}`;
    } else if (phaseInfo.phase === "cut" && phaseInfo.total > 0) {
      statusDetail = `클립 추출 ${phaseInfo.step}/${phaseInfo.total}`;
    } else {
      statusDetail = PHASE_LABELS[phaseInfo.phase] || "진행 중";
    }
  } else {
    statusDetail = `클립 ${highlights.length}개 · 실패 ${failedCount}개`;
  }

  return (
    <aside style={{
      width: 240, padding: "12px 12px", borderRight: "1px solid var(--border)",
      background: "var(--surface)", display: "flex", flexDirection: "column",
      flexShrink: 0,
    }}>
      {/* Logo */}
      <div className="row gap-10" style={{ padding: "0 10px", marginBottom: 14 }}>
        <div className="col" style={{ lineHeight: 1.2 }}>
          <div style={{ fontSize: 16, fontWeight: 800 }}>maimai DX</div>
          <div style={{ fontSize: 11.5, color: "var(--muted)", letterSpacing: "0.06em" }}>RATING CLIPPER</div>
        </div>
      </div>

      {/* Nav */}
      <div className="col gap-2" style={{ marginBottom: 14 }}>
        <div style={{ fontSize: 10.5, fontWeight: 700, color: "var(--muted)", padding: "0 12px 6px", letterSpacing: "0.08em" }}>
          WORKSPACE
        </div>
        <NavItem id="main"       label="메인"        icon={I.scan}  activeScreen={screen} setScreen={setScreen} />
        <NavItem id="scan"       label="스캔 결과"   icon={I.list}  badge={detections.length || null} activeScreen={screen} setScreen={setScreen} />
        <NavItem id="highlights" label="하이라이트"  icon={I.film}  badge={failedCount || null}        activeScreen={screen} setScreen={setScreen} />
      </div>

      <div className="grow" />

      {/* Manual button — above progress card */}
      <button onClick={() => setScreen("manual")} className="row gap-8" style={{
        width: "100%", height: 36, padding: "0 12px", marginBottom: 8,
        border: 0,
        background: screen === "manual" ? "var(--surface-2)" : "transparent",
        color: screen === "manual" ? "var(--fg)" : "var(--fg-2)",
        borderRadius: "var(--r-md)", cursor: "pointer",
        fontFamily: "inherit", fontSize: 12.5,
        fontWeight: screen === "manual" ? 700 : 600,
        textAlign: "left", transition: "background .12s",
      }}
      onMouseEnter={(e) => { if (screen !== "manual") e.currentTarget.style.background = "var(--surface-2)"; }}
      onMouseLeave={(e) => { if (screen !== "manual") e.currentTarget.style.background = "transparent"; }}>
        <span style={{ color: screen === "manual" ? "var(--accent)" : "var(--muted)", display: "inline-flex" }}>{I.book}</span>
        <span className="grow">사용설명서</span>
      </button>

      {/* Status card */}
      <div className="card" style={{ padding: "14px 16px" }}>
        <div className="row gap-6" style={{ marginBottom: 7 }}>
          <span className={`dot ${dotCls}`} />
          <span style={{ fontSize: 12.5, fontWeight: 700 }}>{statusLabel}</span>
        </div>
        <div style={{ fontSize: 11.5, color: "var(--muted)", lineHeight: 1.5,
          overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {statusDetail}
        </div>
      </div>
    </aside>
  );
}

// ── Main screen ───────────────────────────────────────────────────────────────

function ScreenMain({ bridge, scanStatus, setScanStatus, vodInfo, setVodInfo, log, setLog,
                      detections, setDetections, highlights, setHighlights,
                      phaseInfo, setPhaseInfo, envCheckItems, onRecheck, errorBanner, setErrorBanner,
                      clipSelect, setClipSelect, ocrEdit, setOcrEdit,
                      autoUpload, setAutoUpload, songOcr, setSongOcr }) {
  const [url, setUrl]               = useState("");
  const [startRating, setStartRating] = useState("");
  const [ratingError, setRatingError] = useState("");
  const [tStart, setTStart]         = useState("");
  const [tEnd, setTEnd]             = useState("");
  const [tStartError, setTStartError] = useState("");
  const [tEndError, setTEndError]   = useState("");
  const [workers, setWorkers]       = useState(0);
  const [checking, setChecking]     = useState(false);
  const [urlErr, setUrlErr]         = useState("");
  const logRef                      = useRef(null);

  // Load last URL on mount
  useEffect(() => {
    if (!bridge) return;
    bridge.get_last_url((saved) => { if (saved) setUrl(saved); });
  }, [bridge]);

  // Reset checking state when result (or error) arrives
  useEffect(() => {
    if (vodInfo !== null) setChecking(false);
  }, [vodInfo]);

  // Auto-scroll log
  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [log]);

  function handleCheck() {
    if (!bridge || !url.startsWith("http")) return;
    setChecking(true);
    setVodInfo(null);
    setTStartError("");
    setTEndError("");
    bridge.check_status(url);
  }

  function handleStart() {
    if (!bridge) return;
    if (envBlocked) return;
    if (!vodInfo || vodInfo.error) {
      setUrlErr("URL 확인을 먼저 해주세요.");
      setTimeout(() => setUrlErr(""), 3000);
      return;
    }
    setUrlErr("");

    let hasError = false;

    // Rating validation
    const rating = parseInt(startRating, 10);
    if (!startRating || isNaN(rating) || rating < RATING_MIN || rating > RATING_MAX) {
      setRatingError(`${RATING_MIN}~${RATING_MAX} 범위의 숫자를 입력하세요.`);
      hasError = true;
    } else {
      setRatingError("");
    }

    // Time validation (VOD only — live has no fixed duration)
    let startSec = null, endSec = null;
    if (!vodInfo.is_live) {
      if (tStart.trim()) {
        startSec = parseTimeJS(tStart);
        if (startSec === null || startSec < 0) {
          setTStartError("MM:SS 또는 HH:MM:SS 형식으로 입력하세요.");
          hasError = true;
        } else if (vodInfo.duration && startSec >= vodInfo.duration) {
          setTStartError(`영상 길이(${fmtSec(vodInfo.duration)})를 초과합니다.`);
          hasError = true;
        } else {
          setTStartError("");
        }
      } else {
        setTStartError("");
      }

      if (tEnd.trim()) {
        endSec = parseTimeJS(tEnd);
        if (endSec === null || endSec <= 0) {
          setTEndError("MM:SS 또는 HH:MM:SS 형식으로 입력하세요.");
          hasError = true;
        } else if (vodInfo.duration && endSec > vodInfo.duration) {
          setTEndError(`영상 길이(${fmtSec(vodInfo.duration)})를 초과합니다.`);
          hasError = true;
        } else {
          setTEndError("");
        }
      } else {
        setTEndError("");
      }

      if (!hasError && startSec !== null && endSec !== null && startSec >= endSec) {
        setTEndError("종료 시간이 시작 시간보다 뒤여야 합니다.");
        hasError = true;
      }
    }

    if (hasError) return;

    setLog([]);
    setDetections([]);
    setHighlights([]);
    setPhaseInfo(null);
    setScanStatus("running");
    bridge.start_pipeline(JSON.stringify({
      url,
      startRating: rating,
      tStart, tEnd, autoUpload,
      workers: parseInt(workers, 10) || 0,
      isLive: vodInfo.is_live,
      buffer: 6,
      songOcr,
    }));
  }

  function handleStop() {
    if (!bridge) return;
    bridge.stop_pipeline();
  }

  function handleClearLog() { setLog([]); }

  const running  = scanStatus === "running";
  const scanDone = scanStatus === "scan_done";
  const envBlocked = envCheckItems.some(
    item => getEffectiveRole(item.id, autoUpload, songOcr) === "required"
           && item.status !== "ok" && item.status !== "loading"
  );
  const canStart = !running && !scanDone && !!vodInfo && !vodInfo.error && !!startRating.trim() && !envBlocked;

  const uploaded  = highlights.filter(h => h.status === "uploaded").length;
  const failedUpl = highlights.filter(h => h.status === "failed").length;
  const hintUpl   = [
    uploaded  > 0 ? `${uploaded} 완료`  : null,
    failedUpl > 0 ? `${failedUpl} 실패` : null,
  ].filter(Boolean).join(" · ") || "-";

  // Auto-dismiss error banner after 5 seconds
  useEffect(() => {
    if (!errorBanner) return;
    const timer = setTimeout(() => setErrorBanner(null), 5000);
    return () => clearTimeout(timer);
  }, [errorBanner]);

  return (
    <div className="col" style={{ padding: "24px 28px" }}>
      {/* Error banner */}
      {errorBanner && (
        <div style={{
          position: "fixed", top: 0, left: 240, right: 0, zIndex: 999,
          background: "var(--danger)", color: "#fff",
          padding: "10px 24px", fontSize: 13, fontWeight: 600,
          display: "flex", alignItems: "center", justifyContent: "space-between",
          boxShadow: "0 2px 8px rgba(0,0,0,0.2)",
        }}>
          <span>❌ {errorBanner}</span>
          <button onClick={() => setErrorBanner(null)} style={{
            background: "none", border: 0, color: "#fff",
            cursor: "pointer", fontSize: 18, lineHeight: 1, padding: "0 4px",
          }}>✕</button>
        </div>
      )}

      {/* Header */}
      <div className="row between" style={{ marginBottom: 18 }}>
        <div>
          <h1>메인</h1>
          <div className="muted" style={{ fontSize: 13, marginTop: 6 }}>
            YouTube URL · 시작 레이팅 · 구간 입력 후 시작
          </div>
        </div>
        <div className="col gap-6" style={{ alignItems: "flex-end" }}>
          <div className="row gap-8">
            {running
              ? <button className="btn danger lg" onClick={handleStop}>
                  {vodInfo?.is_live && phaseInfo?.phase === "monitor" ? "■ 수집 중단" : "■ 중지"}
                </button>
              : <button
                  className="btn lg"
                  onClick={handleStart}
                  style={canStart
                    ? { background: "var(--accent)", borderColor: "var(--accent)", color: "var(--accent-fg)" }
                    : { background: "var(--surface-3)", borderColor: "var(--border)", color: "var(--muted)", cursor: "default" }
                  }
                >
                  {I.play}시작
                </button>
            }
          </div>
          {urlErr && <div style={{ fontSize: 11.5, color: "var(--danger)" }}>{urlErr}</div>}
          {!urlErr && envBlocked && (
            <div style={{ fontSize: 11.5, color: "var(--danger)" }}>
              {getEnvBlockReason(envCheckItems, autoUpload, songOcr)}
            </div>
          )}
        </div>
      </div>

      {/* URL card */}
      <div className="card col" style={{ padding: "20px 22px", gap: 16, marginBottom: 18 }}>
        <div className="col gap-6">
          <label style={{ fontSize: 11.5, fontWeight: 700, color: "var(--muted)", letterSpacing: "0.04em" }}>
            YOUTUBE URL
          </label>
          <div className="row gap-8">
            <input
              className="input mono"
              value={url}
              onChange={e => { setUrl(e.target.value); setVodInfo(null); }}
              onKeyDown={e => e.key === "Enter" && handleCheck()}
              placeholder="https://www.youtube.com/watch?v=..."
              disabled={running}
            />
            <button
              className={`btn${checking ? "" : vodInfo && !vodInfo.error ? " primary" : ""}`}
              onClick={handleCheck}
              disabled={checking || running}
              style={!checking && vodInfo && !vodInfo.error
                ? { background: "var(--success)", borderColor: "var(--success)", color: "#fff", flexDirection: "row", flexWrap: "nowrap", whiteSpace: "nowrap" }
                : { flexDirection: "row", flexWrap: "nowrap", whiteSpace: "nowrap" }}
            >
              {checking ? "확인 중..." : vodInfo && !vodInfo.error ? <>{I.check}<span style={{ marginLeft: 5 }}>확인</span></> : "상태 확인"}
            </button>
          </div>
        </div>

        {/* VOD info panel */}
        {vodInfo && (
          vodInfo.error ? (
            <div className="row gap-8" style={{
              marginTop: 6, padding: "12px 14px", background: "var(--danger-soft)",
              borderRadius: "var(--r-md)", color: "var(--danger)", fontSize: 12,
            }}>
              {I.warn}
              <span><strong>URL 확인 실패:</strong> {vodInfo.error}</span>
            </div>
          ) : (
            <div className="row gap-16" style={{ marginTop: 6, padding: "12px 14px", background: "var(--surface-2)", borderRadius: "var(--r-md)" }}>
              <VodThumb w={108} h={64} />
              <div className="col grow gap-6" style={{ minWidth: 0 }}>
                <div className="row gap-6">
                  <span className="chip success">{I.check}{vodInfo.is_live ? "라이브" : "VOD"}</span>
                  {vodInfo.duration && <span className="chip mono num">{fmtSec(vodInfo.duration)}</span>}
                </div>
                <div style={{ fontSize: 13, fontWeight: 700, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {vodInfo.title}
                </div>
                <div className="muted" style={{ fontSize: 12 }}>{vodInfo.channel}</div>
              </div>
            </div>
          )
        )}

        <hr className="hr" style={{ margin: "6px 0" }} />

        {/* Input form */}
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 16 }}>
          <FormField label="시작 레이팅">
            <input className="input num" value={startRating}
              onChange={e => { setStartRating(e.target.value); setRatingError(""); }}
              placeholder={`예: 15000 (${RATING_MIN}~${RATING_MAX})`} disabled={running}
              style={ratingError ? { borderColor: "var(--danger)" } : {}} />
            <FieldError msg={ratingError} />
          </FormField>
          <FormField label="시작 시간">
            <input className="input mono" value={tStart}
              onChange={e => { setTStart(e.target.value); setTStartError(""); }}
              placeholder="HH:MM:SS (생략 시 처음부터)"
              disabled={running || vodInfo?.is_live}
              style={tStartError ? { borderColor: "var(--danger)" } : {}} />
            <FieldError msg={tStartError} />
          </FormField>
          <FormField label="종료 시간">
            <input className="input mono" value={tEnd}
              onChange={e => { setTEnd(e.target.value); setTEndError(""); }}
              placeholder="HH:MM:SS (생략 시 끝까지)"
              disabled={running || vodInfo?.is_live}
              style={tEndError ? { borderColor: "var(--danger)" } : {}} />
            <FieldError msg={tEndError} />
          </FormField>
        </div>

        <div className="col gap-10" style={{ paddingTop: 6 }}>
          <div className="card" style={{ padding: "10px 16px" }}>
            <div className="muted" style={{ fontSize: 11, marginBottom: 8 }}>파이프라인</div>
            <div className="row gap-20">
              <Toggle checked={clipSelect} onChange={setClipSelect} label="클립 선택" disabled={running}
                desc="스캔 완료 후 클립 선택 화면 표시 (OFF: 전체 자동 선택)" />
              <Toggle checked={ocrEdit} onChange={setOcrEdit} label="OCR 편집" disabled={running}
                desc="OCR 인식 결과를 직접 확인·수정 (OFF: 자동 적용)" />
            </div>
          </div>
          <div className="card" style={{ padding: "10px 16px" }}>
            <div className="muted" style={{ fontSize: 11, marginBottom: 8 }}>출력</div>
            <div className="row gap-20">
              <Toggle checked={autoUpload} onChange={setAutoUpload} label="자동 YouTube 업로드" disabled={running}
                desc={autoUpload ? "클립 생성 후 YouTube에 업로드 (하루 10개 제한)" : "클립만 저장, 업로드 안 함"} />
              <Toggle checked={songOcr} onChange={setSongOcr} label="곡 정보 추출" disabled={running}
                desc={songOcr ? "곡명 · 달성률 · 레벨 상수 인식" : "곡 정보 없이 레이팅만 기록"} />
            </div>
          </div>
        </div>
      </div>

      {/* Env check */}
      <EnvCheck items={envCheckItems} onRecheck={onRecheck}
        onYoutubeReauth={() => bridge && bridge.reauthenticate_youtube()}
        onJacketUpdate={() => bridge && bridge.update_jacket_index()}
        autoUpload={autoUpload} songOcr={songOcr} />

      {/* Stats */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 16, marginBottom: 18 }}>
        <Stat label="감지 구간"   val={detections.length} hint="" />
        <Stat label="추출된 클립" val={highlights.length} hint="highlights/ 폴더" />
        <Stat label="업로드"
          val={<><span className="num">{uploaded}</span><span style={{ fontSize: 18, color: "var(--muted)", fontWeight: 600 }}> / {highlights.length}</span></>}
          hint={hintUpl} />
      </div>

      {/* Phase progress bar */}
      {phaseInfo && <div style={{ marginBottom: 18 }}><PhaseBar phaseInfo={phaseInfo} isLive={vodInfo?.is_live} /></div>}

      {/* Log */}
      <div className="col gap-8">
        <div className="row between">
          <h3>실시간 로그</h3>
          <div className="row gap-6">
            <span className="muted" style={{ fontSize: 11 }}>print() → GUI 전달</span>
            <button className="btn ghost sm" onClick={handleClearLog}>{I.trash}지우기</button>
          </div>
        </div>
        <div className="console" style={{ maxHeight: 320, overflowY: "auto" }} ref={logRef}>
          {log.length === 0 && (
            <span style={{ color: "#53555b" }}>파이프라인이 시작되면 로그가 여기에 표시됩니다.</span>
          )}
          {log.map((l, i) => (
            <div key={i}>
              <span className="ts">[{l.t}]</span>{" "}
              <span className={l.lvl}>{l.msg}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

// ── Achievement rank helper ───────────────────────────────────────────────────

function achievementToRank(ach) {
  if (ach >= 100.5) return "SSS+";
  if (ach >= 100.0) return "SSS";
  if (ach >= 99.5)  return "SS+";
  if (ach >= 99.0)  return "SS";
  if (ach >= 98.0)  return "S+";
  if (ach >= 97.0)  return "S";
  if (ach >= 94.0)  return "AAA";
  if (ach >= 90.0)  return "AA";
  if (ach >= 80.0)  return "A";
  if (ach >= 75.0)  return "BBB";
  if (ach >= 70.0)  return "BB";
  if (ach >= 60.0)  return "B";
  if (ach >= 50.0)  return "C";
  return "D";
}

// ── Inline edit panel ─────────────────────────────────────────────────────────

function InlineEditPanel({ bridge, det, onSave, onCancel }) {
  const [songTitle,     setSongTitle]     = useState(det.song_title     || "");
  const [difficulty,    setDifficulty]    = useState(det.difficulty     || "");
  const [achievement,   setAchievement]   = useState(det.achievement    != null ? String(det.achievement)    : "");
  const [internalLevel, setInternalLevel] = useState(det.internal_level != null ? String(det.internal_level) : "");
  const [suggestions,   setSuggestions]   = useState([]);
  const [showSug,       setShowSug]       = useState(false);
  const [hovIdx,        setHovIdx]        = useState(-1);

  const achNum = achievement !== "" ? parseFloat(achievement) : null;
  const rank   = achNum != null && !isNaN(achNum) ? achievementToRank(achNum) : null;

  // 곡명 자동완성
  useEffect(() => {
    if (!bridge || !songTitle.trim()) { setSuggestions([]); setShowSug(false); return; }
    const t = setTimeout(() => {
      bridge.search_songs(songTitle.trim(), (json) => {
        let list = [];
        try { list = JSON.parse(json); } catch { return; }
        setSuggestions(list);
        setShowSug(list.length > 0);
        setHovIdx(-1);
      });
    }, 180);
    return () => clearTimeout(t);
  }, [bridge, songTitle]);

  // 곡명+난이도 → 내부 레벨 자동 입력
  useEffect(() => {
    if (!bridge || !songTitle || !difficulty) return;
    bridge.lookup_internal_level(songTitle, difficulty, (level) => {
      if (level) setInternalLevel(level);
    });
  }, [bridge, songTitle, difficulty]);

  function selectSuggestion(title) {
    setSongTitle(title);
    setSuggestions([]);
    setShowSug(false);
  }

  function handleTitleKeyDown(e) {
    if (!showSug || suggestions.length === 0) return;
    if (e.key === "ArrowDown") { e.preventDefault(); setHovIdx(i => Math.min(i + 1, suggestions.length - 1)); }
    if (e.key === "ArrowUp")   { e.preventDefault(); setHovIdx(i => Math.max(i - 1, 0)); }
    if (e.key === "Enter" && hovIdx >= 0) { e.preventDefault(); selectSuggestion(suggestions[hovIdx]); }
    if (e.key === "Escape") { setShowSug(false); }
  }

  const inputSt = {
    background: "var(--surface-2)", border: "1px solid var(--border)",
    borderRadius: "var(--r-sm)", padding: "8px 10px",
    color: "var(--text)", fontSize: 13, outline: "none",
    width: "100%", boxSizing: "border-box",
  };

  return (
    <div style={{ display: "flex", gap: 24, padding: "20px 24px", background: "var(--surface-1)", borderTop: "1px solid var(--border)" }}>
      <div className="col" style={{ alignItems: "center", gap: 8, flexShrink: 0 }}>
        <FrameThumb bridge={bridge} detId={det.id} size="lg" />
        <div className="muted" style={{ fontSize: 11, textAlign: "center" }}>결과화면 캡처</div>
        {!det.song_title && (
          <div style={{ fontSize: 11, padding: "2px 8px", borderRadius: "var(--r-sm)", background: "#fff3cd", color: "#856404" }}>
            ⚠ OCR 인식 실패
          </div>
        )}
      </div>
      <div className="col" style={{ flex: 1, gap: 14 }}>
        <div style={{ fontWeight: 700, fontSize: 13 }}>곡 정보 직접 입력</div>
        {/* 곡명 + 자동완성 드롭다운 */}
        <div className="col" style={{ gap: 4 }}>
          <label style={{ fontSize: 12, color: "var(--muted)" }}>곡명</label>
          <div style={{ position: "relative" }}>
            <input style={inputSt} value={songTitle}
              onChange={e => setSongTitle(e.target.value)}
              onKeyDown={handleTitleKeyDown}
              onBlur={() => setTimeout(() => setShowSug(false), 150)}
              onFocus={() => suggestions.length > 0 && setShowSug(true)}
              placeholder="곡명 검색..." />
            {showSug && (
              <div style={{
                position: "absolute", top: "100%", left: 0, right: 0, zIndex: 200,
                background: "var(--surface-2)", border: "1px solid var(--border)",
                borderRadius: "var(--r-sm)", boxShadow: "0 4px 12px rgba(0,0,0,0.3)",
                maxHeight: 220, overflowY: "auto",
              }}>
                {suggestions.map((s, i) => (
                  <div key={s} onMouseDown={() => selectSuggestion(s)} onMouseEnter={() => setHovIdx(i)}
                    style={{
                      padding: "8px 12px", fontSize: 13, cursor: "pointer",
                      background: i === hovIdx ? "var(--surface-3)" : "transparent",
                    }}>
                    {s}
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
          <div className="col" style={{ gap: 4 }}>
            <label style={{ fontSize: 12, color: "var(--muted)" }}>난이도</label>
            <select style={{ ...inputSt, cursor: "pointer" }} value={difficulty} onChange={e => setDifficulty(e.target.value)}>
              <option value="">-</option>
              {["BASIC", "ADVANCED", "EXPERT", "MASTER", "Re:MASTER"].map(d => (
                <option key={d} value={d}>{d}</option>
              ))}
            </select>
          </div>
          <div className="col" style={{ gap: 4 }}>
            <label style={{ fontSize: 12, color: "var(--muted)" }}>달성률 (%)</label>
            <input style={inputSt} type="number" step="0.0001" min="0" max="101.5"
              value={achievement} onChange={e => setAchievement(e.target.value)} placeholder="-" />
            {rank && <div style={{ fontSize: 11, color: "var(--muted)" }}>랭크: <strong>{rank}</strong> (자동 계산)</div>}
          </div>
        </div>
        <div className="col" style={{ gap: 4 }}>
          <label style={{ fontSize: 12, color: "var(--muted)" }}>내부 레벨</label>
          <input style={{ ...inputSt, width: "40%" }} type="number" step="0.1"
            value={internalLevel} onChange={e => setInternalLevel(e.target.value)} placeholder="-" />
          <div style={{ fontSize: 11, color: "var(--muted)" }}>곡명+난이도 확정 시 자동 입력</div>
        </div>
        <div className="row gap-8" style={{ marginTop: 4 }}>
          <button className="btn accent" onClick={() => onSave({
            song_title: songTitle || null, difficulty: difficulty || null,
            achievement: achNum, rank, internal_level: internalLevel !== "" ? parseFloat(internalLevel) : null,
          })}>✓ 저장</button>
          <button className="btn ghost" onClick={onCancel}>취소</button>
          <span className="muted" style={{ fontSize: 12 }}>저장 후 클립 제목·설명에 반영됩니다</span>
        </div>
      </div>
    </div>
  );
}

// ── Scan results screen ───────────────────────────────────────────────────────

function ScreenScan({ bridge, detections, canClip, onStartClipping, onQuit, clipSelect, ocrReady, onConfirmOcr, phaseInfo }) {
  const [sel, setSel]                 = useState(null);
  const [selectedIds, setSelectedIds] = useState(() => new Set(detections.map(d => d.id)));
  const [expandedId, setExpandedId]   = useState(null);
  const [rowEdits, setRowEdits]       = useState({});
  const autoStartedRef                = useRef(false);

  // clipSelect=false: 스캔 완료 즉시 전체 항목 자동 클립 생성
  useEffect(() => {
    if (!clipSelect && canClip && detections.length > 0 && !autoStartedRef.current) {
      autoStartedRef.current = true;
      onStartClipping(new Set(detections.map(d => d.id)));
    }
  }, [canClip, clipSelect]);

  // Auto-select new detections as they arrive; clear when detections reset to empty
  useEffect(() => {
    if (detections.length === 0) {
      setSelectedIds(new Set());
      return;
    }
    setSelectedIds(prev => {
      const next = new Set(prev);
      detections.forEach(d => next.add(d.id));
      return next;
    });
  }, [detections]);

  const total = detections.reduce((a, b) => a + b.delta, 0);
  const max   = detections.reduce((a, b) => Math.max(a, b.delta), 0);

  function toggleId(id) {
    setSelectedIds(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }

  function selectAll()   { setSelectedIds(new Set(detections.map(d => d.id))); }
  function deselectAll() { setSelectedIds(new Set()); }

  function handleStartClipping() {
    if (!onStartClipping || selectedIds.size === 0) return;
    onStartClipping(selectedIds);
  }

  function handleSaveEdit(id, vals) {
    setRowEdits(prev => ({ ...prev, [id]: vals }));
    setExpandedId(null);
  }

  function handleConfirm() {
    if (!onConfirmOcr) return;
    const payload = detections.map(d => {
      const edit = rowEdits[d.id] || {};
      const ach = edit.achievement !== undefined ? edit.achievement : (d.achievement ?? null);
      const rnk = ach != null ? achievementToRank(ach) : (edit.rank ?? d.rank ?? null);
      return {
        id: d.id,
        song_title:     edit.song_title     !== undefined ? edit.song_title     : (d.song_title     ?? null),
        difficulty:     edit.difficulty     !== undefined ? edit.difficulty     : (d.difficulty     ?? null),
        achievement:    ach,
        rank:           rnk,
        internal_level: edit.internal_level !== undefined ? edit.internal_level : (d.internal_level ?? null),
      };
    });
    onConfirmOcr(payload);
  }

  return (
    <div className="col" style={{ padding: "24px 28px", gap: 20 }}>
      <div className="row between" style={{ marginBottom: 18 }}>
        <div>
          <h1>스캔 결과</h1>
          <div className="muted" style={{ fontSize: 13, marginTop: 4 }}>
            {canClip
              ? "클립을 생성할 항목을 선택하세요"
              : "VOD에서 감지된 레이팅 상승 · 자동 다운로드 · 자동 업로드"}
          </div>
        </div>
        <div className="row gap-8" style={{ alignItems: "center" }}>
          <button className="btn" onClick={() => bridge && bridge.open_analysis_folder()}>{I.folder}<span style={{marginLeft:5}}>결과 사진</span></button>
          {ocrReady ? (
            <button
              className="btn lg"
              onClick={handleConfirm}
              style={{ background: "var(--accent)", borderColor: "var(--accent)", color: "var(--accent-fg)" }}
            >
              {I.check}확인 — 클립 생성 시작 ({detections.length})
            </button>
          ) : canClip && <>
            <button className="btn ghost sm" onClick={deselectAll}>전체 해제</button>
            <button className="btn ghost sm" onClick={selectAll}>전체 선택</button>
            {selectedIds.size === 0 && (
              <button className="btn danger lg" onClick={onQuit}>✕ 종료</button>
            )}
            <button
              className="btn lg"
              onClick={handleStartClipping}
              style={selectedIds.size > 0
                ? { background: "var(--accent)", borderColor: "var(--accent)", color: "var(--accent-fg)" }
                : { background: "var(--surface-3)", borderColor: "var(--border)", color: "var(--muted)", cursor: "default" }
              }
            >
              {I.play}클립 생성 시작 ({selectedIds.size})
            </button>
          </>}
        </div>
      </div>

      {phaseInfo && ["download", "ocr"].includes(phaseInfo.phase) && (
        <div style={{ marginBottom: 12 }}>
          <PhaseBar phaseInfo={phaseInfo} isLive={false} />
        </div>
      )}

      {(canClip || ocrReady) && (
        <div style={{
          padding: "12px 16px", background: "var(--success-soft)",
          borderRadius: "var(--r-md)", fontSize: 13, color: "var(--success)",
          fontWeight: 600,
        }}>
          {ocrReady
            ? `✅ OCR 분석 완료 — 곡 정보를 확인하고 수정한 뒤 확인 버튼을 눌러주세요.`
            : `✅ ${detections.length}건 감지 완료 — 클립을 생성할 항목을 선택하세요.`}
        </div>
      )}

      <div style={{ display: "grid", gridTemplateColumns: "repeat(3,1fr)", gap: 16, marginBottom: 18 }}>
        <Stat label="감지"      val={detections.length}                              hint="" />
        <Stat label="총 상승"   val={detections.length ? `+${total}` : "-"}          hint="세션 합계"    successVal={total > 0} />
        <Stat label="최대 상승" val={detections.length ? `+${max}`   : "-"}          hint="단일 플레이"  successVal={max > 0} />
      </div>

      <div className="card" style={{ overflow: "hidden" }}>
        {detections.length === 0 ? (
          <div style={{ padding: 40, textAlign: "center", color: "var(--muted)", fontSize: 13 }}>
            스캔이 완료되면 결과가 여기에 표시됩니다.
          </div>
        ) : (
          <table className="tbl">
            <thead>
              <tr>
                {canClip && <th style={{ width: 36 }} />}
                <th style={{ width: 40 }}>#</th>
                <th style={{ width: 100 }}>감지 시각</th>
                <th style={{ width: 100 }}>플레이 시작</th>
                <th style={{ width: 80 }}>모드</th>
                <th style={{ width: 150 }}>레이팅</th>
                <th style={{ width: 60, textAlign: "right" }}>변동</th>
                <th style={{ width: 160 }}>곡명</th>
                <th style={{ width: 90 }}>난이도</th>
                <th style={{ width: 70, textAlign: "right" }}>레벨</th>
                <th style={{ width: 100, textAlign: "right" }}>달성률</th>
                {ocrReady && <th style={{ width: 72 }} />}
              </tr>
            </thead>
            <tbody>
              {detections.map((d, i) => {
                const isExpanded = expandedId === d.id;
                const merged     = { ...d, ...(rowEdits[d.id] || {}) };
                const diffKey    = merged.difficulty
                  ? merged.difficulty.toLowerCase().replace("re:master", "remaster").replace(/\s/g, "")
                  : "";
                const colCount   = (canClip ? 1 : 0) + 10 + (ocrReady ? 1 : 0);
                return (
                  <React.Fragment key={d.id}>
                    <tr className={sel === d.id ? "selected" : ""}
                      onClick={() => setSel(d.id)} style={{ cursor: "pointer" }}>
                      {canClip && (
                        <td onClick={e => e.stopPropagation()} style={{ textAlign: "center" }}>
                          <input type="checkbox" checked={selectedIds.has(d.id)}
                            onChange={() => toggleId(d.id)}
                            style={{ cursor: "pointer" }} />
                        </td>
                      )}
                      <td className="num muted" style={{ fontSize: 12 }}>{String(i + 1).padStart(2, "0")}</td>
                      <td className="mono num" style={{ fontSize: 13, fontWeight: 600 }}>{d.t}</td>
                      <td className="mono num muted" style={{ fontSize: 12 }}>{d.play_t || "-"}</td>
                      <td>{d.mode
                        ? <span className={`chip ${modeClass(d.mode)}`} style={{ whiteSpace: "nowrap" }}>{modeLabel(d.mode)}</span>
                        : <span className="muted-2" style={{ fontSize: 12 }}>-</span>}
                      </td>
                      <td>
                        <div className="row gap-8" style={{ fontSize: 12.5 }}>
                          <span className="num muted">{d.before}</span>
                          <span className="muted-2">→</span>
                          <span className="num" style={{ fontWeight: 700 }}>{d.after}</span>
                        </div>
                      </td>
                      <td style={{ textAlign: "right" }}>
                        <span className="delta up" style={{ fontSize: 14 }}>+{d.delta}</span>
                      </td>
                      <td style={{ maxWidth: 200 }}>
                        {merged.song_title
                          ? <div className="col gap-4">
                              <span style={{ fontWeight: 700, fontSize: 13, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{merged.song_title}</span>
                              <div className="row gap-4">
                                {merged.rank && <span style={{ fontSize: 11, fontWeight: 700, color: "var(--success)" }}>{merged.rank}</span>}
                              </div>
                            </div>
                          : <span className="muted-2" style={{ fontSize: 12 }}>-</span>}
                      </td>
                      <td>
                        {merged.difficulty
                          ? <span className={`chip diff-${diffKey}`}>{merged.difficulty}</span>
                          : <span className="muted-2" style={{ fontSize: 12 }}>-</span>}
                      </td>
                      <td style={{ textAlign: "right" }}>
                        {merged.internal_level != null
                          ? <span className="mono num" style={{ fontSize: 13, fontWeight: 600 }}>{Number(merged.internal_level).toFixed(1)}</span>
                          : <span className="muted-2" style={{ fontSize: 12 }}>-</span>}
                      </td>
                      <td style={{ textAlign: "right" }}>
                        {merged.achievement != null
                          ? <span className="mono num" style={{ fontSize: 13, fontWeight: 600, color: "var(--accent)" }}>{Number(merged.achievement).toFixed(4)}%</span>
                          : <span className="muted-2" style={{ fontSize: 12 }}>-</span>}
                      </td>
                      {ocrReady && (
                        <td onClick={e => e.stopPropagation()} style={{ textAlign: "center" }}>
                          <button className="btn ghost sm"
                            onClick={() => setExpandedId(isExpanded ? null : d.id)}
                            style={{ padding: "3px 8px", fontSize: 12, whiteSpace: "nowrap" }}
                          >
                            {isExpanded ? "닫기" : "✏️ 수정"}
                          </button>
                        </td>
                      )}
                    </tr>
                    {isExpanded && (
                      <tr>
                        <td colSpan={colCount} style={{ padding: 0 }}>
                          <InlineEditPanel
                            bridge={bridge}
                            det={merged}
                            onSave={vals => handleSaveEdit(d.id, vals)}
                            onCancel={() => setExpandedId(null)}
                          />
                        </td>
                      </tr>
                    )}
                  </React.Fragment>
                );
              })}
            </tbody>
          </table>
        )}
      </div>

      <div className="muted" style={{ fontSize: 11.5, lineHeight: 1.8 }}>
        게임 모드·플레이 시작 시간은 역추적 후 확정됩니다.<br />
        곡명은 자켓 이미지 매칭으로, 난이도·달성률은 화면 인식으로 판별한 결과입니다. 신뢰도가 낮거나 인식에 실패한 항목은 빈 칸으로 표시됩니다. ✏️ 수정 버튼으로 직접 입력하세요.
      </div>
    </div>
  );
}

// ── Highlights screen ─────────────────────────────────────────────────────────

function ScreenHighlights({ bridge, highlights, setHighlights }) {
  const failed = highlights.filter(h => h.status === "failed");

  function handleRetry(file) {
    if (!bridge) return;
    setHighlights(prev => prev.map(h => h.file === file ? { ...h, status: "queued" } : h));
    bridge.retry_upload(file);
  }

  function handleRetryAll() {
    if (!bridge) return;
    bridge.retry_all_failed();
    setHighlights(prev => prev.map(h => h.status === "failed" ? { ...h, status: "queued" } : h));
  }

  function handleOpenFolder() {
    if (bridge) bridge.open_highlights_folder();
  }

  const counts = {
    uploaded:  highlights.filter(h => h.status === "uploaded").length,
    uploading: highlights.filter(h => h.status === "uploading").length,
    queued:    highlights.filter(h => h.status === "queued").length,
    failed:    failed.length,
  };
  const subtitle = [
    `${highlights.length}개 클립`,
    counts.uploaded  ? `${counts.uploaded} 완료`  : null,
    counts.uploading ? `${counts.uploading} 진행`  : null,
    counts.queued    ? `${counts.queued} 대기`     : null,
    counts.failed    ? `${counts.failed} 실패`     : null,
  ].filter(Boolean).join(" · ");

  return (
    <div className="col" style={{ padding: "24px 28px", gap: 20 }}>
      <div className="row between" style={{ marginBottom: 18 }}>
        <div>
          <h1>하이라이트</h1>
          <div className="muted" style={{ fontSize: 13, marginTop: 6 }}>
            highlights/ · {subtitle || "클립 없음"}
          </div>
        </div>
        <div className="row gap-8">
          <button className="btn" onClick={handleOpenFolder}>{I.folder}<span style={{marginLeft:5}}>폴더 열기</span></button>
          {failed.length > 0 && (
            <button className="btn accent" onClick={handleRetryAll}>
              {I.retry}실패 재시도 ({failed.length})
            </button>
          )}
        </div>
      </div>

      {highlights.length === 0 ? (
        <div className="card card-pad" style={{ textAlign: "center", color: "var(--muted)", fontSize: 13 }}>
          하이라이트 클립이 생성되면 여기에 표시됩니다.
        </div>
      ) : (
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
          {highlights.map(h => (
            <div key={h.file} className="card" style={{ padding: 18, minWidth: 0 }}>
              <div className="row gap-14" style={{ alignItems: "flex-start" }}>
                <div style={{ position: "relative", flexShrink: 0 }}>
                  {h.id ? <FrameThumb bridge={bridge} detId={h.id} /> : <VodThumb w={120} h={68} />}
                  <button onClick={() => bridge && bridge.open_clip(h.file)} title="클립 재생"
                          style={{ position: "absolute", top: 4, left: 4, width: 22, height: 22,
                                   borderRadius: 4, border: "none", cursor: "pointer",
                                   background: "rgba(0,0,0,0.6)", color: "#fff", fontSize: 11,
                                   display: "flex", alignItems: "center", justifyContent: "center" }}>▶</button>
                  {h.duration && (
                    <div style={{
                      position: "absolute", bottom: 4, right: 4,
                      background: "rgba(0,0,0,0.7)", color: "#fff",
                      padding: "1px 5px", borderRadius: 3,
                      fontSize: 10, fontFamily: "var(--font-mono)",
                    }}>{h.duration}</div>
                  )}
                </div>
                <div className="col grow gap-6" style={{ minWidth: 0 }}>
                  <div className="row between gap-8">
                    <div className="row gap-8" style={{ minWidth: 0 }}>
                      <span className="mono num" style={{ fontSize: 13, fontWeight: 700 }}>{h.t}</span>
                      <span className="delta up" style={{ fontSize: 13 }}>+{h.delta}</span>
                    </div>
                    <UploadChip status={h.status} />
                  </div>
                  <div className="row gap-6">
                    {h.mode
                      ? <span className={`chip ${modeClass(h.mode)}`}>{modeLabel(h.mode)}</span>
                      : <span className="chip" style={{ opacity: 0.75 }} title="역추적 실패 — 결과 3분 전으로 추정한 시작 지점">시작 추정</span>}
                    {h.size && <span className="chip mono num">{h.size}</span>}
                  </div>
                  <div className="muted mono" style={{ fontSize: 11, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {h.file}
                  </div>
                  {h.status === "uploading" && (
                    <div className="col gap-4" style={{ marginTop: 2 }}>
                      <div className="pbar"><i style={{ width: `${Math.round((h.progress || 0) * 100)}%` }} /></div>
                      <div className="muted num" style={{ fontSize: 11 }}>
                        {Math.round((h.progress || 0) * 100)}%
                      </div>
                    </div>
                  )}
                  {h.status === "failed" && (
                    <div className="row gap-6" style={{ marginTop: 2 }}>
                      {h.error && <span className="chip danger mono" style={{ fontSize: 10.5 }}>{h.error}</span>}
                      <button className="btn sm" onClick={() => handleRetry(h.file)}>{I.retry}재시도</button>
                    </div>
                  )}
                  {h.status === "uploaded" && h.url && (
                    <div className="row gap-8" style={{ marginTop: 2, fontSize: 11 }}>
                      <span className="muted mono" style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{h.url}</span>
                      {h.date && <><span className="muted">·</span><span className="muted">{h.date}</span></>}
                    </div>
                  )}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Frame thumbnail (OCR edit screen) ────────────────────────────────────────

function FrameThumb({ bridge, detId, size }) {
  const [url, setUrl] = useState(null);
  useEffect(() => {
    if (!bridge) return;
    bridge.get_result_frame_url(detId, (u) => setUrl(u || null));
  }, [bridge, detId]);
  if (!url) return <span className="muted-2" style={{ fontSize: 12 }}>-</span>;
  const w = size === "lg" ? 280 : 120;
  const h = size === "lg" ? 168 : 68;
  return (
    <img src={url} alt="" onClick={() => bridge && bridge.open_result_frame(detId)} style={{
      width: w, height: h, objectFit: "contain",
      borderRadius: 4, background: "var(--surface-2)", display: "block",
      cursor: "zoom-in",
    }} />
  );
}

// ── OCR edit screen ───────────────────────────────────────────────────────────

function ScreenOcrEdit({ bridge, ocrData, onConfirm }) {
  const [rows, setRows] = useState(() => ocrData.map(d => ({ ...d })));

  const inputSt = {
    background: "var(--surface-2)", border: "1px solid var(--border)",
    borderRadius: 4, padding: "3px 8px", fontSize: 12,
    color: "var(--fg)", fontFamily: "inherit", outline: "none",
    width: "100%", boxSizing: "border-box",
  };

  function update(id, field, value) {
    setRows(prev => prev.map(r => r.id === id ? { ...r, [field]: value } : r));
  }

  if (ocrData.length === 0) {
    return (
      <div className="col" style={{ padding: "24px 28px", gap: 20 }}>
        <h1>곡 정보 편집</h1>
        <div className="card card-pad" style={{ textAlign: "center", color: "var(--muted)", fontSize: 13 }}>
          OCR 데이터가 없습니다. 파이프라인을 실행하면 결과가 여기에 표시됩니다.
        </div>
      </div>
    );
  }

  return (
    <div className="col" style={{ padding: "24px 28px", gap: 20 }}>
      <div className="row between" style={{ marginBottom: 4 }}>
        <div>
          <h1>곡 정보 편집</h1>
          <div className="muted" style={{ fontSize: 13, marginTop: 4 }}>
            OCR로 인식된 곡 정보를 확인하고 필요하면 수정하세요.
          </div>
        </div>
        <button
          className="btn lg"
          style={{ background: "var(--accent)", borderColor: "var(--accent)", color: "var(--accent-fg)" }}
          onClick={() => onConfirm(rows)}
        >
          {I.play}확인 — 클립 생성 시작 ({rows.length})
        </button>
      </div>

      <div className="card" style={{ overflow: "auto" }}>
        <table className="tbl">
          <thead>
            <tr>
              <th style={{ width: 40 }}>#</th>
              <th style={{ width: 90 }}>시각</th>
              <th style={{ width: 160 }}>레이팅</th>
              <th>곡명</th>
              <th style={{ width: 110 }}>난이도</th>
              <th style={{ width: 72 }}>레벨</th>
              <th style={{ width: 110 }}>달성률 (%)</th>
              <th style={{ width: 130 }}>결과화면</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row, i) => (
              <tr key={row.id}>
                <td className="num muted" style={{ fontSize: 12 }}>{String(i + 1).padStart(2, "0")}</td>
                <td className="mono num" style={{ fontSize: 12 }}>{fmtSec(row.timestamp)}</td>
                <td>
                  <div className="row gap-8" style={{ fontSize: 12.5 }}>
                    <span className="num muted">{row.before}</span>
                    <span className="muted-2">→</span>
                    <span className="num" style={{ fontWeight: 700 }}>{row.after}</span>
                    <span className="delta up">+{row.change}</span>
                  </div>
                </td>
                <td>
                  <input style={inputSt} value={row.song_title || ""}
                    onChange={e => update(row.id, "song_title", e.target.value || null)}
                    placeholder="곡명 없음" />
                </td>
                <td>
                  <input style={inputSt} value={row.difficulty || ""}
                    onChange={e => update(row.id, "difficulty", e.target.value || null)}
                    placeholder="-" />
                </td>
                <td>
                  <input style={inputSt} type="number" step="0.1"
                    value={row.internal_level ?? ""}
                    onChange={e => update(row.id, "internal_level", e.target.value ? parseFloat(e.target.value) : null)}
                    placeholder="-" />
                </td>
                <td>
                  <input style={inputSt} type="number" step="0.0001"
                    value={row.achievement ?? ""}
                    onChange={e => update(row.id, "achievement", e.target.value ? parseFloat(e.target.value) : null)}
                    placeholder="-" />
                </td>
                <td><FrameThumb bridge={bridge} detId={row.id} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="muted" style={{ fontSize: 11.5 }}>
        OCR 신뢰도가 낮거나 인식에 실패한 항목은 빈 칸으로 표시됩니다. 빈 칸은 곡 정보 없음으로 처리됩니다.
      </div>
    </div>
  );
}

// ── Settings screen ───────────────────────────────────────────────────────────

// ── Manual screen helpers ────────────────────────────────────────────────────

const ManualSection = ({ num, title, subtitle, children }) => (
  <div className="col gap-16">
    <div className="col gap-4">
      <div className="mono num" style={{ fontSize: 11, color: "var(--accent)", fontWeight: 700, letterSpacing: "0.08em" }}>SECTION {num}</div>
      <h2>{title}</h2>
      {subtitle && <div className="muted" style={{ fontSize: 13.5, marginTop: 2, lineHeight: 1.6 }}>{subtitle}</div>}
    </div>
    {children}
  </div>
);

const ManualStep = ({ n, children }) => (
  <div className="row gap-12" style={{ alignItems: "flex-start", padding: "7px 0" }}>
    <div style={{
      flexShrink: 0, width: 22, height: 22, borderRadius: "50%",
      background: "var(--fg)", color: "#fff",
      display: "inline-flex", alignItems: "center", justifyContent: "center",
      fontSize: 11, fontWeight: 700, fontFamily: "var(--font-mono)",
      marginTop: 1,
    }}>{n}</div>
    <div style={{ flex: 1, fontSize: 13, lineHeight: 1.7, color: "var(--fg-2)" }}>{children}</div>
  </div>
);

const ManualNote = ({ kind = "info", children }) => {
  const map = {
    info:    { bg: "var(--info-soft)",    bd: "var(--info)",    icon: "💡" },
    warn:    { bg: "var(--warning-soft)", bd: "var(--warning)", icon: "⚠️" },
    danger:  { bg: "var(--danger-soft)",  bd: "var(--danger)",  icon: "❌" },
    success: { bg: "var(--success-soft)", bd: "var(--success)", icon: "✓" },
  }[kind];
  return (
    <div className="row gap-10" style={{
      padding: "11px 14px", background: map.bg,
      borderLeft: `3px solid ${map.bd}`,
      borderRadius: "var(--r-sm)", fontSize: 12.5,
      alignItems: "flex-start", lineHeight: 1.65,
    }}>
      <span style={{ flexShrink: 0, fontSize: 14, lineHeight: 1.4 }}>{map.icon}</span>
      <span style={{ color: "var(--fg-2)", flex: 1 }}>{children}</span>
    </div>
  );
};

const ManualCode = ({ children }) => (
  <code style={{
    fontFamily: "var(--font-mono)", fontSize: 11.5,
    padding: "1.5px 6px", background: "var(--surface-3)",
    borderRadius: 4, color: "var(--fg)", letterSpacing: 0,
  }}>{children}</code>
);

const ManualBlock = ({ children }) => (
  <pre style={{
    fontFamily: "var(--font-mono)", fontSize: 12,
    padding: "14px 16px", background: "#0b0d13", color: "#e3e4e8",
    borderRadius: "var(--r-md)", margin: 0, overflow: "auto", lineHeight: 1.7,
  }}>{children}</pre>
);

const ManualSubhead = ({ children }) => (
  <h3 style={{ marginTop: 8, fontSize: 14, fontWeight: 700 }}>{children}</h3>
);

const ManualLink = ({ href, children }) => (
  <a href={href} target="_blank" rel="noreferrer" style={{ color: "var(--accent)", textDecoration: "none", fontWeight: 600 }}>{children}</a>
);

// ── Manual section content ───────────────────────────────────────────────────

const ManualSystem = () => (
  <ManualSection num="01" title="시스템 요구사항" subtitle="실행에 필요한 최소 / 권장 환경입니다.">
    <table className="tbl" style={{ background: "transparent" }}>
      <thead><tr>
        <th style={{ width: 110 }}>항목</th>
        <th>최소</th>
        <th>권장</th>
      </tr></thead>
      <tbody>
        <tr><td style={{ fontWeight: 700 }}>OS</td><td>Windows 10 64-bit</td><td>Windows 11 64-bit</td></tr>
        <tr><td style={{ fontWeight: 700 }}>Python</td><td>3.10 이상</td><td>3.11 이상</td></tr>
        <tr><td style={{ fontWeight: 700 }}>RAM</td><td>4 GB</td><td>8 GB 이상</td></tr>
        <tr><td style={{ fontWeight: 700 }}>GPU</td><td>— (CPU로도 동작)</td><td>NVIDIA CUDA 지원 GPU</td></tr>
        <tr><td style={{ fontWeight: 700 }}>브라우저</td><td>Firefox</td><td>Firefox</td></tr>
      </tbody>
    </table>
    <ManualNote kind="warn"><strong>현재 Windows 전용</strong>입니다. macOS · Linux는 지원하지 않습니다.</ManualNote>
    <ManualNote kind="info">GPU(CUDA)가 없으면 CPU로 동작하지만 스캔 속도가 크게 느려집니다. NVIDIA GPU가 있다면 <ManualCode>setup/setup.bat</ManualCode> 실행 시 CUDA 버전이 자동으로 설치됩니다.</ManualNote>
  </ManualSection>
);

const ManualInstall = () => (
  <ManualSection num="02" title="최초 설치" subtitle="처음 한 번만 진행하세요.">
    <ManualSubhead>① Python 설치</ManualSubhead>
    <div className="col">
      <ManualStep n="1"><ManualLink href="https://www.python.org/downloads">python.org/downloads</ManualLink> 접속</ManualStep>
      <ManualStep n="2">노란색 <strong>Download Python 3.x.x</strong> 버튼 클릭</ManualStep>
      <ManualStep n="3">다운로드된 파일 실행</ManualStep>
      <ManualStep n="4">⚠️ <strong>반드시</strong> 설치 화면 맨 아래 <ManualCode>Add python.exe to PATH</ManualCode> 체크박스 체크</ManualStep>
      <ManualStep n="5"><strong>Install Now</strong> 클릭 → 설치 완료 후 <strong>Close</strong></ManualStep>
    </div>

    <ManualSubhead>② 패키지 설치</ManualSubhead>
    <div className="col">
      <ManualStep n="1"><ManualCode>setup</ManualCode> 폴더 안의 <ManualCode>setup.bat</ManualCode> 파일 더블클릭</ManualStep>
      <ManualStep n="2">검은 창이 열리면서 자동으로 설치됨</ManualStep>
      <ManualStep n="3"><strong>"설치 완료!"</strong> 메시지가 나오면 아무 키 눌러서 닫기</ManualStep>
    </div>
  </ManualSection>
);

const ManualYoutube = () => (
  <ManualSection num="03" title="YouTube 로그인 설정" subtitle="yt-dlp가 Firefox 브라우저의 로그인 세션을 직접 읽어 사용합니다.">
    <div style={{ fontSize: 13, lineHeight: 1.7, color: "var(--fg-2)" }}>
      별도 파일 내보내기가 필요 없고, Firefox에 로그인만 되어 있으면 됩니다.
    </div>
    <div className="col">
      <ManualStep n="1"><strong>Firefox</strong> 브라우저를 설치합니다 (없을 경우)</ManualStep>
      <ManualStep n="2">Firefox에서 <strong>YouTube</strong>(youtube.com)에 접속해 Google 계정으로 <strong>로그인</strong></ManualStep>
      <ManualStep n="3">그것으로 끝입니다 — 프로그램이 알아서 쿠키를 읽습니다</ManualStep>
    </div>
  </ManualSection>
);

const ManualGoogle1 = () => (
  <ManualSection num="04" title="Google Cloud 설정" subtitle="YouTube 자동 업로드에 필요한 Google Cloud 프로젝트를 만들고 API를 활성화합니다.">
    <ManualSubhead>① Google Cloud 프로젝트 만들기</ManualSubhead>
    <div className="col">
      <ManualStep n="1"><ManualLink href="https://console.cloud.google.com">console.cloud.google.com</ManualLink> 접속</ManualStep>
      <ManualStep n="2">업로드에 사용할 YouTube 채널의 <strong>Google 계정으로 로그인</strong></ManualStep>
      <ManualStep n="3">화면 상단 왼쪽의 <strong>"프로젝트 선택"</strong> 드롭다운 클릭</ManualStep>
      <ManualStep n="4">오른쪽 상단 <strong>"새 프로젝트"</strong> 클릭</ManualStep>
      <ManualStep n="5">프로젝트 이름 <ManualCode>maimai</ManualCode> 입력 → <strong>만들기</strong> 클릭</ManualStep>
      <ManualStep n="6">생성 알림 후 <strong>"프로젝트 선택"</strong> 클릭</ManualStep>
    </div>

    <ManualSubhead>② YouTube Data API 활성화</ManualSubhead>
    <div className="col">
      <ManualStep n="1">햄버거 메뉴(☰) → <strong>"API 및 서비스"</strong> → <strong>"라이브러리"</strong></ManualStep>
      <ManualStep n="2">검색창에 <ManualCode>YouTube Data API v3</ManualCode> 입력 → 클릭</ManualStep>
      <ManualStep n="3">파란색 <strong>"사용 설정"</strong> 버튼 클릭</ManualStep>
    </div>

    <ManualSubhead>③ OAuth 동의 화면 설정</ManualSubhead>
    <div className="col">
      <ManualStep n="1"><strong>"API 및 서비스"</strong> → <strong>"OAuth 동의 화면"</strong></ManualStep>
      <ManualStep n="2"><strong>"외부"</strong> 선택 → <strong>"만들기"</strong></ManualStep>
      <ManualStep n="3">앱 이름 <ManualCode>maimai</ManualCode>, 본인 Gmail을 사용자 지원 / 개발자 연락처 이메일에 입력 → <strong>"저장 후 계속"</strong></ManualStep>
      <ManualStep n="4">범위 화면 그대로 <strong>"저장 후 계속"</strong></ManualStep>
      <ManualStep n="5">테스트 사용자 → <strong>"+ ADD USERS"</strong> → 본인 Gmail 추가 → <strong>"저장 후 계속"</strong></ManualStep>
    </div>
  </ManualSection>
);

const ManualGoogle2 = () => (
  <ManualSection num="05" title="client_secret.json 발급" subtitle="OAuth 클라이언트 파일을 다운로드하고 프로그램에 등록합니다.">
    <ManualSubhead>① OAuth 클라이언트 ID 만들기</ManualSubhead>
    <div className="col">
      <ManualStep n="1"><strong>"사용자 인증 정보"</strong> → <strong>"+ 사용자 인증 정보 만들기"</strong> → <strong>"OAuth 클라이언트 ID"</strong></ManualStep>
      <ManualStep n="2">애플리케이션 유형 <strong>"데스크톱 앱"</strong> 선택 → <strong>"만들기"</strong></ManualStep>
      <ManualStep n="3">팝업창에서 <strong>"JSON 다운로드"</strong> 클릭</ManualStep>
      <ManualStep n="4">파일 이름을 <ManualCode>client_secret.json</ManualCode> 으로 변경</ManualStep>
      <ManualStep n="5"><ManualCode>config/credentials/</ManualCode> 폴더 안에 복사</ManualStep>
    </div>

    <ManualSubhead>② 처음 실행 시 Google 로그인</ManualSubhead>
    <div className="col">
      <ManualStep n="1">프로그램을 처음 실행하면 브라우저가 자동으로 열림</ManualStep>
      <ManualStep n="2">Google 로그인 → <strong>"계속"</strong> → <strong>"YouTube에서 동영상 관리"</strong> 체크 → <strong>"계속"</strong></ManualStep>
      <ManualStep n="3">브라우저 닫으면 자동으로 진행됨</ManualStep>
    </div>
    <ManualNote kind="info">이후 실행부터는 로그인 창이 뜨지 않습니다. 업로드가 갑자기 안 되면 메인 화면 환경 점검 패널의 <strong>🔑 재인증</strong> 버튼을 클릭하세요.</ManualNote>
  </ManualSection>
);

const ManualRun = () => (
  <ManualSection num="06" title="매번 실행하는 법" subtitle="설치가 끝났다면 매번 이 흐름만 따르면 됩니다.">
    <div className="col">
      <ManualStep n="1"><ManualCode>maimai_clipper.exe</ManualCode> 더블클릭</ManualStep>
      <ManualStep n="2">GUI 창이 열리면:
        <ul style={{ margin: "6px 0 0", paddingLeft: 20, lineHeight: 1.85 }}>
          <li><strong>YouTube URL</strong> 입력 후 <strong>상태 확인</strong></li>
          <li><strong>시작 레이팅</strong> 입력 (예: <ManualCode>14000</ManualCode>)</li>
          <li><strong>시작 / 종료 시간</strong> 입력 (생략하면 전체 구간 분석)</li>
          <li><strong>자동 YouTube 업로드</strong> 토글 설정</li>
          <li><strong>시작</strong> 버튼 클릭</li>
        </ul>
      </ManualStep>
      <ManualStep n="3">스캔 완료 → <strong>스캔 결과</strong> 화면으로 자동 이동
        <ul style={{ margin: "6px 0 0", paddingLeft: 20, lineHeight: 1.85 }}>
          <li>감지된 레이팅 상승 항목이 표시됩니다</li>
          <li>오른쪽 상단의 <strong>전체 선택 / 해제</strong> 또는 체크박스로 선택</li>
          <li><strong>클립 생성 시작 (N)</strong> 버튼 → 다운로드 · 클립 커팅 · 업로드 진행</li>
          <li>OCR 결과가 틀렸다면 <strong>✏️ 수정</strong> 버튼으로 직접 수정</li>
          <li>클립 생성 안 하려면 전체 해제 후 <strong>✕ 종료</strong> 버튼으로 닫기</li>
        </ul>
      </ManualStep>
    </div>
    <ManualNote kind="warn">라이브 모드에서는 분석 시작 전에 이미 진행 중인 플레이의 클립은 저장되지 않습니다. 녹화는 분석 시작 시점부터 이루어지므로, 반드시 플레이 전에 분석을 시작하세요.</ManualNote>
  </ManualSection>
);

const ManualLive = () => (
  <ManualSection num="07" title="라이브 모드 사용법" subtitle="실시간 방송 중 레이팅 상승을 감지해 클립을 자동으로 저장합니다.">
    <ManualNote kind="info">라이브 모드는 <strong>Phase 1 (수집)</strong>과 <strong>Phase 2 (처리)</strong>로 나뉩니다. 방송 중에 클립을 모으고, 방송 후에 선택·편집·업로드합니다.</ManualNote>
    <ManualSubhead>Phase 1 — 실시간 수집</ManualSubhead>
    <div className="col">
      <ManualStep n="1">YouTube 라이브 URL 입력 후 <strong>상태 확인</strong> → 시작 레이팅 입력 → <strong>시작</strong></ManualStep>
      <ManualStep n="2">레이팅 상승이 감지될 때마다 자동으로:
        <ul style={{ margin: "6px 0 0", paddingLeft: 20, lineHeight: 1.85 }}>
          <li>게임 시작 시점 역추적</li>
          <li>클립 파일 저장 (<ManualCode>highlights/_temp_live_*.mp4</ManualCode>)</li>
          <li>결과 화면 이미지 저장 (<strong>곡 정보 추출</strong> ON일 때)</li>
        </ul>
      </ManualStep>
      <ManualStep n="3">방송이 끝나면 <strong>■ 수집 중단</strong> 버튼 클릭</ManualStep>
    </div>
    <ManualNote kind="warn"><strong>■ 수집 중단</strong>은 전체 종료가 아닙니다. 수집을 멈추고 Phase 2로 넘어가는 버튼입니다. 완전히 종료하려면 Phase 2가 끝난 후 <strong>■ 중지</strong>를 누르세요.</ManualNote>
    <ManualSubhead>Phase 2 — 클립 선택 · 편집 · 업로드</ManualSubhead>
    <div className="col">
      <ManualStep n="4">수집 중단 후 <strong>스캔 결과</strong> 화면으로 자동 이동</ManualStep>
      <ManualStep n="5">저장할 클립을 체크박스로 선택 → <strong>클립 생성 시작</strong>
        <ul style={{ margin: "6px 0 0", paddingLeft: 20, lineHeight: 1.85 }}>
          <li>선택한 항목만 곡 정보 분석</li>
          <li>곡 정보가 틀렸다면 <strong>✏️ 수정</strong>으로 직접 수정</li>
          <li>자동 업로드 ON이면 YouTube에 업로드</li>
        </ul>
      </ManualStep>
    </div>
    <ManualNote kind="info">방송 시작 전에 프로그램을 먼저 켜야 합니다. 분석 시작 이전에 진행된 플레이의 클립은 저장되지 않습니다.</ManualNote>
  </ManualSection>
);

const ManualJacket = () => (
  <ManualSection num="08" title="곡명 인식 방식" subtitle="결과 화면의 자켓 이미지를 곡 DB의 자켓과 대조해 곡을 식별합니다.">
    <ManualNote kind="info">별도 설정이나 API 키가 필요 없습니다. 첫 분석 시 자켓 이미지를 자동으로 내려받습니다.</ManualNote>
    <div className="col">
      <ManualStep n="1">결과 화면에서 자켓 영역을 잘라 곡 DB의 자켓 <strong>1,600여 장</strong>과 대조</ManualStep>
      <ManualStep n="2">일치도가 충분히 높고 2등과 차이가 크면 그대로 확정</ManualStep>
      <ManualStep n="3">자켓이 비슷한 곡끼리 접전이면 <strong>곡명 OCR</strong>로 후보를 가림</ManualStep>
      <ManualStep n="4">일치하는 자켓이 없으면 <strong>미등록 곡</strong>으로 처리 (곡 DB에 아직 없는 신곡)</ManualStep>
    </div>
    <ManualNote kind="info">자켓 인덱스는 <ManualCode>cache/jacket_index.npz</ManualCode>에 저장됩니다(약 2.5MB). 곡 DB에 신곡이 추가되면 환경 점검 패널의 <strong>자켓 인덱스</strong> 항목에 알림이 뜨고, <strong>⬇️ 업데이트</strong> 버튼으로 새 곡의 자켓만 받을 수 있습니다.</ManualNote>
    <ManualNote kind="warn">우타게(宴会場) 보면은 레이팅에 반영되지 않아 인식 대상에서 제외됩니다.</ManualNote>
  </ManualSection>
);

const ManualCreds = () => (
  <ManualSection num="09" title="config/credentials 폴더에 있어야 하는 파일">
    <table className="tbl" style={{ background: "transparent" }}>
      <thead><tr>
        <th style={{ width: 200 }}>파일 이름</th>
        <th>설명</th>
        <th style={{ width: 120 }}>갱신</th>
      </tr></thead>
      <tbody>
        <tr>
          <td><ManualCode>client_secret.json</ManualCode></td>
          <td>Google API 인증 (YouTube 업로드용)</td>
          <td><span className="chip">한 번만</span></td>
        </tr>
        <tr>
          <td><ManualCode>youtube_token.json</ManualCode></td>
          <td>자동 생성됨</td>
          <td><span className="chip success">자동 갱신</span></td>
        </tr>
      </tbody>
    </table>
  </ManualSection>
);

const ManualErrors1 = () => (
  <ManualSection num="10" title="오류가 날 때 — 환경 점검" subtitle="프로그램 시작 시 8개 항목을 자동으로 확인합니다.">
    <ManualNote kind="info">프로그램 시작 시 <strong>환경 점검</strong>이 자동 실행됩니다. ffmpeg · Tesseract OCR · YOLO 모델 · YouTube 로그인 · Google 인증 파일 · 자켓 인덱스 · maimai DB · GPU 8개 항목을 확인합니다.</ManualNote>
    <table className="tbl" style={{ background: "transparent" }}>
      <thead><tr>
        <th>항목</th>
        <th style={{ width: 90 }}>분류</th>
        <th>시작 버튼 영향</th>
      </tr></thead>
      <tbody>
        <tr><td style={{ fontWeight: 600 }}>ffmpeg</td><td><span className="chip danger">필수</span></td><td>오류 시 시작 불가</td></tr>
        <tr><td style={{ fontWeight: 600 }}>Tesseract OCR</td><td><span className="chip danger">필수</span></td><td>오류 시 시작 불가</td></tr>
        <tr><td style={{ fontWeight: 600 }}>YOLO 모델</td><td><span className="chip danger">필수</span></td><td>오류 시 시작 불가</td></tr>
        <tr><td style={{ fontWeight: 600 }}>YouTube 로그인</td><td><span className="chip danger">필수</span></td><td>오류 시 시작 불가</td></tr>
        <tr><td style={{ fontWeight: 600 }}>Google 인증 파일</td><td><span className="chip warning">조건부</span></td><td>자동 업로드 ON일 때만 필수</td></tr>
        <tr><td style={{ fontWeight: 600 }}>자켓 인덱스</td><td><span className="chip warning">조건부</span></td><td>곡 정보 추출 ON일 때만 필수</td></tr>
        <tr><td style={{ fontWeight: 600 }}>maimai DB</td><td><span className="chip warning">조건부</span></td><td>곡 정보 추출 ON일 때만 필수</td></tr>
        <tr><td style={{ fontWeight: 600 }}>GPU(CUDA)</td><td><span className="chip">선택</span></td><td>없어도 시작 가능 (CPU 동작)</td></tr>
      </tbody>
    </table>
  </ManualSection>
);

const ManualErrors2 = () => (
  <ManualSection num="11" title="오류가 날 때 — 해결법" subtitle="흔한 오류와 해결 방법입니다.">
    <table className="tbl" style={{ background: "transparent" }}>
      <thead><tr>
        <th style={{ width: "45%" }}>오류</th>
        <th>해결법</th>
      </tr></thead>
      <tbody>
        <tr><td>ffmpeg / Tesseract / CUDA / torch 오류</td><td><ManualCode>setup/setup.bat</ManualCode> 다시 실행</td></tr>
        <tr><td>YOLO 모델 오류</td><td><ManualCode>best_nano.pt</ManualCode> 파일이 프로젝트 루트에 있는지 확인</td></tr>
        <tr><td>YouTube 로그인 경고</td><td>Firefox에서 youtube.com에 로그인했는지 확인</td></tr>
        <tr><td>Google 인증 파일 경고</td><td><ManualCode>config/credentials/client_secret.json</ManualCode> 준비 여부 확인</td></tr>
        <tr><td>자켓 인덱스 오류</td><td>인터넷 연결 확인 (자켓 다운로드에 필요). 첫 분석 시 자동으로 다시 시도합니다</td></tr>
        <tr><td>"Sign in to confirm you're not a bot"</td><td>Firefox에서 YouTube 로그인 확인</td></tr>
        <tr><td>곡 정보가 표시되지 않음</td><td><strong>곡 정보 추출</strong> 토글 ON 확인</td></tr>
        <tr><td>YouTube 업로드 실패 / 인증 오류</td><td>환경 점검 패널의 <strong>🔑 재인증</strong> 클릭</td></tr>
        <tr><td>Python을 찾을 수 없음</td><td>Python 재설치 (PATH 체크 확인)</td></tr>
      </tbody>
    </table>
  </ManualSection>
);

// ── Manual screen ────────────────────────────────────────────────────────────

function ScreenManual({ setScreen }) {
  const SECTIONS = [
    { id: "system",   label: "시스템 요구사항",        num: "01", Comp: ManualSystem  },
    { id: "install",  label: "최초 설치",              num: "02", Comp: ManualInstall },
    { id: "youtube",  label: "YouTube 로그인",        num: "03", Comp: ManualYoutube },
    { id: "google1",  label: "Google Cloud 설정",     num: "04", Comp: ManualGoogle1 },
    { id: "google2",  label: "client_secret.json 발급", num: "05", Comp: ManualGoogle2 },
    { id: "run",      label: "매번 실행하기",           num: "06", Comp: ManualRun     },
    { id: "live",     label: "라이브 모드",             num: "07", Comp: ManualLive    },
    { id: "jacket",   label: "곡명 인식 방식",           num: "08", Comp: ManualJacket  },
    { id: "creds",    label: "credentials 파일",       num: "09", Comp: ManualCreds   },
    { id: "errors1",  label: "오류 해결 — 환경 점검",    num: "10", Comp: ManualErrors1 },
    { id: "errors2",  label: "오류 해결 — 해결법",      num: "11", Comp: ManualErrors2 },
  ];

  const [active, setActive] = useState("system");
  const idx     = SECTIONS.findIndex(s => s.id === active);
  const current = SECTIONS[idx];
  const Body    = current.Comp;
  const prev    = idx > 0 ? SECTIONS[idx - 1] : null;
  const next    = idx < SECTIONS.length - 1 ? SECTIONS[idx + 1] : null;

  useEffect(() => {
    function onKey(e) {
      if (e.key === "ArrowLeft"  && prev) setActive(prev.id);
      if (e.key === "ArrowRight" && next) setActive(next.id);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [prev, next]);

  return (
    <div className="col" style={{ padding: "24px 28px" }}>
      <div style={{ marginBottom: 20 }}>
        <h1>사용설명서</h1>
        <div className="muted" style={{ fontSize: 13, marginTop: 6 }}>
          처음 설치부터 매번 실행, 오류 해결까지
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "220px 1fr", gap: 28, alignItems: "flex-start" }}>
        {/* TOC */}
        <nav className="col" style={{ position: "sticky", top: 24, gap: 2 }}>
          <div style={{ fontSize: 10.5, fontWeight: 700, color: "var(--muted)", padding: "0 12px 8px", letterSpacing: "0.08em" }}>
            목차
          </div>
          {SECTIONS.map(s => {
            const isActive = active === s.id;
            return (
              <button key={s.id} onClick={() => setActive(s.id)} className="row gap-10" style={{
                width: "100%", padding: "9px 14px",
                border: 0, background: isActive ? "var(--surface-2)" : "transparent",
                color: isActive ? "var(--fg)" : "var(--fg-2)",
                borderRadius: "var(--r-md)", cursor: "pointer",
                fontFamily: "inherit", fontSize: 13,
                fontWeight: isActive ? 700 : 500,
                textAlign: "left", alignItems: "center",
              }}>
                <span className="mono num" style={{
                  fontSize: 10.5,
                  color: isActive ? "var(--accent)" : "var(--muted-2)",
                  fontWeight: 700, letterSpacing: "0.06em", flexShrink: 0,
                }}>{s.num}</span>
                <span className="grow" style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{s.label}</span>
              </button>
            );
          })}
        </nav>

        {/* Content */}
        <div className="card" style={{ padding: "30px 36px", maxWidth: 780 }}>
          <Body />

          {/* Prev / Next */}
          <div className="row between" style={{ marginTop: 36, paddingTop: 20, borderTop: "1px solid var(--border)" }}>
            {prev ? (
              <button className="btn ghost" onClick={() => setActive(prev.id)} style={{ height: 50, padding: "0 16px" }}>
                <div className="col" style={{ alignItems: "flex-start", gap: 2, lineHeight: 1.2 }}>
                  <span style={{ fontSize: 11, color: "var(--muted)", fontWeight: 500 }}>← 이전</span>
                  <span style={{ fontSize: 13, fontWeight: 700 }}>{prev.label}</span>
                </div>
              </button>
            ) : <span />}
            {next ? (
              <button className="btn ghost" onClick={() => setActive(next.id)} style={{ height: 50, padding: "0 16px" }}>
                <div className="col" style={{ alignItems: "flex-end", gap: 2, lineHeight: 1.2 }}>
                  <span style={{ fontSize: 11, color: "var(--muted)", fontWeight: 500 }}>다음 →</span>
                  <span style={{ fontSize: 13, fontWeight: 700 }}>{next.label}</span>
                </div>
              </button>
            ) : <span />}
          </div>
        </div>
      </div>
    </div>
  );
}

// ── App root ──────────────────────────────────────────────────────────────────

function App() {
  const [bridge,         setBridge]         = useState(null);
  const [screen,         setScreen]         = useState("main");
  const [scanStatus,     setScanStatus]     = useState("idle");
  const [vodInfo,        setVodInfo]        = useState(null);
  const [log,            setLog]            = useState([]);
  const [detections,     setDetections]     = useState([]);
  const [highlights,     setHighlights]     = useState([]);
  const [phaseInfo,      setPhaseInfo]      = useState(null);
  const [envCheckItems,  setEnvCheckItems]  = useState(ENV_CHECK_INITIAL);
  const [errorBanner,    setErrorBanner]    = useState(null);
  const [autoUpload,     setAutoUpload]     = useState(true);
  const [songOcr,        setSongOcr]        = useState(true);
  const [ocrData,        setOcrData]        = useState([]);
  const [ocrReady,       setOcrReady]       = useState(false);
  const [clipSelect,     setClipSelect]     = useState(true);
  const [ocrEdit,        setOcrEdit]        = useState(true);
  const settingsLoadedRef                   = useRef(false);

  useEffect(() => {
    if (!bridge || !settingsLoadedRef.current) return;
    bridge.save_settings(JSON.stringify({ clipSelect, ocrEdit, autoUpload, songOcr }));
  }, [clipSelect, ocrEdit, autoUpload, songOcr]);

  useEffect(() => {
    if (typeof qt === "undefined") {
      console.warn("QWebChannel not available (dev mode)");
      return;
    }
    new QWebChannel(qt.webChannelTransport, (channel) => {
      const b = channel.objects.bridge;
      setBridge(b);

      b.status_result.connect((json) => setVodInfo(JSON.parse(json)));
      b.status_error.connect((msg)  => setVodInfo({ error: msg }));

      b.log_line.connect((json) => setLog(prev => {
        const next = [...prev, JSON.parse(json)];
        return next.length > 1000 ? next.slice(-1000) : next;   // 장시간 세션 렌더 성능 (A-27)
      }));

      b.detection_added.connect((json)  => setDetections(prev => [...prev, JSON.parse(json)]));
      b.detection_updated.connect((json) => {
        const upd = JSON.parse(json);
        setDetections(prev => prev.map(d => d.id === upd.id ? { ...d, ...upd } : d));
      });
      b.highlight_added.connect((json)  => setHighlights(prev => [...prev, JSON.parse(json)]));
      b.highlight_updated.connect((json) => {
        const upd = JSON.parse(json);
        setHighlights(prev => prev.map(h => h.file === upd.file ? { ...h, ...upd } : h));
      });

      b.phase_update.connect((json) => setPhaseInfo(JSON.parse(json)));

      b.scan_finished.connect(() => {
        setScanStatus(prev => prev === "scan_done" ? prev : "done");
        setPhaseInfo(null);
        setScreen("scan");
      });
      b.scan_done.connect(() => { setScanStatus("scan_done"); setScreen("scan"); });
      b.pipeline_error.connect((msg) => {
        setScanStatus("error");
        setPhaseInfo(null);
        setErrorBanner(prev => prev === msg ? prev : msg);
      });
      b.pipeline_stopped.connect(() => { setScanStatus("idle"); setPhaseInfo(null); });

      b.env_check_result.connect((json) => setEnvCheckItems(JSON.parse(json)));
      b.run_env_check();

      b.ocr_ready.connect((json) => {
        const payload = JSON.parse(json);
        setOcrData(payload);
        setDetections(prev => {
          const idMap = {};
          payload.forEach(p => { idMap[p.id] = p; });
          return prev.map(d => {
            const ocr = idMap[d.id];
            if (!ocr) return d;
            return {
              ...d,
              song_title:     ocr.song_title     != null ? ocr.song_title     : d.song_title,
              difficulty:     ocr.difficulty     != null ? ocr.difficulty     : d.difficulty,
              achievement:    ocr.achievement    != null ? ocr.achievement    : d.achievement,
              rank:           ocr.rank           != null ? ocr.rank           : d.rank,
              internal_level: ocr.internal_level != null ? ocr.internal_level : d.internal_level,
            };
          });
        });
        setOcrReady(true);
        setScreen("scan");
      });
      b.load_settings((settingsJson) => {
        const s = JSON.parse(settingsJson);
        setClipSelect(s.clipSelect !== undefined ? !!s.clipSelect : true);
        setOcrEdit(s.ocrEdit     !== undefined ? !!s.ocrEdit     : true);
        if (s.autoUpload !== undefined) setAutoUpload(!!s.autoUpload);
        if (s.songOcr    !== undefined) setSongOcr(!!s.songOcr);
        settingsLoadedRef.current = true;
      });
    });
  }, []);

  function handleRecheck() {
    if (!bridge) return;
    setEnvCheckItems(ENV_CHECK_INITIAL);
    bridge.run_env_check();
  }

  function handleStartClipping(selectedIds) {
    if (!bridge || selectedIds.size === 0) return;
    setOcrData([]);
    setOcrReady(false);
    setScanStatus("running");
    bridge.start_clipping(JSON.stringify({ ids: Array.from(selectedIds), songOcr }));
    setScreen("main");
  }

  function handleConfirmOcr(editedRows) {
    if (!bridge) return;
    bridge.confirm_ocr(JSON.stringify(editedRows));
    setOcrReady(false);
    setOcrData([]);
    setScreen("main");
    setScanStatus("running");
  }

  function handleQuit() {
    if (bridge) bridge.quit_app();
  }

  // 다운로드 완료 → 스캔 결과 화면 자동 이동
  useEffect(() => {
    if (phaseInfo?.phase === "cut") setScreen("scan");
  }, [phaseInfo?.phase]);

  // 탭 타이틀 업데이트
  useEffect(() => {
    const labels = { main: "메인", scan: "스캔 결과", highlights: "하이라이트", ocr_edit: "곡 정보 편집", manual: "사용설명서" };
    document.title = `maimai.clipper · ${labels[screen] || screen}`;
  }, [screen]);

  return (
    <div className="mm" style={{ display: "flex", height: "100%", background: "var(--bg)" }}>
      <Sidebar
        screen={screen} setScreen={setScreen}
        scanStatus={scanStatus}
        detections={detections}
        highlights={highlights}
        phaseInfo={phaseInfo}
      />
      <main style={{ flex: 1, overflowY: "auto" }}>
        {screen === "main" && (
          <ScreenMain
            bridge={bridge}
            scanStatus={scanStatus} setScanStatus={setScanStatus}
            vodInfo={vodInfo} setVodInfo={setVodInfo}
            log={log} setLog={setLog}
            detections={detections} setDetections={setDetections}
            highlights={highlights} setHighlights={setHighlights}
            phaseInfo={phaseInfo} setPhaseInfo={setPhaseInfo}
            envCheckItems={envCheckItems}
            onRecheck={handleRecheck}
            errorBanner={errorBanner}
            setErrorBanner={setErrorBanner}
            clipSelect={clipSelect}
            setClipSelect={setClipSelect}
            ocrEdit={ocrEdit}
            setOcrEdit={setOcrEdit}
            autoUpload={autoUpload}
            setAutoUpload={setAutoUpload}
            songOcr={songOcr}
            setSongOcr={setSongOcr}
          />
        )}
        {screen === "scan" && (
          <ScreenScan
            bridge={bridge}
            detections={detections}
            canClip={scanStatus === "scan_done"}
            onStartClipping={handleStartClipping}
            onQuit={handleQuit}
            clipSelect={clipSelect}
            ocrReady={ocrReady}
            onConfirmOcr={handleConfirmOcr}
            phaseInfo={phaseInfo}
          />
        )}
        {screen === "highlights" && (
          <ScreenHighlights
            bridge={bridge}
            highlights={highlights} setHighlights={setHighlights}
          />
        )}
        {screen === "ocr_edit" && (
          <ScreenOcrEdit
            bridge={bridge}
            ocrData={ocrData}
            onConfirm={handleConfirmOcr}
          />
        )}
        {screen === "manual" && <ScreenManual setScreen={setScreen} />}
      </main>
    </div>
  );
}

// 손상된 페이로드/렌더 예외 하나로 트리 전체가 언마운트되어 흰 화면이 되는 것을 방지
class ErrorBoundary extends React.Component {
  constructor(props) { super(props); this.state = { error: null }; }
  static getDerivedStateFromError(error) { return { error }; }
  componentDidCatch(error, info) { console.error("UI 오류:", error, info); }
  render() {
    if (this.state.error) {
      return (
        <div style={{ padding: 24, color: "var(--fg)" }}>
          <h3>화면 오류가 발생했습니다</h3>
          <pre style={{ whiteSpace: "pre-wrap", fontSize: 12, color: "var(--muted)" }}>
            {String(this.state.error)}
          </pre>
          <button onClick={() => this.setState({ error: null })}
                  style={{ marginTop: 12, padding: "6px 14px", cursor: "pointer" }}>
            다시 시도
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}

const root = ReactDOM.createRoot(document.getElementById("root"));
root.render(<ErrorBoundary><App /></ErrorBoundary>);
