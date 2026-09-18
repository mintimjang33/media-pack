# -*- coding: utf-8 -*-
"""aimaster 프롬프트 템플릿(경제학 유튜브 이미지 프롬프트_V1.pdf)의 고정 캐릭터 스타일로
코카콜라 유닛 25씬을 다시 짠다. 앵커/참조 첨부가 필요 없다 — 이 스타일은 매 프롬프트에
캐릭터 전신 묘사를 통째로 반복해서 일관성을 유지하는 방식이라(이미지 대 이미지 참조가 아니라
텍스트 반복으로 일관성 확보), Flow의 앵커 참조 메커니즘 자체를 안 써도 된다.
"""
import json

STYLE = ("Korean webtoon style illustration, dynamic and lively: A stick figure character "
         "with round white face, simple black dot eyes, curved line mouth, wearing navy blue "
         "business suit with red necktie and white gloves, brown shoes. High contrast colors, "
         "vibrant and lively cartoon style, dramatic scene energy, expressive character pose, "
         "strong visual impact, clean line art, flat colors with bold color accents, "
         "educational comic aesthetic.")


def scene(id_, section, expr, action, left, right, location, bg, props, mood):
    prompt = (
        f"Eyes {expr}. {action} "
        f"Layout: LEFT side shows {left}. RIGHT side shows {right}. "
        f"Character positioned at {location}. Background: {bg}. Props: {props}. "
        f"Scene mood: {mood}."
    )
    return {"id": id_, "section": section, "prompt": prompt}


SCENES = [
    scene("S01_COLDOPEN", "콜드오픈",
          "wide open in stunned disbelief, mouth dropped open",
          "Character staggers back half a step, both white-gloved hands flying up near face.",
          "a giant antique contract glowing under a single spotlight, the number \"$300\" huge and glowing gold at the bottom",
          "the character frozen in shock, staring at the number",
          "a dim vintage study",
          "deep brown and black vintage gradient, one dramatic golden spotlight beam, dust particles drifting in the light",
          "an old fountain pen resting on the contract, ink bottle beside it",
          "stunned historical revelation, maximum disbelief"),

    scene("S02_OPENING", "오프닝",
          "wide and alert, eyebrows raised",
          "Character leans forward pointing with one hand toward the scene, other hand on hip.",
          "a glowing convenience store fridge at night packed with red soda cans",
          "a rising stock ticker graphic overlay with a large dollar sign",
          "beside the fridge",
          "cool blue night gradient background with warm red product glow contrast",
          "a stock ticker banner scrolling numbers",
          "intriguing modern-day setup, curiosity hook"),

    scene("S03_ANDES", "1장",
          "curious, slightly narrowed, focused",
          "Character tilts head, one hand on chin in a thinking pose.",
          "a stylized Andes mountain range silhouette at dusk",
          "a glowing green coca leaf icon large and detailed",
          "in front of the mountain silhouette",
          "warm earthy green and brown gradient",
          "a small satchel of leaves at the character's feet",
          "historical curiosity, tracing origins"),

    scene("S04_CHEMLAB", "1장",
          "wide open with alarm, eyebrows sharply angled",
          "Character points urgently with a fully extended arm, leaning forward aggressively.",
          "a glowing glass vial of white crystalline powder labeled with a warning glow",
          "the character pointing at it with alarm",
          "beside a wooden laboratory bench",
          "dark navy laboratory background, single bright glow radiating from the vial",
          "scattered glass beakers and a handwritten label",
          "dangerous new discovery, alarmed excitement"),

    scene("S05_SURGERY", "1장",
          "wide with triumphant excitement",
          "Character stands tall, chest out, one fist raised high in triumph.",
          "a glowing medical cross icon beside an eye symbol",
          "the character celebrating the breakthrough",
          "in a simplified clinical setting",
          "warm golden glow gradient",
          "a small dropper bottle glowing softly",
          "medical breakthrough triumph"),

    scene("S06_PHARMACIST", "2장",
          "tired, half-lidded, worried",
          "Character has one hand on chin, head tilted, shoulders slumped slightly, eyebrow furrowed.",
          "an apothecary counter lined with glass bottles, one bottle glowing ominous red labeled as a painkiller",
          "the character looking troubled at the glowing bottle",
          "behind the apothecary counter",
          "dim navy pharmacy interior",
          "a mortar and pestle, scattered prescription papers",
          "quiet personal struggle, addiction weighing heavy"),

    scene("S07_SIXCUPS", "2장",
          "half-lidded, downcast",
          "Character shrugs with both palms turned upward, shoulders slumped in a discouraged pose.",
          "a soda fountain machine with a faint glowing number \"6\" above it",
          "the character shrugging at the underwhelming result",
          "beside the soda fountain",
          "muted beige gradient, dim flat lighting",
          "a few scattered coins on the counter",
          "underwhelming humble beginning"),

    scene("S08_CONTRACT300", "2장",
          "wide open in regret and shock, mouth open",
          "Character's hand pushes a glowing contract away across a desk while the other hand covers half of the face in regret.",
          "the same glowing contract from the cold open, \"$300\" huge and golden at the bottom",
          "the character wincing as the deal is sealed",
          "at the vintage desk",
          "dark background with one dramatic golden spotlight on the number",
          "an old fountain pen, ink bottle",
          "the pivotal costly decision, echo of the opening reveal"),

    scene("S09_CANDLER", "3장",
          "confident, sharp focus",
          "Character stands tall pointing forward decisively with one hand, chest out.",
          "an open ledger book with rising green numbers",
          "the character gesturing confidently toward the future",
          "at a business desk",
          "warm golden gradient with rising green arrow graphics",
          "a stack of coins turning into a taller stack",
          "ambitious businessman's rising confidence"),

    scene("S10_LOGO", "3장",
          "bright and joyful, curved happy",
          "Character raises both arms in a V shape above the head, small jump, big curved smile.",
          "an elegant glowing cursive logo being written by an unseen hand",
          "the character celebrating the creative moment",
          "beside the glowing logo",
          "warm cream gradient background with golden highlight on the logo",
          "a fountain pen with a trailing ink flourish",
          "joyful birth of a brand"),

    scene("S11_BOTTLECONFUSE", "4장",
          "narrowed with concern, one eyebrow raised",
          "Character points at the row of bottles with a confused, concerned expression.",
          "several nearly identical glass bottles lined up with large question marks above them",
          "the character pointing out the confusion problem",
          "in front of the bottle row",
          "dim tavern navy tone background",
          "blurred silhouettes of confused customers in the background",
          "a branding problem exposed"),

    scene("S12_CURVEBOTTLE", "4장",
          "wide with triumphant realization",
          "Character stands tall, one fist raised high, chin lifted upward in triumph.",
          "a glowing curved bottle silhouette identified by touch in darkness",
          "the character celebrating the clever solution",
          "in near darkness with one spotlight",
          "dark background, golden spotlight on the bottle shape",
          "a hand outline wrapped around the ridged glass shape",
          "clever solution triumph"),

    scene("S13_SANTA", "4장",
          "bright and joyful",
          "Character raises both arms in a V shape above the head, big curved smile, small jump.",
          "a red Santa Claus icon holding a glowing soda bottle",
          "the character celebrating alongside",
          "beside the Santa icon",
          "warm red and white winter gradient with falling snowflake shapes",
          "sparkling snowflakes and a wrapped gift box",
          "joyful global cultural icon born"),

    scene("S14_BLINDTEST", "5장",
          "narrowed with worry, eyebrow furrowed",
          "Character has one hand on chin, head tilted, watching nervously.",
          "two unmarked paper cups on a table with a small arrow pointing to one as the winner",
          "the character watching with growing worry",
          "beside a glowing TV screen",
          "dramatic blue TV light glow, dark room background",
          "a breaking news banner glowing on the screen behind",
          "confidence cracking, tension rising"),

    scene("S15_PROTEST", "5장",
          "bulging wide in panic, mouth open",
          "Character staggers backward, knees buckling, both white-gloved hands shooting up to face, body trembling.",
          "a crowd holding glowing protest signs and an overflowing stack of angry letters",
          "the character in full panic mode",
          "in front of the protesting crowd",
          "dark red alert gradient background, warning glow pulsing",
          "a ringing telephone with motion lines, scattered papers",
          "public backlash, maximum panic energy"),

    scene("S16_RELIEF", "5장",
          "relieved, soft curved smile",
          "Character wipes forehead with one hand in relief, other hand giving a thumbs up.",
          "a classic curved bottle glowing as it returns to a shelf with a checkmark glow",
          "the character relieved and smiling",
          "beside the shelf",
          "warm golden opportunity gradient",
          "a checkmark icon glowing softly above the bottle",
          "crisis resolved, warm relief"),

    scene("S17_RIVALRY", "6장",
          "sharp and competitive, narrowed",
          "Character stands with arms crossed, gesturing between two objects with a competitive stance.",
          "one glass bottle with an elegant cursive label glowing red",
          "another glass bottle with a bold label glowing blue",
          "between the two bottles",
          "split red versus blue dramatic duel gradient",
          "small lightning bolt accents between the two bottles",
          "rivalry sparks, competitive tension"),

    scene("S18_BANKRUPT", "6장",
          "wide with surprise, eyebrows raised",
          "Character throws both hands up in disbelief.",
          "a stack of documents stamped \"REJECTED\" glowing red twice",
          "the character reacting with surprise at the repeated rejection",
          "beside the document stack",
          "dark financial crisis navy gradient",
          "scattered legal papers falling",
          "repeated rejection and downfall"),

    scene("S19_GLOBALSCALE", "7장",
          "wide with amazement, big smile",
          "Character raises both arms in a V shape above the head in celebration, small jump.",
          "a glowing world map with hundreds of small light points across every continent",
          "a rising stock ticker showing \"$380B\" in bold glowing numbers",
          "in front of the glowing globe",
          "warm golden opportunity gradient with rising green arrows across the map",
          "small soda can icons scattered across the glowing map points",
          "overwhelming triumphant scale"),

    scene("S20_FANTA", "8장",
          "curious with a small smirk",
          "Character tilts head with one hand on chin, slight amused smile.",
          "a factory production line bottling a glowing green soda under wartime blocked-import symbols",
          "the character explaining the surprising twist",
          "beside the factory line",
          "muted wartime orange and grey gradient",
          "a blocked shipping crate icon with a red cross overlay",
          "an unexpected wartime invention"),

    scene("S21_SPACE", "8장",
          "wide with awe, sparkling excitement",
          "Character raises both arms in a V shape above the head in celebration, big smile, small jump.",
          "an astronaut's gloved hand holding a specially designed soda can",
          "glowing stars and a spacecraft window",
          "floating beside the astronaut hand",
          "deep space navy gradient with glowing star points",
          "small floating star particles",
          "historic space achievement, wonder and awe"),

    scene("S22_COCALEAF_BUFFETT", "8장",
          "confident, informative half-smile",
          "Character points with one hand while the other hand rests on hip in an informative pose.",
          "an official import stamp glowing on a legal document beside a coca leaf icon",
          "a Warren-Buffett-style elderly investor silhouette in front of a rising stock chart",
          "between the two scenes",
          "neutral informational warm beige gradient with golden highlight accents",
          "a small rising stock chart icon",
          "surprising facts still unfolding today"),

    scene("S23_CLOSING", "클로징",
          "confident, soft satisfied smile",
          "Character stands tall with chest out, one hand gesturing toward the transformation, other hand on hip.",
          "a small faded antique contract glowing dim gold on the dark side",
          "a large vivid modern soda can glowing bright on the light side",
          "at the center, bridging both sides",
          "a dramatic split gradient from dark vintage brown on the left to bright modern warm tone on the right",
          "a glowing arrow connecting the old contract to the modern can",
          "the dramatic reversal completed, satisfying conclusion"),
]

job = {"ratio": "16:9", "video_ratio": "16:9", "style": STYLE, "images": SCENES}

with open("flow.json", "w", encoding="utf-8") as f:
    json.dump(job, f, ensure_ascii=False, indent=1)

print("OK, scenes:", len(SCENES))
