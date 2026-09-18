import React from "react";
import {Composition} from "remotion";
import {EconVideo, type EconCut, type EconSub, type CaptionStyle} from "./econ/EconVideo";
// econ_cuts.json은 src/econ/ 안에 있다(tsconfig resolveJsonModule) — 빌드 타임에
// 그대로 인라인되므로 스튜디오 미리보기 시 별도 fetch가 필요 없다.
import econData from "./econ/econ_cuts.json";

const FPS = 30;

// 2026-09-14 — 사용자 지적: "왜 자꾸 이게(EngShorts) 보이는거야?" — EngShorts는 이 경제학
// 영상과 무관한 별개 프로젝트(9:16 공학쇼츠)인데, 같은 Root.tsx에 같이 등록돼 있어서
// 스튜디오를 열 때마다 같이 보였다. 게다가 EngShorts.tsx:143의 calculateMetadata가 깨져서
// ("Cannot convert undefined or null to object") 스튜디오 전체를 크래시시키고 있었다 —
// EconVideo 작업 도중 스튜디오가 아예 안 열리는 사고로 발견됨. 소스 파일(src/engshorts/)은
// 그대로 두고, 여기 등록만 빼서 경제학 작업 중엔 안 보이고 안 깨지게 분리한다.
export const RemotionRoot: React.FC = () => (
  <>
    {/* 경제학 파이프라인 미리보기 — 16:9 롱폼, 13번 단계 실제 씬 데이터(econ_cuts.json)로 조립 */}
    <Composition
      id="EconVideo"
      component={EconVideo}
      durationInFrames={FPS * 800}
      fps={FPS}
      width={1920}
      height={1080}
      defaultProps={{
        cuts: (econData as {cuts: EconCut[]}).cuts,
        subs: (econData as {subs: EconSub[]}).subs,
        narrationUrl: (econData as {narrationUrl: string}).narrationUrl,
        captionStyle: (econData as {captionStyle?: CaptionStyle}).captionStyle,
      }}
      calculateMetadata={({props}: {props: {cuts?: EconCut[]}}) => {
        const total = (props.cuts ?? []).reduce((a, c) => Math.max(a, c.s + c.d), 0);
        return {durationInFrames: Math.max(FPS, Math.round(total * FPS))};
      }}
    />
  </>
);
