import math
from datetime import datetime
import pandas as pd

def haversine(lat1, lon1, lat2, lon2):
    R = 6371.0  # 地球半徑 (km)
    dlat, dlon = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1))*math.cos(math.radians(lat2))*math.sin(dlon/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))

def normalize_numeric_columns(df, columns, fill_value=0):
    for col in columns:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(fill_value)
        else:
            df[col] = fill_value
    return df

def categorize_parking_type(raw_value):
    if pd.isna(raw_value) or str(raw_value).strip() == "":
        return "無車位"
    p_str = str(raw_value).strip()
    if p_str in ["坡道平面", "一樓平面", "升降平面"]:
        return "平面車位"
    # 補上 "機械" 的防呆判斷
    if p_str in ["坡道機械", "升降機械", "機械"]:
        return "機械車位"
    return p_str

def infer_parking_price(row):
    p_price = row.get('parking_price', 0)
    if pd.notna(p_price) and float(p_price) > 0:
        return float(p_price)

    p_area = row.get('parking_area', 0)
    p_type = row.get('車位', "無車位")
    has_ownership = pd.notna(p_area) and float(p_area) > 0

    if p_type == "平面車位":
        base_price = 1550000 if has_ownership else 1150000
    elif p_type == "機械車位":
        base_price = 800000 if has_ownership else 500000
    else:
        return 0
        
    # 防護：推估車位數量 (避免多車位總價沒扣乾淨)
    p_count = max(1, round(float(p_area) / 33.0)) if float(p_area) > 0 else 1
    return base_price * p_count

def recalc_unit_price(df, sqm_to_ping=0.3025):
    # 1. 確保防呆與型態轉換
    df['parking_area'] = pd.to_numeric(df['parking_area'], errors='coerce').fillna(0).astype(float)
    df['price'] = pd.to_numeric(df['price'], errors='coerce').fillna(0).astype(float)
    df['total_build_area'] = pd.to_numeric(df['total_build_area'], errors='coerce').fillna(0).astype(float)

    # 🚨 核心整合：直接在這裡執行車位分類與計價，確保 app.py 傳進來不會漏掉
    if 'parking_type' in df.columns:
        df['車位'] = df['parking_type'].apply(categorize_parking_type)
    else:
        df['車位'] = "無車位"
    df['parking_price'] = df.apply(infer_parking_price, axis=1)

    # 2. 進行向量化運算 (保留你原本的高效寫法)
    net_price = (df['price'] - df['parking_price']).clip(lower=0)
    
    # 條件：有車位，且相減後剩餘面積 >= 15 平方公尺 (防分母坍塌)
    eligible = (df['parking_area'] > 0) & ((df['total_build_area'] - df['parking_area']) >= 15)
    
    # 符合條件才相減，否則維持原總面積
    net_area = df['total_build_area'].where(~eligible, df['total_build_area'] - df['parking_area'])
    
    # 轉換為坪數
    net_area_ping = net_area * sqm_to_ping

    # 計算單價，若最終坪數小於 5 坪，單價強制給 0
    df['unit_price_p'] = (net_price / net_area_ping / 10000).where(net_area_ping >= 5, 0)
    
    return df

def format_date(roc_date_str):
    try:
        s = str(int(float(roc_date_str)))
        year = int(s[:-4]) + 1911
        return f"{year}-{s[-4:-2]}-{s[-2:]}"
    except:
        return "無資料"

def calculate_age_from_roc(roc_date_str):
    try:
        s = str(int(float(roc_date_str)))
        build_year = int(s[:-4])
        current_roc_year = datetime.now().year - 1911
        return max(current_roc_year - build_year, 0)
    except:
        return None
def filter_detached_top10(df):
    """
    透天厝專用選案策略：
    1. 同門牌若有多筆成交，只保留時間最近的一筆。
    2. 選出 5 筆最新成交。
    3. 從剩餘名單中選出 5 筆距離最近的案例。
    4. 合併為最多 10 筆參考紀錄。
    """
    if df.empty:
        return df
        
    # 依照日期排序，去除重複門牌 (保留最新)
    df_sorted = df.sort_values('deal_date', ascending=False)
    merged_pool = df_sorted.drop_duplicates(subset=['address'], keep='first').copy()
    merged_pool['sort_date'] = merged_pool['deal_date'].astype(str)

    # 挑選 5 筆最新
    latest_5 = merged_pool.sort_values('sort_date', ascending=False).head(5)
    
    # 挑選 5 筆最近 (必須排除已經被選入最新 5 筆的清單)
    remaining = merged_pool[~merged_pool.index.isin(latest_5.index)]
    closest_5 = remaining.sort_values('dist', ascending=True).head(5)

    # 合併並回傳
    top_10 = pd.concat([latest_5, closest_5])
    return top_10
