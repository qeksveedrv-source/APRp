import math
from datetime import datetime
import pandas as pd
import numpy as np
from modules import settings


def haversine(lat1, lon1, lat2, lon2):
    """計算兩點經緯度之球面大圓距離 (公里)"""
    R = 6371.0  # 地球半徑 (km)
    dlat, dlon = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlon / 2) ** 2
    )
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def vectorized_haversine(lat1, lon1, lat2_series, lon2_series):
    """向量化極速版：一次計算一整排 DataFrame 的地理距離"""
    R = 6371.0
    lat1, lon1 = np.radians(lat1), np.radians(lon1)
    lat2, lon2 = np.radians(lat2_series), np.radians(lon2_series)

    dlat = lat2 - lat1
    dlon = lon2 - lon1

    a = (
        np.sin(dlat / 2) ** 2
        + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2) ** 2
    )
    c = 2 * np.arcsin(np.sqrt(a))
    return R * c


# ==========================================
# 資料清洗與型態轉換工具
# ==========================================


def normalize_numeric_columns(df, columns):
    """將指定欄位轉為數值型態，保留 NaN 以辨識資料缺失"""
    for col in columns:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def categorize_parking_type(row):
    """分類車位基本型態與權利狀態 (例如：平面所有權、機械使用權)"""
    p_type = row.get("parking_type")
    p_area = row.get("parking_area")

    # 判斷是否無車位
    if (
        pd.isna(p_type)
        or str(p_type).strip() == ""
        or str(p_type).strip().lower() == "nan"
    ):
        return "無車位"

    p_str = str(p_type).strip()

    # 1. 判定基本車位型態
    if p_str in ["坡道平面", "一樓平面", "升降平面"]:
        base_type = "平面"
    elif p_str in ["坡道機械", "升降機械", "機械"]:
        base_type = "機械"
    else:
        base_type = p_str  # 非預期字串則保留原字

    # 2. 判定權利狀態：面積大於 0.1 坪/㎡ 為所有權，否則為使用權
    try:
        has_area = pd.notna(p_area) and float(p_area) > 0.1
    except (ValueError, TypeError):
        has_area = False

    ownership = "所有權" if has_area else "使用權"

    return f"{base_type}{ownership}"


def infer_parking_price(row):
    """
    智能拆算車位總價：
    1. 若實價登錄已有明確車位價格 (>0)，直接採用實登價，不讀取預設字典。
    2. 若實登車位價為 0 或未標示，則根據物件所在「縣市」與「車位型態」動態對照 settings.PARKING_PRICE_TABLE 扣除。
    """
    # 規則一：實登車位價格存在且 > 0 直接使用
    p_price = row.get("parking_price", row.get("parking_price_total", 0))
    try:
        if pd.notna(p_price) and float(p_price) > 0:
            return float(p_price)
    except (ValueError, TypeError):
        pass

    # 規則二：實登價格為 0，啟動縣市與車位型態動態預設扣除機制
    p_type = row.get("車位", row.get("parking_type", "無車位"))
    p_area = row.get("parking_area")

    if "無車位" in str(p_type):
        return 0.0

    # 辨識所在縣市 (優先讀取 city 欄位，若無則從 address 解析，備援預設為花蓮縣)
    address_str = str(row.get("address", ""))
    city = row.get("city")

    if not city:
        for c in settings.PARKING_PRICE_TABLE.keys():
            if c in address_str:
                city = c
                break

    if not city or city not in settings.PARKING_PRICE_TABLE:
        city = "花蓮縣"  # Fallback 備援縣市

    city_parking_dict = settings.PARKING_PRICE_TABLE.get(
        city, settings.PARKING_PRICE_TABLE["花蓮縣"]
    )

    # 辨識車位權利與型態 key
    has_ownership = "所有權" in p_type

    if "平面" in p_type:
        price_key = "平面所有權" if has_ownership else "平面使用權"
    elif "機械" in p_type:
        price_key = "機械所有權" if has_ownership else "機械使用權"
    else:
        return 0.0

    base_price = city_parking_dict.get(price_key, 0.0)

    # 推算車位個數 (約 33 平方公尺對應一車位)
    try:
        p_count = (
            max(1, round(float(p_area) / 33.0))
            if (has_ownership and pd.notna(p_area) and float(p_area) > 0)
            else 1
        )
    except (ValueError, TypeError):
        p_count = 1

    return float(base_price * p_count)


def recalc_unit_price(df, sqm_to_ping=0.3025):
    """重新計算集合住宅之純建物「實質單價 (萬元/坪)」"""
    parking_area = pd.to_numeric(df["parking_area"], errors="coerce").fillna(0)
    price = pd.to_numeric(df["price"], errors="coerce").fillna(0)
    total_build_area = pd.to_numeric(
        df["total_build_area"], errors="coerce"
    ).fillna(0)

    if "parking_type" in df.columns:
        df["車位"] = df.apply(categorize_parking_type, axis=1)
    else:
        df["車位"] = "無車位"

    # 動態智能拆算車位總價 (帶入每列之 address/city 與車位欄位)
    df["parking_price"] = df.apply(infer_parking_price, axis=1)

    # 扣除車位後的房屋純總價
    net_price = (price - df["parking_price"]).clip(lower=0)

    # 扣除車位後的房屋純坪數 (當權狀面積扣除車位面積後仍 >= 15 平方公尺才予以扣除)
    eligible = (parking_area > 0) & ((total_build_area - parking_area) >= 15)
    net_area = total_build_area.where(
        ~eligible, total_build_area - parking_area
    )
    net_area_ping = net_area * sqm_to_ping

    # 計算實質單價 (萬元/坪)
    df["unit_price_p"] = np.where(
        net_area_ping >= 5, (net_price / net_area_ping) / 10000.0, 0.0
    )
    return df


# =========================================================================
# 民國年拆解與日期轉換工具
# =========================================================================


def _parse_roc_ym(roc_str):
    """智慧拆解各式民國年字串（如 1120512、0004509），回傳 (年, 月)"""
    if (
        pd.isna(roc_str)
        or str(roc_str).strip() == ""
        or str(roc_str).strip().lower() in ["none", "nan"]
    ):
        return None, None
    try:
        clean_num = int(float(str(roc_str).strip()))
        s = str(clean_num)
        if len(s) >= 6:
            return int(s[:-4]), int(s[-4:-2])
        elif len(s) >= 4:
            return int(s[:-2]), int(s[-2:])
        else:
            return int(s), 1
    except (ValueError, TypeError):
        return None, None


def _parse_roc_year(roc_str):
    """僅擷取民國年份"""
    y, _ = _parse_roc_ym(roc_str)
    return y


def format_date(roc_date_str):
    """將民國年月日字串轉換為西元 YYYY-MM-DD 格式"""
    y, m = _parse_roc_ym(roc_date_str)
    if y is not None and m is not None:
        try:
            s = str(int(float(roc_date_str)))
            day = s[-2:].zfill(2) if len(s) >= 6 else "01"
            return f"{y + 1911}-{str(m).zfill(2)}-{day}"
        except (ValueError, TypeError):
            pass
    return "無資料"


def calculate_age_from_roc(roc_date_str):
    """根據民國建築完成日計算屋齡"""
    y, _ = _parse_roc_ym(roc_date_str)
    if y is not None:
        return max((datetime.now().year - 1911) - y, 0)
    return None


# =========================================================================
# 透天選案與屋齡清洗引擎
# =========================================================================


def filter_detached_top10(df):
    """選案策略：保留交易日期最新、分數最高的前 10 筆紀錄（多重排序錨點穩定版）"""
    if df.empty:
        return df

    sort_cols = []
    sort_orders = []

    if "deal_date" in df.columns:
        sort_cols.append("deal_date")
        sort_orders.append(False)  # 降冪（最新在前）
    if "total_score" in df.columns:
        sort_cols.append("total_score")
        sort_orders.append(False)  # 降冪（高分在前）
    if "id" in df.columns:
        sort_cols.append("id")
        sort_orders.append(True)  # 升冪（ID 對齊）

    if sort_cols:
        top_10 = (
            df.sort_values(by=sort_cols, ascending=sort_orders)
            .head(10)
            .copy()
        )
    else:
        top_10 = df.head(10).copy()

    return top_10


def fix_building_age(df):
    """屋齡清洗引擎：進行 36 個月新屋審查，無資料者填寫 np.nan"""
    if df.empty or "calc_age" not in df.columns:
        return df

    current_roc = datetime.now().year - 1911
    b_col = "build_date" if "build_date" in df.columns else None
    d_col = "deal_date" if "deal_date" in df.columns else None

    def _recalc_age(row):
        if (
            not b_col
            or pd.isna(row[b_col])
            or str(row[b_col]).strip() == ""
            or str(row[b_col]).strip().lower() in ["none", "nan"]
        ):
            return np.nan

        try:
            b_year, b_month = _parse_roc_ym(row[b_col])
            if b_year is None or b_year <= 0:
                return np.nan

            # 新成屋審查：交易日與建築日差距小於 36 個月判定為新屋 (0年)
            if d_col and pd.notna(row[d_col]):
                d_year, d_month = _parse_roc_ym(row[d_col])
                if d_year is not None and d_month is not None:
                    months_diff = (d_year - b_year) * 12 + (d_month - b_month)
                    if months_diff < 36:
                        return 0

            return max(current_roc - b_year, 0)
        except (ValueError, TypeError):
            pass

        age = row.get("calc_age")
        return np.nan if (pd.isna(age) or float(age) == current_roc) else age

    df["calc_age"] = df.apply(_recalc_age, axis=1)
    return df
