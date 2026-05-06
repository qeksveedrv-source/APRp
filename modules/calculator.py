import pandas as pd
import numpy as np

class RealEstateValuator:

    # ==========================================
    # 🌟 1. 建築造價與折舊模型 (透天厝專用)
    # ==========================================
    @staticmethod
    def get_building_cost(material, age):
        material = str(material)
        if "鋼筋混凝土" in material and "磚" not in material:
            base_price = 10.0  # 全新 RC 基準造價
            life_span = 50.0   # 耐用年限
        else:
            base_price = 8.0   # 全新加強磚造
            life_span = 35.0   
        cost = base_price * (1 - (age / life_span))
        return max(cost, 2.0)

    # ==========================================
    # 🌟 2. 透天厝估價引擎 (成本法 + 加權中位數溢價)
    # ==========================================
    @classmethod
    def run_detached_valuation(cls, target, df, land_price):
        target_build_cost = cls.get_building_cost(target['material'], target['age'])
        target_base_cost = (target['land'] * land_price) + (target['build'] * target_build_cost)

        premiums, weights, df_premiums = [], [], []

        for idx, row in df.iterrows():
            # 對齊欄位：material, total_build_area, price[cite: 4]
            b_cost = cls.get_building_cost(row.get('material', target['material']), row.get('calc_age', target['age']))
            record_cost = (row['land_area'] * land_price) + (row['total_build_area'] * b_cost)

            if record_cost > 0:
                price_10k = row['price'] / 10000
                premium = (price_10k - record_cost) / record_cost
            else:
                premium = 0

            premiums.append(premium)
            weights.append(row.get('total_score', 1))
            df_premiums.append(f"{premium * 100:+.1f}%")

        df['market_premium'] = df_premiums

        if premiums:
            sorted_indices = sorted(range(len(premiums)), key=lambda k: premiums[k])
            sorted_premiums = [premiums[i] for i in sorted_indices]
            sorted_weights = [weights[i] for i in sorted_indices]
            cum_weights, curr_sum = [], 0
            for w in sorted_weights:
                curr_sum += w
                cum_weights.append(curr_sum)
            total_weight = sum(sorted_weights)
            median_premium = 0
            for i, cw in enumerate(cum_weights):
                if cw >= total_weight / 2.0:
                    median_premium = sorted_premiums[i]
                    break
        else:
            median_premium = 0

        anchor_price = target_base_cost * (1 + median_premium)
        return anchor_price * 0.95, anchor_price * 1.05, df_premiums

    # ==========================================
    # 🌟 3. 集合住宅估價引擎 (大樓/華廈/公寓)
    # ==========================================
    @classmethod
    def run_apartment_valuation(cls, df):
        # 對齊欄位：price, parking_price, total_build_area, parking_area[cite: 4]
        df['net_price'] = df['price'] - df['parking_price'].fillna(0)
        df['net_area'] = df['total_build_area'] - df['parking_area'].fillna(0)
        
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
        # 對齊欄位：target_type, parking_type, parking_area[cite: 4]
        target_str = str(row.get('target_type', ''))
        b_type = str(row.get('parking_type', ''))
        b_area = row.get('parking_area', 0)

        if '車位' not in target_str:
            return "無", "-"
        
        main_type = "平面" if "平面" in b_type else "機械" if "機械" in b_type else "其他"
        right = "所有權" if b_area > 0 else "使用權"
        
        return main_type, right
