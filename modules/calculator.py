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

    # ==========================================
    #  2. 透天厝估價引擎 (次高與次低溢價平均法 + 負數剔除機制)
    # ==========================================
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
        df['premium_rate'] = np.where(
            df['case_base_cost'] > 0, 
            (df['p_wan'] - df['case_base_cost']) / df['case_base_cost'], 
            0.0
        )

        # 2. 剔除負數，只保留溢價係數 >= 0 的有效案件
        valid_df = df[df['premium_rate'] >= 0].copy()
        valid_df['market_premium'] = valid_df['premium_rate'].round(2)
        
        # 3. 採次高及次低的平均認定
        premiums = valid_df['premium_rate'].tolist()
        
        if len(premiums) >= 4:
            sorted_premiums = sorted(premiums)
            final_premium_rate = (sorted_premiums[-2] + sorted_premiums[1]) / 2.0
        elif len(premiums) > 0:
            # 防呆：如果附近有效的案件少於4件，則直接取算術平均
            final_premium_rate = np.mean(premiums)
        else:
            final_premium_rate = 0.0
            
        # 4. 標的市值(萬元) = 總成本(萬元) × (1 + 最終認定的溢價係數)
        target_final_price = target_base_cost * (1 + final_premium_rate)
        
        # 回傳最終合理區間，並將「剔除負數後的 valid_df」傳回給 app.py 畫表
        return target_final_price * settings.PRICE_LOWER_BOUND, target_final_price * settings.PRICE_UPPER_BOUND, valid_df
    
    # ==========================================
    # 3. 集合住宅估價引擎 (實質單價法 + 加權平均)
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

    # ==========================================
    # 4. 車位資訊解析工具 (修正坪數顯示錯誤)
    # ==========================================
    @staticmethod
    def get_berth_info(row):
        target_str = str(row.get('target_type', ''))
        p_type = str(row.get('parking_type', ''))
        p_area_sqm = row.get('parking_area', 0) # 這是原始的平方公尺
        
        if '車位' not in target_str or pd.isna(p_area_sqm) or p_area_sqm == 0:
            return "無車位"
            
        # 將原始的「平方公尺」乘以設定檔常數，轉換為真實的「坪數」再做顯示
        p_area_ping = p_area_sqm * 0.3025
        
        if any(keyword in p_type for keyword in ['坡道平面', '一樓平面', '升降平面']):
            return f"平面 ({p_area_ping:.1f}坪)"
        elif any(keyword in p_type for keyword in ['升降機械', '坡道機械', '機械']):
            return f"機械 ({p_area_ping:.1f}坪)"
        elif p_type and str(p_type) != 'nan' and str(p_type).strip() != '':
            return f"其他 ({p_area_ping:.1f}坪)"
        return f"有車位 ({p_area_ping:.1f}坪)"
