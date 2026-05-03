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
    # check_same_thread=False 為 Streamlit 多執行緒環境必備參數
    return sqlite3.connect(db_path, check_same_thread=False)

# 初始化 session_state，用來儲存估價結果
if 'valuation_results' not in st.session_state:
    st.session_state.valuation_results = None

# ==========================================
# 頂部區塊：網頁標題與參數輸入
# ==========================================
st.title("🏠 花蓮房地估價系統（APRp）")
st.markdown("##### 資料庫：112年度至115年第一季")
st.markdown("---")

with st.container():
    st.header("📌 目標物件參數輸入")
    
    col_a, col_b = st.columns([2, 1])
    with col_a:
        addr = st.text_input("輸入目標地址", "花蓮市中正路")
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
            land_price = st.number_input("土地行情 (萬/坪)", min_value=0.0, value=25.0, step=0.1)
        with c3:
            build_area = st.number_input("建物總面積 (坪)", min_value=0.0, value=50.0, step=0.1)
        
        c4, c5 = st.columns(2)
        with c4:
            material = st.selectbox("主要建材", ["鋼筋混凝土", "鋼筋混凝土加強磚造"])
        with c5:
            age = st.number_input("屋齡 (年)", min_value=0, value=10)
        is_first_floor = True 
    else:
        c1, c2, c3 = st.columns(3)
        with c1:
            build_area = st.number_input("建物權狀面積 (坪)", min_value=0.0, value=35.0, step=0.1)
        with c2:
            age = st.number_input("屋齡 (年)", min_value=0, value=15)
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
        # 改用快取函式取得連線
        conn = get_db_connection()
        
        if conn is None:
            st.error("找不到資料庫 (data/hualien.db)")
            st.stop()
            
        # 1. 抓取候選池
        raw_pool = get_neighbor_data(conn, loc.latitude, loc.longitude, b_type, addr)
        
        # --- 篩選條件：集合住宅避開地下室紀錄 ---
        if not raw_pool.empty and b_type != "透天厝":
            # 排除樓層資訊包含「地下」的案例
            if 'floor_info' in raw_pool.columns:
                raw_pool = raw_pool[~raw_pool['floor_info'].str.contains('地下', na=False)]
            
            # 排除門牌名稱最後面有「地下室」的案例
            if 'address' in raw_pool.columns:
                raw_pool = raw_pool[~raw_pool['address'].str.endswith('地下室', na=False)]
        
        if not raw_pool.empty:
            # 2. 權重計分
            scored_pool = score_neighbors(raw_pool, age, is_first_floor)
            
            numeric_cols = ['land_area', 'building_area', 'total_price', 'main_building_area', 'berth_price', 'berth_area']
            for col in numeric_cols:
                if col in scored_pool.columns:
                    scored_pool[col] = pd.to_numeric(scored_pool[col], errors='coerce').fillna(0)
            
            scored_pool['land_area'] = (scored_pool['land_area'] * 0.3025).round(2)
            scored_pool['building_area'] = (scored_pool['building_area'] * 0.3025).round(2)
            if 'berth_area' in scored_pool.columns:
                scored_pool['berth_area'] = (scored_pool['berth_area'] * 0.3025).round(2)

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

            st.session_state.valuation_results = {
                'addr': addr, 'lat': loc.latitude, 'lon': loc.longitude,
                'top_10': top_10, 'eval_text': eval_text, 'eval_mode': eval_mode,
                'b_type': b_type, 'build_area': build_area
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
    st.warning("⚠️ 3 公里範圍內查無符合型態的成交紀錄。")
elif res is not None:
    st.markdown("---")
    st.success(f"📍 定位成功：{res['addr']} ({res['lat']:.5f}, {res['lon']:.5f})")

    m_col1, m_col2 = st.columns(2)
    if res['b_type'] == "透天厝":
        m_col1.metric("⚖️ 建議行情區間", res['eval_text'])
        m_col2.info(f"估價模式：{res['eval_mode']}")
    else:
        m_col1.metric("⚖️ 建物實質單價建議", res['eval_text'])
        st.write(f"👉 {res['eval_mode']}")

    st.write("### 📋 近鄰成交參考紀錄")
    top_10 = res['top_10']
    top_10['dist_m'] = (top_10['dist'] * 1000).astype(int)

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

    view_mode = st.radio("檢視模式", ["📱 手機優先 (Top 5 卡片)", "💻 完整資料表"], horizontal=True, label_visibility="collapsed")

    if view_mode == "📱 手機優先 (Top 5 卡片)":
        for i, (_, row) in enumerate(top_10.head(5).iterrows()):
            with st.expander(f"📍 {row['address']} ({row['transaction_date']})", expanded=(i==0)):
                if res['b_type'] == "透天厝":
                    # 顯示：距離、屋齡、土地面積、建物面積、市場溢價係數、權重分數
                    c1, c2 = st.columns(2)
                    c1.metric("距離", f"{row['dist_m']} m")
                    c2.metric("屋齡", f"{row['calc_age']} 年")
                    
                    st.write(f"📐 **土地面積**：{row['land_area']} 坪")
                    st.write(f"🏠 **建物面積**：{row['building_area']} 坪")
                    st.write(f"📈 **市場溢價係數**：{row['market_premium']}")
                    st.progress(min(row['total_score']/100, 1.0), text=f"權重分數：{row['total_score']:.1f}")
                else:
                    # 顯示：距離、屋齡、建物權狀面積、主建物單價、車位類型＆權利
                    c1, c2 = st.columns(2)
                    c1.metric("距離", f"{row['dist_m']} m")
                    c2.metric("屋齡", f"{row['calc_age']} 年")
                    
                    st.write(f"🏢 **建物權狀面積**：{row['building_area']} 坪")
                    st.write(f"💰 **主建物單價**：{row['main_unit_price']} 萬/坪")
                    st.write(f"🚗 **車位類型＆權利**：{row['berth_display']}")
                    st.progress(min(row['total_score']/100, 1.0), text=f"權重分數：{row['total_score']:.1f}")
    else:
        st.dataframe(top_10[list(display_cols.keys())].rename(columns=display_cols), use_container_width=True)

    if res['b_type'] != "透天厝":
        st.markdown("---")
        st.write("### 💡 車位建議行情參考")
        parking_ref = pd.DataFrame({
            "類型": ["平面", "平面", "機械", "機械"],
            "權利": ["所有權", "使用權", "所有權", "使用權"],
            "建議行情": ["130～180萬", "90～140萬", "60～100萬", "30～70萬"]
        })
        st.table(parking_ref)
