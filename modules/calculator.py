import pandas as pd
import numpy as np
from modules import settings

class RealEstateValuator:

    # 🌟 新增：缺少的計算成本函式 (這會被上方函式呼叫)
    @staticmethod
    def calculate_cost(land_area, build_area, age, material):
        # 取得單坪折舊後的成本
        unit_cost = RealEstateValuator.get_building_cost(material, age)
        return (build_area * unit_cost)

    @staticmethod
    def get_building_cost(material, age):
        """
        採用在地金融機構（信合社）實戰比例階梯表
        特色：直接讀取 settings 既有的 RC/磚造 基準造價，套用前快後慢階梯折舊。
        """
        # 1. 根據材質自動判斷基準造價 (直接沿用您原本的 settings 設定)
        material = str(material)
        if "鋼筋混凝土" in material and "磚" not in material:
            base = settings.BUILD_COST_RC      # RC造價
        else:
            base = settings.BUILD_COST_BRICK   # 加強磚造價

        # 2. 套用信合社實戰比例階梯 (以 100% 為基準)
        if age <= 3: 
            rate = 1.00    # 基準點
        elif age <= 5: 
            rate = 0.92    
        elif age <= 7: 
            rate = 0.83    
        elif age <= 9: 
            rate = 0.75    
        elif age <= 11: 
            rate = 0.67    
        elif age <= 15: 
            rate = 0.58    # 緩衝期
        elif age <= 25: 
            rate = 0.50    # 正式進入十年一階
        elif age <= 35: 
            rate = 0.42    
        elif age <= 45: 
            rate = 0.33    
        else: 
            rate = 0.25    # 46年以上殘值底線 (RC為3萬 / 磚造為2萬)
            
        return base * rate

    @staticmethod
    def run_detached_valuation(target_data, top_10_df, land_price):
        """透天厝估價引擎：採用加權平均溢價係數"""
        
        # 計算目標物件標準成本
        t_cost = RealEstateValuator.calculate_cost(
            target_data['land'], target_data['build'], target_data['age'], target_data['material']
        )
        target_total_cost = (target_data['land'] * land_price) + t_cost

        premiums = []
        weighted_premiums_sum = 0
        total_weights = top_10_df['total_score'].sum()

        for idx, row in top_10_df.iterrows():
            c_cost = RealEstateValuator.calculate_cost(
                row['land_area'], row['total_build_area'], row['calc_age'], "鋼筋混凝土" 
            )
            case_total_cost = (row['land_area'] * land_price) + c_cost
            
            # 計算溢價率 (避免除以零)
            if case_total_cost > 0:
                premium = (row['price'] / 10000 - case_total_cost) / case_total_cost
            else:
                premium = 0
            
            premiums.append(round(premium, 4))
            weighted_premiums_sum += (premium * row['total_score'])

        if total_weights > 0:
            final_premium = weighted_premiums_sum / total_weights
        else:
            final_premium = 0.1 

        center_price = target_total_cost * (1 + final_premium)
        low_bound = center_price * settings.PRICE_LOWER_BOUND
        high_bound = center_price * settings.PRICE_UPPER_BOUND

        return low_bound, high_bound, premiums
    
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
