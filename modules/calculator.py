import pandas as pd
import numpy as np

class RealEstateValuator:

    # ==========================================
    # 🌟 1. 建築造價與折舊模型 (透天厝專用)
    # ==========================================
    @staticmethod
    def get_building_cost(material, age):
        """
        簡易造價折舊模型 (萬/坪)
        依據建築材質與屋齡推算現時造價
        """
        material = str(material)
        # 判斷 RC (鋼筋混凝土) 或 加強磚造
        if "鋼筋混凝土" in material and "磚" not in material:
            base_price = 10.0  # 全新 RC 基準造價: 10萬/坪
            life_span = 50.0   # 耐用年限: 50年
        else:
            base_price = 8.0   # 全新加強磚造 基準造價: 8萬/坪
            life_span = 35.0   # 耐用年限: 35年

        # 直線折舊法：剩餘價值 = 基準造價 * (1 - (已使用年數 / 耐用年限))
        cost = base_price * (1 - (age / life_span))
        
        # 設定最低殘值為 2 萬/坪 (房子再老，骨架仍有基本殘值)
        return max(cost, 2.0)

    # ==========================================
    # 🌟 2. 透天厝估價引擎 (成本法 + 加權中位數溢價)
    # ==========================================
    @classmethod
    def run_detached_valuation(cls, target, df, land_price):
        """透天厝估價邏輯 - 加權中位數法"""
        
        # 1. 計算目標物件的「標準基準總價」(未加市場溢價前)
        target_build_cost = cls.get_building_cost(target['material'], target['age'])
        target_base_cost = (target['land'] * land_price) + (target['build'] * target_build_cost)

        premiums = []
        weights = []
        df_premiums = []

        # 2. 計算鄰近每一筆案例的「市場溢價係數」
        for idx, row in df.iterrows():
            # 取出該筆案例的建材與屋齡，計算其專屬的殘值造價
            b_cost = cls.get_building_cost(row.get('building_material', target['material']), row.get('calc_age', target['age']))
            
            # 推算該歷史案例的理論基準總價
            record_cost = (row['land_area'] * land_price) + (row['building_area'] * b_cost)

            if record_cost > 0:
                price_10k = row['total_price'] / 10000
                # 公式：[成交總價 - 理論基準價] / 理論基準價 = 市場溢價係數
                premium = (price_10k - record_cost) / record_cost
            else:
                premium = 0

            premiums.append(premium)
            weights.append(row.get('total_score', 1)) # 讀取 data_processor 算出的權重分數
            
            # 格式化為帶有正負號的百分比 (例如 +15.2% 或 -5.4%)，供前端顯示
            df_premiums.append(f"{premium * 100:+.1f}%")

        # 將算好的百分比寫回 DataFrame，讓 app.py 可以顯示在表格中
        df['market_premium'] = df_premiums

        # 3. 實作「加權中位數」尋找市場共識 (抵抗極端值干擾)
        if premiums:
            # 將溢價係數從小到大排序，並同步排序對應的權重
            sorted_indices = sorted(range(len(premiums)), key=lambda k: premiums[k])
            sorted_premiums = [premiums[i] for i in sorted_indices]
            sorted_weights = [weights[i] for i in sorted_indices]

            # 累加權重
            cum_weights = []
            curr_sum = 0
            for w in sorted_weights:
                curr_sum += w
                cum_weights.append(curr_sum)

            total_weight = sum(sorted_weights)
            median_premium = 0

            # 找出累加權重跨過 50% 門檻的那一個溢價係數
            for i, cw in enumerate(cum_weights):
                if cw >= total_weight / 2.0:
                    median_premium = sorted_premiums[i]
                    break
        else:
            median_premium = 0

        # 4. 根據加權中位數推算市場最終總價，並給予上下 5% 的合理區間
        anchor_price = target_base_cost * (1 + median_premium)
        low = anchor_price * 0.95
        high = anchor_price * 1.05

        return low, high, df_premiums

    # ==========================================
    # 🌟 3. 集合住宅估價引擎 (大樓/華廈/公寓)
    # ==========================================
    @classmethod
    def run_apartment_valuation(cls, df):
        """集合住宅估價邏輯 - 實質單價推算法"""
        # 3. 估價公式：建物實質價格 = total_price – berth_price
        df['net_price'] = df['total_price'] - df['berth_price'].fillna(0)
        # 建物單價 = 建物實質價格 / (building_area – berth_area）
        df['net_area'] = df['building_area'] - df['berth_area'].fillna(0)
        
        df['net_area'] = df['net_area'].apply(lambda x: x if x > 0 else 1)
        df['unit_price_p'] = (df['net_price'] / 10000) / df['net_area']
        
        if not df.empty:
            valid_df = df.dropna(subset=['unit_price_p']).copy()
            if not valid_df.empty:
                weights = valid_df['total_score'].fillna(1).values
                prices = valid_df['unit_price_p'].values
                avg_unit_price = np.average(prices, weights=weights) if np.sum(weights) > 0 else np.mean(prices)
            else:
                avg_unit_price = 0
        else:
            avg_unit_price = 0
            
        return avg_unit_price * 0.95, avg_unit_price * 1.05

    # ==========================================
    # 🌟 4. 車位資訊解析工具
    # ==========================================
    @staticmethod
    def get_berth_info(row):
        """
        2. 判定有無車位與產權
        target 包含 '車位' 才有車位
        berth_area > 0 為所有權，否則為使用權
        """
        target_str = str(row.get('target', ''))
        b_type = str(row.get('berth_category', ''))
        b_area = row.get('berth_area', 0)

        if '車位' not in target_str:
            return "無", "-"
        
        # 判定類型
        main_type = "平面" if "平面" in b_type else "機械" if "機械" in b_type else "其他"
        # 判定權利：berth_area 欄位有數字(>0)就是所有權
        right = "所有權" if b_area > 0 else "使用權"
        
        return main_type, right
