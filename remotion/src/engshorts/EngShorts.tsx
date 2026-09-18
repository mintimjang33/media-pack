/**
 * 공학 쇼츠(engineering-shorts) 메인 컴포지션 — 9:16 / 1080x1920.
 *
 * 데이터 주도: cuts[] 를 주면 컷마다 베이스 이미지 + 카메라 + 오버레이를 합성한다.
 * 컷 N개 ≠ 이미지 N장 — 베이스 소수를 카메라·오버레이로 쪼개는 게 이 포맷의 방식.
 */
import React from "react";
import {AbsoluteFill, Img, OffthreadVideo, Sequence, interpolate, staticFile, useCurrentFrame, useVideoConfig} from "remotion";
import {
  DimLine, EngLabel, TempGauge, FlowArrow, RedX, KoChapter, ThicknessProfile, Subtitle, subEm,
} from "./parts";

type XY = {x: number; y: number};

export type Cam = {
  /** 시작/끝 배율. 1 = 원본 */
  from?: number; to?: number;
  /** 팬 중심 (0~100%). 기본 화면 중앙 */
  cx?: number; cy?: number;
};

export type Ov =
  | {t: "dim"; from: XY; to: XY; label: string; delay?: number; flip?: boolean}
  | {t: "eng"; at: XY; text: string; delay?: number; dir?: "left" | "right"; warn?: boolean}
  | {t: "gauge"; y: number; min: number; max: number; band: [number, number]; title: string; delay?: number; overlap?: [number, number]; overlapDelay?: number; unit?: string; color?: string}
  | {t: "flow"; arrows: {from: XY; to: XY}[]; delay?: number; stagger?: number; width?: number}
  | {t: "x"; at: XY; size?: number; delay?: number}
  | {t: "ko"; text: string; y?: number; delay?: number; size?: number; accent?: boolean}
  | {t: "profile"; mode: "flat" | "flatHigh" | "u"; y?: number; delay?: number; caption?: string; ghost?: "u"; axis?: string}
  | {t: "dim2"; from: XY; to: XY; label: string; delay?: number};

export type Cut = {
  /** 시작 초 */
  s: number;
  /** 길이 초 */
  d: number;
  /** 베이스 이미지 키 (images 맵의 키). 없으면 직전 컷 유지 */
  base?: string;
  cam?: Cam;
  ov?: Ov[];
  /** 이 컷 구간의 자막. 배열이면 컷 안에서 순차 재생(한 줄에 안 들어가는 문장을 시간으로 쪼갠 것) */
  sub?: string | string[];
  /** 암전 */
  black?: boolean;
  /** 좌우 분할: 두 베이스를 나란히 */
  split?: [string, string];
  /** ★H3 로컬 생성 영상 클립 — public/ 상대경로. 정지 이미지로 못 하는 것(김·육즙·터짐)에만 쓴다 */
  clip?: string;
  /** 클립 시작 오프셋(초) */
  clipFrom?: number;
};

export type EngShortsProps = {
  /** 키 → public/ 상대경로 */
  images: Record<string, string>;
  cuts: Cut[];
  fps?: number;
};

const src = (p: string) => (p.startsWith("http") ? p : staticFile(p));

/** 베이스 이미지 + 카메라(줌·팬). cover 로 채우고 스케일/이동. */
const Base: React.FC<{path: string; cam?: Cam; dur: number}> = ({path, cam, dur}) => {
  const f = useCurrentFrame();
  const from = cam?.from ?? 1.0;
  const to = cam?.to ?? from;
  const k = interpolate(f, [0, Math.max(1, dur - 1)], [from, to], {extrapolateRight: "clamp"});
  const cx = cam?.cx ?? 50;
  const cy = cam?.cy ?? 50;
  return (
    <AbsoluteFill style={{overflow: "hidden", backgroundColor: "#d9d6d0"}}>
      <Img
        src={src(path)}
        style={{
          width: "100%", height: "100%", objectFit: "cover",
          transform: `scale(${k})`, transformOrigin: `${cx}% ${cy}%`,
        }}
      />
    </AbsoluteFill>
  );
};

/**
 * 컷 하나 안에서 자막 여러 조각을 순차 재생한다.
 * 길이는 글자 폭(em)에 비례해 나누되 조각당 최소 18프레임 — 그보다 짧으면 읽기 전에 사라진다.
 */
const SubSeq: React.FC<{texts: string[]; dur: number}> = ({texts, dur}) => {
  const w = texts.map(subEm);
  const total = w.reduce((a, b) => a + b, 0) || 1;
  const MIN = 18;
  let raw = w.map((x) => Math.max(MIN, Math.round((x / total) * dur)));
  // 합이 컷 길이를 넘으면 긴 조각부터 깎는다(마지막 조각이 잘려 사라지는 걸 막는다)
  let over = raw.reduce((a, b) => a + b, 0) - dur;
  while (over > 0) {
    const i = raw.indexOf(Math.max(...raw));
    if (raw[i] <= MIN) break;
    raw[i] -= 1;
    over -= 1;
  }
  let at = 0;
  return (
    <>
      {texts.map((t, i) => {
        const from = at;
        const d = i === texts.length - 1 ? Math.max(1, dur - from) : raw[i];
        at += d;
        return (
          <Sequence key={i} from={from} durationInFrames={Math.max(1, d)} layout="none">
            <Subtitle text={t} />
          </Sequence>
        );
      })}
    </>
  );
};

const Overlay: React.FC<{o: Ov}> = ({o}) => {
  switch (o.t) {
    case "dim":
    case "dim2":
      return <DimLine from={o.from} to={o.to} label={o.label} delay={o.delay} flip={(o as any).flip} />;
    case "eng":
      return <EngLabel at={o.at} text={o.text} delay={o.delay} dir={o.dir} warn={o.warn} />;
    case "gauge":
      return <TempGauge y={o.y} min={o.min} max={o.max} band={o.band} title={o.title} delay={o.delay} overlap={o.overlap} overlapDelay={o.overlapDelay} unit={o.unit} color={o.color} />;
    case "flow":
      return <FlowArrow arrows={o.arrows} delay={o.delay} stagger={o.stagger} width={o.width} />;
    case "x":
      return <RedX at={o.at} size={o.size} delay={o.delay} />;
    case "ko":
      return <KoChapter text={o.text} y={o.y} delay={o.delay} size={o.size} accent={o.accent} />;
    case "profile":
      return <ThicknessProfile mode={o.mode} y={o.y} delay={o.delay} caption={o.caption} ghost={o.ghost} axis={o.axis} />;
    default:
      return null;
  }
};

export const EngShorts: React.FC<EngShortsProps> = ({images, cuts, fps: fpsProp}) => {
  const {fps} = useVideoConfig();
  const F = fpsProp ?? fps;
  // base 미지정 컷은 직전 컷의 base 를 상속
  let last = cuts.find((c) => c.base)?.base ?? Object.keys(images)[0];
  const resolved = cuts.map((c) => {
    if (c.base) last = c.base;
    return {...c, _base: last};
  });

  return (
    <AbsoluteFill style={{backgroundColor: "#0a0a0a"}}>
      {resolved.map((c, i) => {
        const start = Math.round(c.s * F);
        const dur = Math.max(1, Math.round(c.d * F));
        return (
          <Sequence key={i} from={start} durationInFrames={dur} layout="none">
            {c.black ? (
              <AbsoluteFill style={{backgroundColor: "#000"}} />
            ) : c.clip ? (
              <AbsoluteFill style={{overflow: "hidden", backgroundColor: "#0a0a0a"}}>
                <OffthreadVideo
                  src={src(c.clip)}
                  startFrom={Math.round((c.clipFrom ?? 0) * F)}
                  muted
                  style={{width: "100%", height: "100%", objectFit: "cover"}}
                />
              </AbsoluteFill>
            ) : c.split ? (
              <AbsoluteFill style={{flexDirection: "column"}}>
                <div style={{flex: 1, overflow: "hidden", position: "relative"}}>
                  <Img src={src(images[c.split[0]])} style={{position: "absolute", inset: 0, width: "100%", height: "100%", objectFit: "cover"}} />
                </div>
                <div style={{height: 6, background: "#E8262A"}} />
                <div style={{flex: 1, overflow: "hidden", position: "relative"}}>
                  <Img
                    src={src(images[c.split[1]])}
                    style={{position: "absolute", inset: 0, width: "100%", height: "100%", objectFit: "cover", transform: "scale(1.35)"}}
                  />
                </div>
              </AbsoluteFill>
            ) : (
              <Base path={images[c._base!]} cam={c.cam} dur={dur} />
            )}

            {(c.ov ?? []).map((o, j) => (
              <Overlay key={j} o={o} />
            ))}

            {c.sub ? (
              Array.isArray(c.sub)
                ? <SubSeq texts={c.sub} dur={dur} />
                : <Subtitle text={c.sub} />
            ) : null}
          </Sequence>
        );
      })}
    </AbsoluteFill>
  );
};

export const defaultEngShorts: EngShortsProps = {
  images: {A: "engshorts/mandu_A.png", B: "engshorts/mandu_B.png", C: "engshorts/mandu_C.png", D: "engshorts/mandu_D.png", E: "engshorts/mandu_E.png"},
  cuts: [
    {s: 0, d: 2.4, base: "A", cam: {from: 1.0, to: 1.06}},
    {s: 2.4, d: 6.6, base: "A", cam: {from: 1.06, to: 1.18}, sub: "삶으면 터져야 정상인데\n안 터지는 게 있습니다.", ov: [{t: "ko", text: "만두", delay: 30}]},
  ],
};
