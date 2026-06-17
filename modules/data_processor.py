import pandas as pd
import math
import numpy as np
from datetime import datetime
from modules import settings
from modules.utils import vectorized_haversine

# ==========================================
# 1. 工具函式：建材標準化 (新增)
# ==========================================
def normalize_material(mat_str):
    if pd.isna(mat_str): return "排除"
    s = str(mat_str).strip().upper()
    
    if any(k in s for k in ["加強磚造", "磚造", "ＲＣ磚", "RC磚"]):
        return "鋼筋混凝土加強磚造"
    elif any(k in s for k in ["鋼筋混凝土", "ＲＣ", "RC"]):
        return "鋼筋混凝土"      
    # 其他全部標記為排除
    return "排除"

# ==========================================
# 2. 工具函式：屋齡計算
# ==========================================
def _safe_calc_age(x, current_year, default_age):
    """強健的屋齡計算：防止實登髒資料導致系統崩潰"""
    if pd.isna(x):
        return default_age
    s = str(x).strip().replace('.0', '')
    if not s or s == '0' or s.lower() in ['nan', 'none', '-', 'nat']:
        return default_age
    try:
        if len(s) >= 5:
            build_year = int(s[:-4])
        else:
            build_year = int(s)
        if build_year > current_year or build_year < 1:
            return default_age
        return current_year - build_year
    except ValueError:
        return default_age

# ==========================================
# 3. 主邏輯：抓取資料並清洗
# ==========================================
def get_neighbor_data(conn, t_lat, t_lon, b_type, t_addr=""):
    """從資料庫抓取初步候選池，加入 Bounding Box 邊界框"""
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

    # 去重：只保留時間最新的一筆
    if not df.empty:
        df['deal_date'] = df['deal_date'].astype(str)
        df = df.sort_values('deal_date', ascending=False)
        df = df.drop_duplicates(subset=['address'], keep='first').copy()

    # 距離計算 (向量化極速版)
    df['dist'] = vectorized_haversine(t_lat, t_lon, df['Response_Y'].astype(float), df['Response_X'].astype(float))

    # 套用距離與筆數限制
    target_df = df[df['dist'] <= settings.SEARCH_RADIUS_KM].sort_values('dist').head(settings.MAX_CANDIDATES).copy()
    
    # ==========================================
    # 建材正規化 (確保後續估價引擎運算無誤)
    # ==========================================
    if 'material' in target_df.columns:
        target_df['material'] = target_df['material'].apply(normalize_material)
        target_df = target_df[target_df['material'] != "排除"].copy()
        
    return target_df

def score_neighbors(df, target_age, is_first_floor_checked, b_type=""):
    """權重計分邏輯 (透天與集合住宅獨立雙軌計分)"""
    if df.empty:
        return df

    if not is_first_floor_checked and 'floor_level' in df.columns:
        df = df[~df['floor_level'].str.contains('一層', na=False)].copy()
        if df.empty:
            return df

    current_roc_year = datetime.now().year - 1911

    def calc_score(row):
        score = 0
        
        # --- 準備共用運算變數 ---
        try:
            deal_year = int(str(row['deal_date'])[:-4])
            year_diff = current_roc_year - deal_year
        except:
            year_diff = 99
            
        try:
            build_year = int(str(row['build_date'])[:-4])
            row_age = current_roc_year - build_year
        except:
            row_age = target_age
            
        age_diff = abs(row_age - target_age)
        dist_m = row.get('dist', 999) * 1000

        # ==========================================
        # 🏠 透天厝計分邏輯
        # ==========================================
        if b_type == "透天厝":
            # 1. 交易年份
            if year_diff <= 1: score += settings.SCORE_HOUSE["DEAL_1_YEAR"]
            elif year_diff <= 2: score += settings.SCORE_HOUSE["DEAL_2_YEAR"]
            elif year_diff <= 3: score += settings.SCORE_HOUSE["DEAL_3_YEAR"]
            else: score += settings.SCORE_HOUSE["DEAL_OVER_3"]
            
            # 2. 距離計分
            if dist_m <= 50: score += settings.SCORE_HOUSE["DIST_50M"]
            elif dist_m <= 250: score += settings.SCORE_HOUSE["DIST_250M"]
            elif dist_m <= 500: score += settings.SCORE_HOUSE["DIST_500M"]
            else: score += settings.SCORE_HOUSE["DIST_BASE"]

        # ==========================================
        # 🏢 集合住宅計分邏輯
        # ==========================================
        else:
            # 1. 交易年份
            if year_diff <= 1: score += settings.SCORE_APT["DEAL_1_YEAR"]
            elif year_diff <= 2: score += settings.SCORE_APT["DEAL_2_YEAR"]
            elif year_diff <= 3: score += settings.SCORE_APT["DEAL_3_YEAR"]
            else: score += settings.SCORE_APT["DEAL_OVER_3"]
            
            # 2. 距離計分
            if dist_m <= 50: score += settings.SCORE_APT["DIST_50M"]
            elif dist_m <= 250: score += settings.SCORE_APT["DIST_250M"]
            elif dist_m <= 500: score += settings.SCORE_APT["DIST_500M"]
            else: score += settings.SCORE_APT["DIST_BASE"]
            
            # 3. 屋齡差距計分 (使用獨立參數與範圍判定)
            if age_diff == 0:
                score += settings.SCORE_APT["AGE_DIFF_0"]
            elif age_diff <= 10:
                score += settings.SCORE_APT["AGE_DIFF_10"]
            else: score += settings.SCORE_APT["AGE_DIFF_11"]
            
        return score

    df['total_score'] = df.apply(calc_score, axis=1)
    
    # 呼叫安全解析函式
    df['calc_age'] = df['build_date'].apply(
        lambda x: _safe_calc_age(x, current_roc_year, target_age)
    )
    return df.sort_values('total_score', ascending=False)
