/**
 * INFO 스틸 배치 렌더 — CLEAN 이미지 위에 "글자 없는" 계측 그래픽만 얹는다.
 *
 * ★프레임 1장 = INFO 1장. `remotion render EngInfo <dir> --sequence` 한 번으로
 *   전 컷의 INFO 를 뽑는다(스틸을 컷마다 따로 부르면 번들 재기동 비용이 컷 수만큼 붙는다).
 *
 * 산출물은 H3 fl2va 의 last_frame 으로 들어간다 — 그래서 글자가 있으면 안 되고
 * (생성모델이 한글을 뭉갠다) CLEAN 과 구도가 픽셀 단위로 같아야 한다.
 * 규격 = .claude/skills/engineering-shorts/SKILL.md STEP 6b
 */
import React from "react";
import {AbsoluteFill, Img, staticFile, useCurrentFrame} from "remotion";
import {
  Anchor, DimLine, EngRenderCtx, FlowArrow, HotZone, RedX, Streamline, TempGauge, ThicknessProfile,
} from "./parts";

type XY = {x: number; y: number};

/** INFO 그래픽 — 전부 글자 없는 도형. 텍스트 부품(ko·eng)은 여기 안 온다. */
export type Gfx =
  | {t: "anchor"; at: XY; r?: number; color?: string}
  | {t: "stream"; pts: XY[]; lanes?: number; gap?: number; color?: string; width?: number}
  | {t: "hot"; at: XY; rx?: number; ry?: number; color?: string; strength?: number}
  | {t: "dim"; from: XY; to: XY; width?: number}
  | {t: "flow"; arrows: {from: XY; to: XY}[]; width?: number}
  | {t: "x"; at: XY; size?: number}
  | {t: "gauge"; y: number; min: number; max: number; band: [number, number]; overlap?: [number, number]; unit?: string; color?: string}
  | {t: "profile"; mode: "flat" | "flatHigh" | "u"; y?: number; ghost?: "u"; axis?: string};

export type InfoItem = {
  /** CLIP/KF ID — 파일명 추적용(화면엔 절대 안 나온다) */
  id: string;
  /** CLEAN 이미지 — public/ 상대경로 */
  clean: string;
  gfx: Gfx[];
};

export type EngInfoProps = {items: InfoItem[]};

const src = (p: string) => (p.startsWith("http") ? p : staticFile(p));

const One: React.FC<{g: Gfx}> = ({g}) => {
  switch (g.t) {
    case "anchor":
      return <Anchor at={g.at} r={g.r} color={g.color} />;
    case "stream":
      return <Streamline pts={g.pts} lanes={g.lanes} gap={g.gap} color={g.color} width={g.width} />;
    case "hot":
      return <HotZone at={g.at} rx={g.rx} ry={g.ry} color={g.color} strength={g.strength} />;
    case "dim":
      // ★INFO 는 608px 폭이라 EngShorts 기본 굵기(0.4)로는 선이 사라진다
      return <DimLine from={g.from} to={g.to} label="" width={g.width ?? 2.5} />;
    case "flow":
      return <FlowArrow arrows={g.arrows} width={g.width ?? 3} />;
    case "x":
      return <RedX at={g.at} size={g.size} />;
    case "gauge":
      return <TempGauge y={g.y} min={g.min} max={g.max} band={g.band} title="" overlap={g.overlap} unit={g.unit} color={g.color} />;
    case "profile":
      return <ThicknessProfile mode={g.mode} y={g.y} ghost={g.ghost} axis={g.axis} />;
    default:
      return null;
  }
};

export const EngInfo: React.FC<EngInfoProps> = ({items}) => {
  const f = useCurrentFrame();
  const it = items[Math.min(items.length - 1, Math.max(0, f))];
  return (
    <EngRenderCtx.Provider value={{still: true, noText: true}}>
      <AbsoluteFill style={{backgroundColor: "#000"}}>
        <Img src={src(it.clean)} style={{width: "100%", height: "100%", objectFit: "cover"}} />
        {it.gfx.map((g, i) => (
          <One key={i} g={g} />
        ))}
      </AbsoluteFill>
    </EngRenderCtx.Provider>
  );
};

export const defaultEngInfo: EngInfoProps = {
  items: [
    {
      id: "KF-00",
      clean: "engshorts/mandu_A.png",
      gfx: [
        {t: "anchor", at: {x: 42, y: 46}},
        {t: "stream", pts: [{x: 42, y: 46}, {x: 52, y: 60}, {x: 62, y: 72}]},
        {t: "hot", at: {x: 62, y: 74}},
      ],
    },
  ],
};
