"""
외부 API 호출 클라이언트 (국토교통부 실거래가 + 카카오맵)
"""

import os
import httpx
from xml.etree import ElementTree

def _data_key() -> str:
    return os.getenv("DATA_GO_KR_API_KEY", "")

def _kakao_key() -> str:
    return os.getenv("KAKAO_REST_API_KEY", "")

# 국토교통부 실거래가 API 엔드포인트
API_ENDPOINTS = {
    # 아파트
    ("아파트", "매매"): "https://apis.data.go.kr/1613000/RTMSDataSvcAptTrade/getRTMSDataSvcAptTrade",
    ("아파트", "전월세"): "https://apis.data.go.kr/1613000/RTMSDataSvcAptRent/getRTMSDataSvcAptRent",
    # 오피스텔
    ("오피스텔", "매매"): "https://apis.data.go.kr/1613000/RTMSDataSvcOffiTrade/getRTMSDataSvcOffiTrade",
    ("오피스텔", "전월세"): "https://apis.data.go.kr/1613000/RTMSDataSvcOffiRent/getRTMSDataSvcOffiRent",
    # 연립다세대 (빌라)
    ("연립다세대", "매매"): "https://apis.data.go.kr/1613000/RTMSDataSvcRHTrade/getRTMSDataSvcRHTrade",
    ("연립다세대", "전월세"): "https://apis.data.go.kr/1613000/RTMSDataSvcRHRent/getRTMSDataSvcRHRent",
}

# 카카오 API
KAKAO_KEYWORD_SEARCH_URL = "https://dapi.kakao.com/v2/local/search/keyword.json"


async def fetch_trade(region_code: str, deal_ymd: str, property_type: str = "아파트") -> list[dict]:
    """매매 실거래가 조회"""
    url = API_ENDPOINTS.get((property_type, "매매"))
    if not url:
        return []
    params = {
        "serviceKey": _data_key(),
        "LAWD_CD": region_code,
        "DEAL_YMD": deal_ymd,
        "numOfRows": "100",
        "pageNo": "1",
    }
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
        return _parse_molit_xml(resp.text, trade_type="매매", property_type=property_type)
    except httpx.HTTPStatusError:
        return []


async def fetch_rent(region_code: str, deal_ymd: str, property_type: str = "아파트") -> list[dict]:
    """전월세 실거래가 조회"""
    url = API_ENDPOINTS.get((property_type, "전월세"))
    if not url:
        return []
    params = {
        "serviceKey": _data_key(),
        "LAWD_CD": region_code,
        "DEAL_YMD": deal_ymd,
        "numOfRows": "100",
        "pageNo": "1",
    }
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
        return _parse_molit_xml(resp.text, trade_type="전월세", property_type=property_type)
    except httpx.HTTPStatusError:
        return []


def _parse_molit_xml(xml_text: str, trade_type: str, property_type: str = "아파트") -> list[dict]:
    """국토교통부 XML 응답 파싱"""
    results = []
    root = ElementTree.fromstring(xml_text)
    items = root.findall(".//item")
    for item in items:
        data: dict = {"거래유형": trade_type, "매물종류": property_type}
        field_map = {
            # 이름 필드 (API마다 태그명이 다를 수 있음)
            "aptNm": "아파트",
            "offiNm": "아파트",
            "mhouseNm": "아파트",
            # 공통 필드
            "umdNm": "법정동",
            "excluUseAr": "전용면적",
            "floor": "층",
            "buildYear": "건축년도",
            "dealYear": "년",
            "dealMonth": "월",
            "dealDay": "일",
            "dealAmount": "거래금액",
            "deposit": "보증금액",
            "monthlyRent": "월세금액",
        }
        for xml_tag, key in field_map.items():
            el = item.find(xml_tag)
            if el is not None and el.text:
                val = el.text.strip()
                if val:
                    data[key] = val
        if data.get("아파트"):
            results.append(data)
    return results


async def kakao_keyword_search(query: str) -> dict | None:
    """카카오 키워드 장소 검색 → 첫 번째 결과의 좌표 반환"""
    headers = {"Authorization": f"KakaoAK {_kakao_key()}"}
    params = {"query": query, "size": "1"}
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(KAKAO_KEYWORD_SEARCH_URL, headers=headers, params=params)
        resp.raise_for_status()
    body = resp.json()
    docs = body.get("documents", [])
    if not docs:
        return None
    doc = docs[0]
    return {
        "place_name": doc.get("place_name", ""),
        "address": doc.get("address_name", ""),
        "x": float(doc["x"]),
        "y": float(doc["y"]),
    }


async def kakao_nearby_places(dong_name: str, region_name: str, category_code: str, size: int = 3) -> list[dict]:
    """법정동 근처 장소 검색

    category_code:
        SW8 = 지하철역
        SC4 = 학교
        PS3 = 어린이집/유치원
    """
    headers = {"Authorization": f"KakaoAK {_kakao_key()}"}

    # 먼저 법정동 좌표를 검색
    dong_coord = await kakao_keyword_search(f"{region_name} {dong_name}")
    if not dong_coord:
        return []

    # 좌표 기반 카테고리 검색 (거리 계산 가능)
    category_url = "https://dapi.kakao.com/v2/local/search/category.json"
    params = {
        "category_group_code": category_code,
        "x": str(dong_coord["x"]),
        "y": str(dong_coord["y"]),
        "radius": "2000",
        "sort": "distance",
        "size": str(size),
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(category_url, headers=headers, params=params)
            resp.raise_for_status()
        body = resp.json()
        places = []
        for doc in body.get("documents", []):
            places.append({
                "name": doc.get("place_name", ""),
                "distance": doc.get("distance", ""),
            })
        return places
    except httpx.HTTPStatusError:
        return []


async def kakao_coord_to_region(x: float, y: float) -> str | None:
    """좌표 → 행정구역(구) 변환"""
    headers = {"Authorization": f"KakaoAK {_kakao_key()}"}
    url = "https://dapi.kakao.com/v2/local/geo/coord2regioncode.json"
    params = {"x": str(x), "y": str(y)}
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(url, headers=headers, params=params)
        resp.raise_for_status()
    body = resp.json()
    docs = body.get("documents", [])
    for doc in docs:
        if doc.get("region_type") == "H":
            return doc.get("region_2depth_name", "")
    return None
