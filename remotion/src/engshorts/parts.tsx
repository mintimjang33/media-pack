/**
 * 공학 쇼츠(engineering-shorts) 오버레이 부품 8종.
 *
 * 규격 = knowledge/30-playbooks/engineering-shorts-production.md
 * - 전부 **빨강 단일색**. 영문 라벨=권위 / 한글 라벨=이해 의 이중구조.
 * - 좌표는 전부 **퍼센트**(베이스 이미지가 바뀌어도 그대로 쓰기 위함).
 */
import React from "react";
import {Easing, interpolate, useCurrentFrame} from "remotion";
import {fontFamily} from "../components/parts";

export const RED = "#FF3B3B";                       // 경고·실패 전용
export const RED_DIM = "rgba(255,59,59,0.55)";
/** ★홀로그램 계측 팔레트 — 라인·라벨·게이지의 기본색 */
export const CY = "#38E8FF";
export const CY_DIM = "rgba(56,232,255,0.5)";
export const GLOW = "0 0 6px rgba(56,232,255,0.9), 0 0 18px rgba(56,232,255,0.45)";

// ★수치 가독성 기준 (1080 폭). 스킬 하한 = 치수 40 / 영문 30 / 캡션 34.
//   실사 배경 위에서 하한값은 겨우 읽히는 수준이라 한 단계씩 올려 잡았다.
//   ENG_PX 를 바꾸면 칩 폭 계수가 자동으로 따라간다(아래 chipW).
export const ENG_PX = 34;   // 영문 라벨
export const DIM_PX = 46;   // 치수 숫자
export const VAL_PX = 48;   // 게이지 값
// 숫자 뒤에 까는 다크 칩 — 실사 배경에선 textShadow 만으로 안 버틴다.
export const NUMCHIP = "rgba(4,14,20,0.72)";
export const GLOW_RED = "0 0 6px rgba(255,59,59,0.9), 0 0 18px rgba(255,59,59,0.4)";
/** ★4색 체계(SKILL STEP 6b) — 흐름=CY / 압력·위험=ORANGE~RED / 수분·냉각=WATER / 핵심수치=GOLD */
export const ORANGE = "#FF7A28";
export const WATER = "#CFE9FF";
export const GOLD = "#FFC83D";

/**
 * ★INFO 스틸 렌더 모드.
 * `still` = 애니메이션 진행도를 전부 1로 고정(마지막 상태), `noText` = 글자 부품을 뺀다.
 * INFO 는 생성모델에 넘어가는 목표 프레임이라 글자가 있으면 안 된다(SKILL STEP 6b).
 */
export const EngRenderCtx = React.createContext({still: false, noText: false});

/** 홀로그램 스캔 티어링 — 미세하게 떨리는 느낌 */
export const useFlicker = (delay = 0, amp = 0.06) => {
  const f = useCurrentFrame();
  const {still} = React.useContext(EngRenderCtx);
  if (still || f < delay) return 1;
  const n = Math.sin(f * 1.7) * 0.5 + Math.sin(f * 0.53) * 0.5;
  return 1 - amp * (0.5 + 0.5 * n);
};

/** 타이핑 진행도 — 글자가 한 자씩 찍히게 */
export const useType = (len: number, delay = 0, cps = 34) => {
  const f = useCurrentFrame();
  const {still} = React.useContext(EngRenderCtx);
  if (still) return len;
  const n = Math.floor(Math.max(0, f - delay) / (30 / cps));
  return Math.min(len, n);
};

/** 0→1 등장 진행도(선형). 페이드 전용. */
export const useIn = (delay = 0, dur = 8) => {
  const f = useCurrentFrame();
  const {still} = React.useContext(EngRenderCtx);
  if (still) return 1;
  return interpolate(f - delay, [0, dur], [0, 1], {extrapolateLeft: "clamp", extrapolateRight: "clamp"});
};

/** ★그려지는 진행도 — easeOutCubic. 선·화살표가 "쭉" 뻗는 느낌을 만든다. */
export const useDraw = (delay = 0, dur = 16) => {
  const f = useCurrentFrame();
  const {still} = React.useContext(EngRenderCtx);
  if (still) return 1;
  return interpolate(f - delay, [0, dur], [0, 1], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
    easing: Easing.out(Easing.cubic),
  });
};

/** 살짝 오버슈트하며 튀어나오는 진행도 — 칩·라벨용. */
export const usePop = (delay = 0, dur = 12) => {
  const f = useCurrentFrame();
  const {still} = React.useContext(EngRenderCtx);
  if (still) return 1;
  return interpolate(f - delay, [0, dur], [0, 1], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
    easing: Easing.bezier(0.16, 1.2, 0.3, 1),
  });
};

/** 점멸(강조). period 프레임 주기. */
export const useBlink = (delay = 0, period = 24) => {
  const f = useCurrentFrame();
  const {still} = React.useContext(EngRenderCtx);
  if (still || f < delay) return 1;
  return 0.55 + 0.45 * Math.abs(Math.sin(((f - delay) / period) * Math.PI));
};

/** 글자 부품이 스스로 빠지는 스위치 — INFO 모드에서 true */
export const useNoText = () => React.useContext(EngRenderCtx).noText;

type XY = {x: number; y: number}; // 0~100 (%)

// ─────────────────────────────────────────────────────────── DimLine
/** 치수선 — 두 점 사이에 화살표 + 숫자. */
export const DimLine: React.FC<{
  from: XY; to: XY; label: string; delay?: number; flip?: boolean;
  /** ★non-scaling-stroke 라 단위가 뷰포트 px 다. INFO(608px 폭)에선 0.4 이 서브픽셀이라 안 보인다 */
  width?: number;
}> = ({from, to, label, delay = 0, flip, width = 0.4}) => {
  const p = useDraw(delay, 16);              // 선: 쭉 뻗는다
  const lp = usePop(delay + 8, 12);          // 라벨: 선이 절반쯤 갔을 때 팝
  const noText = useNoText();                // INFO 모드 = 선만 남기고 숫자는 뺀다
  const mx = (from.x + to.x) / 2;
  const my = (from.y + to.y) / 2;
  const ang = (Math.atan2(to.y - from.y, to.x - from.x) * 180) / Math.PI;
  return (
    <div style={{position: "absolute", inset: 0}}>
      <svg viewBox="0 0 100 100" preserveAspectRatio="none" style={{position: "absolute", inset: 0, width: "100%", height: "100%"}}>
        <defs>
          <marker id="dimA" markerWidth="6" markerHeight="6" refX="3" refY="3" orient="auto">
            <path d="M0,0 L6,3 L0,6 Z" fill={CY} />
          </marker>
        </defs>
        <line
          x1={from.x} y1={from.y} x2={from.x + (to.x - from.x) * p} y2={from.y + (to.y - from.y) * p}
          stroke={CY} strokeWidth={width} vectorEffect="non-scaling-stroke"
          markerStart="url(#dimA)" markerEnd="url(#dimA)"
        />
      </svg>
      <div
        style={{
          position: "absolute", left: `${mx}%`, top: `${my}%`,
          opacity: noText ? 0 : lp,
          // ★한글 라벨은 절대 회전시키지 않는다 — 세로로 누우면 못 읽는다.
          //   (2026-09-03 실렌더: CUT11 "이격 확보" 가 90도 누워 나왔다.)
          //   숫자·단위 같은 ASCII 라벨만 가파른 선에서 회전을 허용한다.
          transform: `translate(-50%,${flip ? "20%" : "-140%"}) rotate(${
            Math.abs(ang) > 60 && !/[가-힣]/.test(label) ? -90 : 0
          }deg) scale(${0.82 + 0.18 * lp})`,
          color: CY, fontFamily, fontWeight: 900, fontSize: DIM_PX, letterSpacing: -0.5,
          paintOrder: "stroke fill",
          textShadow: `0 0 4px rgba(0,0,0,0.95), ${GLOW}`, whiteSpace: "nowrap",
          // ★실사 배경 위 숫자는 글로우만으로 안 읽힌다 — 다크 칩을 깐다.
          background: NUMCHIP, padding: "3px 12px",
          border: `1.5px solid ${CY_DIM}`,
        }}
      >
        {label}
      </div>
    </div>
  );
};

// ─────────────────────────────────────────────────────────── EngLabel
/** 영문 라벨 — 지시선이 그어진 뒤 글자가 타이핑된다. 홀로그램 계측 룩. */
export const EngLabel: React.FC<{
  at: XY; text: string; delay?: number; dir?: "left" | "right"; lead?: number; warn?: boolean;
}> = ({at, text, delay = 0, dir = "right", lead = 12, warn}) => {
  const p = useDraw(delay, 14);                       // 지시선
  const n = useType(text.length, delay + 8, 40);      // 글자
  const fl = useFlicker(delay);
  const noText = useNoText();                         // INFO 모드 = 지시선·앵커만 남긴다
  const C = warn ? RED : CY;
  // 칩 폭까지 계산해 클램프 — 앵커만 잡으면 긴 라벨이 경계를 넘는다.
  // 30px·letterSpacing 1.6 기준 글자당 약 1.85% + 패딩 3%.
  // ★칩 폭(%)은 fontSize 에서 유도한다 — 상수로 박아두면 크기를 올릴 때 조용히 틀어져
  //   긴 라벨이 화면 밖으로 나간다(2.25 는 30px 기준값이었다).
  const chipW = text.length * (2.25 * (ENG_PX / 30)) + 5;
  const raw = dir === "right" ? at.x + lead : at.x - lead;
  const ex =
    dir === "right"
      ? Math.max(4, Math.min(96 - chipW, raw))
      : Math.max(4 + chipW, Math.min(96, raw));
  return (
    <div style={{position: "absolute", inset: 0, opacity: fl}}>
      <svg viewBox="0 0 100 100" preserveAspectRatio="none" style={{position: "absolute", inset: 0, width: "100%", height: "100%"}}>
        <line x1={at.x} y1={at.y} x2={at.x + (ex - at.x) * p} y2={at.y}
              stroke={C} strokeWidth={0.3} vectorEffect="non-scaling-stroke" opacity={p > 0 ? 1 : 0} />
        <circle cx={at.x} cy={at.y} r={0.9 * Math.min(1, p * 3)} fill="none"
                stroke={C} strokeWidth={0.35} vectorEffect="non-scaling-stroke" />
        <circle cx={at.x} cy={at.y} r={0.35 * Math.min(1, p * 3)} fill={C} />
      </svg>
      <div
        style={{
          position: "absolute", left: `${ex}%`, top: `${at.y}%`,
          transform: `translate(${dir === "right" ? "0" : "-100%"},-50%)`,
          color: C, fontFamily, fontWeight: 800, fontSize: ENG_PX, letterSpacing: 1.6,
          padding: "7px 14px", whiteSpace: "nowrap",
          background: "rgba(4,14,20,0.62)",
          border: `1.5px solid ${warn ? RED_DIM : CY_DIM}`,
          borderLeft: `4px solid ${C}`,
          textShadow: warn ? GLOW_RED : GLOW,
          boxShadow: `0 0 18px ${warn ? "rgba(255,59,59,0.25)" : "rgba(56,232,255,0.25)"}`,
          opacity: noText || n <= 0 ? 0 : 1,
        }}
      >
        {text.slice(0, n)}
        {n < text.length ? <span style={{opacity: 0.85}}>_</span> : null}
      </div>
    </div>
  );
};

// ─────────────────────────────────────────────────────────── TempGauge
/** 온도 게이지 — min~max 범위 위에 구간을 칠한다. overlap 이 오면 흰색으로 번쩍. */
export const TempGauge: React.FC<{
  y: number; min: number; max: number; band: [number, number];
  // ★unit 은 필수 개념이다 — 예전엔 "℃" 가 박혀 있어서 장력 게이지에 쓰면
  //   단위가 거짓말을 했다. 기본값은 온도 유지(기존 호출부 무변경).
  title: string; delay?: number; overlap?: [number, number]; overlapDelay?: number; unit?: string;
  /** ★바 색. 기본 시안(온도·중립). 장력 게이지는 주황을 넘겨야 색 계약이 유지된다. */
  color?: string;
}> = ({y, min, max, band, title, delay = 0, overlap, overlapDelay = 0, unit = "℃", color}) => {
  const p = useIn(delay, 6);
  const fill = useDraw(delay + 3, 18);   // 바가 차오르는 진행도
  const ob = useBlink(overlapDelay, 18);
  const noText = useNoText();            // INFO 모드 = 바·패널만, 제목/수치는 뺀다
  const C = color ?? CY;
  const pct = (v: number) => ((v - min) / (max - min)) * 100;
  const L = pct(band[0]);
  const W = pct(band[1]) - L;
  return (
    <div style={{position: "absolute", left: "6%", right: "6%", top: `${y}%`, opacity: p, fontFamily,
                 background: "rgba(2,10,16,0.90)", border: `1.5px solid ${CY_DIM}`,
                 padding: "10px 14px 14px", boxShadow: "0 0 26px rgba(0,0,0,0.5)"}}>
      {noText ? null : (
        <div style={{display: "inline-block", background: "rgba(4,14,20,0.75)", color: CY, fontSize: 30, fontWeight: 900, letterSpacing: 1.4, marginBottom: 10, padding: "5px 14px", border: `1.5px solid ${CY_DIM}`, borderLeft: `4px solid ${CY}`, textShadow: GLOW}}>{title}</div>
      )}
      <div style={{position: "relative", height: 56, background: "rgba(4,14,20,0.62)", border: `2px solid ${CY_DIM}`, boxShadow: "0 0 22px rgba(56,232,255,0.22) inset"}}>
        <div style={{position: "absolute", left: `${L}%`, width: `${W * fill}%`, top: 0, bottom: 0, background: C, boxShadow: `0 0 16px ${C}`}} />
        {overlap ? (
          <div
            style={{
              position: "absolute", left: `${pct(overlap[0])}%`, width: `${pct(overlap[1]) - pct(overlap[0])}%`,
              top: -4, bottom: -4, background: "#fff", opacity: overlapDelay ? ob : 0,
              boxShadow: "0 0 26px rgba(255,255,255,0.9)",
            }}
          />
        ) : null}
        {/* ★unit 이 빈 문자열이면 값 숫자를 아예 그리지 않는다 — 장력처럼 실제 단위가
            없는 양에 "0–35" 를 띄우면 없는 정밀도를 주장하게 된다. 제목(축 라벨)만 남긴다. */}
        {noText || !unit ? null : (
          // ★값은 **오른쪽 끝**에 붙인다. 왼쪽(left:L%)에 두면 흐름 배치된 제목 칩과
          //   같은 자리에 겹쳐서 제목이 가려진다(2026-09-03 실렌더에서 "장력"이 사라졌다).
          <div style={{position: "absolute", right: 0, top: -58, color: "#fff", fontSize: VAL_PX, fontWeight: 900, textShadow: `0 0 4px rgba(0,0,0,0.95), ${GLOW}`, whiteSpace: "nowrap", background: NUMCHIP, padding: "2px 12px", border: `1.5px solid ${CY_DIM}`}}>
            {band[0]}–{band[1]}{unit}
          </div>
        )}
      </div>
    </div>
  );
};

// ─────────────────────────────────────────────────────────── FlowArrow
/** 흐름 화살표 — 여러 갈래를 한 번에. 열·압력·수분·힘 전부 이걸로. */
export const FlowArrow: React.FC<{
  arrows: {from: XY; to: XY}[]; delay?: number; stagger?: number; width?: number;
}> = ({arrows, delay = 0, stagger = 2, width = 0.55}) => (
  <svg viewBox="0 0 100 100" preserveAspectRatio="none" style={{position: "absolute", inset: 0, width: "100%", height: "100%"}}>
    <defs>
      <marker id="flowA" markerWidth="5" markerHeight="5" refX="4" refY="2.5" orient="auto">
        <path d="M0,0 L5,2.5 L0,5 Z" fill={CY} />
      </marker>
    </defs>
    {arrows.map((a, i) => (
      <FlowOne key={i} a={a} delay={delay + i * stagger} width={width} />
    ))}
  </svg>
);

const FlowOne: React.FC<{a: {from: XY; to: XY}; delay: number; width: number}> = ({a, delay, width}) => {
  const p = useDraw(delay, 18);
  return (
    <line
      x1={a.from.x} y1={a.from.y}
      x2={a.from.x + (a.to.x - a.from.x) * p} y2={a.from.y + (a.to.y - a.from.y) * p}
      stroke={CY} strokeWidth={width} vectorEffect="non-scaling-stroke"
      markerEnd="url(#flowA)" opacity={p > 0 ? 1 : 0}
    />
  );
};

// ─────────────────────────────────────────────────────────── RedX
/** 굵은 빨간 X — 부정/실패. */
export const RedX: React.FC<{at: XY; size?: number; delay?: number}> = ({at, size = 22, delay = 0}) => {
  // 두 획을 순차로 긋는다 — 손으로 X 치는 느낌.
  const a = useDraw(delay, 7);
  const b = useDraw(delay + 5, 7);
  const h = size / 2;
  return (
    <svg viewBox="0 0 100 100" preserveAspectRatio="none" style={{position: "absolute", inset: 0, width: "100%", height: "100%"}}>
      <g transform={`translate(${at.x} ${at.y})`}>
        <line x1={-h} y1={-h} x2={-h + size * a} y2={-h + size * a}
              stroke={RED} strokeWidth={2.6} vectorEffect="non-scaling-stroke" strokeLinecap="round" opacity={a > 0 ? 1 : 0} />
        <line x1={h} y1={-h} x2={h - size * b} y2={-h + size * b}
              stroke={RED} strokeWidth={2.6} vectorEffect="non-scaling-stroke" strokeLinecap="round" opacity={b > 0 ? 1 : 0} />
      </g>
    </svg>
  );
};

// ─────────────────────────────────────────────────────────── KoChapter
/** 큰 한글 챕터 라벨 — 이해 담당. 세로 화면 정보밀도용. */
export const KoChapter: React.FC<{
  text: string; y?: number; delay?: number; size?: number; accent?: boolean;
}> = ({text, y = 14, delay = 0, size = 96, accent}) => {
  const p = useIn(delay, 7);
  if (useNoText()) return null;              // INFO 모드 = 한글 라벨 자체가 없다
  return (
    <div
      style={{
        position: "absolute", left: 0, right: 0, top: `${y}%`, textAlign: "center",
        opacity: p, transform: `translateY(${(1 - p) * 26}px)`,
      }}
    >
      {/* ★다크 칩 필수 — accent(시안) 글자는 밝은 하늘 위에서 글로우만으로는 사라진다.
          2026-09-03 실렌더에서 CUT24 의 "처짐 ×2 → 장력 ÷2" 가 실제로 안 보였다. */}
      <span
        style={{
          display: "inline-block",
          fontFamily, fontWeight: 900, fontSize: size, lineHeight: 1.15, letterSpacing: -2,
          color: accent ? CY : "#fff",
          background: NUMCHIP, padding: "6px 20px",
          border: `1.5px solid ${accent ? CY_DIM : "rgba(255,255,255,0.28)"}`,
          paintOrder: "stroke fill",
          textShadow: accent
            ? `0 0 5px rgba(0,0,0,0.9), ${GLOW}`
            : "0 0 4px rgba(0,0,0,0.95), 0 0 10px rgba(0,0,0,0.85)",
        }}
      >
        {text}
      </span>
    </div>
  );
};

// ─────────────────────────────────────────────────────────── ThicknessProfile
/**
 * ★이 라인의 핵심 장치.
 * 피/벽 두께 프로파일 그래프. mode="flat" → 평평(상식) / "u" → 가장자리만 솟음(역발상).
 * 같은 컴포넌트가 morph 로 뒤집히는 게 "역발상의 시각 증명"이 된다.
 */
export const ThicknessProfile: React.FC<{
  mode: "flat" | "flatHigh" | "u" | "decay" | "stair"; y?: number; h?: number; delay?: number; morphFrames?: number; caption?: string;
  /** 비교용 유령 곡선 — "이래야 하는데" 를 점선으로 겹친다 */
  ghost?: "u" | "decay";
  /** ★축 이름. 예전엔 "두께" 가 박혀 있어 다른 물리량을 그릴 때 축이 거짓말을 했다. */
  axis?: string;
}> = ({mode, y = 60, h = 13, delay = 0, morphFrames = 16, caption, ghost, axis = "두께"}) => {
  const f = useCurrentFrame();
  const noText = useNoText();                // INFO 모드 = 곡선·면만, 축 라벨·캡션은 뺀다
  const t = interpolate(f - delay, [0, morphFrames], [0, 1], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: Easing.inOut(Easing.cubic),
  });
  const N = 61;
  const X0 = 10, W = 80;
  const target = (u: number) => {
    if (mode === "flat") return 0.30;
    if (mode === "flatHigh") return 0.58;
    // ★감쇠 — 왼쪽에서 시작해 급격히 떨어져 바닥에 붙는다(터널 진입 휘도처럼).
    //   `u` 로 그리면 "다시 올라간다"로 읽혀 데이터를 거짓말한다.
    if (mode === "decay") return 0.06 + 0.94 * Math.exp(-u * 6.2);
    // ★계단 — 같은 낙차를 단계로 나눠 내려간다(경계부→이행부→완화부→기본부).
    if (mode === "stair") {
      const k = Math.min(3, Math.floor(u * 4));
      return [1.0, 0.62, 0.34, 0.14][k];
    }
    const edge = Math.pow(Math.abs(u - 0.5) * 2, 2.6);   // 가장자리에서 급상승
    return 0.20 + edge * 0.80;
  };
  const base = mode === "u" ? 0.58 : (mode === "decay" || mode === "stair") ? 1.0 : 0.30;
  const pts: [number, number][] = Array.from({length: N}, (_, i) => {
    const u = i / (N - 1);
    const v = base + (target(u) - base) * t;
    return [X0 + u * W, y + h - v * h];
  });
  const top = pts.map(([x, yy]) => `${x},${yy}`).join(" ");
  const ghostPts = ghost
    ? Array.from({length: N}, (_, i) => {
        const u = i / (N - 1);
        const v = ghost === "decay"
          ? 0.06 + 0.94 * Math.exp(-u * 6.2)                       // "원래는 이렇게 떨어진다"
          : 0.20 + Math.pow(Math.abs(u - 0.5) * 2, 2.6) * 0.80;
        return `${X0 + u * W},${y + h - v * h}`;
      }).join(" ")
    : null;
  // ★선이 아니라 "면"으로 그린다 — 피 단면처럼 읽혀야 의미가 생긴다.
  const area = `${X0},${y + h} ${top} ${X0 + W},${y + h}`;
  return (
    <div style={{position: "absolute", inset: 0}}>
      <svg viewBox="0 0 100 100" preserveAspectRatio="none" style={{position: "absolute", inset: 0, width: "100%", height: "100%"}}>
        {/* 패널 — 피사체와 분리해 그래프임을 분명히 한다 */}
        <rect x={X0 - 2} y={y - 3} width={W + 4} height={h + 5} fill="rgba(4,14,20,0.78)"
              stroke={CY_DIM} strokeWidth={0.4} vectorEffect="non-scaling-stroke" />
        {/* 기준선(안쪽 = 소가 닿는 면) */}
        <line x1={X0} y1={y + h} x2={X0 + W} y2={y + h} stroke={CY} strokeWidth={0.5}
              vectorEffect="non-scaling-stroke" opacity={0.9} />
        {/* 두께 면 */}
        <polygon points={area} fill={CY} fillOpacity={0.34} />
        {ghostPts ? (
          <polyline points={ghostPts} fill="none" stroke="#fff" strokeWidth={1.4}
                    strokeDasharray="2 1.6" vectorEffect="non-scaling-stroke"
                    strokeLinejoin="round" opacity={0.8} />
        ) : null}
        <polyline points={top} fill="none" stroke={CY} strokeWidth={2.4}
                  vectorEffect="non-scaling-stroke" strokeLinejoin="round" />
      </svg>
      {/* 축 힌트 — 이게 없으면 무슨 그래프인지 모른다 */}
      {noText ? null : (
        <div style={{position: "absolute", left: "2%", top: `${y - 1}%`, color: CY, fontFamily,
                     fontWeight: 900, fontSize: 30, letterSpacing: 1, textShadow: GLOW,
                     background: NUMCHIP, padding: "2px 8px"}}>
          {axis}
        </div>
      )}
      {caption && !noText ? (
        <div
          style={{
            position: "absolute", left: 0, right: 0, top: `${y - 6}%`, textAlign: "center",
            color: "#fff", fontFamily, fontWeight: 900, fontSize: 38, letterSpacing: -0.5,
            paintOrder: "stroke fill",
            textShadow: "0 0 4px rgba(0,0,0,0.95), 0 0 9px rgba(0,0,0,0.85), 0 3px 14px rgba(0,0,0,0.6)",
          }}
        >
          {caption}
        </div>
      ) : null}
    </div>
  );
};

// ─────────────────────────────────────────────────────────── Subtitle
/**
 * 자막 폭(em) 추정 — 한글은 전각 1.0, 공백·라틴·문장부호는 좁다.
 * ★파이썬 쪽 자막 쪼개기(mk_cuts)와 같은 계수를 쓴다. 한쪽만 바꾸면 줄이 넘친다.
 */
export const subEm = (t: string): number =>
  [...t].reduce(
    (a, ch) =>
      a +
      (ch === " " ? 0.31 : /[0-9A-Za-z]/.test(ch) ? 0.55 : /[.,?!·~'"]/.test(ch) ? 0.42 : 1),
    0
  );

/** 자막 가용 폭(px) — 1080 폭에서 좌우 6% 여백을 뺀 값 */
export const SUB_AVAIL = 950;

// ─────────────────────────────────────────────────────────── Subtitle
/**
 * 하단 자막 번인. ★**항상 한 줄**(2026-08-20 사용자 지시).
 * 줄바꿈 대신 폭에 맞춰 글자 크기를 줄인다 — 줄 수가 변하면 자막 덩어리의 세로 위치가
 * 컷마다 출렁여서 읽는 눈이 따라가다 끊긴다. 한 줄로 고정하면 기준선이 안 움직인다.
 * 한 줄에 안 들어갈 만큼 긴 문장은 **시간으로 쪼개서**(EngShorts 가 순차 재생) 넘긴다.
 */
export const Subtitle: React.FC<{text: string}> = ({text}) => {
  if (useNoText()) return null;              // INFO 모드 = 자막 없음
  const size = Math.max(30, Math.min(54, SUB_AVAIL / subEm(text)));
  return (
  <>
  <div
    style={{
      position: "absolute", left: "3%", right: "3%", top: "78%",
      textAlign: "center", fontFamily, fontWeight: 800, fontSize: size, lineHeight: 1.32,
      color: "#fff", whiteSpace: "nowrap",
      // ★WebkitTextStroke 금지 — 획을 안쪽으로 파먹어 한글이 뭉개진다.
      // paint-order 로 외곽선을 바깥에 그리고, 그림자는 대비용으로만 얕게.
      WebkitTextStrokeWidth: 0,
      paintOrder: "stroke fill",
      textShadow:
        "0 0 3px rgba(0,0,0,0.95), 0 0 3px rgba(0,0,0,0.95), 0 0 6px rgba(0,0,0,0.9), 0 2px 10px rgba(0,0,0,0.7)",
    }}
  >
    {text}
  </div>
  <div style={{position: "absolute", left: "38%", right: "38%", top: "76.4%", height: 2,
               background: `linear-gradient(90deg, transparent, ${CY}, transparent)`,
               boxShadow: "0 0 12px rgba(56,232,255,0.7)"}} />
  </>
  );
};

// ═══════════════════════════════════════════════════ INFO 전용 그래픽 3종
// 글자가 없는 "장면 공간 그래픽". CLEAN→INFO 보간의 목표 상태를 그리는 데 쓴다.
// (SKILL STEP 6b — 앵커 → 선 → 화살촉 → 발광 순으로 조립되게 요소를 분리해 둔다)

/** 앵커 — 실제 구조 위 한 점을 집는 빈 원. 조립의 출발점. */
export const Anchor: React.FC<{at: XY; r?: number; delay?: number; color?: string; width?: number}> = ({
  at, r = 1.1, delay = 0, color = CY, width = 2.5,
}) => {
  const p = usePop(delay, 10);
  return (
    <svg viewBox="0 0 100 100" preserveAspectRatio="none" style={{position: "absolute", inset: 0, width: "100%", height: "100%"}}>
      <circle cx={at.x} cy={at.y} r={r * p} fill="none" stroke={color} strokeWidth={width}
              vectorEffect="non-scaling-stroke" opacity={p} />
      <circle cx={at.x} cy={at.y} r={r * 0.3 * p} fill={color} opacity={p} />
    </svg>
  );
};

/**
 * 유선 — 곡면을 따라 흐르는 선 다발.
 * pts 를 지나는 부드러운 곡선을 offset 만큼 평행 이동해 n 갈래로 뽑는다.
 * ★평행 직선이 아니라 **피사체 곡률을 따라가야** 생성모델이 장면에 녹여준다.
 */
export const Streamline: React.FC<{
  pts: XY[]; lanes?: number; gap?: number; delay?: number; color?: string; width?: number;
}> = ({pts, lanes = 3, gap = 3.2, delay = 0, color = CY, width = 3}) => {
  const p = useDraw(delay, 18);
  // 법선 방향 = 첫→끝 벡터의 수직
  const dx = pts[pts.length - 1].x - pts[0].x;
  const dy = pts[pts.length - 1].y - pts[0].y;
  const L = Math.hypot(dx, dy) || 1;
  const nx = -dy / L;
  const ny = dx / L;
  // ⛔ dasharray 로 그리지 않는다 — non-scaling-stroke 아래선 dash 단위도 뷰포트 px 라
  // pathLength 를 무시하고 1px 점선이 돼버린다(실측). 대신 점을 잘라서 그린다.
  const path = (k: number) => {
    const q = pts.map((v) => ({x: v.x + nx * gap * k, y: v.y + ny * gap * k}));
    const span = (q.length - 1) * p;
    const last = Math.floor(span);
    const frac = span - last;
    let d = `M ${q[0].x} ${q[0].y}`;
    for (let i = 1; i <= Math.min(last, q.length - 1); i++) {
      const a = q[i - 1];
      const b = q[i];
      d += ` Q ${a.x + (b.x - a.x) * 0.5} ${a.y} ${b.x} ${b.y}`;
    }
    if (last < q.length - 1 && frac > 0) {
      const a = q[last];
      const b = q[last + 1];
      const ex = a.x + (b.x - a.x) * frac;
      const ey = a.y + (b.y - a.y) * frac;
      d += ` Q ${a.x + (ex - a.x) * 0.5} ${a.y} ${ex} ${ey}`;
    }
    return d;
  };
  const mid = (lanes - 1) / 2;
  return (
    <svg viewBox="0 0 100 100" preserveAspectRatio="none" style={{position: "absolute", inset: 0, width: "100%", height: "100%"}}>
      {Array.from({length: lanes}, (_, i) => (
        <path
          key={i} d={path(i - mid)} fill="none" stroke={color} strokeWidth={width}
          vectorEffect="non-scaling-stroke" strokeLinecap="round" strokeLinejoin="round"
          opacity={0.55 + 0.45 * (1 - Math.abs(i - mid) / (mid || 1))}
        />
      ))}
    </svg>
  );
};

/** 에너지 존 — 힘·열·압력이 몰리는 자리를 번지는 발광으로 표시. 경고색 기본. */
export const HotZone: React.FC<{
  at: XY; rx?: number; ry?: number; delay?: number; color?: string; strength?: number;
}> = ({at, rx = 16, ry = 9, delay = 0, color = ORANGE, strength = 0.75}) => {
  const p = useIn(delay, 12);
  const id = `hz${Math.round(at.x)}_${Math.round(at.y)}`;
  return (
    <svg viewBox="0 0 100 100" preserveAspectRatio="none" style={{position: "absolute", inset: 0, width: "100%", height: "100%"}}>
      <defs>
        <radialGradient id={id}>
          <stop offset="0%" stopColor={color} stopOpacity={strength * p} />
          <stop offset="40%" stopColor={color} stopOpacity={strength * 0.82 * p} />
          <stop offset="72%" stopColor={color} stopOpacity={strength * 0.34 * p} />
          <stop offset="100%" stopColor={color} stopOpacity={0} />
        </radialGradient>
      </defs>
      <ellipse cx={at.x} cy={at.y} rx={rx} ry={ry} fill={`url(#${id})`} />
    </svg>
  );
};
