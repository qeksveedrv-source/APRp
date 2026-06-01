import streamlit as st
import pandas as pd
import sqlite3
import os
import folium
from folium.plugins import BeautifyIcon
from streamlit_folium import st_folium
from datetime import datetime
from geopy.geocoders import ArcGIS
from modules.calculator import RealEstateValuator
from modules.data_processor import get_neighbor_data, score_neighbors
from modules import settings

st.set_page_config(page_title="花蓮吉安房地訪價系統 (APRp)", layout="wide")

# --- 加入列印優化 CSS ---
st.markdown("""
    <style>
    @media print {
        /* 1. 白底黑字 */
        body, .stApp, .main, .block-container, [data-testid="stAppViewContainer"] {
            background-color: #FFFFFF !important;
            background: #FFFFFF !important;
        }
        h1, h2, h3, h4, h5, h6, p, span, div, label, li, td, th {
            color: #000000 !important;
        }

        /* 2. 徹底隱藏 Streamlit 預設頂部(Header)與側邊介面 */
        header, footer, [data-testid="stHeader"], [data-testid="stToolbar"], [data-testid="stSidebar"] {
            display: none !important;
            height: 0 !important;
            margin: 0 !important;
            padding: 0 !important;
        }

        /* 3. 消滅所有隱藏元件的幽靈佔位 */
        .element-container:has(.no-print), 
        .element-container:has([data-testid="stTextInput"]), 
        .element-container:has([data-testid="stSelectbox"]), 
        .element-container:has([data-testid="stNumberInput"]), 
        .element-container:has([data-testid="stCheckbox"]), 
        .element-container:has([data-testid="stButton"]),
        .element-container:has([data-testid="stAlert"]),
        [data-testid="stHorizontalBlock"]:has([data-testid="stTextInput"]),
        [data-testid="stHorizontalBlock"]:has([data-testid="stNumberInput"]) {
            display: none !important;
            height: 0 !important;
            margin: 0 !important;
            padding: 0 !important;
        }
        
        hr { display: none !important; }

        /* 4. 歸零所有垂直與水平的預設間隔 */
        [data-testid="stVerticalBlock"], [data-testid="stHorizontalBlock"] {
            gap: 0rem !important;
            padding: 0 !important;
        }

        /* 5. A4 邊距與最頂端對齊設定 */
        @page {
            size: A4;
            margin: 1.2cm 1cm 1cm 1cm !important; /* 稍微縮減上邊距 */
        }
        
        /* 🚀 核心修正：強制主容器貼齊頂部，消除預設超大 padding 並往上拉提 */
        div.block-container {
            padding-top: 0rem !important;
            margin-top: -2.5rem !important; 
            padding-bottom: 0rem !important;
            max-width: 100% !important;
        }
        
        /* 防止表格斷頁 */
        .folium-map, table, .stDataFrame, [data-testid="stTable"] {
            page-break-inside: avoid !important;
        }
    }
    </style>
""", unsafe_allow_html=True)
# ==========================================
# 定位結果快取一小時，避免重複扣 API 額度
# ==========================================
@st.cache_data(ttl=settings.CACHE_TTL_SEC)
def get_geocode(address):
    from geopy.geocoders import ArcGIS
    geocoder = ArcGIS()
    return geocoder.geocode(address)
# ==========================================
# 資料庫連線快取
# ==========================================
@st.cache_resource
def get_db_connection():
    """管理 SQLite 連線快取，避免頻繁開啟與關閉檔案"""
    db_path = settings.DB_PATH
    if not os.path.exists(db_path):
        return None
    return sqlite3.connect(db_path, check_same_thread=False)

# 初始化 session_state，用來儲存訪價結果
if 'valuation_results' not in st.session_state:
    st.session_state.valuation_results = None

# ==========================================
# 頂部區塊：網頁標題與參數輸入
# ==========================================
st.markdown("<h1 class='no-print'>🏠 花蓮吉安房地訪價系統（APRp）</h1>", unsafe_allow_html=True)
st.markdown("<h5 class='no-print'>資料庫112年到115年第一季</h5>", unsafe_allow_html=True)
st.markdown("<p class='no-print'><b>訪價模式</b><br>透天厝 = 成本法折舊＋市場溢價調整、集合住宅 = 實質單價拆算 ＋ 相似度權重加權</p>", unsafe_allow_html=True)
st.markdown("<hr class='no-print'>", unsafe_allow_html=True)

with st.container():
    st.markdown("<h2 class='no-print'>📌 目標物件參數輸入</h2>", unsafe_allow_html=True)
   
    col_a, col_b = st.columns([2, 1])
    with col_a:
        addr = st.text_input("輸入目標地址", "花蓮縣吉安鄉")
    with col_b:
        b_type = st.selectbox("建物型態", [
            "透天厝", 
            "住宅大樓(11樓有電梯)", 
            "華廈(10樓有電梯)", 
            "公寓(5樓無電梯)"
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
        
        is_first_floor = st.checkbox("包含一樓成交紀錄", value=False)

    run_btn = st.button("🚀 開始評估系統", use_container_width=True, type="primary")

# ==========================================
# 運算邏輯區
# ==========================================
if run_btn:
    with st.spinner("正在定位地址與搜尋鄰近案例..."):
        # 如果使用者沒有輸入花蓮縣，系統自動補上以利精準定位
        search_addr = addr if addr.startswith("花蓮縣") else "花蓮縣" + addr
        loc = get_geocode(search_addr)
    
    if loc:
        conn = get_db_connection()
        if conn is None:
            st.error("找不到資料庫 (data/hualien.db)")
            st.stop()
            
        # 1. 抓取候選池
        raw_pool = get_neighbor_data(conn, loc.latitude, loc.longitude, b_type, addr)
        
        if not raw_pool.empty:
            # 集合住宅避開地下室紀錄
            if b_type != "透天厝":
                if 'floor_level' in raw_pool.columns:
                    raw_pool = raw_pool[~raw_pool['floor_level'].str.contains('地下', na=False)]
                if 'address' in raw_pool.columns:
                    raw_pool = raw_pool[~raw_pool['address'].str.endswith('地下室', na=False)]
            
            # 2. 權重計分 (傳入目標坪數與型態，供距離與面積計分使用)
            scored_pool = score_neighbors(raw_pool, age, is_first_floor, build_area, b_type)
            
            # 2. 權重計分
            scored_pool = score_neighbors(raw_pool, age, is_first_floor, build_area, b_type)
            
            # --- 絕對分數門檻 (>=10分) ---
            final_pool = scored_pool[scored_pool['total_score'] >= settings.MIN_TOTAL_SCORE].copy()
            
            # 依照分數由高到低排序，最多取前 10 筆作為估價基準
            top_10 = final_pool.sort_values(['total_score', 'deal_date'], ascending=[False, False]).head(10)
            
            valuation_msg = f"嚴格權重篩選：共找到 {len(top_10)} 筆權重達標 (>=10分) 之相似紀錄"

            # 數值型態與單位轉換
            numeric_cols = ['land_area', 'total_build_area', 'price', 'main_area', 'parking_price', 'parking_area']
            for col in numeric_cols:
                if col in final_pool.columns:
                    final_pool[col] = pd.to_numeric(final_pool[col], errors='coerce').fillna(0)
            
            # 面積換算改用 settings 常數
            top_10['land_area'] = (top_10['land_area'] * settings.SQM_TO_PING).round(2)
            top_10['total_build_area'] = (top_10['total_build_area'] * settings.SQM_TO_PING).round(2)

            if b_type == "透天厝":
                target_data = {'land': land_area, 'build': build_area, 'age': age, 'material': material_val}
                # 🌟 改為傳入完整的 final_pool，並由引擎回傳過濾好的正數 top_10
                low, high, top_10 = RealEstateValuator.run_detached_valuation(target_data, final_pool, land_price)
                eval_text = f"{int(low):,} 萬 - {int(high):,} 萬"
                eval_mode = "透天厝成本法 (依相似度權重篩選)"
            else:
                low_up, high_up = RealEstateValuator.run_apartment_valuation(top_10)
                eval_text = f"{low_up:.1f} 萬/坪 - {high_up:.1f} 萬/坪"
                eval_mode = f"依面積推算總價：{int(low_up * build_area):,}萬 ~ {int(high_up * build_area):,}萬(不含車位）"

            # 數值型態與單位轉換
            numeric_cols = ['land_area', 'total_build_area', 'price', 'main_area', 'parking_price', 'parking_area']
            for col in numeric_cols:
                if col in final_pool.columns:
                    final_pool[col] = pd.to_numeric(final_pool[col], errors='coerce').fillna(0)
            
            # 面積換算改用 settings 常數
            final_pool['land_area'] = (final_pool['land_area'] * settings.SQM_TO_PING).round(2)
            final_pool['total_build_area'] = (final_pool['total_build_area'] * settings.SQM_TO_PING).round(2)
            
            # 透天分組邏輯：同門牌若有多筆成交，只保留時間最近的一筆
            if b_type == "透天厝":
                                
                # 1. 先依照交易日期(deal_date)由新到舊排序
                final_pool = final_pool.sort_values('deal_date', ascending=False)
                
                # 2. 剔除重複的門牌，因為已經排過序，保留的第一筆 (keep='first') 就是最新的一筆
                merged_pool = final_pool.drop_duplicates(subset=['address'], keep='first').copy()
                
                # 3. 設定排序用的日期欄位
                merged_pool['sort_date'] = merged_pool['deal_date'].astype(str)
                
                # 選案策略
                latest_5 = merged_pool.sort_values('sort_date', ascending=False).head(5)
                remaining = merged_pool[~merged_pool.index.isin(latest_5.index)]
                closest_5 = remaining.sort_values('dist', ascending=True).head(5)
                top_10 = pd.concat([latest_5, closest_5])
                
                target_data = {'land': land_area, 'build': build_area, 'age': age, 'material': material_val}
                # 🌟 直接用 top_10 接收引擎過濾好、且已經內建好溢價係數的最終資料表！
                low, high, top_10 = RealEstateValuator.run_detached_valuation(target_data, final_pool, land_price)
                eval_text = f"{int(low):,} 萬 - {int(high):,} 萬"
                eval_mode = "透天厝成本法 (5最新+5最近加權)"
                
                # 排序顯示：最新成交日在最上面
                top_10 = top_10.sort_values('deal_date', ascending=False)
            else:
                # 集合住宅選案
                top_10 = final_pool.head(10).copy()
                low_up, high_up = RealEstateValuator.run_apartment_valuation(top_10)
                eval_text = f"{low_up:.1f} 萬/坪 - {high_up:.1f} 萬/坪"
                eval_mode = f"依面積推算總價：{int(low_up * build_area):,}萬 ~ {int(high_up * build_area):,}萬(不含車位）"
                
                # 排序顯示：最新成交日在最上面
                top_10 = top_10.sort_values('deal_date', ascending=False)

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
    # =========================================================================
    # 👑 1. A4 列印 (上邊 2cm，其餘三邊 1cm)
    # =========================================================================
    st.markdown("""
        <style>
        @media print {
            /* 嚴格設定 A4 尺寸與使用者指定的邊距 */
            @page {
                size: A4;
                margin: 2cm 1cm 1cm 1cm !important;
            }
            /* 隱藏所有網頁原生 UI、側邊欄、裝飾線 */
            header, footer, .stDeployButton, [data-testid="stToolbar"], [data-testid="stSidebar"] {
                display: none !important;
            }
            /* 調整主容器寬度，使其完美填滿 A4 紙張邊界 */
            .main .block-container {
                padding-top: 0rem !important;
                padding-bottom: 0rem !important;
                max-width: 100% !important;
            }
            /* 防止地圖與表格在中間被尷尬切頁 */
            .folium-map, table, .stDataFrame {
                page-break-inside: avoid !important;
            }
        }
        </style>
    """, unsafe_allow_html=True)

    # =========================================================================
    # 右邊置右的「另存 PDF」按鈕
    # =========================================================================
    title_col, btn_col = st.columns([4, 1])
    with title_col:
        # 加上 no-print 讓這行標題在列印時隱藏
        st.markdown("<h3 class='no-print'>🏠 房地自動訪價報告演算法結果</h3>", unsafe_allow_html=True)
    with btn_col:
        # 利用 HTML iframe 安全執行，並在 iframe 內部注入列印時隱藏按鈕的 CSS
        st.components.v1.html("""
            <style>
                @media print { button { display: none !important; } }
            </style>
            <div style="text-align: right; padding-top: 5px;">
                <button onclick="window.parent.print()" style="
                    padding: 9px 18px; 
                    background-color: #28a745; 
                    color: white; 
                    border: none; 
                    border-radius: 4px; 
                    cursor: pointer; 
                    font-size: 14px;
                    font-weight: bold;
                    box-shadow: 0 2px 4px rgba(0,0,0,0.15);
                ">
                    🖨️ 產出另存 PDF
                </button>
            </div>
        """, height=45)

    # =========================================================================
    # 【最上面】顯示地圖且帶有標記 (自動調整縮放距離)
    # =========================================================================
    st.markdown("---")
    st.write("### 🗺️ 訪價標的與周邊參考標的地圖")

    # 初始化基本地圖
    m = folium.Map(location=[res['lat'], res['lon']], zoom_start=15)

    # 標記紅色「目標物件」
    folium.Marker(
        [res['lat'], res['lon']],
        popup=f"<b>⭐ 目標物件</b><br>{res['addr']}",
        tooltip="目標物件",
        icon=folium.Icon(color="red", icon="home", prefix='fa')
    ).add_to(m)

    top_10_df = res['top_10'].copy()
    map_labels = []

    # 🌟 建立座標收集清單，首項先放入「目標物件」座標
    all_coordinates = [[res['lat'], res['lon']]]

    # 迴圈標記參考標的
    label_idx = 1
    for idx, row in top_10_df.iterrows():
        try:
            ref_lat = float(row['Response_Y'])
            ref_lon = float(row['Response_X'])

            if label_idx > 10:
                map_labels.append("")
                continue

            current_label = f"參考{label_idx}"
            map_labels.append(current_label)

            # 🌟 只要座標有效，就將其加入全體座標清單中
            all_coordinates.append([ref_lat, ref_lon])

            if res['b_type'] == "透天厝":
                price_info = f"總價: {int(row['price']/10000)} 萬"
            else:
                price_info = f"單價: {row['unit_price_p']:.1f} 萬/坪"

            popup_text = f"""
            <b>【{current_label}】{row['address']}</b><br>
            📏 距離: {int(row['dist']*1000)} 公尺<br>
            🏠 屋齡: {row['calc_age']} 年<br>
            💰 {price_info}
            """

            b_icon = BeautifyIcon(
                icon_shape='circle',
                number=str(label_idx),
                text_color='white',
                background_color='#1f77b4',
                border_color='#1f77b4',
                inner_icon_style='font-weight: bold; font-size: 13px; margin-top: 2px;'
            )

            folium.Marker(
                [ref_lat, ref_lon],
                popup=folium.Popup(popup_text, max_width=250),
                tooltip=f"{current_label}: {row['address']}",
                icon=b_icon
            ).add_to(m)

            label_idx += 1

        except ValueError:
            map_labels.append("")
            continue

    # 🌟 關鍵改進：利用 fit_bounds 功能，全自動調整地圖的中心點與縮放大小，確保看得到所有標記
    if len(all_coordinates) > 1:
        m.fit_bounds(all_coordinates)

    # 渲染地圖至網頁上 (寬度略微調大以優化 A4 橫向佔比)
    st_folium(m, width=1100, height=500, returned_objects=[])

    # =========================================================================
    # 【地圖下面】顯示目標物件的資料與訪價區間 
    # =========================================================================
    
    # 🚀 調整 1：設定地圖與基本資料之間的精確間距
    st.markdown("<div style='margin-top: 1.2cm;'></div>", unsafe_allow_html=True)
    st.write("### 📋 目標物件基本資料與行情估算")
    
    # 🟢 保持強健的動態變數偵測機制
    top_10_df = res['top_10'].copy()
    display_age = "未知"
    display_build = "-"
    display_land = "-"
    
    # 1. 屋齡變數偵測
    if 'target' in locals() or 'target' in globals():
        t_obj = locals().get('target') if 'target' in locals() else globals().get('target')
        if isinstance(t_obj, dict) and 'age' in t_obj:
            display_age = t_obj['age']
            
    if display_age == "未知":
        for var_name in ['target_age', 'house_age', 'age', 'calc_age', 't_age', 'apt_age', 'building_age', 'b_age']:
            if var_name in locals() or var_name in globals():
                display_age = locals().get(var_name) if var_name in locals() else globals().get(var_name)
                break

    # 2. 建物面積變數偵測
    if 'target' in locals() or 'target' in globals():
        t_obj = locals().get('target') if 'target' in locals() else globals().get('target')
        if isinstance(t_obj, dict) and 'build' in t_obj:
            display_build = t_obj['build']
            
    if display_build == "-":
        for var_name in ['target_build_area', 'build_area', 'total_build_area', 'build', 't_build', 'ping', 'area', 'apt_area', 'house_area']:
            if var_name in locals() or var_name in globals():
                display_build = locals().get(var_name) if var_name in locals() else globals().get(var_name)
                break

    # 3. 土地面積變數偵測
    if 'target' in locals() or 'target' in globals():
        t_obj = locals().get('target') if 'target' in locals() else globals().get('target')
        if isinstance(t_obj, dict) and 'land' in t_obj:
            display_land = t_obj['land']
            
    if display_land == "-":
        for var_name in ['land_area', 'target_land_area', 'land', 't_land']:
            if var_name in locals() or var_name in globals():
                display_land = locals().get(var_name) if var_name in locals() else globals().get(var_name)
                break

    # 4. 訪價上下限區間變數偵測
    val_low, val_high = None, None
    for var_name in ['low_bound', 'low_price', 'min_price', 'pred_low', 'low', 'min_val', 'unit_low', 'unit_high']:
        if var_name in locals() or var_name in globals():
            val_low = locals().get(var_name) if var_name in locals() else globals().get(var_name)
            break
    for var_name in ['high_bound', 'high_price', 'max_price', 'pred_high', 'high', 'max_val']:
        if var_name in locals() or var_name in globals():
            val_high = locals().get(var_name) if var_name in locals() else globals().get(var_name)
            break
            
    if val_low is None and isinstance(res, dict): val_low = res.get('low_bound')
    if val_high is None and isinstance(res, dict): val_high = res.get('high_bound')

    # 保險機制
    if val_low is None and res.get('b_type') != "透天厝":
        if 'unit_price_p' in top_10_df.columns and 'total_score' in top_10_df.columns:
            valid_mask = top_10_df['unit_price_p'].notna() & top_10_df['total_score'].notna()
            sub_df = top_10_df[valid_mask]
            if not sub_df.empty and sub_df['total_score'].sum() > 0:
                avg_unit_price = (sub_df['unit_price_p'] * sub_df['total_score']).sum() / sub_df['total_score'].sum()
                # 改用 settings 中的區間參數
                val_low = avg_unit_price * settings.PRICE_LOWER_BOUND
                val_high = avg_unit_price * settings.PRICE_UPPER_BOUND

    # 5. 預先組裝行情文字字串
    price_text = ""
    caption_text = ""
    if val_low is not None and val_high is not None:
        if res.get('b_type') == "透天厝":
            display_low = int(val_low / 10000) if val_low > 100000 else int(val_low)
            display_high = int(val_high / 10000) if val_high > 100000 else int(val_high)
            price_text = f"💰 合理行情：{display_low}萬 ～ {display_high}萬"
        else:
            try:
                build_float = float(display_build)
                apt_total_low = val_low * build_float
                apt_total_high = val_high * build_float
                price_text = f"💰 合理行情（不含車位）：{int(apt_total_low)}萬 ～ {int(apt_total_high)}萬"
                caption_text = f"💡 主建物單價區間：{val_low:.1f}萬 ～ {val_high:.1f}萬 / 坪"
            except:
                price_text = f"💰 合理行情（不含車位）：{val_low:.1f}萬 ～ {val_high:.1f}萬 / 坪"

    # 第一行排版：地址與區間
    target_address = res.get('addr', '未知地址').replace("花蓮縣", "")
    st.markdown(f"#### 📍 標的地址：{target_address} &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; {price_text}")
    if caption_text:
        st.caption(caption_text)

    # =========================================================================
    # 🚀 調整 2：建物型態、屋齡、土地面積、建物面積，使用 HTML 強制放大到 18px
    # =========================================================================
    st.markdown("<div style='margin-top: 10px;'></div>", unsafe_allow_html=True)
    
    # 格式化數值以利顯示
    final_age_str = f"{display_age} 年" if display_age != "未知" else "未知"
    final_land_str = f"{display_land} 坪" if res.get('b_type') == "透天厝" else "- 坪"
    final_build_str = f"{display_build} 坪"

    # 使用 Streamlit 欄位配合客製化 CSS 容器，確保字體放大到 20px
    detail_cols = st.columns(4)
    with detail_cols[0]:
        st.markdown(f"<div style='font-size: 20px; color: inherit;'>🏢 <b>建物型態</b>：{res.get('b_type', '未知')}</div>", unsafe_allow_html=True)
    with detail_cols[1]:
        st.markdown(f"<div style='font-size: 20px; color: inherit;'>📅 <b>屋齡</b>：{final_age_str}</div>", unsafe_allow_html=True)
    with detail_cols[2]:
        st.markdown(f"<div style='font-size: 20px; color: inherit;'>🌱 <b>土地面積</b>：{final_land_str}</div>", unsafe_allow_html=True)
    with detail_cols[3]:
        st.markdown(f"<div style='font-size: 20px; color: inherit;'>📐 <b>建物面積</b>：{final_build_str}</div>", unsafe_allow_html=True)

    # 🚀 調整 3：設定基本資料與下方近鄰成交參考紀錄表之間的精確間距
    st.markdown("<div style='margin-top: 1.2cm;'></div>", unsafe_allow_html=True)
            
    # =========================================================================
    # 【最下面】顯示近鄰成交參考紀錄表
    # =========================================================================
    top_10_df['標記'] = map_labels

    # 🌟 核心修正：將歷史實價登錄案例中的「花蓮縣」全面清洗移除
    if 'address' in top_10_df.columns:
        top_10_df['address'] = top_10_df['address'].astype(str).apply(lambda x: x.replace("花蓮縣", ""))

    # 統一處理距離轉換 (將 dist 公里轉換為公尺)
    if 'dist' in top_10_df.columns:
        top_10_df['距離(m)'] = (top_10_df['dist'] * 1000).astype(int)
    elif 'dist_m' in top_10_df.columns:
        top_10_df = top_10_df.rename(columns={'dist_m': '距離(m)'})
    else:
        top_10_df['距離(m)'] = "-"

    # 分流欄位清洗與嚴格排序
    if res['b_type'] == "透天厝":
        top_10_df['成交價(萬)'] = top_10_df['price'].apply(lambda x: f"{x/10000:,.0f}" if pd.notna(x) else "-")
        top_10_df['權重'] = top_10_df['total_score'] 
        
        top_10_df['market_premium'] = top_10_df['market_premium'].apply(lambda x: f"{x * 100:.0f}%" if pd.notna(x) else "-")
        
        top_10_df = top_10_df.rename(columns={
            'address': '門牌', 'total_build_area': '建物面積(坪)',
            'calc_age': '屋齡(年)', 'land_area': '土地面積(坪)',
            'market_premium': '溢價係數', 'deal_date': '成交日'
        })
          
        desired_columns = [
            '標記', '門牌', '距離(m)', '建物面積(坪)', '屋齡(年)', 
            '土地面積(坪)', '成交價(萬)', '溢價係數',  '成交日',
        ]
    else:  # 集合住宅
        top_10_df['實登價格(萬)'] = top_10_df['price'].apply(lambda x: f"{x/10000:,.0f}" if pd.notna(x) else "-")
        top_10_df['權重'] = top_10_df['total_score']  # 🌟 新增權重欄位

        if 'unit_price_p' in top_10_df.columns:
            top_10_df['主建物單價'] = top_10_df['unit_price_p'].round(1)
        else: top_10_df['主建物單價'] = "-"

        if 'berth_display' in top_10_df.columns: top_10_df = top_10_df.rename(columns={'berth_display': '車位'})
        elif 'parking_type' in top_10_df.columns: top_10_df['車位'] = top_10_df['parking_type'].fillna("無車位")
        else: top_10_df['車位'] = "-"

        top_10_df = top_10_df.rename(columns={
            'address': '門牌', 'total_build_area': '權狀面積(坪)',
            'calc_age': '屋齡(年)', 'deal_date': '成交日'
        })

        desired_columns = [
            '標記', '門牌', '距離(m)', '權狀面積(坪)', '屋齡(年)',
            '主建物單價', '車位', '實登價格(萬)',  '成交日', 
        ]
    # ==========================================
    # 欄位過濾，並剔除空案例
    # ==========================================
    final_table_df = top_10_df[desired_columns].copy()
    final_table_df = final_table_df[final_table_df['標記'] != ""]

    # 1. 透天厝的面積處理
    if '建物面積(坪)' in final_table_df.columns:
        final_table_df['建物面積(坪)'] = final_table_df['建物面積(坪)'].apply(lambda x: f"{float(x):.2f}" if pd.notna(x) and x != "-" else x)
    
    if '土地面積(坪)' in final_table_df.columns:
        final_table_df['土地面積(坪)'] = final_table_df['土地面積(坪)'].apply(lambda x: f"{float(x):.2f}" if pd.notna(x) and x != "-" else x)
        
    # 2. 集合住宅的面積處理 
    if '權狀面積(坪)' in final_table_df.columns:
        final_table_df['權狀面積(坪)'] = final_table_df['權狀面積(坪)'].apply(lambda x: f"{float(x):.2f}" if pd.notna(x) and x != "-" else x)
    # 3. 集合住宅的單價處理 
    if '主建物單價' in final_table_df.columns:
        final_table_df['主建物單價'] = final_table_df['主建物單價'].apply(lambda x: f"{float(x):.1f}" if pd.notna(x) and x != "-" else x)
        
    # =========================================================================
    # 呈現對齊地圖的完整資料表 (強制網頁與 PDF 列印框線完全顯現版)
    # =========================================================================
    st.markdown("<br class='no-print'>", unsafe_allow_html=True)
    st.write("### 📊 近鄰成交參考紀錄表 ")
    
    # 💉 注入進階 CSS：除了放大字體，更強行命令瀏覽器在列印時「必須繪製邊框」
    st.markdown("""
        <style>
        /* --- 網頁畫面與列印通用的表格外觀優化 --- */
        [data-testid="stTable"] table {
            width: 100% !important;
            border-collapse: collapse !important; /* 確保內外框線完美融合成單一線條 */
            border: 2px solid #333333 !important;  /* 表格最外圍的厚實大框線 */
            background-color: #FFFFFF !important;
        }
        
        /* 標題列 (th) 樣式：加深背景色，文字加粗 */
        [data-testid="stTable"] th {
            font-size: 16px !important; 
            font-weight: bold !important;
            text-align: center !important;
            background-color: #F8F9FA !important; /* 淺灰色標題背景 */
            border-bottom: 2px solid #333333 !important; /* 標題與內容之間的分隔粗線 */
            border-right: 1px solid #CCCCCC !important;  /* 標題彼此間的垂直線 */
            padding: 8px !important;
            color: #000000 !important;
        }
        
        /* 內容資料列 (td) 樣式：工整的細格線 */
        [data-testid="stTable"] td {
            font-size: 16px !important; 
            text-align: center !important;
            border: 1px solid #CCCCCC !important; /* 內容儲存格四周的輕量格線 */
            padding: 8px !important;
            color: #000000 !important;
        }

        /* --- 👑 核心：針對 A4 PDF 列印的強制框線防線 --- */
        @media print {
            [data-testid="stTable"] table, 
            [data-testid="stTable"] th, 
            [data-testid="stTable"] td {
                /* 強制瀏覽器在生成 PDF 時必須保留背景色與所有邊框細節 */
                -webkit-print-color-adjust: exact !important;
                print-color-adjust: exact !important;
                
                /* 再次加固列印邊框顏色與粗細，防止被瀏覽器淡化 */
                border: 1px solid #444444 !important; 
            }
            [data-testid="stTable"] table {
                border: 2px solid #000000 !important; /* 列印時最外圍維持黑框 */
            }
            [data-testid="stTable"] th {
                border-bottom: 2px solid #000000 !important; /* 列印時標題下方維持黑粗線 */
                background-color: #F0F0F0 !important;
            }
        }
        </style>
    """, unsafe_allow_html=True)

    # 將「標記」設為表格索引
    final_table_df = final_table_df.set_index('標記')

    # 使用 st.table() 渲染 (此時會完美套用上面的雙重框線 CSS)
    st.table(final_table_df)

    # =========================================================================
    # 在表格下方加入：透天厝訪價邏輯說明
    # =========================================================================
    if res.get('b_type') == "透天厝":
        st.markdown("""
            <style>
            /* 針對說明區塊的列印優化 */
            .algo-box {
                font-size: 13px; 
                color: #333333; 
                background-color: #F8F9FA; 
                padding: 12px 18px; 
                border-radius: 6px; 
                border: 1px solid #DDDDDD; 
                line-height: 1.6;
                margin-top: 15px;
            }
            @media print {
                .algo-box {
                    font-size: 11px !important; /* 列印時字體稍微縮小以節省空間 */
                    border: 1px solid #888888 !important;
                    background-color: #F8F9FA !important;
                    -webkit-print-color-adjust: exact !important;
                    print-color-adjust: exact !important;
                    page-break-inside: avoid !important; /* 避免說明區塊被切頁 */
                }
            }
            </style>
            
            <div class="algo-box">
                <b style="font-size: 15px; color: #000;">💡 系統估價邏輯說明：透天厝 (成本法折舊 ＋ 市場溢價調整 ＋ 加權中位數) 複合型演算法</b><br>
                <div style="display: flex; gap: 20px; margin-top: 8px;">
                    <div style="flex: 1;">
                        <b>第一階段：權重計分與案例篩選</b> (於周邊 1 公里內搜尋並為歷史案例打分)<br>
                        • <b>計分規則</b>：<br>
                        &nbsp;&nbsp;1. <b>交易年份</b>：1年內加6分、2年內加2分、3年內加1分、逾3年扣3分。<br>
                        &nbsp;&nbsp;3. <b>距離遠近</b>：250m內加4分、500m內加3分、逾500m加1分。<br>
                        • <b>篩選門檻</b>：權重未達 之案例直接剔除不列入參考，確保合理性。
                    </div>
                    <div class="algo-column" style="flex: 1.1;">
                        <b>第二階段：估價引擎運算</b><br>
                        • <b>透天厝模式 (極端值排除法)</b>：<br>
                        &nbsp;&nbsp;1. 計算案例溢價係數 <span style="font-size: 11px;">(成交價-總成本)/總成本</span>。<br>
                        &nbsp;&nbsp;2. <b>剔除負數</b>：溢價係數為負者不具參考價值，直接排除。<br>
                        &nbsp;&nbsp;3. <b>次高次低平均</b>：排除最高與最低值，取次高與次低係數平均。<br>
                        &nbsp;&nbsp;4. 預測中心價 = 標的總成本 × (1 + 平均認定溢價係數)。<br>
                        • <b>合理區間</b>：將預測中心價乘上 ±6%，得出最終合理行情區間。
                    </div>
                </div>
            </div>
        """, unsafe_allow_html=True)
    # =========================================================================
    # 🌟 12. 集合住宅訪價邏輯說明（內嵌車位建議行情表）
    # =========================================================================
    elif res.get('b_type') != "透天厝":
        st.markdown("""
            <style>
            /* 針對說明區塊的列印優化 */
            .algo-box {
                font-size: 13px; 
                color: #333333; 
                background-color: #F8F9FA; 
                padding: 12px 18px; 
                border-radius: 6px; 
                border: 1px solid #DDDDDD; 
                line-height: 1.6;
                margin-top: 15px;
            }
            .algo-column {
                flex: 1;
            }
            /* 內嵌車位表格樣式 */
            .parking-mini-table {
                width: 100%;
                border-collapse: collapse;
                margin-top: 6px;
                font-size: 12px;
            }
            .parking-mini-table th, .parking-mini-table td {
                border: 1px solid #DDDDDD !important;
                padding: 4px 6px !important;
                text-align: center !important;
                color: #000000 !important;
            }
            .parking-mini-table th {
                background-color: #E9ECEF !important;
                font-weight: bold;
            }
            @media print {
                .algo-box {
                    font-size: 11px !important; 
                    border: 1px solid #888888 !important;
                    background-color: #F8F9FA !important;
                    -webkit-print-color-adjust: exact !important;
                    print-color-adjust: exact !important;
                    page-break-inside: avoid !important; 
                }
                .parking-mini-table th, .parking-mini-table td {
                    border: 1px solid #666666 !important;
                }
                .parking-mini-table th {
                    background-color: #EFEFEF !important;
                }
            }
            </style>
            
            <div class="algo-box">
                <b style="font-size: 15px; color: #000;">💡 系統估價邏輯說明：集合住宅 (實質單價拆算 ＋ 相似度權重加權)</b><br>
                <div style="display: flex; gap: 20px; margin-top: 8px;">
                    <div class="algo-column" style="flex: 1.1;">
                        <b>第一階段：權重計分與案例篩選</b> (周邊 1 公里內)<br>
                        • <b>計分規則</b>：<br>
                        &nbsp;&nbsp;1. <b>交易年份</b>：1年內加7分、2年內加5分、3年內加3分、逾3年扣3分。<br>
                        &nbsp;&nbsp;3. <b>距離遠近</b>：100m內加3分、500m內加2分、逾500m加1分。<br>
                        • <b>篩選門檻</b>：權重未達 之案例直接剔除不列入參考，確保合理性。
                    </div>
                    <div class="algo-column" style="flex: 1.1;">
                        <b>第二階段：估價引擎運算</b><br>
                        • <b>車位拆算</b>：剔除車位價格與面積，還原純房屋的「實質單價 (萬/坪)」。<br>
                        • <b>加權平均單價</b>：將 10 筆案例的實質單價，依照第一階段算出的「總分」進行加權平均。<br>
                        &nbsp;&nbsp;<span style="color: #555; font-size: 11px;">(公式：Σ(各案例實質單價 × 各案例總分) / Σ(所有案例總分) )</span><br>
                        • <b>預測中心總價</b>：加權平均單價 × 目標物件建物面積 (不含車位)。<br>
                        • <b>合理區間</b>：將預測中心價乘上 ±6%，得出最終合理行情區間。
                    </div>
                    <div class="algo-column" style="flex: 0.8;">
                        <b>🚗 車位建議行情參考</b><br>
                        <table class="parking-mini-table">
                            <thead>
                                <tr><th>類型</th><th>權利</th><th>建議行情</th></tr>
                            </thead>
                            <tbody>
                                <tr><td>平面</td><td>所有權</td><td>130～180萬</td></tr>
                                <tr><td>平面</td><td>使用權</td><td>90～140萬</td></tr>
                                <tr><td>機械</td><td>所有權</td><td>60～100萬</td></tr>
                                <tr><td>機械</td><td>使用權</td><td>30～70萬</td></tr>
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>
        """, unsafe_allow_html=True)
