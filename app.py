import re
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
from modules.utils import (
    categorize_parking_type,
    infer_parking_price,
    normalize_numeric_columns,
    recalc_unit_price,
    fix_building_age,
)
from modules import settings

st.set_page_config(page_title="宜花東房地訪價系統 (APRp)", layout="wide")

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
@st.cache_resource
def get_geocoder():
    return ArcGIS()

@st.cache_data(ttl=settings.CACHE_TTL_SEC)
def get_geocode(address):
    geocoder = get_geocoder()
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

#當使用者修改任何輸入條件時，清除下方的舊估價結果
def clear_results():
    st.session_state.valuation_results = None
# ==========================================
# 頂部區塊：網頁標題與參數輸入
# ==========================================
st.markdown("<h1 class='no-print'>🏠 宜花東房地訪價系統（APRp）</h1>", unsafe_allow_html=True)
st.markdown("<h5 class='no-print'>資料庫112年到115年第一季</h5>", unsafe_allow_html=True)
st.markdown("<p class='no-print'><b>訪價模式</b><br>透天厝 = 成本法折舊＋市場溢價調整、集合住宅 = 實質單價拆算 ＋ 相似度權重加權</p>", unsafe_allow_html=True)
st.markdown("<hr class='no-print'>", unsafe_allow_html=True)

with st.container():
    st.markdown("<h2 class='no-print'>📌 目標物件參數輸入</h2>", unsafe_allow_html=True)
   
    col_a, col_b = st.columns([2, 1])
    with col_a:
        loc_col1, loc_col2 = st.columns([1, 3])
        with loc_col1:
            city = st.selectbox("縣市", ["宜蘭縣", "花蓮縣", "台東縣"], index=1)
        with loc_col2:
            street_addr = st.text_input("輸入目標詳細地址 (鄉鎮市區+道路門牌)", on_change=clear_results)
        
    # 後台運算時自動拼接
    addr = city + street_addr
    with col_b:
        b_type = st.selectbox("建物型態", [
            "透天厝", 
            "住宅大樓(11樓有電梯)", 
            "華廈(10樓有電梯)", 
            "公寓(5樓無電梯)"
        ], on_change=clear_results)
    
    if b_type == "透天厝":
        c1, c2, c3 = st.columns(3)
        with c1:
            land_area = st.number_input("土地面積 (坪)", min_value=0.0, value=30.0, step=0.1, on_change=clear_results)
        with c2:
            land_price = st.number_input("土地行情 (萬/坪)", min_value=0.0, value=14.0, step=0.1, on_change=clear_results)
        with c3:
            build_area = st.number_input("建物總面積 (坪)", min_value=0.0, value=60.0, step=0.1, on_change=clear_results)
        
        c4, material_col = st.columns([1, 1])
        with material_col:
            material_val = st.selectbox("主要建材", ["鋼筋混凝土", "鋼筋混凝土加強磚造"])
        with c4:
            age = st.number_input("屋齡 (年)", min_value=0, value=20, on_change=clear_results)
        is_first_floor = True 
    else:
        c1, c2, c3 = st.columns(3)
        with c1:
            build_area = st.number_input("權狀不含車位面積 (坪)", min_value=0.0, value=30.0, step=0.1, on_change=clear_results)
        with c2:
            age = st.number_input("屋齡 (年)", min_value=0, value=0, on_change=clear_results)
        
        is_first_floor = st.checkbox("包含一樓成交紀錄", value=False)

    run_btn = st.button("🚀 開始評估系統", use_container_width=True, type="primary")

# ==========================================
# 運算邏輯區
# ==========================================
if run_btn:
    if not addr.strip() or len(addr) < 5:
        st.warning("⚠️ 請輸入完整的詳細地址再進行試算。")
    else:
        with st.spinner("正在定位地址與搜尋鄰近案例..."):
            loc = get_geocode(addr)
    
    if loc:
        conn = get_db_connection()
        if conn is None:
            st.error("找不到資料庫 (data/YHT.db)")
            st.stop()
            
        # 1. 抓取候選池
        raw_pool = get_neighbor_data(conn, loc.latitude, loc.longitude, b_type, addr)
        raw_pool = fix_building_age(raw_pool)
        
        if not raw_pool.empty:
            # 集合住宅避開地下室紀錄
            if b_type != "透天厝":
                if 'floor_level' in raw_pool.columns:
                    raw_pool = raw_pool[~raw_pool['floor_level'].str.contains('地下', na=False)]
                if 'address' in raw_pool.columns:
                    raw_pool = raw_pool[~raw_pool['address'].str.endswith('地下室', na=False)]
            
            # 2. 權重計分
            scored_pool = score_neighbors(raw_pool, age, is_first_floor, b_type)
            
            # --- 絕對分數門檻 ---
            final_pool = scored_pool[scored_pool['total_score'] >= settings.MIN_TOTAL_SCORE].copy()
            
            # 防呆機制，若無任何案例達標，立即中止運算並報錯
            if final_pool.empty:
                st.session_state.valuation_results = "empty"
                st.rerun()

            # =====================================================================
            # 數值轉換與車位價格智能拆算
            # =====================================================================
            numeric_cols = ['land_area', 'total_build_area', 'price', 'parking_price', 'parking_area']
            final_pool = normalize_numeric_columns(final_pool, numeric_cols)

            if b_type != "透天厝":
                from modules.utils import recalc_unit_price
                final_pool = recalc_unit_price(final_pool)

            final_pool['total_build_area'] = (final_pool['total_build_area'] * 0.3025).round(2)
            
            if b_type == "透天厝":
                final_pool['land_area'] = (final_pool['land_area'] * 0.3025).round(2)

            # =====================================================================
            # 選案與估價引擎運算 
            # =====================================================================
            if b_type == "透天厝":
                from modules.utils import filter_detached_top10
                top_10 = filter_detached_top10(final_pool)
                
                valuation_msg = f"嚴格權重與距離篩選：共找到 {len(top_10)} 筆透天參考紀錄"
                
                target_data = {'land': land_area, 'build': build_area, 'age': age, 'material': material_val}
                # 傳入篩選好的 top_10 給引擎運算
                low, high, top_10 = RealEstateValuator.run_detached_valuation(target_data, top_10, land_price)
                
                eval_text = f"{int(low):,} 萬 - {int(high):,} 萬"
                eval_mode = "透天厝成本法 (5最新+5最近加權)"

            else:
                # 集合住宅選案：依照分數由高到低排序，最多取前 10 筆
                top_10 = final_pool.sort_values(['total_score', 'deal_date'], ascending=[False, False]).head(10).copy()
                valuation_msg = f"嚴格權重篩選：共找到 {len(top_10)} 筆權重達標 (>=10分) 之相似紀錄"
                
                # 集合住宅建立目標資料字典
                target_data = {'build': build_area, 'age': age}

                low_up, high_up = RealEstateValuator.run_apartment_valuation(top_10)
                eval_text = f"{low_up:.1f} 萬/坪 - {high_up:.1f} 萬/坪"
                eval_mode = f"依面積推算總價：{int(low_up * build_area):,}萬 ~ {int(high_up * build_area):,}萬(不含車位）"
                
            st.session_state.valuation_results = {
                'addr': addr, 'lat': loc.latitude, 'lon': loc.longitude,
                'top_10': top_10, 'eval_text': eval_text, 'eval_mode': eval_mode,
                'b_type': b_type, 'build_area': build_area,
                'valuation_msg': valuation_msg,
                'land_price': land_price if b_type == "透天厝" else None,
                'material_val': material_val if b_type == "透天厝" else None,
                
                'low_bound': low if b_type == "透天厝" else low_up,
                'high_bound': high if b_type == "透天厝" else high_up,
                
                'target_data': target_data, 
                'excluded_labels': []
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
    # 1. A4 列印 (上邊 2cm，其餘三邊 1cm)
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

    # ================= 強制將列印 CSS 注入地圖內部 =================
    m.get_root().header.add_child(folium.Element("""
        <style>
        /* 網頁上：為標記加上一點陰影增加立體感 */
        .beautify-marker {
            box-shadow: 1px 2px 5px rgba(0,0,0,0.5) !important;
        }
        @media print {
            /* 1. 強制瀏覽器印出標記的「背景顏色」(解決透明問題的核心) */
            * {
                -webkit-print-color-adjust: exact !important;
                print-color-adjust: exact !important;
            }
            
            /* 2.修復標記消失：絕對不能使用 transform，改用 zoom 進行安全放大，並強制顯示 */
            .leaflet-marker-icon {
                display: block !important;
                opacity: 1 !important;
                zoom: 1.2 !important; /* 安全地放大 1.2 倍，不會破壞原本的經緯度定位 */
            }
            
            /* 3. 將地圖底圖對比度調高，讓標記更清晰 */
            .leaflet-tile { 
                filter: contrast(1.2) brightness(0.9) !important; 
            }
        }
        </style>
    """))

    # 標記紅色「目標物件」- 改用純 CSS 的 BeautifyIcon，確保列印絕對不掉色
    target_icon = BeautifyIcon(
        icon_shape='circle',         # 改用圓形，與參考物件的設計語彙統一
        number='★',                  # 使用純文字的星星符號，保證列印時不用讀取字型也不會消失
        text_color='white',
        background_color='#E53935',  # 醒目的鮮紅色
        border_color='#B71C1C',      # 深紅色厚邊框，增加列印對比度
        inner_icon_style='font-size: 20px; margin-top: -2px;' # 將星星放大並微調置中
    )

    folium.Marker(
        [res['lat'], res['lon']],
        popup=f"<b>⭐ 目標物件</b><br>{res['addr']}",
        tooltip="目標物件",
        icon=target_icon
    ).add_to(m)

    top_10_df = res['top_10'].copy()
    map_labels = []

    # 建立座標收集清單，首項先放入「目標物件」座標
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

            # ================= 🌟 優化參考標記設計 =================
            b_icon = BeautifyIcon(
                icon_shape='circle',
                number=str(label_idx),
                text_color='white',
                background_color='#1f77b4',
                border_color='#073863',  # 將邊框顏色調深到深藍色，增加列印對比
                inner_icon_style='font-weight: bold; font-size: 16px; margin-top: 1px;' # 字體從 13px 放大到 16px
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

    # 利用 fit_bounds 功能，全自動調整地圖的中心點與縮放大小，確保看得到所有標記
    if len(all_coordinates) > 1:
        m.fit_bounds(all_coordinates)

    # 渲染地圖至網頁上 (地圖大小)
    st_folium(m, width=1100, height=600, returned_objects=[])

    # =========================================================================
    # 【地圖下面】顯示目標物件的資料與訪價區間 
    # =========================================================================
    
    # 1：設定地圖與基本資料之間的間距
    st.markdown("<div style='margin-top: 0.8cm;'></div>", unsafe_allow_html=True)
    st.write("### 📋 目標物件基本資料與行情估算")
    
    top_10_df = res['top_10'].copy()
    # === 1. 統一安全解析 target_data (放在讀取變數的最前面) ===
    t_data = res.get('target_data')
    if isinstance(t_data, str):
        import json
        try:
            t_data = json.loads(t_data)
        except:
            t_data = {}
    elif not isinstance(t_data, dict):
        t_data = {}
    
    # === 2. 接下來取值，通通把原本的 res.get('target_data', {}) 替換成 t_data ===
    display_age = t_data.get('age', res.get('build_age', res.get('age', '未知')))
    display_build = res.get('build_area', t_data.get('build', '-'))
    display_land = res.get('land_area', t_data.get('land', '-'))

    val_low = res.get('low_bound')
    val_high = res.get('high_bound')

    if val_low is None and res.get('b_type') != "透天厝":
        if 'unit_price_p' in top_10_df.columns and 'total_score' in top_10_df.columns:
            valid_mask = top_10_df['unit_price_p'].notna() & top_10_df['total_score'].notna()
            sub_df = top_10_df[valid_mask]
            if not sub_df.empty and sub_df['total_score'].sum() > 0:
                avg_unit_price = (sub_df['unit_price_p'] * sub_df['total_score']).sum() / sub_df['total_score'].sum()
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
                caption_text = f"💡 建坪單價區間：{val_low:.1f}萬 ～ {val_high:.1f}萬 / 坪"
            except:
                price_text = f"💰 合理行情（不含車位）：{val_low:.1f}萬 ～ {val_high:.1f}萬 / 坪"

    # 第一行排版：地址與區間
    target_address = re.sub(r"^(宜蘭縣|花蓮縣|台東縣)", "", res.get('addr', '未知地址'))
    st.markdown(f"#### 📍 標的地址：{target_address} &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; {price_text}")
    if caption_text:
        st.caption(caption_text)

    # =========================================================================
    # 2：建物型態、屋齡、土地面積、建物面積，使用 HTML 強制放大到 18px
    # =========================================================================
    st.markdown("<div style='margin-top: 10px;'></div>", unsafe_allow_html=True)
    
    # 格式化數值以利顯示
    final_age_str = f"{display_age} 年" if display_age != "未知" else "未知"
    final_land_str = f"{display_land} 坪" if res.get('b_type') == "透天厝" else "- 坪"
    final_build_str = f"{display_build} 坪"

    # 使用 Streamlit 欄位配合客製化 CSS 容器，確保字體放大到 20px
    # ================= 第一排基本資料 =================
    detail_cols = st.columns(4)
    with detail_cols[0]:
        st.markdown(f"<div style='font-size: 20px; color: inherit;'>🏢 <b>建物型態</b>：{res.get('b_type', '未知')}</div>", unsafe_allow_html=True)
    with detail_cols[1]:
        st.markdown(f"<div style='font-size: 20px; color: inherit;'>📅 <b>屋齡</b>：{final_age_str}</div>", unsafe_allow_html=True)
    with detail_cols[2]:
        st.markdown(f"<div style='font-size: 20px; color: inherit;'>🌱 <b>土地面積</b>：{final_land_str}</div>", unsafe_allow_html=True)
    with detail_cols[3]:
        st.markdown(f"<div style='font-size: 20px; color: inherit;'>📐 <b>建物面積</b>：{final_build_str}</div>", unsafe_allow_html=True)

    # ================= 第二排追加資料 (僅透天厝) =================
    if res.get('b_type') == "透天厝":
        display_land_price = res.get('land_price', 14.0)
        display_material = res.get('material_val', '鋼筋混凝土')
        
        detail_cols_row2 = st.columns(4)
        # 前面兩個欄位 detail_cols_row2[0] 與 [1] 不放東西直接留白
        
        with detail_cols_row2[2]:
            st.markdown(f"<div style='font-size: 20px; color: inherit; padding-top: 8px;'>💲 <b>土地行情</b>：{display_land_price} 萬/坪</div>", unsafe_allow_html=True)
        with detail_cols_row2[3]:
            st.markdown(f"<div style='font-size: 20px; color: inherit; padding-top: 8px;'>🧱 <b>建材</b>：{display_material}</div>", unsafe_allow_html=True)


    # 設定基本資料與下方鄰近成交參考紀錄表之間的間距
    st.markdown("<div style='margin-top: 0.5cm;'></div>", unsafe_allow_html=True)
            
    # =========================================================================
    # 【最下面】顯示鄰近成交參考紀錄表
    # =========================================================================
    top_10_df['標記'] = map_labels

    # 將歷史實價登錄案例全面清洗移除
    if 'address' in top_10_df.columns:
        top_10_df['address'] = top_10_df['address'].astype(str).apply(lambda x: re.sub(r"^(宜蘭縣|花蓮縣|台東縣)", "", x))

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
        
        # 追加防呆：確保引擎有正確傳回 market_premium 才運算
        if 'market_premium' in top_10_df.columns:
            top_10_df['溢價係數'] = top_10_df['market_premium'].apply(lambda x: f"{x * 100:.0f}%" if pd.notna(x) else "-")
        else:
            top_10_df['溢價係數'] = "-"
            
        top_10_df = top_10_df.rename(columns={
            'address': '門牌', 'total_build_area': '建物面積(坪)',
            'calc_age': '屋齡(年)', 'land_area': '土地面積(坪)',
            'deal_date': '成交日'
        })
          
        desired_columns = [
            '標記', '門牌', '距離(m)', '建物面積(坪)', '屋齡(年)', 
            '土地面積(坪)', '成交價(萬)', '溢價係數', '權重', '成交日',
        ]  
        
    else:  # 集合住宅
        top_10_df['實登價格(萬)'] = top_10_df['price'].apply(lambda x: f"{x/10000:,.0f}" if pd.notna(x) else "-")
        top_10_df['權重'] = top_10_df['total_score']  

        if 'unit_price_p' in top_10_df.columns:
            top_10_df['建坪單價'] = top_10_df['unit_price_p'].round(1)
        else: top_10_df['建坪單價'] = "-"

        if '車位' not in top_10_df.columns:
            top_10_df['車位'] = "無車位"

        top_10_df = top_10_df.rename(columns={
            'address': '門牌', 'total_build_area': '權狀面積(坪)',
            'calc_age': '屋齡(年)', 'deal_date': '成交日'
        })

        desired_columns = [
            '標記', '門牌', '距離(m)', '權狀面積(坪)', '屋齡(年)',
            '建坪單價', '車位', '實登價格(萬)', '權重', '成交日', 
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
    if '建坪單價' in final_table_df.columns:
        final_table_df['建坪單價'] = final_table_df['建坪單價'].apply(lambda x: f"{float(x):.1f}" if pd.notna(x) and x != "-" else x)
        
    # =========================================================================
    # 4. 數值轉換為人類閱讀文字 (解決新成屋與不詳顯示)
    # =========================================================================
    if '屋齡(年)' in final_table_df.columns:
        def _format_ui_age(x):
            if pd.isna(x) or str(x).strip().lower() in ["nan", "none", "-", "nat"]:
                return "不詳"
            try:
                val = float(x)
                if val == 0:
                    return "新成屋(0年)"
                return f"{int(val)}"
            except:
                return "不詳"
        final_table_df['屋齡(年)'] = final_table_df['屋齡(年)'].apply(_format_ui_age)  
        
    # =========================================================================
    # 呈現對齊地圖的完整資料表 (強制網頁與 PDF 列印框線完全顯現版)
    # =========================================================================
    st.markdown("<br class='no-print'>", unsafe_allow_html=True)
    st.write("### 📊 鄰近成交參考紀錄表 ")
    
    # 注入進階 CSS：除了放大字體，更強行命令瀏覽器在列印時「必須繪製邊框」
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

        /* --- 針對 A4 PDF 列印的強制框線防線 --- */
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

    # ================= 動態刪除（行情重算）與 列印保留雙線 核心邏輯 =================
    # 1. 確保源頭的 res 包含完整的標記名稱，以供重算與對照
    res['top_10']['標記'] = map_labels
    
    # 2. 建立包含「刪除」勾選狀態，新增的欄位會自動安穩地排在「最右邊」
    final_table_df['刪除'] = final_table_df['標記'].isin(res.get('excluded_labels', []))
    
    # 將「標記」設為表格索引，Streamlit 會自動把它鎖在最左邊
    final_table_df = final_table_df.set_index('標記')

    # 3. 雙軌顯示 CSS：網頁互動表放大與置中；列印 PDF 切換純 HTML
    st.markdown("""
        <style>
        /* 網頁版：放大字體並置中 */
        .stDataEditor [data-testid="stDataEditor"] {
            font-size: 18px !important;
        }
        .stDataEditor td, .stDataEditor th {
            text-align: center !important;
            vertical-align: middle !important;
        }

        @media screen { 
            /* 螢幕上隱藏我們等下要產生的純 HTML 列印專用表 */
            .print-only-table-wrapper { display: none !important; }
        }
        @media print { 
            /* 列印時隱藏互動式 Dataframe，顯示純 HTML 表 */
            [data-testid="stDataFrame"] { display: none !important; } 
            .print-only-table-wrapper { display: block !important; } 
        }
        
        /* 為繞過 Streamlit 過濾的純 HTML 表格重新套用工整的框線樣式 */
        table.print-only-table {
            width: 100% !important;
            border-collapse: collapse !important;
            border: 2px solid #000000 !important;
            background-color: #FFFFFF !important;
        }
        table.print-only-table th {
            font-size: 16px !important; 
            font-weight: bold !important;
            text-align: center !important;
            background-color: #F0F0F0 !important;
            border-bottom: 2px solid #000000 !important;
            border-right: 1px solid #444444 !important;
            padding: 8px !important;
            color: #000000 !important;
            -webkit-print-color-adjust: exact !important;
        }
        table.print-only-table td {
            font-size: 16px !important; 
            text-align: center !important;
            border: 1px solid #444444 !important;
            padding: 8px !important;
            color: #000000 !important;
        }
        </style>
    """, unsafe_allow_html=True)

    # 4. 【網頁操作版】：互動式 Data Editor 
    edited_df = st.data_editor(
        final_table_df,
        column_config={
            # 刪除欄位在最右邊，使用 small 即可完美呈現小勾選框
            "刪除": st.column_config.CheckboxColumn("刪除", help="勾選後排除此筆，系統將自動重算", width="small")
        },
        disabled=[col for col in final_table_df.columns if col != '刪除'],
        use_container_width=True
    )

    # 透過 Index 抓取
    if '刪除' in edited_df.columns:
        deleted_indices = edited_df[edited_df['刪除'] == True].index.tolist()
    else:
        deleted_indices = []

    # 5. 【A4 列印版 - 繞過過濾法】：將被刪除的資料轉換為純 HTML，設定特定欄位樣式與粗體邏輯
    print_df = final_table_df.drop(columns=['刪除'], errors='ignore').astype(str).copy()
    
    # 將「標記」從索引還原為一般的第一個欄位
    print_df = print_df.reset_index()
    
    # 針對每一格資料進行判斷與樣式套用
    for row_idx in print_df.index:
        label = print_df.at[row_idx, '標記']
        is_deleted = label in deleted_indices
        
        for col in print_df.columns:
            val = print_df.at[row_idx, col]
            
            if is_deleted:
                # 若該紀錄被勾選刪除
                if col == '標記':
                    # 只有「標記」欄位加上紅色單線刪除線，不用粗體
                    print_df.at[row_idx, col] = f"<span style='text-decoration: line-through; text-decoration-style: solid; text-decoration-color: #ff4b4b; text-decoration-thickness: 3px; color: #a0a0a0; font-style: italic;'>{val}</span>"
                else:
                    # 其他資訊(門牌之後)「不加刪除線、不加粗」，僅用灰色斜體標示已失效
                    print_df.at[row_idx, col] = f"<span style='color: #a0a0a0; font-style: italic;'>{val}</span>"
            else:
                # 若該紀錄「沒有」被刪除，則整列字體顯示為粗體
                print_df.at[row_idx, col] = f"<span style='font-weight: bold; color: #000000;'>{val}</span>"

    # 將 DataFrame 轉為 HTML，加上 index=False 就不會產生多餘的空白列與雙層表頭！
    html_table = print_df.to_html(escape=False, classes="print-only-table", index=False)
    st.markdown(f'<div class="print-only-table-wrapper">{html_table}</div>', unsafe_allow_html=True)

    # =========================================================================
    # 【後台重算機制】：監測勾選變動，即時重新推算合理行情區間
    # =========================================================================
    if deleted_indices != res.get('excluded_labels', []):
        res['excluded_labels'] = deleted_indices
        
        # 篩選出「未被勾選刪除」的有效近鄰案例進行重新估價
        active_pool = res['top_10'][~res['top_10']['標記'].isin(deleted_indices)].copy()
        
        if len(active_pool) > 0:
            if res.get('b_type') == "透天厝" and res.get('target_data'):
                new_low, new_high, _ = RealEstateValuator.run_detached_valuation(
                    res['target_data'], active_pool, res.get('land_price', 14.0)
                )
            else:
                new_low, new_high = RealEstateValuator.run_apartment_valuation(active_pool)
            
            # 將重新計算過後的行情數據覆寫回 session_state
            res['low_bound'] = new_low
            res['high_bound'] = new_high
        else:
            # 防呆機制：若 10 筆參考紀錄全被勾選刪除，則行情歸零
            res['low_bound'], res['high_bound'] = 0, 0 
        
        st.session_state.valuation_results = res
        st.rerun()  # 強制重新渲染，使畫面上方的估價結果即時同步！

    # =========================================================================
    # 在表格下方加入：透天厝訪價邏輯說明 (網頁顯示，列印時隱藏)
    # =========================================================================
    if res.get('b_type') == "透天厝":
        st.markdown("""
            <style>
            /* 針對說明區塊的網頁顯示優化 */
            .algo-box {
                font-size: 13px; 
                color: #333333; 
                background-color: #F8F9FA; 
                padding: 15px 20px; 
                border-radius: 6px; 
                border: 1px solid #DDDDDD; 
                line-height: 1.6;
                margin-top: 15px;
            }
            /* 🚀 核心修正：列印 PDF 時，徹底隱藏這個區塊 */
            @media print {
                .algo-box {
                    display: none !important;
                }
            }
            </style>
            
            <div class="algo-box">
                <b style="font-size: 18px; color: #000;">💡 系統估價邏輯說明：透天厝 (成本法折舊 ＋ 市場溢價調整 ＋ 人工輔助刪除) 複合型演算法</b><br>
                <div style="display: flex; gap: 20px; margin-top: 10px;">
                    <div style="flex: 1;">
                        <b>第一階段：權重計分與案例篩選</b> (於周邊 1 公里內搜尋並為歷史案例打分)<br>
                        • <b>計分規則</b>：<br>
                        &nbsp;&nbsp;1. <b>交易年份</b>：1年內加6分、2年內加2分、3年內加1分、逾3年扣3分。<br>
                        &nbsp;&nbsp;2. <b>距離遠近</b>：50m內加4分、250m內加3分、500m內加2分、逾500m加1分。<br>
                        • <b>選案策略</b>：同門牌去重複，保留 7 筆最新 + 3 筆距離最近」的紀錄。<br>
                        • <b>實登備註</b>：'親友', '急買', '急賣', '員工', '關係人', '借名', '畸零地', '保留地'。<br>
                        • <b>上述特殊交易</b>：資料庫轉檔過程，已篩選剃除。<br>
                    </div>
                    <div class="algo-column" style="flex: 1.1;">
                        <b>第二階段：估價引擎運算</b><br>
                        • <b>透天厝模式 (極端值排除法)</b>：<br>
                        &nbsp;&nbsp;1. 計算案例溢價係數 <span style="font-size: 11px;">(成交價-總成本)/總成本</span>。<br>
                        &nbsp;&nbsp;2. <b>剔除負數</b>：溢價係數為負者不具參考價值，直接排除。<br>
                        &nbsp;&nbsp;3. <b>次高次低平均</b>：排除最高與最低值，取次高與次低係數平均。<br>
                        &nbsp;&nbsp;4. 預測中心價 = 標的總成本 × (1 + 平均認定溢價係數)。<br>
                        • <b>合理區間</b>：中心價 ±6% 認定合理行情區間。另需人工判斷刪除偏離紀錄。
                    </div>
                </div>
            </div>
        """, unsafe_allow_html=True)
    # =========================================================================
    # 12. 集合住宅訪價邏輯說明（內嵌車位建議行情表，列印時隱藏）
    # =========================================================================
    elif res.get('b_type') != "透天厝":
        st.markdown("""
            <style>
            /* 針對說明區塊的網頁顯示優化 */
            .algo-box {
                font-size: 16px; 
                color: #333333; 
                background-color: #F8F9FA; 
                padding: 15px 20px; 
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
                margin-top: 8px;
                font-size: 14px;
            }
            .parking-mini-table th, .parking-mini-table td {
                border: 1px solid #DDDDDD !important;
                padding: 6px 8px !important;
                text-align: center !important;
                color: #000000 !important;
            }
            .parking-mini-table th {
                background-color: #E9ECEF !important;
                font-weight: bold;
            }
            
            /* 🚀 核心修正：列印 PDF 時，徹底隱藏這個區塊 */
            @media print {
                .algo-box {
                    display: none !important; 
                }
            }
            </style>
            
            <div class="algo-box">
                <b style="font-size: 18px; color: #000;">💡 系統估價邏輯說明：集合住宅 (實質單價拆算 ＋ 相似度權重加權)</b><br>
                <div style="display: flex; gap: 20px; margin-top: 10px;">
                    <div class="algo-column" style="flex: 1.1;">
                        <b>第一階段：權重計分與案例篩選</b> (周邊 1 公里內)<br>
                        • <b>計分規則</b>：<br>
                        &nbsp;&nbsp;1. <b>交易年份</b>：1年內加6分、2年內加2分、3年內加1分、逾3年扣3分。<br>
                        &nbsp;&nbsp;2. <b>距離遠近</b>：50m內加4分、250m內加3分、500m內加2分、逾500m加1分。<br>
                        &nbsp;&nbsp;3. <b>屋齡差距</b>：同屋齡加5分、屋齡差 10 年內加3分、屋齡差 11年以上扣3分。<br>
                        • <b>實登備註</b>：'親友', '急買', '急賣', '員工', '關係人', '借名', '畸零地', '保留地'。<br>
                        • <b>上述特殊交易</b>：資料庫轉檔過程，已篩選剃除。<br>
                    </div>
                    <div class="algo-column" style="flex: 1.1;">
                        <b>第二階段：估價引擎運算</b><br>
                        • <b>車位拆算</b>：剔除車位價格與面積，還原純房屋的「實質單價 (萬/坪)」。<br>
                        • <b>車位價格標示為 0</b>：若實登車位價為 0，系統將自動扣除估算：「平面所有權」扣155萬、「平面使用權」扣115萬、「機械所有權」扣80萬、「機械使用權」扣50萬。
                        • <b>加權平均單價</b>：將 10 筆案例的實質單價，依照第一階段算出的「總分」進行加權平均。<br>
                        &nbsp;&nbsp;<span style="color: #555; font-size: 14px;">(公式：Σ(各案例實質單價 × 各案例總分) / Σ(所有案例總分) )</span><br>
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
