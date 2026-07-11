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
    #  2. 交易加權強化法
    # =========================================================================
    @classmethod
    def run_detached_valuation(cls, target, df, land_price):
        # 1. 計算目標物件基準成本
        target_build_cost = cls.calculate_cost(target['land'], target['build'], target['age'], target['material'])
        target_base_cost = (target['land'] * land_price) + target_build_cost

        if df.empty:
            return 0, 0, df

        df = df.copy()
        
        # 批次計算所有案例的建物成本與總成本
        df['b_cost'] = df.apply(lambda row: cls.calculate_cost(
            row.get('land_area', 0), 
            row.get('total_build_area', 0), 
            row.get('calc_age', 0), 
            row.get('material', '')
        ), axis=1)
        
        df['case_base_cost'] = (df['land_area'] * land_price) + df['b_cost']
        df['p_wan'] = df['price'] / 10000.0
        
        # 計算原始溢價係數
        df['premium_rate'] = np.where(
            df['case_base_cost'] > 0, 
            (df['p_wan'] - df['case_base_cost']) / df['case_base_cost'], 
            0.0
        )
        
        # 🌟 防護機制一：排除嚴重低於市場成本、折舊不合理的「超低負溢價極端值」
        # 若成交價低於總成本超過 15% (即溢價率 < -0.15)，視為瑕疵、親友漏報或特殊案件，直接不計入
        valid_df = df[df['premium_rate'] >= -0.15].copy()
        valid_df['market_premium'] = valid_df['premium_rate'].round(2)
        
        premiums = valid_df['premium_rate'].tolist()
        
        if premiums:
            # 市場溢價區間依然忠實呈現當前池子的最低與最高
            low_premium = min(premiums)
            high_premium = max(premiums)
            
            # 🌟 防護機制二：時間加權乘數（讓最新半年的成交案發揮關鍵影響力）
            # 讀取原本的 total_score (包含年份與距離加分)，如果是1年內成交者，額外給予 1.5 倍的算力乘數
            # 這樣可以強行把大於18個月、拉低行情的舊案權重壓低
            raw_weights = valid_df['total_score'].fillna(1).values
            
            # 若原始資料包含 deal_date，偵測成交時效
            time_multipliers = []
            for _, row in valid_df.iterrows():
                # 簡單利用 total_score 的基礎分數判定 (原本 1 年內交易年度加權會拿到最高的 6 分)
                if row.get('total_score', 0) >= 6: 
                    time_multipliers.append(1.5)  # 最新案件，權重放大 1.5 倍
                else:
                    time_multipliers.append(1.0)  # 舊案件維持原權重
                    
            # 最終複合權重 = 相似度總分 × 時效乘數
            final_weights = raw_weights * np.array(time_multipliers)
            
            # 執行高時效加權平均
            if np.sum(final_weights) > 0:
                avg_premium = np.average(premiums, weights=final_weights)
            else:
                avg_premium = np.mean(premiums)
                
            # 中心預測總價 = 標的總成本 × (1 + 高時效加權平均溢價)
            center_price = target_base_cost * (1 + avg_premium)
            
            val_low_bound = center_price * 0.90   # 中間值 - 10%
            val_high_bound = center_price * 1.10  # 中間值 + 10%
        else:
            val_low_bound = target_base_cost
            val_high_bound = target_base_cost
            
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
