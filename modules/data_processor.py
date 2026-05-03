import pandas as pd
import math
import sqlite3
from datetime import datetime

def haversine(lat1, lon1, lat2, lon2):
    """
    計算兩點間的球面距離 (公里)
    """
    R = 6371  # 地球半徑
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(dlon / 2) ** 2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

def get_neighbor_data(conn, t_lat, t_lon, b_type, t_addr=""):
    """
    從資料庫抓取初步候選池
    策略：直接取 3 公里內最接近的 30 筆，不設半徑跳出限制
    """
    # 擷取關鍵字避免括號干擾 (例如：住宅大樓 -> 住宅大樓)
    core_keyword = b_type.split('(')[0].strip()
    
    query = f"""
        SELECT * FROM records 
        WHERE building_type LIKE '%{core_keyword}%' 
        AND target LIKE '%房地%' 
        AND Response_X != ''
    """
    df = pd.read_sql(query, conn)
    
    if df.empty:
        return df

    # 1. 街路排他邏輯 (如果目標在街，剔除在路的紀錄)
    if '街' in t_addr:
        df = df[~df['address'].str.contains('路', na=False)]

    # 2. 計算所有案例與目標點的距離
    df['dist'] = df.apply(
        lambda r: haversine(t_lat, t_lon, float(r['Response_Y']), float(r['Response_X'])), 
        axis=1
    )

    # 3. 篩選 3 公里內最接近的前 30 筆作為候選池
    # 增加至 30 筆是為了給「5最新+5最近」選案策略留出足夠的優質樣本
    target_df = df[df['dist'] <= 3.0].sort_values('dist').head(30)
    
    return target_df

def score_neighbors(df, target_age, is_first_floor_checked):
    """
    權重計分邏輯：動態年度權重與相似度評分
    """
    if df.empty:
        return df

    # 1. 排除一樓邏輯 (若未勾選)
    if not is_first_floor_checked:
        # 掃描實登常見的一樓標註方式
        df = df[~df['shifting_level'].str.contains('一層', na=False)].copy()
        if df.empty: return df

    # 🌟 動態獲取當前民國年份
    current_roc_year = datetime.now().year - 1911

    def calc_score(row):
        score = 0
        
        # --- A. 成交時間權重 (系統自動判斷) ---
        try:
            deal_year = int(str(row['transaction_date'])[:-4])
            year_diff = current_roc_year - deal_year
            
            if year_diff <= 1:   # 當年度及前一年度 (最高參考價值)
                score += 6
            elif year_diff == 2: # 前前一年度 (參考價值減半)
                score += 3
            else:                # 前前前一年度 (僅剩基本參考價值)
                score += 1
        except:
            score += 1

        # --- B. 屋齡相似度加分 ---
        try:
            build_year = int(str(row['construction_date'])[:-4])
            row_age = current_roc_year - build_year
        except:
            row_age = target_age # 萬一無完工日期，視同目標屋齡
            
        age_diff = abs(row_age - target_age)

        if age_diff <= 2:
            score += 4
        elif age_diff <= 5:
            score += 3
        elif age_diff <= 10:
            score += 2
        else:
            score += 1

        # --- C. 地理距離加分 ---
        if row['dist'] <= 1.0:
            score += 3
        elif row['dist'] <= 2.0:
            score += 2
        # 2km - 3km 區間不另加分

        # --- D. 屋齡斷層懲罰 ---
        # 若屋齡差距超過 11 年，代表產品定位可能有顯著差異，權重打 8 折
        if age_diff > 11:
            score = score * 0.8
            
        return score

    # 執行計分
    df['total_score'] = df.apply(calc_score, axis=1)
    
    # 存回計算後的屋齡備用，避免 app.py 重複計算
    df['calc_age'] = df['construction_date'].apply(
        lambda x: current_roc_year - int(str(x)[:-4]) if pd.notna(x) and str(x).strip() != '' else target_age
    )
    
    return df.sort_values('total_score', ascending=False)
