// EngShorts parts.tsx 가 기대하는 유일한 공용 심볼.
// 원본 레포에선 큰 공용 parts.tsx 에 들어 있지만, 여기선 이것만 있으면 된다.
import {loadFont} from "@remotion/google-fonts/NotoSansKR";

// 필요한 가중치·서브셋만 — 옵션 없이 부르면 요청이 폭증해 렌더가 간헐 실패한다.
const {fontFamily} = loadFont("normal", {
  weights: ["400", "700", "900"],
  subsets: ["korean", "latin"],
  ignoreTooManyRequestsWarning: true,
});

export {fontFamily};
