#!/usr/bin/env python3
"""
Seed per-country city profiles into Tigris + Postgres.

Mirrors ``seed_culture_calendars.py``: the data here is curated by hand
(Claude Code), NOT generated at runtime.  Each country gets one Tigris
object ``culture/cities/<CC>.json`` and one ``city_profiles`` Pg row whose
payload holds every city we offer for that country:

    cities[<name>] = {lat, lon, timezone, language, vibe}

Consumed by ``claw_soul.core.city`` (three-tier lookup) for:
  - planner weather (real coordinates, not a hardcoded Shanghai default)
  - companion timezone resolution
  - the hometown "vibe" blurb baked into the persona backstory

Run with the same env as the calendar seeder:
    SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, BUCKET_NAME,
    AWS_ENDPOINT_URL_S3, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY
"""

from __future__ import annotations

import json
import logging
import os
import sys
from typing import Any

import httpx

TIGRIS_PREFIX = "culture/cities"
SCHEMA_VERSION = 1

CITIES: dict[str, dict] = {}


def _c(lat: float, lon: float, tz: str, lang: str, vibe: str = "",
       events: list[dict] | None = None) -> dict:
    return {"lat": lat, "lon": lon, "timezone": tz, "language": lang,
            "vibe": vibe, "events": events or []}


def _ev(month: int, day: int, name: str, emoji: str, significance: str) -> dict:
    """A signature city event (annual, approximate date — for grounding,
    not ticketing).  ``significance`` gives the agent context to riff on."""
    return {"month": month, "day": day, "name": name, "emoji": emoji,
            "significance": significance}


def _register(cc: str, cities: dict[str, dict]) -> None:
    CITIES[cc] = {
        "country_code": cc,
        "schema_version": SCHEMA_VERSION,
        "cities": cities,
    }


# ── East Asia ───────────────────────────────────────────────────────────

_register("CN", {
    "北京": _c(39.9042, 116.4074, "Asia/Shanghai", "zh-CN",
              "北京有四季，秋天最舒服。胡同的早晨满是豆汁、煎饼果子的味道。"
              "三里屯、五道营、南锣鼓巷是年轻人混的地方，冬天涮羊肉配二锅头是经典。"),
    "上海": _c(31.2304, 121.4737, "Asia/Shanghai", "zh-CN",
              "上海最有意思的是法租界那一带，梧桐树下慢慢走。"
              "早上小馄饨配葱油拌面，晚上去外滩看灯。生煎、本帮菜、咖啡馆密度高得离谱。"),
    "深圳": _c(22.5431, 114.0579, "Asia/Shanghai", "zh-CN",
              "深圳节奏快，年轻人多。南山一带是科技公司聚集地，南头古城旧改的小店不错。"
              "海边日落很好看，蛇口的渔人码头傍晚一定要去。"),
    "杭州": _c(30.2741, 120.1551, "Asia/Shanghai", "zh-CN",
              "杭州慢，西湖是日常背景。龙井村喝茶、灵隐寺爬山，节奏不像一线城市。"
              "湖边骑车、运河边的咖啡馆、龙井虾仁——一切都和水有关。"),
    "广州": _c(23.1291, 113.2644, "Asia/Shanghai", "zh-CN",
              "广州人吃东西最认真。早茶是仪式感，凌晨大排档夜宵延伸到天亮。"
              "天河车水马龙，老西关一砖一瓦都有故事。"),
    "成都": _c(30.5728, 104.0668, "Asia/Shanghai", "zh-CN",
              "成都生活感最强。茶馆、麻将、火锅，下午有事不要紧。"
              "宽窄巷子是给游客的，本地人去玉林路那种老社区。"),
    "南京": _c(32.0603, 118.7969, "Asia/Shanghai", "zh-CN",
             "南京有六朝的旧气，梧桐盖满中山陵的路。盐水鸭配鸭血粉丝汤，秦淮河夜里灯影很足。"),
    "厦门": _c(24.4798, 118.0894, "Asia/Shanghai", "zh-CN",
             "厦门是慢的海边城市。环岛路骑车、沙茶面配花生汤，鼓浪屿的老别墅藏在三角梅后面。"),
    "苏州": _c(31.2989, 120.5853, "Asia/Shanghai", "zh-CN",
             "苏州把园林过成日常。评弹、奥灶面、平江路的小桥流水，节奏比上海软一截。"),
})

_register("TW", {
    "台北": _c(25.0330, 121.5654, "Asia/Taipei", "zh-TW",
              "台北的雨像永遠下不完。永康街的小店密度高，誠品書店是夜晚的好去處。"
              "牛肉麵、滷肉飯、夜市芋圓——什麼時候肚子餓都有得吃。"),
    "高雄": _c(22.6273, 120.3014, "Asia/Taipei", "zh-TW",
             "高雄比台北鬆，海港的風很大。駁二倉庫改成藝術區，瑞豐夜市吃到撐，愛河邊散步剛剛好。"),
    "台中": _c(24.1477, 120.6736, "Asia/Taipei", "zh-TW",
             "台中天氣好、步調慢。審計新村、草悟道、宮原眼科，太陽餅配珍奶，市區開車比台北舒服。"),
    "台南": _c(22.9999, 120.2270, "Asia/Taipei", "zh-TW",
             "台南是台灣的老靈魂。巷弄裡都是小吃，牛肉湯當早餐，廟口同老屋咖啡，慢得理直氣壯。"),
})

_register("HK", {
    "中環": _c(22.2810, 114.1588, "Asia/Hong_Kong", "zh-HK",
              "中環是上班的地方，但晚上一鑽進蘭桂坊就完全變了。"
              "上環的咖啡店、蘇豪的小酒吧、半山扶手電梯——很多事都在斜坡上發生。"),
    "灣仔":   _c(22.2783, 114.1722, "Asia/Hong_Kong", "zh-HK",
             "灣仔新舊交疊，舊樓下面係新潮餐廳。利東街、藍屋、會展，落樓就有大牌檔同精品咖啡。"),
    "尖沙咀": _c(22.2978, 114.1722, "Asia/Hong_Kong", "zh-HK",
             "尖沙咀對住維港，遊客同名牌店密集。海旁睇幻彩詠香江，重慶大廈裏藏住咖喱同世界各地的人。"),
    "上環":   _c(22.2866, 114.1497, "Asia/Hong_Kong", "zh-HK",
             "上環係斜路同海味舖嘅地頭，文青小店越開越多。蘇豪上去就係半山，老茶餐廳同精品店並排。"),
})

_register("MO", {
    "氹仔":     _c(22.1563, 113.5610, "Asia/Macau", "zh-HK",
             "氹仔一邊係賭場度假村，一邊係官也街嘅葡撻同手信舖。新舊澳門喺度撞個正著。"),
    "澳門半島": _c(22.1987, 113.5439, "Asia/Macau", "zh-HK",
             "澳門半島係老城心臟。大三巴、議事亭前地、葡式碎石路，茶餐廳同葡國菜一街之隔。"),
    "路環":     _c(22.1188, 113.5587, "Asia/Macau", "zh-HK",
             "路環係澳門最靜嘅一角，漁村味重。安德魯嘅葡撻、黑沙海灘，慢到時間都停低。"),
})

_register("JP", {
    "東京": _c(35.6762, 139.6503, "Asia/Tokyo", "ja",
              "東京は街ごとに表情が違う。新宿のネオン、谷中の静けさ、代官山のおしゃれ、神保町の古本街。"
              "四季がはっきりして、秋の銀杏並木と春の桜が一年を区切る。"
              "ラーメンの味は区ごとに違う、夜は焼き鳥かバーで一杯。",
        events=[
            _ev(3, 28, "花見シーズン", "🌸", "桜が咲いて、公園はブルーシートとお花見の宴会で埋まる"),
        ]),
    "京都": _c(35.0116, 135.7681, "Asia/Tokyo", "ja",
              "京都は時間の流れが違う。寺と神社が日常の風景で、鴨川沿いの散歩が一番の贅沢。"
              "湯豆腐、抹茶、町家のカフェ——どれも控えめだけど芯がある。"),
    "大阪": _c(34.6937, 135.5023, "Asia/Tokyo", "ja",
              "大阪は飯と笑い。たこ焼き、お好み焼き、串カツ——食い倒れの本気度が違う。"
              "難波のごちゃっとした路地、心斎橋のネオン、人懐っこさが街そのもの。"),
    "横浜": _c(35.4437, 139.6380, "Asia/Tokyo", "ja",
             "横浜は港の街。みなとみらいの夜景、中華街の点心、海沿いをぶらぶら歩くのが日課。"),
    "札幌": _c(43.0618, 141.3545, "Asia/Tokyo", "ja",
             "札幌は冬が主役。雪まつり、味噌ラーメン、ジンギスカン。夏は短いけど大通公園が気持ちいい。"),
    "福岡": _c(33.5904, 130.4017, "Asia/Tokyo", "ja",
             "福岡は食と人の距離が近い。屋台のとんこつ、もつ鍋、明太子。コンパクトで暮らしやすい街。"),
})

_register("KR", {
    "서울": _c(37.5665, 126.9780, "Asia/Seoul", "ko",
              "서울은 빠른 도시. 강남은 일하는 곳, 홍대는 노는 곳, 성수는 새로 뜨는 동네. "
              "한강의 야경, 골목골목의 카페, 길거리 떡볶이까지—하루가 너무 짧다."),
    "부산": _c(35.1796, 129.0756, "Asia/Seoul", "ko",
             "부산은 바다의 도시. 광안리 야경, 자갈치 회, 해운대 산책. 사투리가 정겹고 사람들이 화통하다."),
    "인천": _c(37.4563, 126.7052, "Asia/Seoul", "ko",
             "인천은 바다와 공항의 도시. 차이나타운, 송도의 신도시 스카이라인, 월미도 바닷바람—서울과는 또 다른 결."),
    "대구": _c(35.8714, 128.6014, "Asia/Seoul", "ko",
             "대구는 분지라 여름이 맹렬하다. 막창과 납작만두, 김광석 거리, 패션의 도시라는 자부심이 은근하다."),
})

# ── Southeast Asia ──────────────────────────────────────────────────────

_register("SG", {
    "Orchard": _c(1.3048, 103.8318, "Asia/Singapore", "en",
                  "Singapore's Orchard is the shopping spine, but the magic is in the side "
                  "streets — Emerald Hill's heritage shophouses, Killiney Road's old "
                  "kopitiams. Year-round 28°C and afternoon thunderstorms."),
    "Tiong Bahru": _c(1.2847, 103.8270, "Asia/Singapore", "en",
                      "Tiong Bahru is the prewar art-deco quarter Singapore's hipsters "
                      "claimed. Toast and kaya for breakfast, a bookshop, indie cafés, and "
                      "the wet market that anchors the whole vibe."),
    "Tanjong Pagar": _c(1.2766, 103.8456, "Asia/Singapore", "en",
             "Tanjong Pagar is old shophouses turned cocktail bars and Korean BBQ, the finance towers right behind — heritage and after-work crowds in one block."),
    "Jurong": _c(1.3329, 103.7436, "Asia/Singapore", "en",
             "Jurong is Singapore's industrial west gone green — the lake gardens, the science centre, sprawling malls, heartland HDB life away from the tourist core."),
})

_register("MY", {
    "Kuala Lumpur": _c(3.1390, 101.6869, "Asia/Kuala_Lumpur", "ms",
             "KL runs hot and humid under the Petronas Towers. Mamak hawkers till 2am, malls as refuge from the rain, a city stitched together by elevated highways."),
    "Penang":       _c(5.4141, 100.3288, "Asia/Kuala_Lumpur", "ms",
             "Penang is hawker heaven on an island. George Town's peeling shophouses and street art, char kway teow and assam laksa, a slow briny heritage pace."),
    "Johor Bahru":  _c(1.4927, 103.7414, "Asia/Kuala_Lumpur", "ms",
             "JB empties into Singapore by day and fills its mega-malls and night markets after dark — cheap, hot, and unpretentious."),
})

_register("TH", {
    "Bangkok":    _c(13.7563, 100.5018, "Asia/Bangkok", "th",
             "Bangkok never quite cools down. Street food on every soi, the BTS gliding over gridlock, temples next to malls, 7-Elevens as a way of life."),
    "Chiang Mai": _c(18.7883, 98.9853, "Asia/Bangkok", "th",
             "Chiang Mai is the chill northern capital — old-city temples, cafés in every lane, night bazaars, cool-season mornings, a big nomad crowd."),
    "Phuket":     _c(7.8804, 98.3923, "Asia/Bangkok", "th",
             "Phuket is beaches, longtails, and humid green hills — Old Town's Sino-Portuguese facades, night markets, a coast that runs party to quiet."),
})

_register("VN", {
    "Hà Nội":  _c(21.0278, 105.8342, "Asia/Ho_Chi_Minh", "vi",
             "Hanoi moves on plastic stools and motorbikes. Pho at dawn, egg coffee in the Old Quarter, the lake a slow grey heart in the middle."),
    "TP. HCM": _c(10.8231, 106.6297, "Asia/Ho_Chi_Minh", "vi",
             "Saigon hums all night. Banh mi carts, iced coffee that is basically dessert, a million motorbikes, District 1 neon and back-alley com tam."),
    "Đà Nẵng": _c(16.0544, 108.2022, "Asia/Ho_Chi_Minh", "vi",
             "Da Nang is Vietnam's beach city — a long sandy coast, the Dragon Bridge breathing fire on weekends, mi quang, mountains a short ride away."),
})

_register("ID", {
    "Jakarta":  _c(-6.2088, 106.8456, "Asia/Jakarta", "id",
             "Jakarta is traffic and warmth in equal measure. Warungs, ojek on your phone, sudden downpours, malls as the living room of the city."),
    "Bali":     _c(-8.4095, 115.1889, "Asia/Makassar", "id",
             "Bali runs on rice terraces, surf, and offerings on every doorstep — Canggu cafés, Ubud's jungle, temple ceremonies, scooter-clogged lanes."),
    "Surabaya": _c(-7.2575, 112.7521, "Asia/Jakarta", "id",
             "Surabaya is Indonesia's blunt, hardworking second city — heat, history, rujak cingur, a no-nonsense warmth under the haze."),
})

_register("PH", {
    "Manila": _c(14.5995, 120.9842, "Asia/Manila", "en",
             "Manila is loud, warm, and family-first. Jeepneys, karaoke, mall culture, and a sunset over the bay that forgives the heat."),
    "Cebu":   _c(10.3157, 123.8854, "Asia/Manila", "en",
             "Cebu is islands, lechon, and easy provincial warmth — a historic core, beaches and dive sites a ferry away, malls as the air-conditioned square."),
    "Davao":  _c(7.1907, 125.4553, "Asia/Manila", "en",
             "Davao is Mindanao's big, orderly city — durian, Mount Apo on the horizon, beaches, and a laid-back pride in being safe and unhurried."),
})

# ── South Asia ──────────────────────────────────────────────────────────

_register("IN", {
    "Mumbai":    _c(19.0760, 72.8777, "Asia/Kolkata", "hi",
                    "Mumbai never sleeps. The local trains, the sea at Marine Drive, vada "
                    "pav as a religion. Monsoons rewire the city for three months a year."),
    "Delhi":     _c(28.7041, 77.1025, "Asia/Kolkata", "hi",
             "Delhi swings between Mughal grandeur and ruthless summers. Chaat in Chandni Chowk, the metro as the great equalizer, winters wrapped in fog and shawls."),
    "Bangalore": _c(12.9716, 77.5946, "Asia/Kolkata", "hi",
             "Bangalore is India's tech heart with a garden-city hangover. Filter coffee, craft breweries, traffic that tests the soul, weather that almost always forgives it."),
    "Chennai":   _c(13.0827, 80.2707, "Asia/Kolkata", "hi",
             "Chennai is hot, coastal, and proud of its Tamil roots — filter coffee, Marina Beach at dusk, temple gopurams, Carnatic music from open windows."),
    "Hyderabad": _c(17.3850, 78.4867, "Asia/Kolkata", "hi",
             "Hyderabad is biryani, the Charminar, and a tech boom in Hitech City — old-city bazaars and gleaming campuses, Urdu and Telugu in one breath."),
    "Pune":      _c(18.5204, 73.8567, "Asia/Kolkata", "hi",
             "Pune is Mumbai's calmer student-and-tech cousin in the hills — pleasant weather, cafés, two-wheelers everywhere, a college-town energy."),
})

_register("PK", {
    "Karachi":   _c(24.8607, 67.0011, "Asia/Karachi", "ur",
             "Karachi is Pakistan's chaotic megacity by the sea — biryani, beach evenings at Clifton, endless traffic, a hustle that never sleeps."),
    "Lahore":    _c(31.5204, 74.3587, "Asia/Karachi", "ur",
             "Lahore is the cultural heart — Mughal monuments, food streets alive at night, gardens, and the saying that if you haven't seen it you haven't lived."),
    "Islamabad": _c(33.6844, 73.0479, "Asia/Karachi", "ur",
             "Islamabad is green, planned, and quiet under the Margalla Hills — wide avenues, hiking trails, a calm unlike the rest of the country."),
})

_register("BD", {
    "Dhaka":      _c(23.8103, 90.4125, "Asia/Dhaka", "bn",
             "Dhaka is dense, loud, and relentless — a million rickshaws, biryani and street food, the Buriganga, monsoon floods, an energy that pulls you in."),
    "Chittagong": _c(22.3569, 91.7832, "Asia/Dhaka", "bn",
             "Chittagong is the port city in the hills by the Bay of Bengal — shipping, Mezban feasts, a gateway to the beaches and tea of the southeast."),
})

# ── Anglosphere ─────────────────────────────────────────────────────────

_register("US", {
    "New York":      _c(40.7128, -74.0060, "America/New_York", "en",
                        "New York runs on density. Bagels at 7am from a corner deli, the "
                        "Met on a Sunday, subway smell, all five boroughs feel like a "
                        "different city. Pizza by the slice, late-night pho in the Village, "
                        "sirens at 2am."),
    "San Francisco": _c(37.7749, -122.4194, "America/Los_Angeles", "en",
                        "San Francisco fog rolls in over the Sunset most afternoons. "
                        "Mission burritos, Dolores Park on a sunny day, the rattle of the "
                        "J-Church. The city is small enough that you keep running into the "
                        "same coffee shops."),
    "Los Angeles":   _c(34.0522, -118.2437, "America/Los_Angeles", "en",
                        "LA is the freeway, the canyons, taco trucks, and a beach you can "
                        "drive to in 20 minutes if there's no traffic. Sunset over the "
                        "Pacific, breakfast burritos at 3pm, hikes that double as shoots."),
    "Seattle":       _c(47.6062, -122.3321, "America/Los_Angeles", "en",
                        "Seattle is grey six months a year and you learn to love it. "
                        "Coffee shops as offices, ferries as commute, the smell of cedar "
                        "after rain."),
    "Boston":        _c(42.3601, -71.0589, "America/New_York", "en",
                        "Boston walks like a European city — small, dense, history at every "
                        "corner. Bagels at Tatte, Red Sox at Fenway, the Esplanade in summer."),
    "Austin":        _c(30.2672, -97.7431, "America/Chicago", "en",
             "Austin is live music, breakfast tacos, and 'keep it weird.' Swims at Barton Springs, BBQ smoke, food trucks, and a summer that means business.",
        events=[
            _ev(3, 13, "SXSW", "🎸", "the city fills with music, film and tech for a week and a half — every venue booked, badges and tacos everywhere"),
            _ev(10, 2, "ACL Fest", "🎶", "Austin City Limits takes over Zilker Park across two October weekends"),
        ]),
    "Chicago":       _c(41.8781, -87.6298, "America/Chicago", "en",
             "Chicago is broad shoulders and lakefront wind. Deep dish, the L rattling overhead, summers worth the brutal winters, neighborhoods that each feel like a town."),
})

_register("CA", {
    "Toronto":   _c(43.6532, -79.3832, "America/Toronto", "en",
                    "Toronto is friendlier than New York and as multicultural as it gets — "
                    "Korean Town, Greektown, Little India all within a streetcar ride."),
    "Vancouver": _c(49.2827, -123.1207, "America/Vancouver", "en",
             "Vancouver is mountains meeting ocean, with rain as the price. Seawall runs, sushi everywhere, ski in the morning and beach by afternoon."),
    "Montreal":  _c(45.5019, -73.5674, "America/Toronto", "en",
             "Montreal is French-Canadian and proud. Bilingual cafes, brutal winters, festivals all summer, a European city on the cheap."),
})

_register("GB", {
    "London":    _c(51.5074, -0.1278, "Europe/London", "en",
                    "London is its weather: a third drizzle, a third overcast, a third "
                    "surprise sun. Pubs, parks, the Tube, Sunday roast. Brick Lane curries, "
                    "Borough Market on a Saturday.",
        events=[
            _ev(8, 25, "Notting Hill Carnival", "🎉", "Caribbean sound systems and huge crowds over the late-August bank holiday"),
        ]),
    "Manchester": _c(53.4808, -2.2426, "Europe/London", "en",
             "Manchester is music, football, and rain you stop noticing. Red-brick warehouses turned bars, two rival teams, a chip on the shoulder that fuels everything."),
    "Edinburgh":  _c(55.9533, -3.1883, "Europe/London", "en",
             "Edinburgh is a stone city under a castle, dramatic in any weather. Closes and wynds, the Fringe taking over every August, a pint in a centuries-old pub.",
        events=[
            _ev(8, 5, "Edinburgh Fringe", "🎭", "the world’s biggest arts festival swallows the city for all of August"),
        ]),
    "Bristol":    _c(51.4545, -2.5879, "Europe/London", "en",
             "Bristol is the West Country's creative independent city — harbourside, Banksy walls, balloon fiestas, Georgian terraces and street art on hills."),
})

_register("IE", {
    "Dublin": _c(53.3498, -6.2603, "Europe/Dublin", "en",
             "Dublin is pubs, talk, and soft grey rain. The Liffey splitting it in two, trad sessions, a city small enough to keep bumping into people you know."),
    "Cork":   _c(51.8985, -8.4756, "Europe/Dublin", "en",
             "Cork calls itself the real capital — a compact city on the Lee, the English Market, a proud witty contrarian streak and great pints."),
    "Galway": _c(53.2707, -9.0568, "Europe/Dublin", "en",
             "Galway is the bohemian west coast — buskers on Shop Street, trad sessions, oysters and Atlantic rain, a festival every other week."),
})

_register("AU", {
    "Sydney":    _c(-33.8688, 151.2093, "Australia/Sydney", "en",
                    "Sydney lives outdoors. The harbour, Bondi, a morning run on the "
                    "coastal walk. Coffee culture is non-negotiable.",
        events=[
            _ev(3, 1, "Mardi Gras", "🌈", "Sydney Gay and Lesbian Mardi Gras — the big early-March parade down Oxford Street"),
        ]),
    "Melbourne": _c(-37.8136, 144.9631, "Australia/Melbourne", "en",
             "Melbourne hides its best in laneways — coffee as religion, street art, four seasons in a day, and a sports obsession that borders on civic duty."),
    "Brisbane":  _c(-27.4698, 153.0251, "Australia/Brisbane", "en",
             "Brisbane is the sunny, easygoing river city — subtropical heat, riverside walks, a relaxed pace, the Gold Coast beaches an hour south."),
})

_register("NZ", {
    "Auckland":     _c(-36.8485, 174.7633, "Pacific/Auckland", "en",
             "Auckland is built on volcanoes and water. Sails on the harbour, a ferry to an island for the weekend, Polynesian and Asian flavours on every corner."),
    "Wellington":   _c(-41.2865, 174.7762, "Pacific/Auckland", "en",
             "Wellington is the windy little capital with a big culture — coffee, craft beer, the harbour, film studios, hills you climb to get anywhere."),
    "Christchurch": _c(-43.5321, 172.6362, "Pacific/Auckland", "en",
             "Christchurch is the garden city rebuilding after the quakes — flat and bike-friendly, the Avon, the Port Hills, the Alps a couple hours west."),
})

# ── Europe ──────────────────────────────────────────────────────────────

_register("DE", {
    "Berlin":  _c(52.5200, 13.4050, "Europe/Berlin", "de",
                  "Berlin is layered — Cold War seams, techno clubs that don't open until "
                  "midnight, Kreuzberg's döner, Mitte's galleries. Long winters but "
                  "extraordinary summers in the parks."),
    "Munich":  _c(48.1351, 11.5820, "Europe/Berlin", "de",
             "Munich is Bavaria's polished capital — beer gardens under chestnut trees, the Englischer Garten, the Alps a weekend away, Oktoberfest swallowing September.",
        events=[
            _ev(9, 20, "Oktoberfest", "🍺", "the Wiesn — dirndls, steins and the whole city in lederhosen for two and a half weeks into early October"),
        ]),
    "Hamburg": _c(53.5511, 9.9937, "Europe/Berlin", "de",
             "Hamburg is the maritime north — canals, the harbour, the Reeperbahn at night, brick warehouses, a cool reserve and more bridges than Venice."),
})

_register("FR", {
    "Paris":     _c(48.8566, 2.3522, "Europe/Paris", "fr",
                    "Paris is its mornings — coffee at the counter, pastry by 10am, the "
                    "long late lunch. Every arrondissement has a personality; you find your "
                    "local within two weeks."),
    "Lyon":      _c(45.7640, 4.8357, "Europe/Paris", "fr",
             "Lyon is France's food capital between two rivers — bouchons, traboules through the old town, a Renaissance core, quietly confident and less rushed than Paris."),
    "Marseille": _c(43.2965, 5.3698, "Europe/Paris", "fr",
             "Marseille is the rough sunlit Mediterranean port — the Vieux-Port, pastis, bouillabaisse, the calanques, a salty multicultural energy all its own."),
})

_register("ES", {
    "Madrid":    _c(40.4168, -3.7038, "Europe/Madrid", "es",
             "Madrid eats late and stays up later. Tapas crawls, the Prado, plazas that fill at midnight, a dry heat under a sky Velazquez kept painting."),
    "Barcelona": _c(41.3851, 2.1734, "Europe/Madrid", "es",
             "Barcelona is Gaudi, the sea, and the long Mediterranean evening. Vermouth at noon, the Gothic Quarter's alleys, a beach you can bike to after work.",
        events=[
            _ev(9, 24, "La Mercè", "🎆", "Barcelona’s big late-September party — castellers, correffoc fire-runs, fireworks over the beach"),
        ]),
    "Valencia":  _c(39.4699, -0.3763, "Europe/Madrid", "es",
             "Valencia is paella's birthplace by the sea — the Turia gardens in a drained riverbed, futuristic architecture, oranges, a gentler pace."),
})

_register("IT", {
    "Rome":     _c(41.9028, 12.4964, "Europe/Rome", "it",
             "Rome wears its ruins like everyday furniture. Espresso standing at the bar, carbonara done right, scooters past the Colosseum, golden light at dusk."),
    "Milan":    _c(45.4642, 9.1900, "Europe/Rome", "it",
             "Milan is Italy's engine — fashion, finance, and aperitivo as a sport. Foggy winters, the Duomo's spires, design week turning the whole city into a showroom."),
    "Florence": _c(43.7696, 11.2558, "Europe/Rome", "it",
             "Florence is a Renaissance open-air museum — the Duomo, the Arno at golden hour, leather markets, bistecca, and tourists you learn to weave around."),
})

_register("NL", {
    "Amsterdam": _c(52.3676, 4.9041, "Europe/Amsterdam", "nl",
             "Amsterdam runs on bikes and canals. Gabled houses, brown cafes, a flat grey sky that breaks into gold, everyone cycling through rain like it is nothing.",
        events=[
            _ev(4, 27, "King's Day", "👑", "Koningsdag — the whole city dressed in orange, canal boats packed, a citywide street market"),
        ]),
    "Rotterdam": _c(51.9244, 4.4777, "Europe/Amsterdam", "nl",
             "Rotterdam is the Netherlands rebuilt bold and modern — daring architecture, the huge port, the Markthal, a working-city grit Amsterdam lacks."),
    "The Hague": _c(52.0705, 4.3007, "Europe/Amsterdam", "nl",
             "The Hague is the stately seat of government and courts — embassies, the beach at Scheveningen, a buttoned-up diplomatic calm by the sea."),
})

_register("SE", {
    "Stockholm":  _c(59.3293, 18.0686, "Europe/Stockholm", "sv",
             "Stockholm is built on fourteen islands — water everywhere, Gamla Stan's old streets, design and fika, dark winters and luminous summer nights."),
    "Gothenburg": _c(57.7089, 11.9746, "Europe/Stockholm", "sv",
             "Gothenburg is the friendly west-coast port — seafood, canals, the archipelago a tram-and-ferry away, a down-to-earth counterweight to Stockholm."),
    "Malmö":      _c(55.6050, 13.0038, "Europe/Stockholm", "sv",
             "Malmö is the multicultural south, a bridge to Copenhagen — falafel and beaches, the Turning Torso, parks, a young and mixed everyday vibe."),
})

_register("CH", {
    "Zürich": _c(47.3769, 8.5417, "Europe/Zurich", "de",
             "Zürich is lakeside money and quiet quality — clean trams, the old town, river swims in summer, the Alps on the horizon, everything precisely on time."),
    "Geneva": _c(46.2044, 6.1432, "Europe/Zurich", "de",
             "Geneva is the international city on the lake — the Jet d'Eau, the UN, watchmakers, fondue, French spoken with a banker's calm and Alpine air."),
    "Bern":   _c(46.9480, 7.4474, "Europe/Zurich", "de",
             "Bern is the storybook capital — arcaded sandstone streets, the looping Aare you swim in summer, bears, clock towers, an unhurried cosiness."),
})

_register("PL", {
    "Warsaw": _c(52.2297, 21.0122, "Europe/Warsaw", "pl",
             "Warsaw is the phoenix capital rebuilt from rubble — a reconstructed old town beside glass towers, milk bars, vodka, a hard-earned forward drive."),
    "Kraków": _c(50.0647, 19.9450, "Europe/Warsaw", "pl",
             "Kraków is Poland's beautiful old soul — the medieval market square, Wawel castle, Kazimierz's bars, students and history on every cobbled corner."),
    "Gdańsk": _c(54.3520, 18.6466, "Europe/Warsaw", "pl",
             "Gdańsk is the Baltic port with Hanseatic gables and amber — the long waterfront, Solidarity history, a salt-air northern charm."),
})

_register("PT", {
    "Lisbon": _c(38.7223, -9.1393, "Europe/Lisbon", "pt",
             "Lisbon is hills, trams, and tiled facades over the Tagus — fado in the alleys, pastéis de nata, miradouros at sunset, a soft golden melancholy."),
    "Porto":  _c(41.1579, -8.6291, "Europe/Lisbon", "pt",
             "Porto is the gritty soulful north — port cellars across the Douro, azulejo churches, francesinha, steep granite lanes and river fog."),
})

# ── Middle East ─────────────────────────────────────────────────────────

_register("AE", {
    "Dubai":     _c(25.2048, 55.2708, "Asia/Dubai", "ar",
             "Dubai is built fast and tall on the Gulf. Malls as cities, brunch culture, desert at the edge of the highway, summers that send everyone indoors."),
    "Abu Dhabi": _c(24.4539, 54.3773, "Asia/Dubai", "ar",
             "Abu Dhabi is the calmer grander capital — the Grand Mosque, the Corniche, museums on Saadiyat, oil money turned into a measured green city."),
    "Sharjah":   _c(25.3463, 55.4209, "Asia/Dubai", "ar",
             "Sharjah is the UAE's cultural conservative emirate — museums, the Corniche, heritage souks, a quieter dry counterpoint to Dubai next door."),
})

_register("SA", {
    "Riyadh": _c(24.7136, 46.6753, "Asia/Riyadh", "ar",
             "Riyadh is the desert capital remaking itself fast — glass towers, malls, the old Diriyah mud-brick quarter, brutal summers, late-night life."),
    "Jeddah": _c(21.4858, 39.1925, "Asia/Riyadh", "ar",
             "Jeddah is the Red Sea gateway — the coral-stone old town of Al-Balad, the corniche, a more relaxed cosmopolitan port-city air than the capital."),
})

_register("IL", {
    "Tel Aviv":  _c(32.0853, 34.7818, "Asia/Jerusalem", "he",
             "Tel Aviv is the beach, Bauhaus, and nonstop — cafés, startups, the Mediterranean at the end of every street, a city that parties till dawn."),
    "Jerusalem": _c(31.7683, 35.2137, "Asia/Jerusalem", "he",
             "Jerusalem is stone and the weight of history — the walled old city's quarters, three faiths, prayer calls and church bells, a charged ancient hush."),
    "Haifa":     _c(32.7940, 34.9896, "Asia/Jerusalem", "he",
             "Haifa is the laid-back northern port up Mount Carmel — the Bahai Gardens cascading to the bay, a mixed easygoing coexistence, sea air and pine."),
})

_register("TR", {
    "Istanbul": _c(41.0082, 28.9784, "Europe/Istanbul", "tr",
             "Istanbul straddles two continents and feels it. Ferries between shores, cay in tulip glasses, the call to prayer over Bosphorus traffic, breakfasts that last hours."),
    "Ankara":   _c(39.9334, 32.8597, "Europe/Istanbul", "tr",
             "Ankara is the planned businesslike capital on the steppe — government, universities, Anitkabir, dry continental weather, far fewer tourists than Istanbul."),
    "Izmir":    _c(38.4237, 27.1428, "Europe/Istanbul", "tr",
             "Izmir is the easygoing Aegean city — a long palm-lined waterfront, sea breeze, secular and relaxed, gateway to Ephesus and the coast."),
})

# ── Latin America ───────────────────────────────────────────────────────

_register("BR", {
    "São Paulo":      _c(-23.5505, -46.6333, "America/Sao_Paulo", "pt",
             "Sao Paulo is endless and electric — concrete, rain, the best food in Brazil. Boteco happy hours, traffic that defeats you, a creative pulse under the grey."),
    "Rio de Janeiro": _c(-22.9068, -43.1729, "America/Sao_Paulo", "pt",
             "Rio is mountains crashing into the sea — Copacabana and Ipanema, Christ over the city, samba and botecos, a beauty that forgives the chaos.",
        events=[
            _ev(2, 14, "Carnaval", "🎭", "the city stops for Carnival — blocos, samba and all-night street parties (dates shift Feb to Mar)"),
        ]),
    "Brasília":       _c(-15.7939, -47.8828, "America/Sao_Paulo", "pt",
             "Brasília is the modernist capital carved from scratch — Niemeyer's curves, vast plazas and superblocks, a city you drive across under big skies."),
})

_register("MX", {
    "Mexico City":  _c(19.4326, -99.1332, "America/Mexico_City", "es",
             "CDMX is high, green, and alive at altitude. Tacos al pastor, Sunday in Chapultepec, mezcal cantinas, a sprawl that hides a thousand neighborhoods."),
    "Guadalajara":  _c(20.6597, -103.3496, "America/Mexico_City", "es",
             "Guadalajara is the heart of mariachi and tequila — colonial plazas, a big arts scene, birria, and a proud traditional Mexican soul."),
    "Monterrey":    _c(25.6866, -100.3161, "America/Monterrey", "es",
             "Monterrey is the industrial north under the Cerro de la Silla — business, mountains for hiking and climbing, cabrito, dry heat and a hard-working edge."),
})

_register("AR", {
    "Buenos Aires": _c(-34.6037, -58.3816, "America/Argentina/Buenos_Aires", "es",
             "Buenos Aires is late dinners and longer talks behind a European facade gone soft. Asado, milonga, cafe con medialunas, a melancholy the tango earned."),
    "Córdoba":      _c(-31.4201, -64.1888, "America/Argentina/Cordoba", "es",
             "Córdoba is Argentina's student city in the central hills — colonial Jesuit blocks, a big university buzz, fernet with cola, an easy interior pace."),
    "Rosario":      _c(-32.9442, -60.6505, "America/Argentina/Buenos_Aires", "es",
             "Rosario is the riverside city on the Paraná — Messi's hometown, the flag monument, beaches on the river islands, a relaxed flat-grid charm."),
})

# ── Africa ──────────────────────────────────────────────────────────────

_register("ZA", {
    "Johannesburg": _c(-26.2041, 28.0473, "Africa/Johannesburg", "en",
             "Joburg is high-veld hustle — gold-rush roots, Maboneng's revival, malls and townships, thunderstorm afternoons, the economic engine with an edge."),
    "Cape Town":    _c(-33.9249, 18.4241, "Africa/Johannesburg", "en",
             "Cape Town lives under Table Mountain between two oceans. Wind that flattens everything, wine an hour away, hikes before work, sunsets that stop conversation."),
    "Durban":       _c(-29.8587, 31.0218, "Africa/Johannesburg", "en",
             "Durban is warm Indian Ocean and curry — the beachfront promenade, bunny chow, a big Indian and Zulu mix, humid subtropical and easygoing."),
})

_register("NG", {
    "Lagos": _c(6.5244, 3.3792, "Africa/Lagos", "en",
             "Lagos is megacity energy at full volume — go-slow traffic, Afrobeats, the lagoon and Atlantic, hustle and ambition, a relentless creative pulse."),
    "Abuja": _c(9.0765, 7.3986, "Africa/Lagos", "en",
             "Abuja is the calm planned capital — wide roads, Aso Rock, government quiet, greener and more orderly than Lagos, the country's measured centre."),
})


# ── Upload ──────────────────────────────────────────────────────────────


def _supabase_url() -> str:
    return os.environ.get("SUPABASE_URL", "").rstrip("/")


def _supabase_key() -> str:
    return os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")


def _tigris_client() -> Any:
    import boto3
    from botocore.config import Config
    return boto3.client(
        "s3",
        endpoint_url=os.environ["AWS_ENDPOINT_URL_S3"],
        region_name=os.environ.get("AWS_REGION", "auto"),
        aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"],
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


def upload_one(cc: str, payload: dict, *, tigris, bucket: str,
               supabase_url: str, supabase_key: str) -> None:
    body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    key = f"{TIGRIS_PREFIX}/{cc}.json"
    tigris.put_object(Bucket=bucket, Key=key, Body=body,
                      ContentType="application/json; charset=utf-8")
    r = httpx.post(
        supabase_url + "/rest/v1/city_profiles",
        params={"on_conflict": "country_code"},
        json={"country_code": cc, "payload": payload, "source": "curated-claude-code"},
        headers={
            "apikey":        supabase_key,
            "Authorization": f"Bearer {supabase_key}",
            "Content-Type":  "application/json",
            "Prefer":        "resolution=merge-duplicates,return=minimal",
        },
        timeout=15,
    )
    if not r.is_success:
        print(f"[{cc}] Pg upsert failed: {r.status_code} {r.text[:200]}",
              file=sys.stderr)


def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")
    supabase_url = _supabase_url()
    supabase_key = _supabase_key()
    bucket = os.environ.get("BUCKET_NAME", "").strip()
    if not (supabase_url and supabase_key and bucket):
        print("Missing SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY / BUCKET_NAME",
              file=sys.stderr)
        return 1

    tigris = _tigris_client()
    total_cities = sum(len(p["cities"]) for p in CITIES.values())
    print(f"Uploading {len(CITIES)} countries / {total_cities} cities to Tigris + Pg…")
    for cc, payload in sorted(CITIES.items()):
        try:
            upload_one(cc, payload, tigris=tigris, bucket=bucket,
                       supabase_url=supabase_url, supabase_key=supabase_key)
            print(f"  ✓ {cc}  ({len(payload['cities'])} cities)")
        except Exception as exc:
            print(f"  ✗ {cc}: {exc}", file=sys.stderr)
    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
