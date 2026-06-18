"""
카카오 부동산 MCP 서버
- search_property: 실거래가 기반 매물 검색
- find_midpoint_property: 두 지점 중간 지점 매물 추천
- get_market_price: 아파트 실거래가/시세 조회
"""

import asyncio
import math
import re
from datetime import datetime, timedelta

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

from kakao_real_estate.api_client import (
    fetch_rent,
    fetch_trade,
    kakao_coord_to_region,
    kakao_keyword_search,
    kakao_nearby_places,
)

VALID_PROPERTY_TYPES = ["아파트", "오피스텔", "연립다세대"]
from kakao_real_estate.region_code import find_region_code, get_sido

load_dotenv()

mcp = FastMCP(
    "kakao-real-estate",
    host="0.0.0.0",
    port=8000,
    transport_security={"enable_dns_rebinding_protection": False},
)


def _recent_months(n: int = 3) -> list[str]:
    now = datetime.now()
    months = []
    for i in range(n):
        dt = now - timedelta(days=30 * i)
        months.append(dt.strftime("%Y%m"))
    return months


def _pyeong(area_m2: float) -> float:
    return round(area_m2 / 3.306, 1)


def _format_price(price_str: str) -> str:
    price = int(price_str.replace(",", "").strip())
    if price >= 10000:
        억 = price // 10000
        나머지 = price % 10000
        if 나머지 > 0:
            return f"{억}억 {나머지:,}만원"
        return f"{억}억"
    return f"{price:,}만원"


def _haversine(x1: float, y1: float, x2: float, y2: float) -> float:
    R = 6371
    dx = math.radians(x2 - x1)
    dy = math.radians(y2 - y1)
    a = math.sin(dy / 2) ** 2 + math.cos(math.radians(y1)) * math.cos(math.radians(y2)) * math.sin(dx / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))


def _extract_dong(keyword: str) -> str | None:
    """입력에서 '~동' 패턴을 추출한다. (예: '화곡동' → '화곡동', '서울 화곡동' → '화곡동')"""
    m = re.search(r"(\S*동)\b", keyword)
    if m:
        return m.group(1)
    return None


def _filter_by_dong(items: list[dict], dong: str) -> list[dict]:
    """법정동 이름으로 필터링. '화곡동' → 법정동에 '화곡' 포함된 것만."""
    dong_name = dong.replace("동", "")
    return [item for item in items if dong_name in item.get("법정동", "")]


def _format_item(item: dict, index: int, trade_type: str, region_name: str = "", region_code: str = "") -> list[str]:
    """매물 한 건을 포맷팅된 문자열 리스트로 변환"""
    lines = []
    apt = item.get("아파트", "정보없음")
    dong = item.get("법정동", "")
    prop_type = item.get("매물종류", "아파트")
    area = float(item.get("전용면적", "0"))
    floor = item.get("층", "?")
    build_year = item.get("건축년도", "")
    year = item.get("년", "")
    month = item.get("월", "")
    day = item.get("일", "")
    station_info = item.get("_nearest_station", "")
    school_info = item.get("_nearest_school", "")
    childcare_info = item.get("_nearest_childcare", "")

    # 이름이 지번만 있는 경우 보완 (예: "(918-15)" → "화곡동 918-15 오피스텔")
    if apt.startswith("(") and apt.endswith(")"):
        jibun = apt[1:-1]
        apt = f"{dong} {jibun} {prop_type}"

    jibun = item.get("지번", "")
    build_info = f" | 건축 {build_year}년" if build_year else ""
    sido = get_sido(region_code) if region_code else ""
    address_parts = [sido, region_name, dong, jibun]
    address = " ".join(p for p in address_parts if p)

    if trade_type == "매매":
        price_display = _format_price(item.get("거래금액", "0"))
        lines.append(f"{index}. 🏢 {apt} ({address})")
        lines.append(f"   면적: {area}㎡ ({_pyeong(area)}평) | {floor}층{build_info}")
        lines.append(f"   매매가: {price_display}")
        lines.append(f"   거래일: {year}.{month}.{day}")
    else:
        deposit = _format_price(item.get("보증금액", "0"))
        monthly = item.get("월세금액", "0").strip()
        if monthly and monthly != "0":
            price_display = f"보증금 {deposit} / 월세 {int(float(monthly.replace(',', ''))):,}만원"
        else:
            price_display = f"전세 {deposit}"
        lines.append(f"{index}. 🏢 {apt} ({address})")
        lines.append(f"   면적: {area}㎡ ({_pyeong(area)}평) | {floor}층{build_info}")
        lines.append(f"   💰 {price_display}")
        lines.append(f"   📅 거래일: {year}.{month}.{day}")
    if station_info or school_info or childcare_info:
        lines.append(f"   ─────────────────────")
    if station_info:
        lines.append(f"   🚇 근처 역")
        for s in station_info.split(" / "):
            lines.append(f"      • {s}")
    if school_info or childcare_info:
        lines.append(f"   🏫 주변 학군")
        if school_info:
            for s in school_info.split(" / "):
                lines.append(f"      • {s}")
        if childcare_info:
            for s in childcare_info.split(" / "):
                lines.append(f"      • {s}")
    lines.append("")
    return lines


def _format_distance(dist_str: str) -> str:
    """거리 문자열을 '거리 + 도보 시간'으로 변환 (도보 평균 시속 4km)"""
    if not dist_str:
        return ""
    dist_m = int(dist_str)
    walk_min = round(dist_m / 67)  # 4km/h ≈ 67m/min
    if dist_m >= 1000:
        return f"{dist_m / 1000:.1f}km, 도보 {walk_min}분"
    return f"{dist_m}m, 도보 {walk_min}분"


async def _add_nearby_info(items: list[dict], region_name: str) -> None:
    """매물 리스트에 근처 지하철역 + 학교 + 어린이집 정보를 추가"""
    dong_cache: dict[str, dict[str, str]] = {}
    for item in items:
        dong = item.get("법정동", "")
        if not dong:
            continue
        if dong not in dong_cache:
            stations, schools, childcares = await asyncio.gather(
                kakao_nearby_places(dong, region_name, "SW8", 2),
                kakao_nearby_places(dong, region_name, "SC4", 2),
                kakao_nearby_places(dong, region_name, "PS3", 2),
            )
            cache_entry: dict[str, str] = {}

            # 지하철역
            if stations:
                parts = []
                for s in stations[:2]:
                    dist_info = _format_distance(s.get("distance", ""))
                    parts.append(f"{s['name']} ({dist_info})" if dist_info else s["name"])
                cache_entry["station"] = " / ".join(parts)

            # 학교
            if schools:
                parts = []
                for s in schools[:2]:
                    dist_info = _format_distance(s.get("distance", ""))
                    parts.append(f"{s['name']} ({dist_info})" if dist_info else s["name"])
                cache_entry["school"] = " / ".join(parts)

            # 어린이집/유치원
            if childcares:
                parts = []
                for s in childcares[:2]:
                    dist_info = _format_distance(s.get("distance", ""))
                    parts.append(f"{s['name']} ({dist_info})" if dist_info else s["name"])
                cache_entry["childcare"] = " / ".join(parts)

            dong_cache[dong] = cache_entry

        info = dong_cache[dong]
        if info.get("station"):
            item["_nearest_station"] = info["station"]
        if info.get("school"):
            item["_nearest_school"] = info["school"]
        if info.get("childcare"):
            item["_nearest_childcare"] = info["childcare"]


async def _resolve_region(keyword: str) -> tuple[str, str, str | None] | None:
    """키워드에서 (구이름, 구코드, 동이름|None)을 반환"""
    dong = _extract_dong(keyword)

    # 먼저 내장 코드에서 검색
    result = find_region_code(keyword)
    if result:
        return result[0], result[1], dong

    # 카카오맵으로 검색
    coord = await kakao_keyword_search(keyword)
    if coord:
        region_name = await kakao_coord_to_region(coord["x"], coord["y"])
        if region_name:
            result = find_region_code(region_name)
            if result:
                # 카카오맵 주소에서 동 이름 추출 시도
                if not dong:
                    dong = _extract_dong(coord.get("address", ""))
                return result[0], result[1], dong

    return None


# ──────────────────────────────────────────────
# Tool 1: 매물 검색 (실거래가 기반)
# ──────────────────────────────────────────────
@mcp.tool()
async def search_property(
    region: str,
    property_type: str = "아파트",
    trade_type: str = "전세",
    min_price: int = 0,
    max_price: int = 999999,
    max_results: int = 5,
) -> str:
    """지역 기반 부동산 실거래 매물을 검색합니다.

    Args:
        region: 검색할 지역 (예: '강남구', '강남역', '화곡동', '서울 마포구 공덕동')
        property_type: 매물 종류 - '아파트', '오피스텔', '연립다세대' 중 하나 (기본값: 아파트). 빌라는 '연립다세대'로 검색.
        trade_type: 거래 유형 - '매매', '전세', '월세' 중 하나 (기본값: 전세)
        min_price: 최소 가격 (만원 단위, 기본값: 0)
        max_price: 최대 가격 (만원 단위, 기본값: 999999)
        max_results: 최대 결과 수 (기본값: 5)
    """
    if property_type == "빌라":
        property_type = "연립다세대"
    if property_type not in VALID_PROPERTY_TYPES:
        return f"매물 종류는 '아파트', '오피스텔', '연립다세대(빌라)' 중 하나를 선택해 주세요."
    resolved = await _resolve_region(region)
    if not resolved:
        return f"'{region}'에 해당하는 지역을 찾을 수 없습니다. 구 이름이나 역 이름으로 검색해 주세요."

    region_name, region_code, dong = resolved
    months = _recent_months(3)

    all_items: list[dict] = []
    for ym in months:
        if trade_type == "매매":
            items = await fetch_trade(region_code, ym, property_type)
        else:
            items = await fetch_rent(region_code, ym, property_type)
        all_items.extend(items)

    # 동 필터링
    if dong:
        all_items = _filter_by_dong(all_items, dong)

    # 가격 필터링
    filtered = []
    for item in all_items:
        if trade_type == "매매":
            price_str = item.get("거래금액", "0")
        else:
            price_str = item.get("보증금액", item.get("거래금액", "0"))
        price = int(price_str.replace(",", "").strip())
        if min_price <= price <= max_price:
            item["_price"] = price
            filtered.append(item)

    filtered.sort(key=lambda x: (x.get("년", ""), x.get("월", ""), x.get("일", "")), reverse=True)
    filtered = filtered[:max_results]

    display_name = f"{region_name} {dong}" if dong else region_name
    if not filtered:
        # 전체 매물에서 최저가 정보 제공
        all_prices = []
        for item in all_items:
            if trade_type == "매매":
                p = item.get("거래금액", "0")
            else:
                p = item.get("보증금액", item.get("거래금액", "0"))
            all_prices.append(int(p.replace(",", "").strip()))
        if all_prices:
            min_p = _format_price(str(min(all_prices)))
            avg_p = _format_price(str(sum(all_prices) // len(all_prices)))
            return (
                f"{display_name} 지역에서 조건에 맞는 {trade_type} 거래 기록이 없습니다.\n"
                f"참고: 해당 지역 {property_type} {trade_type} 최저가는 {min_p}이며, 평균 {avg_p}입니다. (최근 3개월, {len(all_prices)}건)"
            )
        # 다른 매물 종류 시세 참고 제공
        alt_types = [t for t in VALID_PROPERTY_TYPES if t != property_type]
        alt_info = []
        for alt in alt_types:
            alt_items: list[dict] = []
            for ym in months:
                if trade_type == "매매":
                    alt_items.extend(await fetch_trade(region_code, ym, alt))
                else:
                    alt_items.extend(await fetch_rent(region_code, ym, alt))
            if dong:
                alt_items = _filter_by_dong(alt_items, dong)
            if alt_items:
                prices = []
                for item in alt_items:
                    if trade_type == "매매":
                        p = item.get("거래금액", "0")
                    else:
                        p = item.get("보증금액", item.get("거래금액", "0"))
                    prices.append(int(p.replace(",", "").strip()))
                min_p = _format_price(str(min(prices)))
                alt_info.append(f"- {alt} {trade_type}: 최저 {min_p} ({len(prices)}건)")
        msg = f"{display_name} 지역에서 최근 3개월 내 {property_type} {trade_type} 거래 기록이 없습니다."
        if alt_info:
            msg += f"\n\n참고로 같은 지역의 다른 매물 종류 시세입니다:\n" + "\n".join(alt_info)
        return msg

    await _add_nearby_info(filtered, region_name)

    lines = [f"📍 {display_name} 최근 {trade_type} 실거래 내역 (최근 3개월)\n"]
    for i, item in enumerate(filtered, 1):
        lines.extend(_format_item(item, i, trade_type, region_name, region_code))

    return "\n".join(lines)


# ──────────────────────────────────────────────
# Tool 2: 중간 지점 매물 추천
# ──────────────────────────────────────────────
@mcp.tool()
async def find_midpoint_property(
    location_a: str,
    location_b: str,
    property_type: str = "아파트",
    trade_type: str = "전세",
    max_price: int = 999999,
    max_results: int = 5,
) -> str:
    """두 직장/학교의 중간 지점에서 부동산 매물을 추천합니다. 공동 거주자의 통근을 고려한 최적 위치를 찾습니다.

    Args:
        location_a: 첫 번째 출발지 (예: '판교역', '삼성전자', '서울대학교')
        location_b: 두 번째 출발지 (예: '여의도역', 'LG트윈타워', '고려대학교')
        property_type: 매물 종류 - '아파트', '오피스텔', '연립다세대' 중 하나 (기본값: 아파트). 빌라는 '연립다세대'로 검색.
        trade_type: 거래 유형 - '매매', '전세', '월세' 중 하나 (기본값: 전세)
        max_price: 최대 가격 (만원 단위, 기본값: 999999)
        max_results: 최대 결과 수 (기본값: 5)
    """
    if property_type == "빌라":
        property_type = "연립다세대"
    if property_type not in VALID_PROPERTY_TYPES:
        return f"매물 종류는 '아파트', '오피스텔', '연립다세대(빌라)' 중 하나를 선택해 주세요."
    coord_a = await kakao_keyword_search(location_a)
    coord_b = await kakao_keyword_search(location_b)

    if not coord_a:
        return f"'{location_a}'의 위치를 찾을 수 없습니다."
    if not coord_b:
        return f"'{location_b}'의 위치를 찾을 수 없습니다."

    mid_x = (coord_a["x"] + coord_b["x"]) / 2
    mid_y = (coord_a["y"] + coord_b["y"]) / 2

    mid_region = await kakao_coord_to_region(mid_x, mid_y)
    if not mid_region:
        return "중간 지점의 행정구역을 확인할 수 없습니다."

    result = find_region_code(mid_region)
    if not result:
        return f"중간 지점 '{mid_region}'의 지역 코드를 찾을 수 없습니다."

    region_name, region_code = result

    dist_a = _haversine(coord_a["x"], coord_a["y"], mid_x, mid_y)
    dist_b = _haversine(coord_b["x"], coord_b["y"], mid_x, mid_y)

    months = _recent_months(3)
    all_items: list[dict] = []
    for ym in months:
        if trade_type == "매매":
            items = await fetch_trade(region_code, ym, property_type)
        else:
            items = await fetch_rent(region_code, ym, property_type)
        all_items.extend(items)

    filtered = []
    for item in all_items:
        if trade_type == "매매":
            price_str = item.get("거래금액", "0")
        else:
            price_str = item.get("보증금액", item.get("거래금액", "0"))
        price = int(price_str.replace(",", "").strip())
        if price <= max_price:
            item["_price"] = price
            filtered.append(item)

    seen: dict[str, dict] = {}
    filtered.sort(key=lambda x: (x.get("년", ""), x.get("월", ""), x.get("일", "")), reverse=True)
    for item in filtered:
        key = f"{item.get('아파트', '')}_{item.get('전용면적', '')}"
        if key not in seen:
            seen[key] = item
    unique_items = list(seen.values())[:max_results]

    lines = [
        f"🏠 두 지점의 중간 지점 매물 추천\n",
        f"📌 A: {coord_a['place_name']} ({coord_a['address']})",
        f"📌 B: {coord_b['place_name']} ({coord_b['address']})",
        f"📍 중간 지점: {region_name} (A에서 약 {dist_a:.1f}km, B에서 약 {dist_b:.1f}km)\n",
    ]

    if not unique_items:
        lines.append(f"{region_name} 지역에서 최근 3개월 내 조건에 맞는 {trade_type} 거래 기록이 없습니다.")
        return "\n".join(lines)

    await _add_nearby_info(unique_items, region_name)

    lines.append(f"최근 {trade_type} 실거래 내역:\n")
    for i, item in enumerate(unique_items, 1):
        lines.extend(_format_item(item, i, trade_type, region_name, region_code))

    return "\n".join(lines)


# ──────────────────────────────────────────────
# Tool 3: 실거래가/시세 조회
# ──────────────────────────────────────────────
@mcp.tool()
async def get_market_price(
    apartment_name: str,
    region: str = "",
    property_type: str = "아파트",
    months: int = 6,
) -> str:
    """부동산의 실거래가(매매/전월세) 시세를 조회합니다. 최근 거래 가격 추이를 확인할 수 있습니다.

    Args:
        apartment_name: 건물 이름 (예: '래미안푸르지오', '반포자이', '헬리오시티')
        region: 지역 (예: '마포구', '서초구', '화곡동'). 비워두면 카카오맵에서 자동 검색합니다.
        property_type: 매물 종류 - '아파트', '오피스텔', '연립다세대' 중 하나 (기본값: 아파트). 빌라는 '연립다세대'로 검색.
        months: 조회할 기간 (최근 n개월, 기본값: 6, 최대: 12)
    """
    if property_type == "빌라":
        property_type = "연립다세대"
    if property_type not in VALID_PROPERTY_TYPES:
        return f"매물 종류는 '아파트', '오피스텔', '연립다세대(빌라)' 중 하나를 선택해 주세요."
    months = min(months, 12)

    if region:
        resolved = await _resolve_region(region)
    else:
        coord = await kakao_keyword_search(f"{apartment_name} 아파트")
        if coord:
            region_name = await kakao_coord_to_region(coord["x"], coord["y"])
            if region_name:
                r = find_region_code(region_name)
                dong = _extract_dong(coord.get("address", ""))
                resolved = (r[0], r[1], dong) if r else None
            else:
                resolved = None
        else:
            resolved = None

    if not resolved:
        return f"'{apartment_name}'의 위치를 특정할 수 없습니다. region 파라미터에 구 이름을 함께 입력해 주세요. (예: region='마포구')"

    region_name, region_code, dong = resolved
    month_list = _recent_months(months)

    trade_items: list[dict] = []
    rent_items: list[dict] = []

    for ym in month_list:
        trades = await fetch_trade(region_code, ym, property_type)
        rents = await fetch_rent(region_code, ym, property_type)
        trade_items.extend(trades)
        rent_items.extend(rents)

    def match_apt(item: dict) -> bool:
        apt = item.get("아파트", "")
        return apartment_name.replace(" ", "") in apt.replace(" ", "")

    matched_trades = [i for i in trade_items if match_apt(i)]
    matched_rents = [i for i in rent_items if match_apt(i)]

    # 동 필터링
    if dong:
        matched_trades = _filter_by_dong(matched_trades, dong) or matched_trades
        matched_rents = _filter_by_dong(matched_rents, dong) or matched_rents

    if not matched_trades and not matched_rents:
        display = f"{region_name} {dong}" if dong else region_name
        return f"{display} 지역에서 '{apartment_name}'의 최근 {months}개월 거래 기록을 찾을 수 없습니다."

    display_name = f"{region_name} {dong}" if dong else region_name
    lines = [f"📊 {apartment_name} 실거래가 시세 ({display_name}, 최근 {months}개월)\n"]

    if matched_trades:
        matched_trades.sort(key=lambda x: (x.get("년", ""), x.get("월", ""), x.get("일", "")), reverse=True)
        lines.append(f"🔹 매매 거래 ({len(matched_trades)}건)")
        lines.append("-" * 40)

        by_size: dict[str, list[dict]] = {}
        for item in matched_trades:
            area = float(item.get("전용면적", "0"))
            pyeong = round(_pyeong(area))
            key = f"{pyeong}평({area}㎡)"
            by_size.setdefault(key, []).append(item)

        for size_label, items in by_size.items():
            prices = [int(i["거래금액"].replace(",", "").strip()) for i in items if "거래금액" in i]
            if prices:
                avg = sum(prices) // len(prices)
                lines.append(f"  {size_label}: 평균 {_format_price(str(avg))} (최저 {_format_price(str(min(prices)))} ~ 최고 {_format_price(str(max(prices)))})")

        lines.append("")
        lines.append("  최근 거래:")
        for item in matched_trades[:5]:
            area = float(item.get("전용면적", "0"))
            price = _format_price(item.get("거래금액", "0"))
            floor = item.get("층", "?")
            date = f"{item.get('년', '')}.{item.get('월', '')}.{item.get('일', '')}"
            lines.append(f"    {date} | {_pyeong(area)}평 | {floor}층 | {price}")
        lines.append("")

    if matched_rents:
        matched_rents.sort(key=lambda x: (x.get("년", ""), x.get("월", ""), x.get("일", "")), reverse=True)
        lines.append(f"🔹 전월세 거래 ({len(matched_rents)}건)")
        lines.append("-" * 40)

        by_size: dict[str, list[dict]] = {}
        for item in matched_rents:
            area = float(item.get("전용면적", "0"))
            pyeong = round(_pyeong(area))
            key = f"{pyeong}평({area}㎡)"
            by_size.setdefault(key, []).append(item)

        for size_label, items in by_size.items():
            deposits = [int(i.get("보증금액", "0").replace(",", "").strip()) for i in items if i.get("보증금액")]
            if deposits:
                avg = sum(deposits) // len(deposits)
                lines.append(f"  {size_label}: 보증금 평균 {_format_price(str(avg))}")

        lines.append("")
        lines.append("  최근 거래:")
        for item in matched_rents[:5]:
            area = float(item.get("전용면적", "0"))
            deposit = _format_price(item.get("보증금액", "0"))
            monthly = item.get("월세금액", "0").strip()
            floor = item.get("층", "?")
            date = f"{item.get('년', '')}.{item.get('월', '')}.{item.get('일', '')}"
            if monthly and monthly != "0":
                lines.append(f"    {date} | {_pyeong(area)}평 | {floor}층 | 보증금 {deposit} / 월세 {int(float(monthly.replace(',', ''))):,}만원")
            else:
                lines.append(f"    {date} | {_pyeong(area)}평 | {floor}층 | 전세 {deposit}")
        lines.append("")

    return "\n".join(lines)


def main():
    import sys

    if "--stdio" in sys.argv:
        mcp.run(transport="stdio")
    else:
        mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
