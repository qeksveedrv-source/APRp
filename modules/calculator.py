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
        target_build_cost = cls.calculate_cost(target['land'], target['build'], target['age'], target['material'])
        target_base_cost = (target['land'] * land_price) + target_build_cost

        valid_rows = []
        premiums = []

        # 這裡的 df 是從 app.py 傳進來的完整 final_pool (最多30筆)，我們依序過濾
        for idx, row in df.iterrows():
            b_cost = cls.calculate_cost(row.get('land_area',0), row.get('total_build_area',0), row.get('calc_age',0), row.get('material',''))
            case_base_cost = (row.get('land_area',0) * land_price) + b_cost
            
            p_wan = row['price'] / 10000.0
            
            if case_base_cost > 0:
                premium_rate = (p_wan - case_base_cost) / case_base_cost
            else:
                premium_rate = 0.0
                
            # 只收錄溢價係數 >= 0 (非負數) 的案件
            if premium_rate >= 0:
                row_copy = row.copy()
                row_copy['market_premium'] = round(premium_rate, 2)
                valid_rows.append(row_copy)
                premiums.append(premium_rate)
                
            # 只要收集滿 10 個正數樣本，就提早結束迴圈 (盡量維持 10 個)
            if len(valid_rows) == 10:
                break

        # 將過濾後真正要顯示的有效資料轉回 DataFrame
        filtered_top_10 = pd.DataFrame(valid_rows) if valid_rows else df.head(0)
        
        # 採次高及次低的平均認定
        if len(premiums) >= 4:
            sorted_premiums = sorted(premiums)
            second_lowest = sorted_premiums[1]       # 次低 (索引 1)
            second_highest = sorted_premiums[-2]     # 次高 (倒數第 2 個，索引 -2)
            final_premium_rate = (second_highest + second_lowest) / 2.0
        elif len(premiums) > 0:
            # 防呆：如果附近有效的案件少於4件，則直接取算術平均
            final_premium_rate = np.mean(premiums)
        else:
            final_premium_rate = 0.0
            
        # 標的市值(萬元) = 總成本(萬元) × (1 + 最終認定的溢價係數)
        target_final_price = target_base_cost * (1 + final_premium_rate)
        
        # 最終呈現 ±6% 之合理區間，並回傳「過濾好的 top_10」給 app.py 畫地圖跟表格
        return target_final_price * settings.PRICE_LOWER_BOUND, target_final_price * settings.PRICE_UPPER_BOUND, filtered_top_10
    
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
