import pandas as pd
import math
import sqlite3
from datetime import datetime

def haversine(lat1, lon1, lat2, lon2):
    R = 6371  
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(dlon / 2) ** 2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

def get_neighbor_data(conn, t_lat, t_lon, b_type, t_addr=""):
    core_keyword = b_type.split('(')[0].strip()
    
    # 對齊欄位：build_type, target_type[cite: 5]
    query = f"""
        SELECT * FROM records 
        WHERE build_type LIKE '%{core_keyword}%' 
        AND target_type LIKE '%房地%' 
        AND Response_X != ''
    """
    df = pd.read_sql(query, conn)
    
    if df.empty: return df

    if '街' in t_addr:
        df = df[~df['address'].str.contains('路', na=False)]

    df['dist'] = df.apply(
        lambda r: haversine(t_lat, t_lon, float(r['Response_Y']), float(r['Response_X'])), 
        axis=1
    )

    return df[df['dist'] <= 3.0].sort_values('dist').head(30)

def score_neighbors(df, target_age, is_first_floor_checked):
    if df.empty: return df

    if not is_first_floor_checked:
        # 對齊欄位：floor_level[cite: 5]
        df = df[~df['floor_level'].str.contains('一層', na=False)].copy()
        if df.empty: return df

    current_roc_year = datetime.now().year - 1911

    def calc_score(row):
        score = 0
        try:
            # 對齊欄位：deal_date[cite: 5]
            deal_year = int(str(row['deal_date'])[:-4])
            year_diff = current_roc_year - deal_year
            if year_diff <= 1: score += 6
            elif year_diff == 2: score += 3
            else: score += 1
        except: score += 1

        try:
            # 對齊欄位：build_date[cite: 5]
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
    
    # 使用 build_date 計算屋齡備用[cite: 5]
    df['calc_age'] = df['build_date'].apply(
        lambda x: current_roc_year - int(str(x)[:-4]) if pd.notna(x) and str(x).strip() != '' else target_age
    )
    
    return df.sort_values('total_score', ascending=False)
