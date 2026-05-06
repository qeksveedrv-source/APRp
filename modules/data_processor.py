import pandas as pd
import math
import sqlite3
from datetime import datetime

def haversine(lat1, lon1, lat2, lon2):
    """計算兩點間的球面距離 (公里)"""
    R = 6371  
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(dlon / 2) ** 2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

def get_neighbor_data(conn, t_lat, t_lon, b_type, t_addr=""):
    """從資料庫抓取初步候選池，僅保留街路排他邏輯"""
    core_keyword = b_type.split('(')[0].strip()
    
    # 對齊資料庫欄位名稱
    query = f"""
        SELECT * FROM records 
        WHERE build_type LIKE '%{core_keyword}%' 
        AND target_type LIKE '%房地%' 
        AND Response_X != ''
    """
    df = pd.read_sql(query, conn)
    
    if df.empty:
        return df

    # --- 街路排他邏輯：僅當目標在街時，排除路邊案例[cite: 5] ---
    if '街' in t_addr:
        df = df[~df['address'].str.contains('路', na=False)]

    # 2. 計算地理距離[cite: 5]
    df['dist'] = df.apply(
        lambda r: haversine(t_lat, t_lon, float(r['Response_Y']), float(r['Response_X'])), 
        axis=1
    )

    # 3. 篩選 3 公里內最接近的前 30 筆作為候選池[cite: 5]
    target_df = df[df['dist'] <= 3.0].sort_values('dist').head(30)
    
    return target_df

def score_neighbors(df, target_age, is_first_floor_checked):
    """權重計分邏輯，對齊 deal_date, build_date, floor_level[cite: 5]"""
    if df.empty: return df

    if not is_first_floor_checked:
        df = df[~df['floor_level'].str.contains('一層', na=False)].copy()
        if df.empty: return df

    current_roc_year = datetime.now().year - 1911

    def calc_score(row):
        score = 0
        try:
            deal_year = int(str(row['deal_date'])[:-4])
            year_diff = current_roc_year - deal_year
            if year_diff <= 1: score += 6
            elif year_diff == 2: score += 3
            else: score += 1
        except: score += 1

        try:
            build_year = int(str(row['build_date'])[:-4])
            row_age = current_roc_year - build_year
        except: row_age = target_age
            
        age_diff = abs(row_age - target_age)
        if age_diff <= 2: score += 4
        elif age_diff <= 5: score += 3
        elif age_diff <= 10: score += 2
        else: score += 1

        if row['dist'] <= 1.0: score += 3
        elif row['dist'] <= 2.0: score += 2
        if age_diff > 11: score = score * 0.8
        return score

    df['total_score'] = df.apply(calc_score, axis=1)
    df['calc_age'] = df['build_date'].apply(
        lambda x: current_roc_year - int(str(x)[:-4]) if pd.notna(x) and str(x).strip() != '' else target_age
    )
    return df.sort_values('total_score', ascending=False)
