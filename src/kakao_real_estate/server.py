"""
카카오 부동산 MCP 서버
- search_property: 실거래가 기반 매물 검색
- find_midpoint_property: 두 지점 중간 지점 매물 추천
- get_market_price: 아파트 실거래가/시세 조회
"""

import math
import re
from datetime import datetime, timedelta

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

from kakao_real_estate.api_client import (
    fetch_apt_rent,
    fetch_apt_trade,
    kakao_coord_to_region,
    kakao_keyword_search,
)
from kakao_real_estate.region_code import find_region_code

load_dotenv()

mcp = FastMCP("kakao-real-estate")


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


def _format_item(item: dict, index: int, trade_type: str) -> list[str]:
    """매물 한 건을 포맷팅된 문자열 리스트로 변환"""
    lines = []
    apt = item.get("아파트", "정보없음")
    dong = item.get("법정동", "")
    area = float(item.get("전용면적", "0"))
    floor = item.get("층", "?")
    year = item.get("년", "")
    month = item.get("월", "")
    day = item.get("일", "")

    if trade_type == "매매":
        price_display = _format_price(item.get("거래금액", "0"))
        lines.append(f"{index}. {apt} ({dong})")
        lines.append(f"   면적: {area}㎡ ({_pyeong(area)}평) | {floor}층")
        lines.append(f"   매매가: {price_display}")
        lines.append(f"   거래일: {year}.{month}.{day}")
    else:
        deposit = _format_price(item.get("보증금액", "0"))
        monthly = item.get("월세금액", "0").strip()
        if monthly and monthly != "0":
            price_display = f"보증금 {deposit} / 월세 {int(monthly):,}만원"
        else:
            price_display = f"전세 {deposit}"
        lines.append(f"{index}. {apt} ({dong})")
        lines.append(f"   면적: {area}㎡ ({_pyeong(area)}평) | {floor}층")
        lines.append(f"   {price_display}")
        lines.append(f"   거래일: {year}.{month}.{day}")
    lines.append("")
    return lines


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
    trade_type: str = "전세",
    min_price: int = 0,
    max_price: int = 999999,
    max_results: int = 5,
) -> str:
    """지역 기반 부동산 실거래 매물을 검색합니다.

    Args:
        region: 검색할 지역 (예: '강남구', '강남역', '화곡동', '서울 마포구 공덕동')
        trade_type: 거래 유형 - '매매', '전세', '월세' 중 하나 (기본값: 전세)
        min_price: 최소 가격 (만원 단위, 기본값: 0)
        max_price: 최대 가격 (만원 단위, 기본값: 999999)
        max_results: 최대 결과 수 (기본값: 5)
    """
    resolved = await _resolve_region(region)
    if not resolved:
        return f"'{region}'에 해당하는 지역을 찾을 수 없습니다. 구 이름이나 역 이름으로 검색해 주세요."

    region_name, region_code, dong = resolved
    months = _recent_months(3)

    all_items: list[dict] = []
    for ym in months:
        if trade_type == "매매":
            items = await fetch_apt_trade(region_code, ym)
        else:
            items = await fetch_apt_rent(region_code, ym)
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
        return f"{display_name} 지역에서 최근 3개월 내 조건에 맞는 {trade_type} 거래 기록이 없습니다."

    lines = [f"📍 {display_name} 최근 {trade_type} 실거래 내역 (최근 3개월)\n"]
    for i, item in enumerate(filtered, 1):
        lines.extend(_format_item(item, i, trade_type))

    return "\n".join(lines)


# ──────────────────────────────────────────────
# Tool 2: 중간 지점 매물 추천
# ──────────────────────────────────────────────
@mcp.tool()
async def find_midpoint_property(
    location_a: str,
    location_b: str,
    trade_type: str = "전세",
    max_price: int = 999999,
    max_results: int = 5,
) -> str:
    """두 직장/학교의 중간 지점에서 부동산 매물을 추천합니다. 공동 거주자의 통근을 고려한 최적 위치를 찾습니다.

    Args:
        location_a: 첫 번째 출발지 (예: '판교역', '삼성전자', '서울대학교')
        location_b: 두 번째 출발지 (예: '여의도역', 'LG트윈타워', '고려대학교')
        trade_type: 거래 유형 - '매매', '전세', '월세' 중 하나 (기본값: 전세)
        max_price: 최대 가격 (만원 단위, 기본값: 999999)
        max_results: 최대 결과 수 (기본값: 5)
    """
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
            items = await fetch_apt_trade(region_code, ym)
        else:
            items = await fetch_apt_rent(region_code, ym)
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

    lines.append(f"최근 {trade_type} 실거래 내역:\n")
    for i, item in enumerate(unique_items, 1):
        lines.extend(_format_item(item, i, trade_type))

    return "\n".join(lines)


# ──────────────────────────────────────────────
# Tool 3: 실거래가/시세 조회
# ──────────────────────────────────────────────
@mcp.tool()
async def get_market_price(
    apartment_name: str,
    region: str = "",
    months: int = 6,
) -> str:
    """아파트의 실거래가(매매/전월세) 시세를 조회합니다. 최근 거래 가격 추이를 확인할 수 있습니다.

    Args:
        apartment_name: 아파트 이름 (예: '래미안푸르지오', '반포자이', '헬리오시티')
        region: 지역 (예: '마포구', '서초구', '화곡동'). 비워두면 카카오맵에서 자동 검색합니다.
        months: 조회할 기간 (최근 n개월, 기본값: 6, 최대: 12)
    """
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
        trades = await fetch_apt_trade(region_code, ym)
        rents = await fetch_apt_rent(region_code, ym)
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
                lines.append(f"    {date} | {_pyeong(area)}평 | {floor}층 | 보증금 {deposit} / 월세 {int(monthly):,}만원")
            else:
                lines.append(f"    {date} | {_pyeong(area)}평 | {floor}층 | 전세 {deposit}")
        lines.append("")

    return "\n".join(lines)


def main():
    import os
    import sys

    transport = "streamable-http"

    for arg in sys.argv[1:]:
        if arg == "--stdio":
            transport = "stdio"

    if transport == "stdio":
        mcp.run(transport="stdio")
    else:
        os.environ.setdefault("UVICORN_HOST", "0.0.0.0")
        os.environ.setdefault("UVICORN_PORT", "8000")
        mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
