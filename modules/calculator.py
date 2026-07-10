import pandas as pd
import numpy as np
from modules import settings

class RealEstateValuator:

    # 缺少的計算成本函式 (這會被上方函式呼叫)
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
        # 1. 根據材質自動判斷基準造價 
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

    # =========================================================================
    #  2. 🏠 透天厝估價引擎 (次低~次高溢價區間法 | 保留正負向)
    # =========================================================================
    @classmethod
    def run_detached_valuation(cls, target, df, land_price):
        # 1. 計算目標物件基準成本
        target_build_cost = cls.calculate_cost(target['land'], target['build'], target['age'], target['material'])
        target_base_cost = (target['land'] * land_price) + target_build_cost

        if df.empty:
            return 0, 0, df

        df = df.copy() # 避免修改到原始資料的警告
        
        # 批次計算所有案例的建物成本
        df['b_cost'] = df.apply(lambda row: cls.calculate_cost(
            row.get('land_area', 0), 
            row.get('total_build_area', 0), 
            row.get('calc_age', 0), 
            row.get('material', '')
        ), axis=1)
        
        # 批次計算總成本與每萬總價
        df['case_base_cost'] = (df['land_area'] * land_price) + df['b_cost']
        df['p_wan'] = df['price'] / 10000.0
        
        # 批次計算溢價係數 (使用 np.where 防呆，避免分母為 0 導致程式崩潰)
        # 🌟 亮點：保留所有溢價案例（不論正負向）
        df['premium_rate'] = np.where(
            df['case_base_cost'] > 0, 
            (df['p_wan'] - df['case_base_cost']) / df['case_base_cost'], 
            0.0
        )
        
        # 全數保留為有效案件，並四捨五入至小數第二位供畫表
        valid_df = df.copy()
        valid_df['market_premium'] = valid_df['premium_rate'].round(2)
        
        # 2. 🌟 核心修改點：依「次低」與「次高」溢價係數決定市場區間
        premiums = sorted(valid_df['premium_rate'].tolist())
        total_cases = len(premiums)
        
        if total_cases >= 4:
            # 正常狀況：去頭去尾，取次低與次高
            low_premium = premiums[1]       # 次低
            high_premium = premiums[-2]     # 次高
        elif total_cases == 3:
            # 只有 3 筆：取中間值作為單一基準，或展開範圍
            low_premium = premiums[0]
            high_premium = premiums[-1]
        elif total_cases == 2:
            low_premium = premiums[0]
            high_premium = premiums[1]
        elif total_cases == 1:
            low_premium = premiums[0]
            high_premium = premiums[0]
        else:
            low_premium = 0.0
            high_premium = 0.0
            
        # 3. 🌟 行情公式變更：
        # 合理行情區間 = 標的總成本 × (1 + 次低溢價係數) ～ 標的總成本 × (1 + 次高溢價係數)
        val_low_bound = target_base_cost * (1 + low_premium)
        val_high_bound = target_base_cost * (1 + high_premium)
        
        # 回傳最終動態行情區間，並將 valid_df 傳回給前端畫表
        return val_low_bound, val_high_bound, valid_df 
    
    # ==========================================
    # 3. 🏢 集合住宅估價引擎 (實質單價法 + 加權平均)
    # ==========================================
    @classmethod
    def run_apartment_valuation(cls, df):
        # 如果資料為空，或者 app.py 沒有傳入算好的單價，直接回傳 0
        if df.empty or 'unit_price_p' not in df.columns:
            return 0, 0

        valid_df = df[df['unit_price_p'] > 0].dropna(subset=['unit_price_p']).copy()
        
        if not valid_df.empty:
            weights = valid_df['total_score'].fillna(1).values
            prices = valid_df['unit_price_p'].values
            avg_unit_price = np.average(prices, weights=weights) if np.sum(weights) > 0 else np.mean(prices)
        else:
            avg_unit_price = 0
            
        return avg_unit_price * settings.PRICE_LOWER_BOUND, avg_unit_price * settings.PRICE_UPPER_BOUND
