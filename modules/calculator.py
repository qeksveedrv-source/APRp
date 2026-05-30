import pandas as pd
import numpy as np
from modules import settings  # 引入設定檔

class RealEstateValuator:

    # ==========================================
    # 🌟 1. 建築造價與折舊模型 (透天厝專用)
    # ==========================================
    @staticmethod
    def get_building_cost(material, age):
        material = str(material)
        if "鋼筋混凝土" in material and "磚" not in material:
            base_price = settings.BUILD_COST_RC
            life_span = settings.BUILD_USEFUL_LIFE_RC
            # 讀取 RC 最低殘值 (1萬元)
            min_residual = settings.MIN_RESIDUAL_COST_RC
        else:
            base_price = settings.BUILD_COST_BRICK
            life_span = settings.BUILD_USEFUL_LIFE_BRICK
            # 讀取磚造最低殘值 (0.5萬元 = 5000元)
            min_residual = settings.MIN_RESIDUAL_COST_BRICK
            
        cost = base_price * (1 - (age / life_span))
        
        # 回傳算出的殘值與設定的「最低殘值」兩者之間較高的那個
        return max(cost, min_residual)
    # ==========================================
    # 2. 透天厝估價引擎 (成本法 + 加權中位數溢價)
    # ==========================================
    @classmethod
    def run_detached_valuation(cls, target, df, land_price):
        target_build_cost = cls.get_building_cost(target.get('material', ''), target.get('age', 0))
        target_base_cost = (target.get('land', 0) * land_price) + (target.get('build', 0) * target_build_cost)

        # 防呆：如果沒有鄰近案例，直接回傳基礎成本
        if df.empty:
            return target_base_cost * settings.PRICE_LOWER_BOUND, target_base_cost * settings.PRICE_UPPER_BOUND, []

        # 1. 批次計算所有案例的建物單坪造價，強制轉為數值 (Numeric) 並填補 0，避免空值或字串導致運算崩潰
        df['b_cost'] = df.apply(lambda row: cls.get_building_cost(row.get('material', ''), row.get('calc_age', 0)), axis=1)
        land_area = pd.to_numeric(df['land_area'], errors='coerce').fillna(0)
        build_area = pd.to_numeric(df['total_build_area'], errors='coerce').fillna(0)
        # 除以 10000，將資料庫的「元」統一轉換為「萬元」，以對齊成本單位
        price = pd.to_numeric(df['price'], errors='coerce').fillna(0) / 10000.0

        # 2. 批次計算歷史案例總成本，防止「除以 0」產生 Infinity (無限大)
        df['record_cost'] = (land_area * land_price) + (build_area * df['b_cost'])
        df['record_cost'] = df['record_cost'].replace(0, np.nan)
        
        # 3. 批次計算市場溢價率：(實際總價 - 總成本) / 總成本
        df['premium'] = (price - df['record_cost']) / df['record_cost']
        df['premium'] = df['premium'].replace([np.inf, -np.inf], np.nan)
        valid_df = df.dropna(subset=['premium']).copy()

        # 4. 加權中位數計算
        if not valid_df.empty:
            weights = valid_df['total_score'].fillna(1).values
            premiums = valid_df['premium'].values

            # 根據溢價率進行排序
            sorted_indices = np.argsort(premiums)
            sorted_premiums = premiums[sorted_indices]
            sorted_weights = weights[sorted_indices]
            
            # 找出權重累積超過一半的位置 (加權中位數)
            cumulative_weights = np.cumsum(sorted_weights)
            cutoff = np.sum(weights) / 2.0
            median_idx = np.searchsorted(cumulative_weights, cutoff)
            median_idx = min(median_idx, len(sorted_premiums) - 1) 
            
            weighted_median_premium = sorted_premiums[median_idx]
        else:
            weighted_median_premium = 0

        # 5. 推算目標總價與區間
        final_target_price = target_base_cost * (1 + weighted_median_premium)
        
        # 輸出溢價率清單供 app.py 介面使用
        premiums_list = [f"{p:.0%}" for p in valid_df['premium']] if not valid_df.empty else []
        
        return final_target_price * settings.PRICE_LOWER_BOUND, final_target_price * settings.PRICE_UPPER_BOUND, premiums_list

    # ==========================================
    # 3. 集合住宅估價引擎 (實質單價法 + 加權平均)
    # ==========================================
    @classmethod
    def run_apartment_valuation(cls, df):
        if df.empty:
            return 0, 0

        df['net_price'] = df['price'] - df['parking_price'].fillna(0)
        df['net_area'] = df['total_build_area'] - df['parking_area'].fillna(0)
        
        df['net_area'] = df['net_area'].apply(lambda x: x if x > 0 else 1)
        df['unit_price_p'] = (df['net_price'] / 10000) / df['net_area']
        
        valid_df = df.dropna(subset=['unit_price_p']).copy()
        if not valid_df.empty:
            weights = valid_df['total_score'].fillna(1).values
            prices = valid_df['unit_price_p'].values
            avg_unit_price = np.average(prices, weights=weights) if np.sum(weights) > 0 else np.mean(prices)
        else:
            avg_unit_price = 0
            
        return avg_unit_price * settings.PRICE_LOWER_BOUND, avg_unit_price * settings.PRICE_UPPER_BOUND

    # ==========================================
    # 4. 車位資訊解析工具 (保持原樣)
    # ==========================================
    @staticmethod
    def get_berth_info(row):
        target_str = str(row.get('target_type', ''))
        p_type = str(row.get('parking_type', ''))
        p_area = row.get('parking_area', 0)
        
        if '車位' not in target_str or pd.isna(p_area) or p_area == 0:
            return "無車位"
            
        if any(keyword in p_type for keyword in ['坡道平面', '一樓平面']):
            return f"平面 ({p_area:.1f}坪)"
        elif any(keyword in p_type for keyword in ['升降機械', '坡道機械', '機械']):
            return f"機械 ({p_area:.1f}坪)"
        elif p_type and str(p_type) != 'nan':
            return f"其他 ({p_area:.1f}坪)"
        return f"有車位 ({p_area:.1f}坪)"
