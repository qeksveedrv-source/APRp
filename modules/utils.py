import math
from datetime import datetime
import pandas as pd
import numpy as np

def haversine(lat1, lon1, lat2, lon2):
    R = 6371.0  # 地球半徑 (km)
    dlat, dlon = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1))*math.cos(math.radians(lat2))*math.sin(dlon/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))

def vectorized_haversine(lat1, lon1, lat2_series, lon2_series):
    """向量化極速版：一次計算一整排 DataFrame 的距離"""
    R = 6371.0
    lat1, lon1 = np.radians(lat1), np.radians(lon1)
    lat2, lon2 = np.radians(lat2_series), np.radians(lon2_series)
    
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    
    a = np.sin(dlat/2)**2 + np.cos(lat1)*np.cos(lat2)*np.sin(dlon/2)**2
    c = 2 * np.arcsin(np.sqrt(a))
    return R * c
# ==========================================

def normalize_numeric_columns(df, columns):
    """將指定欄位轉為數值型態，保留 NaN 以辨識資料缺失"""
    for col in columns:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')
    return df

def categorize_parking_type(row):

    p_type = row.get('parking_type')
    p_area = row.get('parking_area')
    
    # 判斷是否無車位
    if pd.isna(p_type) or str(p_type).strip() == "" or str(p_type).strip().lower() == "nan":
        return "無車位"
        
    p_str = str(p_type).strip()
    
    # 1. 判定基本車位型態
    if p_str in ["坡道平面", "一樓平面", "升降平面"]:
        base_type = "平面"
    elif p_str in ["坡道機械", "升降機械", "機械"]:
        base_type = "機械"
    else:
        base_type = p_str  # 若有其他非預期字串則保留原字
        
    # 2. 判定權利狀態：有數字(大於0.1)就是所有權，沒數字(或為0/NaN)就是使用權
    try:
        has_area = pd.notna(p_area) and float(p_area) > 0.1
    except (ValueError, TypeError):
        has_area = False
        
    ownership = "所有權" if has_area else "使用權"
    
    return f"{base_type}{ownership}"


def infer_parking_price(row):
    """配合新版車位欄位文字，智能拆算車位總價"""
    p_price = row.get('parking_price', 0)
    if pd.notna(p_price) and float(p_price) > 0:
        return float(p_price)

    p_type = row.get('車位', "無車位")
    p_area = row.get('parking_area')
    
    # 直接從已經串接好的「車位」名稱中識別所有權與類型
    has_ownership = "所有權" in p_type

    if "平面" in p_type:
        base_price = 1550000 if has_ownership else 1150000
    elif "機械" in p_type:
        base_price = 800000 if has_ownership else 500000
    else:
        return 0
        
    try:
        p_count = max(1, round(float(p_area) / 33.0)) if (has_ownership and pd.notna(p_area) and float(p_area) > 0) else 1
    except:
        p_count = 1
        
    return base_price * p_count

def recalc_unit_price(df, sqm_to_ping=0.3025):
    parking_area = pd.to_numeric(df['parking_area'], errors='coerce').fillna(0)
    price = pd.to_numeric(df['price'], errors='coerce').fillna(0)
    total_build_area = pd.to_numeric(df['total_build_area'], errors='coerce').fillna(0)

    if 'parking_type' in df.columns:
        df['車位'] = df.apply(categorize_parking_type, axis=1)
    else:
        df['車位'] = "無車位"
        
    df['parking_price'] = df.apply(infer_parking_price, axis=1)

    net_price = (price - df['parking_price']).clip(lower=0)
    eligible = (parking_area > 0) & ((total_build_area - parking_area) >= 15)
    net_area = total_build_area.where(~eligible, total_build_area - parking_area)
    net_area_ping = net_area * sqm_to_ping

    df['unit_price_p'] = np.where(
        net_area_ping >= 5, 
        (net_price / net_area_ping) / 10000, 
        0
    )
    return df

# =========================================================================
# 拆解民國年字串，同時回傳「年」與「月」
# =========================================================================
def _parse_roc_ym(roc_str):
    """將各式民國年字串（包含帶前導零如0004509）智慧拆解出整數（年, 月）"""
    if pd.isna(roc_str) or str(roc_str).strip() == "" or str(roc_str).strip().lower() in ["none", "nan"]:
        return None, None
    try:
        clean_num = int(float(str(roc_str).strip()))
        s = str(clean_num)
        if len(s) >= 6:   # 例如 1120512 或 450512 -> 擷取年份與月份
            return int(s[:-4]), int(s[-4:-2])
        elif len(s) >= 4: # 例如 11205 或 4509 -> 擷取年份與月份
            return int(s[:-2]), int(s[-2:])
        else:             # 例如 112 或 45 -> 只有年份，月份預設為 1 月
            return int(s), 1
    except:
        return None, None

def _parse_roc_year(roc_str):
    """相容舊版工具，僅取年份"""
    y, _ = _parse_roc_ym(roc_str)
    return y

def format_date(roc_date_str):
    """將民國年月日轉為西元 YYYY-MM-DD 格式顯示"""
    y, m = _parse_roc_ym(roc_date_str)
    if y is not None and m is not None:
        try:
            s = str(int(float(roc_date_str)))
            day = s[-2:].zfill(2) if len(s) >= 6 else "01"
            return f"{y + 1911}-{str(m).zfill(2)}-{day}"
        except:
            pass
    return "無資料"

def calculate_age_from_roc(roc_date_str):
    """簡單版屋齡計算工具"""
    y, _ = _parse_roc_ym(roc_date_str)
    if y is not None:
        return max((datetime.now().year - 1911) - y, 0)
    return None
    
# =========================================================================
# 透天選案保留最新 15 筆
# =========================================================================
def filter_detached_top10(df):
    """
    選案策略：取消同門牌去重複，但精準收攏回全市場最新交易日的前 10 筆紀錄
    （防止湊筆數而撈到 18 個月前的舊資料降低行情）
    """
    if df.empty: 
        return df
        
    top_10 = df.sort_values(by='deal_date', ascending=False).head(10).copy()

    return top_10
    
# =========================================================================
# 屋齡清洗引擎（支援 36 個月新屋判定與 空白標記 NaN）
# =========================================================================
def fix_building_age(df):
    """(資料清洗) 解決早期屋齡誤判，執行36個月新屋審查，空白資料留空以利前端顯示不詳"""
    if df.empty or 'calc_age' not in df.columns:
        return df

    current_roc = datetime.now().year - 1911
    b_col = 'build_date' if 'build_date' in df.columns else None
    d_col = 'deal_date' if 'deal_date' in df.columns else None

    def _recalc_age(row):
        # 1. build_date 是空白，直接回傳 np.nan (代表不詳)，後續計算能安全避開
        if not b_col or pd.isna(row[b_col]) or str(row[b_col]).strip() == "" or str(row[b_col]).strip().lower() in ["none", "nan"]:
            return np.nan

        try:
            b_year, b_month = _parse_roc_ym(row[b_col])
            if b_year is None or b_year <= 0:
                return np.nan

            # 2. 新成屋精準審查：比對建築年月與交易年月是否相差 < 36 個月
            if d_col and pd.notna(row[d_col]):
                d_year, d_month = _parse_roc_ym(row[d_col])
                if d_year is not None and d_month is not None:
                    # 計算兩個時間點的實質月份差
                    months_diff = (d_year - b_year) * 12 + (d_month - b_month)
                    if months_diff < 36:
                        return 0  # 滿足條件，直接判定為新屋（屋齡 0 年）

            # 3. 正常老屋：計算目前實質屋齡
            return max(current_roc - b_year, 0)
        except:
            pass
        
        # 備用救援機制
        age = row.get('calc_age')
        return np.nan if (pd.isna(age) or float(age) == current_roc) else age

    df['calc_age'] = df.apply(_recalc_age, axis=1)
    return df
