/**
 * 경제학 파이프라인 미리보기 컴포지션 — 16:9 롱폼.
 *
 * 13번 단계에서 만든 실제 씬 데이터(econ_cuts.json)를 그대로 순서대로 배치한다.
 * - 카메라 무빙: 각 씬의 movingPrompt(영어 자연어 지시)를 휴리스틱으로 zoom/pan
 *   파라미터로 근사 변환한 값(cam)을 그대로 재생한다 — 정확한 연출이 아니라 방향성만
 *   반영한 1차 근사치다(build_cuts_json.py의 moving_to_cam 참고).
 * - 자막: 12번 SRT를 그대로 하단에 번인한다(faster-whisper 자동 생성본).
 */
import React from "react";
import {AbsoluteFill, Audio, Img, OffthreadVideo, Sequence, Video, getRemotionEnvironment, interpolate, staticFile, useCurrentFrame, useVideoConfig} from "remotion";

const resolveSrc = (p: string) => (p.startsWith("http") ? p : staticFile(p));

export type Cam = {from?: number; to?: number; cx?: number; cy?: number};

export type EconCut = {
  s: number;
  d: number;
  id: string;
  title?: string;
  image?: string;
  clip?: string;
  cam?: Cam;
  /** clip이 있을 때만 의미 있음 — 원본 영상 파일 안에서 몇 초 지점부터 재생할지(초).
   * 2026-09-18 추가, 사용자 지적: "원본영상의 길이가 나와있고 그중 어디부터 어디까지
   * 사용할껀지 설정이 되어있어야해" — 트림 끝은 별도 필드 없이 trimStart+d(이 컷의
   * 타임라인 길이)로 계산한다. 없으면 원본의 처음(0초)부터 쓴다(기존 동작과 동일). */
  trimStart?: number;
};

export type EconSub = {s: number; d: number; text: string};

/** 12번(나레이션·자막) 패널의 자막 미리보기 설정 — unit.captionStyle(DB)과 동일한 값을 그대로
 * 받아서 쓴다. 그 패널은 16:9 미리보기 박스를 480px 폭으로 그리므로(step13-14-LabeledLinksPanel.tsx
 * DEFAULT_PREVIEW_STYLE/previewAspect 참고), 여기 실제 렌더 폭(1920)과의 비율(=4)만큼
 * fontSize를 그대로 확대해서 같은 크기로 보이게 맞춘다. */
export type CaptionStyle = {align?: "left" | "center" | "right"; lines?: 1 | 2; fontSize?: number; bg?: string; color?: string};

export type EconVideoProps = {
  cuts: EconCut[];
  subs?: EconSub[];
  narrationUrl?: string;
  fps?: number;
  captionStyle?: CaptionStyle;
};

const KenBurnsImage: React.FC<{src: string; dur: number; cam?: Cam}> = ({src, dur, cam}) => {
  const f = useCurrentFrame();
  const from = cam?.from ?? 1.0;
  const to = cam?.to ?? 1.06;
  const cx = cam?.cx ?? 50;
  const cy = cam?.cy ?? 50;
  const scale = interpolate(f, [0, Math.max(1, dur - 1)], [from, to], {extrapolateRight: "clamp"});
  return (
    <AbsoluteFill style={{overflow: "hidden", backgroundColor: "#000"}}>
      <Img
        src={resolveSrc(src)}
        style={{
          width: "100%", height: "100%", objectFit: "cover",
          transform: `scale(${scale})`, transformOrigin: `${cx}% ${cy}%`,
        }}
      />
    </AbsoluteFill>
  );
};

/** 하단 자막 — 12번 패널(step13-14-LabeledLinksPanel.tsx)의 미리보기 스타일(unit.captionStyle)을
 * 그대로 따른다. 그 패널은 480px 폭 미리보기 박스 기준으로 fontSize를 쓰므로, 실제 렌더 폭과의
 * 비율(scale)만큼 그대로 확대해서 12번에서 본 것과 같은 크기로 보이게 한다. lines:1이면(짧은 문장
 * 단위로 잘게 쪼갠 최신 자막 기준) 한 줄에 맞도록 폭을 넓게 잡고, lines:2면 기존처럼 좁혀서
 * 자연스럽게 줄바꿈되게 둔다. */
const Subtitle: React.FC<{text: string; style: CaptionStyle; scale: number}> = ({text, style, scale}) => {
  const align = style.align ?? "center";
  const lines = style.lines ?? 2;
  const fontSize = (style.fontSize ?? 15) * scale;
  const bg = style.bg ?? "#000000";
  const color = style.color ?? "#ffffff";
  return (
    <div
      style={{
        position: "absolute", left: "4%", right: "4%", bottom: "5%",
        display: "flex", justifyContent: align === "left" ? "flex-start" : align === "right" ? "flex-end" : "center",
      }}
    >
      <span
        style={{
          display: "inline-block",
          maxWidth: lines === 1 ? "96%" : "70%",
          backgroundColor: bg,
          padding: "0.15em 0.4em",
          borderRadius: 3,
          textAlign: align, fontFamily: "sans-serif", fontWeight: 800,
          fontSize, lineHeight: 1.35, color, whiteSpace: "pre-line",
          wordBreak: "keep-all",
        }}
      >
        {text}
      </span>
    </div>
  );
};

/** 2026-09-18 추가 — 사용자 지적: "편집프로그램중 미리보기에서 저렇게 보이는 프로그램 봤어?"
 * (Studio 미리보기에서 컷마다 1~2초 검은 화면/로딩이 뜨는 게 정상 편집기답지 않다는 지적, 맞는
 * 말이었음). 원인은 OffthreadVideo가 프레임을 실시간으로 서버에 요청해 디코딩하는 방식이라
 * 그런 것 — 렌더링(npx remotion render)에서는 프레임 정확도 때문에 반드시 필요하지만,
 * Studio 미리보기(사람이 스크럽/재생하며 보는 용도)에서는 그 정확도가 필요 없고 브라우저 내장
 * <video> 태그(Video 컴포넌트)가 스트리밍 버퍼링으로 훨씬 매끄럽다. getRemotionEnvironment().
 * isRendering으로 실제 렌더링 중인지 구분해서, 렌더링 때만 OffthreadVideo(정확)를 쓰고
 * 그 외(Studio 미리보기)에는 Video(매끄러움)를 쓴다 — 최종 산출물 품질은 그대로 유지된다.
 */
const ClipVideo: React.FC<{src: string; trimBefore: number; trimAfter: number}> = ({src, trimBefore, trimAfter}) => {
  const {isRendering} = getRemotionEnvironment();
  const commonStyle: React.CSSProperties = {width: "100%", height: "100%", objectFit: "cover"};
  if (isRendering) {
    return <OffthreadVideo src={src} muted trimBefore={trimBefore} trimAfter={trimAfter} style={commonStyle} />;
  }
  return <Video src={src} muted trimBefore={trimBefore} trimAfter={trimAfter} style={commonStyle} />;
};

export const EconVideo: React.FC<EconVideoProps> = ({cuts, subs, narrationUrl, fps: fpsProp, captionStyle}) => {
  const F = fpsProp ?? 30;
  const {width} = useVideoConfig();
  const scale = width / 480; // 12번 패널 미리보기 폭(480px, 16:9) 대비 실제 렌더 폭 비율
  return (
    <AbsoluteFill style={{backgroundColor: "#000"}}>
      {narrationUrl ? <Audio src={resolveSrc(narrationUrl)} /> : null}
      {cuts.map((c, i) => {
        const start = Math.round(c.s * F);
        const dur = Math.max(1, Math.round(c.d * F));
        return (
          <Sequence key={i} from={start} durationInFrames={dur} layout="none">
            {c.clip ? (
              <AbsoluteFill style={{overflow: "hidden", backgroundColor: "#000"}}>
                <ClipVideo
                  src={resolveSrc(c.clip)}
                  trimBefore={Math.round((c.trimStart ?? 0) * F)}
                  trimAfter={Math.round(((c.trimStart ?? 0) + c.d) * F)}
                />
              </AbsoluteFill>
            ) : c.image ? (
              <KenBurnsImage src={c.image} dur={dur} cam={c.cam} />
            ) : (
              <AbsoluteFill style={{backgroundColor: "#111"}} />
            )}
            <div
              style={{
                position: "absolute", left: 16, bottom: 16,
                color: "rgba(255,255,255,0.55)", fontSize: 22, fontFamily: "monospace",
                textShadow: "0 1px 4px rgba(0,0,0,0.8)",
              }}
            >
              {c.id}
            </div>
          </Sequence>
        );
      })}
      {(subs ?? []).map((s, i) => {
        const start = Math.round(s.s * F);
        const dur = Math.max(1, Math.round(s.d * F));
        return (
          <Sequence key={`sub-${i}`} from={start} durationInFrames={dur} layout="none">
            <Subtitle text={s.text} style={captionStyle ?? {}} scale={scale} />
          </Sequence>
        );
      })}
    </AbsoluteFill>
  );
};

export const defaultEconVideo: EconVideoProps = {cuts: [], subs: []};
