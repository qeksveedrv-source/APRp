import pandas as pd
import math
import sqlite3
import re  
from datetime import datetime

# 引入自訂模組
from modules.utils import haversine
from modules import settings  # 引入剛建立的設定檔

def get_neighbor_data(conn, t_lat, t_lon, b_type, t_addr=""):
    """從資料庫抓取初步候選池，加入 Bounding Box 邊界框與街路排他邏輯"""
    core_keyword = b_type.split('(')[0].strip()
    
    # 計算 Bounding Box
    lat_delta = settings.SEARCH_RADIUS_KM / settings.LAT_DEGREE_KM
    lon_delta = settings.SEARCH_RADIUS_KM / (settings.LAT_DEGREE_KM * math.cos(math.radians(t_lat)))
    
    min_lat, max_lat = t_lat - lat_delta, t_lat + lat_delta
    min_lon, max_lon = t_lon - lon_delta, t_lon + lon_delta
    
    query = f"""
        SELECT * FROM records 
        WHERE build_type LIKE '%{core_keyword}%' 
        AND target_type LIKE '%房地%' 
        AND Response_X != ''
        AND CAST(Response_Y AS REAL) BETWEEN {min_lat} AND {max_lat}
        AND CAST(Response_X AS REAL) BETWEEN {min_lon} AND {max_lon}
    """
    df = pd.read_sql(query, conn)
    
    if df.empty:
        return df

    if '街' in t_addr:
        df = df[~df['address'].str.contains('路', na=False)]

    # ==========================================
    # 同門牌多筆紀錄，直接在這裡只保留「時間最新」的一筆
    # ==========================================
    if not df.empty:
        # 1. 確保交易日期格式一致以便排序
        df['deal_date'] = df['deal_date'].astype(str)
        
        # 2. 依照交易日期由新到舊排序
        df = df.sort_values('deal_date', ascending=False)
        
        # 3. 剔除重複的門牌，保留第一筆 (即最新的一筆)，完全不計算平均！
        df = df.drop_duplicates(subset=['address'], keep='first').copy()
    # ==========================================

    # 後續的距離計算 (保持不變)
    df['dist'] = df.apply(
        lambda r: haversine(t_lat, t_lon, float(r['Response_Y']), float(r['Response_X'])), 
        axis=1
    )

    # 套用 settings 中的距離與筆數限制
    target_df = df[df['dist'] <= settings.SEARCH_RADIUS_KM].sort_values('dist').head(settings.MAX_CANDIDATES)
    
    return target_df

def score_neighbors(df, target_age, is_first_floor_checked):
    """權重計分邏輯"""
    if df.empty: return df

    if not is_first_floor_checked:
        df = df[~df['floor_level'].str.contains('一層', na=False)].copy()
        if df.empty: return df

    current_roc_year = datetime.now().year - 1911

    def calc_score(row):
        score = 0
        
        # 1. 交易年份計分
        try:
            deal_year = int(str(row['deal_date'])[:-4])
            year_diff = current_roc_year - deal_year
            if year_diff <= 1: score += settings.SCORE["DEAL_1_YEAR"]
            elif year_diff == 2: score += settings.SCORE["DEAL_2_YEAR"]
            else: score += settings.SCORE["DEAL_BASE"]
        except: score += settings.SCORE["DEAL_BASE"]

        # 2. 屋齡差異計分
        try:
            build_year = int(str(row['build_date'])[:-4])
            row_age = current_roc_year - build_year
        except: row_age = target_age
            
        age_diff = abs(row_age - target_age)
        if age_diff <= 2: score += settings.SCORE["AGE_DIFF_2"]
        elif age_diff <= 5: score += settings.SCORE["AGE_DIFF_5"]
        elif age_diff <= 10: score += settings.SCORE["AGE_DIFF_10"]
        else: score += settings.SCORE["AGE_BASE"]
        
        # 3. 總分懲罰機制 (打折)
        if age_diff > settings.AGE_PENALTY_THRESHOLD: 
            score = score * settings.AGE_PENALTY_RATE
            
        return score

    df['total_score'] = df.apply(calc_score, axis=1)
    df['calc_age'] = df['build_date'].apply(
        lambda x: current_roc_year - int(str(x)[:-4]) if pd.notna(x) and str(x).strip() != '' else target_age
    )
    return df.sort_values('total_score', ascending=False)
