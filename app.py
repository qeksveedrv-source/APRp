import streamlit as st
import pandas as pd
import sqlite3
import os
from datetime import datetime
from geopy.geocoders import ArcGIS
from modules.calculator import RealEstateValuator
from modules.data_processor import get_neighbor_data, score_neighbors

# 設定頁面語系與排版
st.set_page_config(page_title="花蓮房地估價系統 (APRp)", layout="wide")

# ==========================================
# 資料庫連線快取
# ==========================================
@st.cache_resource
def get_db_connection():
    """管理 SQLite 連線快取，避免頻繁開啟與關閉檔案"""
    db_path = os.path.join('data', 'hualien.db')
    if not os.path.exists(db_path):
        return None
    return sqlite3.connect(db_path, check_same_thread=False)

# 初始化 session_state，用來儲存估價結果
if 'valuation_results' not in st.session_state:
    st.session_state.valuation_results = None

# ==========================================
# 頂部區塊：網頁標題與參數輸入
# ==========================================
st.title("🏠 花蓮房地估價系統（APRp）")
st.markdown("##### 核心功能：街路排他邏輯、一年內資料優先篩選")
st.markdown("---")

with st.container():
    st.header("📌 目標物件參數輸入")
    
    col_a, col_b = st.columns([2, 1])
    with col_a:
        addr = st.text_input("輸入目標地址", "花蓮市南京街")
    with col_b:
        b_type = st.selectbox("建物型態", [
            "透天厝", 
            "住宅大樓(11層含以上有電梯)", 
            "華廈(10層含以下有電梯)", 
            "公寓(5樓含以下無電梯)"
        ])
    
    if b_type == "透天厝":
        c1, c2, c3 = st.columns(3)
        with c1:
            land_area = st.number_input("土地面積 (坪)", min_value=0.0, value=30.0, step=0.1)
        with c2:
            land_price = st.number_input("土地行情 (萬/坪)", min_value=0.0, value=14.0, step=0.1)
        with c3:
            build_area = st.number_input("建物總面積 (坪)", min_value=0.0, value=60.0, step=0.1)
        
        c4, material_col = st.columns([1, 1])
        with material_col:
            material_val = st.selectbox("主要建材", ["鋼筋混凝土", "鋼筋混凝土加強磚造"])
        with c4:
            age = st.number_input("屋齡 (年)", min_value=0, value=20)
        is_first_floor = True 
    else:
        c1, c2, c3 = st.columns(3)
        with c1:
            build_area = st.number_input("建物權狀面積 (坪)", min_value=0.0, value=30.0, step=0.1)
        with c2:
            age = st.number_input("屋齡 (年)", min_value=0, value=22)
        with c3:
            parking_type = st.selectbox("預計車位類別", ["無", "平面", "機械"])
        
        is_first_floor = st.checkbox("包含一樓成交紀錄", value=False)

    run_btn = st.button("🚀 開始評估系統", use_container_width=True, type="primary")

# ==========================================
# 運算邏輯區
# ==========================================
if run_btn:
    geocoder = ArcGIS()
    with st.spinner("正在定位地址與搜尋鄰近案例..."):
        loc = geocoder.geocode(addr)
    
    if loc:
        conn = get_db_connection()
        if conn is None:
            st.error("找不到資料庫 (data/hualien.db)")
            st.stop()
            
        # 1. 抓取候選池 (內含街路排他邏輯)
        raw_pool = get_neighbor_data(conn, loc.latitude, loc.longitude, b_type, addr)
        
        # --- 篩選條件：集合住宅避開地下室紀錄 ---
        if not raw_pool.empty and b_type != "透天厝":
            if 'floor_level' in raw_pool.columns:
                raw_pool = raw_pool[~raw_pool['floor_level'].str.contains('地下', na=False)]
            if 'address' in raw_pool.columns:
                raw_pool = raw_pool[~raw_pool['address'].str.endswith('地下室', na=False)]
        
        if not raw_pool.empty:
            # 2. 權重計分
            scored_pool = score_neighbors(raw_pool, age, is_first_floor)
            
            # --- 一年內資料優先篩選邏輯 ---
            now = datetime.now()
            # 民國年日期門檻 (例如 1150506)
            one_year_ago_roc = (now.year - 1911 - 1) * 10000 + now.month * 100 + now.day
            
            # 優先檢查一年內資料
            recent_mask = scored_pool['deal_date'].astype(int) >= one_year_ago_roc
            recent_data = scored_pool[recent_mask].copy()
            
            if len(recent_data) >= 10:
                final_pool = recent_data
                valuation_msg = "採用近一年成交紀錄進行估價"
            else:
                final_pool = scored_pool.copy()
                valuation_msg = "因近一年成交量不足十筆，系統已納入一年以上紀錄以供參考"
            # ---------------------------

            # 數值型態與單位轉換
            numeric_cols = ['land_area', 'total_build_area', 'price', 'main_area', 'parking_price', 'parking_area']
            for col in numeric_cols:
                if col in final_pool.columns:
                    final_pool[col] = pd.to_numeric(final_pool[col], errors='coerce').fillna(0)
            
            final_pool['land_area'] = (final_pool['land_area'] * 0.3025).round(2)
            final_pool['total_build_area'] = (final_pool['total_build_area'] * 0.3025).round(2)
            if 'parking_area' in final_pool.columns:
                final_pool['parking_area'] = (final_pool['parking_area'] * 0.3025).round(2)

            if b_type == "透天厝":
                # 透天分組邏輯
                grouped_records = []
                for a, group in final_pool.groupby('address', sort=False):
                    row = group.iloc[0].copy()
                    if len(group) > 1:
                        row['price'] = group['price'].mean()
                        row['deal_date'] = f"{group['deal_date'].min()}~{group['deal_date'].max()}"
                        row['is_avg'] = True
                    else:
                        row['is_avg'] = False
                    grouped_records.append(row)
                
                merged_pool = pd.DataFrame(grouped_records)
                merged_pool['sort_date'] = merged_pool['deal_date'].astype(str).apply(lambda x: x.split('~')[-1])
                
                # 選案策略：5最新 + 5最近
                latest_5 = merged_pool.sort_values('sort_date', ascending=False).head(5)
                remaining = merged_pool[~merged_pool.index.isin(latest_5.index)]
                closest_5 = remaining.sort_values('dist', ascending=True).head(5)
                top_10 = pd.concat([latest_5, closest_5])
                
                target_data = {'land': land_area, 'build': build_area, 'age': age, 'material': material_val}
                low, high, premiums_list = RealEstateValuator.run_detached_valuation(target_data, top_10, land_price)
                top_10['market_premium'] = premiums_list
                eval_text = f"{int(low):,} 萬 - {int(high):,} 萬"
                eval_mode = "透天厝成本法 (5最新+5最近加權)"
            else:
                # 集合住宅選案：前 10 筆權重最高者
                top_10 = final_pool.head(10).copy()
                low_up, high_up = RealEstateValuator.run_apartment_valuation(top_10)
                eval_text = f"{low_up:.1f} 萬/坪 - {high_up:.1f} 萬/坪"
                eval_mode = f"依目標面積推算總價：{int(low_up * build_area):,}萬 ~ {int(high_up * build_area):,}萬"

            st.session_state.valuation_results = {
                'addr': addr, 'lat': loc.latitude, 'lon': loc.longitude,
                'top_10': top_10, 'eval_text': eval_text, 'eval_mode': eval_mode,
                'b_type': b_type, 'build_area': build_area,
                'valuation_msg': valuation_msg
            }
        else:
            st.session_state.valuation_results = "empty"
    else:
        st.error("❌ 無法定位地址。")

# ==========================================
# 下方區塊：結果顯示
# ==========================================
res = st.session_state.valuation_results

if res == "empty":
    st.warning("⚠️ 查無符合條件的成交紀錄。")
elif res is not None:
    st.markdown("---")
    st.success(f"📍 定位成功：{res['addr']}")
    st.info(f"💡 系統提示：{res['valuation_msg']}")

    m_col1, m_col2 = st.columns(2)
    if res['b_type'] == "透天厝":
        m_col1.metric("⚖️ 建議行情區間", res['eval_text'])
    else:
        m_col1.metric("⚖️ 建物實質單價建議", res['eval_text'])
    m_col2.info(f"估價模式：{res['eval_mode']}")

    st.write("### 📋 近鄰成交參考紀錄")
    top_10 = res['top_10']
    top_10['dist_m'] = (top_10['dist'] * 1000).astype(int)

    if res['b_type'] == "透天厝":
        top_10['price_display'] = top_10.apply(
            lambda r: f"(平均) {int(r['price']/10000)}" if r['is_avg'] else str(int(r['price']/10000)), axis=1
        )
        display_cols = {
            'address': '門牌', 'dist_m': '距離(m)', 'total_build_area': '建物面積(坪)',
            'calc_age': '屋齡(年)', 'land_area': '土地面積(坪)', 'price_display': '成交價(萬)',
            'market_premium': '市場溢價係數', 'deal_date': '成交日'
        }
    else:
        top_10['berth_display'] = top_10.apply(
            lambda r: f"{RealEstateValuator.get_berth_info(r)[0]}{RealEstateValuator.get_berth_info(r)[1]}", axis=1
        )
        top_10['price_10k'] = (top_10['price'] / 10000).astype(int)
        display_cols = {
            'address': '門牌', 'dist_m': '距離(m)', 'total_build_area': '建物權狀面積(坪)',
            'calc_age': '屋齡(年)', 'unit_price_p': '實質單價', 'berth_display': '車位',
            'price_10k': '價格(萬)', 'deal_date': '成交日'
        }

    st.dataframe(top_10[list(display_cols.keys())].rename(columns=display_cols), use_container_width=True)

    # 顯示車位建議行情
    if res['b_type'] != "透天厝":
        st.markdown("---")
        st.write("### 💡 車位建議行情參考")
        parking_ref = pd.DataFrame({
            "類型": ["平面", "平面", "機械", "機械"],
            "權利": ["所有權", "使用權", "所有權", "使用權"],
            "建議行情": ["130～180萬", "90～140萬", "60～100萬", "30～70萬"]
        })
        st.table(parking_ref)
