import pandas as pd
import math
import sqlite3
import re  # 新增：引入正規表示式模組
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

    # --- 街路排他邏輯：僅當目標在街時，排除路邊案例 ---
    if '街' in t_addr:
        df = df[~df['address'].str.contains('路', na=False)]

    # 2. 計算地理距離
    df['dist'] = df.apply(
        lambda r: haversine(t_lat, t_lon, float(r['Response_Y']), float(r['Response_X'])), 
        axis=1
    )

    # 3. 篩選 1 公里內最接近的前 20 筆作為候選池
    target_df = df[df['dist'] <= 1.0].sort_values('dist').head(20)
    
    return target_df

def extract_street_part(addr):
    """解析地址，萃取到巷弄層級以便進行相似度比對"""
    if not isinstance(addr, str) or not addr:
        return ""
    
    # 先移除縣市鄉鎮區，避免因行政區層級不同而比對失敗 (例如：花蓮縣吉安鄉 vs 吉安鄉)
    addr = re.sub(r'^.*?[縣市]', '', addr)
    addr = re.sub(r'^.*?[鄉鎮市區]', '', addr)
    
    # 抓取「路/街/大道」以及緊接在後的「巷、弄」
    m = re.match(r'(.+?[路街道](?:[0-9一二三四五六七八九十百千]+巷)?(?:[0-9一二三四五六七八九十百千]+弄)?)', addr)
    if m:
        return m.group(1)
    return ""

# 注意：此處參數新增了 t_addr="" 預設值，以便接收前端傳來的目標地址
def score_neighbors(df, target_age, is_first_floor_checked, t_addr=""):
    """權重計分邏輯，對齊 deal_date, build_date, floor_level，並新增地址相近加分"""
    if df.empty: return df

    if not is_first_floor_checked:
        df = df[~df['floor_level'].str.contains('一層', na=False)].copy()
        if df.empty: return df

    current_roc_year = datetime.now().year - 1911
    
    # 事前解析好目標地址的核心街道部分 (例如："吉昌二街220巷")
    target_prefix = extract_street_part(t_addr)

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
        
        # ====== 新增邏輯：地址相近或相同加分 ======
        if target_prefix and pd.notna(row.get('address')):
            row_prefix = extract_street_part(str(row['address']))
            # 如果資料庫案例的街道巷弄與目標相同，加 5 分
            if row_prefix and row_prefix == target_prefix:
                score += 5
        # ==========================================

        if age_diff > 11: score = score * 0.8
        return score

    df['total_score'] = df.apply(calc_score, axis=1)
    df['calc_age'] = df['build_date'].apply(
        lambda x: current_roc_year - int(str(x)[:-4]) if pd.notna(x) and str(x).strip() != '' else target_age
    )
    return df.sort_values('total_score', ascending=False)
