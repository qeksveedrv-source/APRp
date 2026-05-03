import streamlit as st
import pandas as pd
import sqlite3
import os
from datetime import datetime
from geopy.geocoders import ArcGIS
from modules.calculator import RealEstateValuator
from modules.data_processor import get_neighbor_data, score_neighbors

# 設定頁面語系與排版
st.set_page_config(page_title="花蓮不動產自動估價系統 (APRp)", layout="wide")

# 初始化 session_state，用來儲存估價結果
if 'valuation_results' not in st.session_state:
    st.session_state.valuation_results = None

# ==========================================
# 側邊欄：參數輸入 (維持原樣)
# ==========================================
with st.sidebar:
    st.header("🏠 目標物件參數")
    addr = st.text_input("輸入目標地址", "花蓮市中正路")
    
    b_type = st.selectbox("建物型態", [
        "透天厝", 
        "住宅大樓(11層含以上有電梯)", 
        "華廈(10層含以下有電梯)", 
        "公寓(5樓含以下無電梯)"
    ])
    
    st.divider()
    
    if b_type == "透天厝":
        land_area = st.number_input("土地面積 (坪)", min_value=0.0, value=30.0, step=0.1)
        land_price = st.number_input("土地行情 (萬/坪)", min_value=0.0, value=25.0, step=0.1)
        build_area = st.number_input("建物總面積 (坪)", min_value=0.0, value=50.0, step=0.1)
        material = st.selectbox("主要建材", ["鋼筋混凝土", "鋼筋混凝土加強磚造"])
        age = st.number_input("屋齡 (年)", min_value=0, value=10)
        is_first_floor = True 
    else:
        is_first_floor = st.checkbox("包含一樓成交紀錄", value=False)
        build_area = st.number_input("建物權狀面積 (坪)", min_value=0.0, value=35.0, step=0.1)
        age = st.number_input("屋齡 (年)", min_value=0, value=15)
        parking_type = st.selectbox("預計車位類別", ["無", "平面", "機械"])

    run_btn = st.button("開始評估系統", use_container_width=True)

# ==========================================
# 主畫面邏輯 (加入 Session State 儲存)
# ==========================================
if run_btn:
    geocoder = ArcGIS()
    with st.spinner("正在定位地址與搜尋鄰近案例..."):
        loc = geocoder.geocode(addr)
    
    if loc:
        db_path = os.path.join('data', 'hualien.db')
        if not os.path.exists(db_path):
            st.error("找不到資料庫 (data/hualien.db)")
            st.stop()
            
        conn = sqlite3.connect(db_path)
        raw_pool = get_neighbor_data(conn, loc.latitude, loc.longitude, b_type, addr)
        
        if not raw_pool.empty:
            # 執行所有原本的計算與評分邏輯
            scored_pool = score_neighbors(raw_pool, age, is_first_floor)
            numeric_cols = ['land_area', 'building_area', 'total_price', 'main_building_area', 'berth_price', 'berth_area']
            for col in numeric_cols:
                if col in scored_pool.columns:
                    scored_pool[col] = pd.to_numeric(scored_pool[col], errors='coerce').fillna(0)
            
            scored_pool['land_area'] = (scored_pool['land_area'] * 0.3025).round(2)
            scored_pool['building_area'] = (scored_pool['building_area'] * 0.3025).round(2)
            if 'berth_area' in scored_pool.columns:
                scored_pool['berth_area'] = (scored_pool['berth_area'] * 0.3025).round(2)

            # 估價計算分流
            if b_type == "透天厝":
                grouped_records = []
                for a, group in scored_pool.groupby('address', sort=False):
                    row = group.iloc[0].copy()
                    if len(group) > 1:
                        row['total_price'] = group['total_price'].mean()
                        row['transaction_date'] = f"{group['transaction_date'].min()}~{group['transaction_date'].max()}"
                        row['is_avg'] = True
                    else:
                        row['is_avg'] = False
                    grouped_records.append(row)
                merged_pool = pd.DataFrame(grouped_records)
                merged_pool['sort_date'] = merged_pool['transaction_date'].astype(str).apply(lambda x: x.split('~')[-1])
                latest_5 = merged_pool.sort_values('sort_date', ascending=False).head(5)
                remaining = merged_pool[~merged_pool.index.isin(latest_5.index)]
                closest_5 = remaining.sort_values('dist', ascending=True).head(5)
                top_10 = pd.concat([latest_5, closest_5])
                
                target_data = {'land': land_area, 'build': build_area, 'age': age, 'material': material}
                low, high, premiums_list = RealEstateValuator.run_detached_valuation(target_data, top_10, land_price)
                top_10['market_premium'] = premiums_list
                eval_text = f"{int(low):,} 萬 - {int(high):,} 萬"
                eval_mode = "透天厝成本法 (5最新+5最近加權)"
            else:
                top_10 = scored_pool.head(10).copy()
                low_up, high_up = RealEstateValuator.run_apartment_valuation(top_10)
                eval_text = f"{low_up:.1f} 萬/坪 - {high_up:.1f} 萬/坪"
                eval_mode = f"依目標面積推算總價 (不含車位)：{int(low_up * build_area):,}萬 ~ {int(high_up * build_area):,}萬"

            # 將結果存入 session_state
            st.session_state.valuation_results = {
                'addr': addr,
                'lat': loc.latitude,
                'lon': loc.longitude,
                'top_10': top_10,
                'eval_text': eval_text,
                'eval_mode': eval_mode,
                'b_type': b_type,
                'build_area': build_area
            }
        else:
            st.session_state.valuation_results = "empty"
        conn.close()
    else:
        st.error("❌ 無法定位地址。")

# ==========================================
# 渲染畫面 (從 Session State 讀取)
# ==========================================
res = st.session_state.valuation_results

if res == "empty":
    st.warning("⚠️ 3 公里範圍內查無符合型態的成交紀錄。")
elif res is not None:
    # 顯示成功定位資訊
    st.success(f"📍 定位成功：{res['addr']} ({res['lat']:.5f}, {res['lon']:.5f})")
    st.divider()

    # 顯示估價結果指標
    col1, col2 = st.columns(2)
    if res['b_type'] == "透天厝":
        col1.metric("⚖️ 建議行情區間", res['eval_text'])
        col2.info(f"估價模式：{res['eval_mode']}")
    else:
        col1.metric("⚖️ 建物實質單價建議", res['eval_text'])
        st.write(f"👉 {res['eval_mode']}")

    st.write("### 📋 近鄰成交參考紀錄")
    top_10 = res['top_10']
    top_10['dist_m'] = (top_10['dist'] * 1000).astype(int)

    # 整理表格顯示欄位 (維持原本的 display_cols 邏輯)
    if res['b_type'] == "透天厝":
        top_10['sort_date_only'] = top_10['transaction_date'].astype(str).apply(lambda x: x.split('~')[0])
        top_10 = top_10.sort_values(by=['is_avg', 'sort_date_only'], ascending=[True, False])
        top_10['price_display'] = top_10.apply(
            lambda r: f"(平均) {int(r['total_price']/10000)}" if r['is_avg'] else str(int(r['total_price']/10000)), axis=1
        )
        display_cols = {
            'address': '門牌', 'dist_m': '距離(m)', 'building_area': '建物面積(坪)',
            'calc_age': '屋齡(年)', 'land_area': '土地面積(坪)', 'price_display': '成交價(萬)',
            'market_premium': '市場溢價係數', 'total_score': '權重分數', 'transaction_date': '成交日'
        }
    else:
        top_10 = top_10.sort_values(by='transaction_date', ascending=False)
        top_10['berth_display'] = top_10.apply(
            lambda r: f"{RealEstateValuator.get_berth_info(r)[0]}{RealEstateValuator.get_berth_info(r)[1]}", axis=1
        )
        top_10['price_10k'] = (top_10['total_price'] / 10000).astype(int)
        top_10['main_unit_price'] = top_10['unit_price_p'].round(1)
        display_cols = {
            'address': '門牌', 'dist_m': '距離(m)', 'building_area': '建物權狀面積(坪)',
            'calc_age': '屋齡(年)', 'main_unit_price': '主建物單價', 'berth_display': '車位類型＆權利',
            'price_10k': '實登價格(萬)', 'total_score': '權重分數', 'transaction_date': '成交日'
        }

    # 檢視模式切換 (現在點選後資料會保留)
    view_mode = st.radio("檢視模式", ["📱 手機優先 (Top 5 卡片)", "💻 完整資料表"], horizontal=True, label_visibility="collapsed")

    if view_mode == "📱 手機優先 (Top 5 卡片)":
        for i, (_, row) in enumerate(top_10.head(5).iterrows()):
            with st.expander(f"📍 {row['address']} ({row['transaction_date']})", expanded=(i==0)):
                c1, c2 = st.columns(2)
                if res['b_type'] == "透天厝":
                    c1.metric("成交價", f"{row['price_display']} 萬")
                    c2.metric("距離", f"{row['dist_m']} m")
                    st.write(f"🏠 **面積**：{row['building_area']} 坪 | **屋齡**：{row['calc_age']} 年")
                else:
                    c1.metric("單價", f"{row['main_unit_price']} 萬/坪")
                    c2.metric("距離", f"{row['dist_m']} m")
                    st.write(f"💰 **總價**：{row['price_10k']} 萬 | **車位**：{row['berth_display']}")
                st.progress(min(row['total_score']/100, 1.0), text=f"權重分數：{row['total_score']:.1f}")
    else:
        st.dataframe(top_10[list(display_cols.keys())].rename(columns=display_cols), use_container_width=True)

    # 底部參考資料 (維持原樣)
    if res['b_type'] != "透天厝":
        st.markdown("---")
        st.write("### 💡 車位建議行情參考")
        parking_ref = pd.DataFrame({
            "類型": ["平面", "平面", "機械", "機械"],
            "權利": ["所有權", "使用權", "所有權", "使用權"],
            "建議行情": ["130～180萬", "90～140萬", "60～100萬", "30～70萬"]
        })
        st.table(parking_ref)
