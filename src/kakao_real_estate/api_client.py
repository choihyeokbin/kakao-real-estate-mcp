"""
외부 API 호출 클라이언트 (국토교통부 실거래가 + 카카오맵)
"""

import os
import httpx
from xml.etree import ElementTree

DATA_GO_KR_API_KEY = os.getenv("DATA_GO_KR_API_KEY", "")
KAKAO_REST_API_KEY = os.getenv("KAKAO_REST_API_KEY", "")

# 국토교통부 실거래가 API 엔드포인트
MOLIT_APT_TRADE_URL = "https://apis.data.go.kr/1613000/RTMSDataSvcAptTrade/getRTMSDataSvcAptTrade"
MOLIT_APT_RENT_URL = "https://apis.data.go.kr/1613000/RTMSDataSvcAptRent/getRTMSDataSvcAptRent"

# 카카오 API
KAKAO_KEYWORD_SEARCH_URL = "https://dapi.kakao.com/v2/local/search/keyword.json"


async def fetch_apt_trade(region_code: str, deal_ymd: str) -> list[dict]:
    """아파트 매매 실거래가 조회"""
    params = {
        "serviceKey": DATA_GO_KR_API_KEY,
        "LAWD_CD": region_code,
        "DEAL_YMD": deal_ymd,
        "numOfRows": "100",
        "pageNo": "1",
    }
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(MOLIT_APT_TRADE_URL, params=params)
        resp.raise_for_status()
    return _parse_molit_xml(resp.text, trade_type="매매")


async def fetch_apt_rent(region_code: str, deal_ymd: str) -> list[dict]:
    """아파트 전월세 실거래가 조회"""
    params = {
        "serviceKey": DATA_GO_KR_API_KEY,
        "LAWD_CD": region_code,
        "DEAL_YMD": deal_ymd,
        "numOfRows": "100",
        "pageNo": "1",
    }
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(MOLIT_APT_RENT_URL, params=params)
            resp.raise_for_status()
        return _parse_molit_xml(resp.text, trade_type="전월세")
    except httpx.HTTPStatusError:
        return []


def _parse_molit_xml(xml_text: str, trade_type: str) -> list[dict]:
    """국토교통부 XML 응답 파싱"""
    results = []
    root = ElementTree.fromstring(xml_text)
    items = root.findall(".//item")
    for item in items:
        data: dict = {"거래유형": trade_type}
        field_map = {
            "aptNm": "아파트",
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
                data[key] = el.text.strip()
        if data.get("아파트"):
            results.append(data)
    return results


async def kakao_keyword_search(query: str) -> dict | None:
    """카카오 키워드 장소 검색 → 첫 번째 결과의 좌표 반환"""
    headers = {"Authorization": f"KakaoAK {KAKAO_REST_API_KEY}"}
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


async def kakao_coord_to_region(x: float, y: float) -> str | None:
    """좌표 → 행정구역(구) 변환"""
    headers = {"Authorization": f"KakaoAK {KAKAO_REST_API_KEY}"}
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
