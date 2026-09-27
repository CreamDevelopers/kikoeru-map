from __future__ import annotations

import math
from dataclasses import dataclass

JAPAN_LAT_MIN, JAPAN_LAT_MAX = 20.0, 46.0
JAPAN_LNG_MIN, JAPAN_LNG_MAX = 122.0, 154.0

EARTH_RADIUS_M = 6_371_008.8

BLUR_METERS = {"exact": 0, "100m": 100, "500m": 500}


def in_japan(lat: float, lng: float) -> bool:
    return JAPAN_LAT_MIN <= lat <= JAPAN_LAT_MAX and JAPAN_LNG_MIN <= lng <= JAPAN_LNG_MAX


def blur_point(lat: float, lng: float, precision: str) -> tuple[float, float]:
    # ランダムにずらすと複数投稿の平均から正確な位置が推定できるため、グリッドの中心に丸める
    meters = BLUR_METERS.get(precision, 0)
    if meters == 0:
        return round(lat, 6), round(lng, 6)
    dlat = meters / 111_320.0
    lat_cell = math.floor(lat / dlat)
    blat = (lat_cell + 0.5) * dlat
    dlng = meters / (111_320.0 * math.cos(math.radians(blat)))
    blng = (math.floor(lng / dlng) + 0.5) * dlng
    return round(blat, 6), round(blng, 6)


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(a)))


def lnglat_to_tile(lng: float, lat: float, z: int) -> tuple[int, int]:
    lat = max(min(lat, 85.05112878), -85.05112878)
    n = 2**z
    x = int((lng + 180.0) / 360.0 * n)
    lat_r = math.radians(lat)
    y = int((1.0 - math.asinh(math.tan(lat_r)) / math.pi) / 2.0 * n)
    return max(0, min(n - 1, x)), max(0, min(n - 1, y))


def tile_to_lnglat(x: int, y: int, z: int) -> tuple[float, float]:
    n = 2**z
    lng = x / n * 360.0 - 180.0
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    return lng, lat


@dataclass(frozen=True)
class Prefecture:
    code: int
    ja: str
    en: str
    region: str
    lat: float
    lng: float


REGIONS: dict[str, tuple[str, str]] = {
    "hokkaido": ("北海道", "Hokkaido"),
    "tohoku": ("東北", "Tohoku"),
    "kanto": ("関東", "Kanto"),
    "chubu": ("中部", "Chubu"),
    "kinki": ("近畿", "Kinki"),
    "chugoku": ("中国", "Chugoku"),
    "shikoku": ("四国", "Shikoku"),
    "kyushu": ("九州・沖縄", "Kyushu & Okinawa"),
}

PREFECTURES: list[Prefecture] = [
    Prefecture(1, "北海道", "Hokkaido", "hokkaido", 43.0642, 141.3469),
    Prefecture(2, "青森県", "Aomori", "tohoku", 40.8244, 140.7400),
    Prefecture(3, "岩手県", "Iwate", "tohoku", 39.7036, 141.1527),
    Prefecture(4, "宮城県", "Miyagi", "tohoku", 38.2688, 140.8721),
    Prefecture(5, "秋田県", "Akita", "tohoku", 39.7186, 140.1024),
    Prefecture(6, "山形県", "Yamagata", "tohoku", 38.2404, 140.3633),
    Prefecture(7, "福島県", "Fukushima", "tohoku", 37.7503, 140.4676),
    Prefecture(8, "茨城県", "Ibaraki", "kanto", 36.3418, 140.4468),
    Prefecture(9, "栃木県", "Tochigi", "kanto", 36.5657, 139.8836),
    Prefecture(10, "群馬県", "Gunma", "kanto", 36.3907, 139.0604),
    Prefecture(11, "埼玉県", "Saitama", "kanto", 35.8570, 139.6489),
    Prefecture(12, "千葉県", "Chiba", "kanto", 35.6051, 140.1233),
    Prefecture(13, "東京都", "Tokyo", "kanto", 35.6895, 139.6917),
    Prefecture(14, "神奈川県", "Kanagawa", "kanto", 35.4478, 139.6425),
    Prefecture(15, "新潟県", "Niigata", "chubu", 37.9026, 139.0236),
    Prefecture(16, "富山県", "Toyama", "chubu", 36.6953, 137.2113),
    Prefecture(17, "石川県", "Ishikawa", "chubu", 36.5947, 136.6256),
    Prefecture(18, "福井県", "Fukui", "chubu", 36.0652, 136.2216),
    Prefecture(19, "山梨県", "Yamanashi", "chubu", 35.6642, 138.5684),
    Prefecture(20, "長野県", "Nagano", "chubu", 36.6513, 138.1810),
    Prefecture(21, "岐阜県", "Gifu", "chubu", 35.3912, 136.7223),
    Prefecture(22, "静岡県", "Shizuoka", "chubu", 34.9769, 138.3831),
    Prefecture(23, "愛知県", "Aichi", "chubu", 35.1802, 136.9066),
    Prefecture(24, "三重県", "Mie", "kinki", 34.7303, 136.5086),
    Prefecture(25, "滋賀県", "Shiga", "kinki", 35.0045, 135.8686),
    Prefecture(26, "京都府", "Kyoto", "kinki", 35.0214, 135.7556),
    Prefecture(27, "大阪府", "Osaka", "kinki", 34.6863, 135.5200),
    Prefecture(28, "兵庫県", "Hyogo", "kinki", 34.6913, 135.1830),
    Prefecture(29, "奈良県", "Nara", "kinki", 34.6851, 135.8329),
    Prefecture(30, "和歌山県", "Wakayama", "kinki", 34.2260, 135.1675),
    Prefecture(31, "鳥取県", "Tottori", "chugoku", 35.5036, 134.2383),
    Prefecture(32, "島根県", "Shimane", "chugoku", 35.4723, 133.0505),
    Prefecture(33, "岡山県", "Okayama", "chugoku", 34.6618, 133.9344),
    Prefecture(34, "広島県", "Hiroshima", "chugoku", 34.3966, 132.4596),
    Prefecture(35, "山口県", "Yamaguchi", "chugoku", 34.1859, 131.4714),
    Prefecture(36, "徳島県", "Tokushima", "shikoku", 34.0658, 134.5593),
    Prefecture(37, "香川県", "Kagawa", "shikoku", 34.3401, 134.0434),
    Prefecture(38, "愛媛県", "Ehime", "shikoku", 33.8417, 132.7661),
    Prefecture(39, "高知県", "Kochi", "shikoku", 33.5597, 133.5311),
    Prefecture(40, "福岡県", "Fukuoka", "kyushu", 33.6064, 130.4181),
    Prefecture(41, "佐賀県", "Saga", "kyushu", 33.2494, 130.2988),
    Prefecture(42, "長崎県", "Nagasaki", "kyushu", 32.7448, 129.8737),
    Prefecture(43, "熊本県", "Kumamoto", "kyushu", 32.7898, 130.7417),
    Prefecture(44, "大分県", "Oita", "kyushu", 33.2381, 131.6126),
    Prefecture(45, "宮崎県", "Miyazaki", "kyushu", 31.9111, 131.4239),
    Prefecture(46, "鹿児島県", "Kagoshima", "kyushu", 31.5602, 130.5581),
    Prefecture(47, "沖縄県", "Okinawa", "kyushu", 26.2124, 127.6809),
]

PREF_BY_CODE: dict[int, Prefecture] = {p.code: p for p in PREFECTURES}


def region_pref_codes(region: str) -> list[int]:
    return [p.code for p in PREFECTURES if p.region == region]
