import streamlit as st
import pandas as pd
import os
import datetime
import re
import uuid
import gspread  # 新增：用於連接 Google Sheets
from google.oauth2.service_account import Credentials  # 新增：用於認證

# --- 設定頁面 ---
st.set_page_config(page_title="旅遊推薦系統", layout="centered")

# --- 定義標準 22 縣市清單 ---
VALID_CITIES = [
    "台北市", "新北市", "桃園市", "台中市", "台南市", "高雄市",
    "基隆市","宜蘭縣", "新竹縣", "苗栗縣", "彰化縣", "南投縣", "雲林縣", "嘉義縣", "屏東縣",
    "花蓮縣", "台東縣"
]

# --- 1. 資料讀取與清洗 ---
@st.cache_data
def load_data():
    try:
        csv_file = 'TAIWAN_FILTERED.csv'
        if not os.path.exists(csv_file):
            return None

        # 讀取資料 (使用 utf-8-sig 處理 BOM)
        df = pd.read_csv(csv_file, encoding='utf-8-sig')

        # 1. 清理欄位名稱 (移除前後空格)
        df.columns = [c.strip() for c in df.columns]

        # 2. 縣市欄位處理：如果 CSV 是 '城市' 則改名為 '縣市'
        if '城市' in df.columns:
            df.rename(columns={'城市': '縣市'}, inplace=True)
        elif '縣市' not in df.columns and '地址' in df.columns:
            def get_city_from_addr(addr):
                if pd.isna(addr): return None
                txt = str(addr).replace('臺', '台')
                for c in VALID_CITIES:
                    if c in txt: return c
                return None
            df['縣市'] = df['地址'].apply(get_city_from_addr)

        # 3. 縣市內容標準化 (臺 -> 台)
        if '縣市' in df.columns:
            df['縣市'] = df['縣市'].astype(str).str.strip().str.replace('臺', '台')
            df = df[df['縣市'].isin(VALID_CITIES)]

        # 4. 清理類別編號
        if '類別編號' in df.columns:
            df['類別編號'] = df['類別編號'].astype(str).str.strip()

        # 5. 清理數字 (評論數)
        def clean_num(x):
            if pd.notnull(x):
                return int(re.sub(r'\D', '', str(x)) or 0)
            return 0

        if '評論數' in df.columns:
            df['評論數'] = df['評論數'].apply(clean_num)

        # 6. 星級處理
        star_col = 'Google 評分' if 'Google 評分' in df.columns else 'Google 星級'
        if star_col in df.columns:
            df['Star'] = pd.to_numeric(df[star_col], errors='coerce').fillna(0.0)
        else:
            df['Star'] = 0.0

        return df
    except Exception as e:
        st.error(f"資料讀取錯誤: {e}")
        return None

# --- 2. 核心邏輯：計算推薦 ---
def process_recommendation(df, user_id, a, manual_cat, selected_city):
    def inv(score): return 6 - score
    # 計算 Big Five 分數
    E = (a['q6'] + inv(a['q1']) + inv(a['q11'])) / 3
    A = (a['q2'] + a['q12'] + inv(a['q7'])) / 3
    C = (a['q8'] + a['q13'] + inv(a['q3'])) / 3
    N = (a['q9'] + a['q14'] + inv(a['q4'])) / 3
    O = (a['q10'] + a['q15'] + inv(a['q5'])) / 3

    f_scores = {
        'F1': 0.715*E - 0.320*C, 'F2': 0.404*E + 0.573*A - 0.223*C,
        'F3': 0.751*E - 0.050*A + 0.129*N - 0.115*O - 0.108*C,
        'F4': 0.617*E + 0.076*N - 0.232*O, 'F5': 0.525*A + 0.078*N + 0.078*O - 0.182*C,
        'F6': 0.790*E - 0.123*A + 0.128*N - 0.204*O - 0.077*C, 'F7': 0.625*A,
        'F8': 0.717*E - 0.309*A - 0.152*O - 0.150*C, 'F9': 0.459*E + 0.187*A - 0.116*O - 0.089*C,
        'F10': 0.649*E - 0.168*A + 0.144*N - 0.143*O + 0.079*C, 'F11': 0.336*E + 0.605*A - 0.365*C
    }
    top_cats = [x[0] for x in sorted(f_scores.items(), key=lambda x: x[1], reverse=True)]

    work_df = df[df['縣市'] == selected_city].copy()
    recs = []
    seen = set()
    rank = 1

    # --- 修改點：將 #1, #2, #3 統稱為 "人格適配" ---
    plan = [(manual_cat, 3, "自選主題"), (top_cats[0], 3, "人格適配"),
            (top_cats[1], 3, "人格適配"), (top_cats[2], 1, "人格適配")]

    for cid, count, lbl in plan:
        pool = work_df[(work_df['類別編號'] == cid) & (~work_df['景點名稱'].isin(seen))]
        if pool.empty: continue
        sorted_pool = pool.sort_values(by=['評論數', 'Star'], ascending=False).head(count)
        for _, r in sorted_pool.iterrows():
            recs.append({
                "rank": rank, "label": lbl, "name": r['景點名稱'],
                "city": r['縣市'], "star": r['Star'], "reviews": r['評論數']
            })
            seen.add(r['景點名稱'])
            rank += 1

    st.session_state.user_data = {
        "name": user_id, "personality": {"E":E,"A":A,"C":C,"N":N,"O":O},
        "selected_city": selected_city, "manual_cat_label": manual_cat
    }
    st.session_state.recs = recs

# --- 重要：替換後的 save_feedback 函式 ---
def save_feedback(scores, text):
    if 'user_data' not in st.session_state:
        st.error("找不到使用者資料，無法儲存。")
        return
        
    u = st.session_state.user_data
    p = u['personality']
    # 修正時區：強制加 8 小時轉為台灣時間
    tw_time = (datetime.datetime.now() + datetime.timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")
    
    # 準備要寫入 Google Sheets 的資料列 (A 到 Q 欄)
    row_data = [
        tw_time, # A. 時間
        u['name'],                                              # B. User_ID
        u['selected_city'],                                   # C. 篩選縣市
        u['manual_cat_label'],                                # D. 篩選主題
        f"{p['E']:.2f}", f"{p['A']:.2f}", f"{p['C']:.2f}",    # E, F, G. 分數
        f"{p['N']:.2f}", f"{p['O']:.2f}",                    # H, I. 分數
        scores["PU1"], scores["PU2"], scores["PU3"],          # J, K, L. PU 問卷
        scores["US1"], scores["US2"], scores["US3"],          # M, N, O. US 問卷
        text,                                                 # P. 其他建議
        "|".join([r['name'] for r in st.session_state.recs])  # Q. 推薦清單
    ]

    try:
        # 連接 Google Sheets
        credentials_dict = dict(st.secrets["gcp_service_account"])
        scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
        creds = Credentials.from_service_account_info(credentials_dict, scopes=scopes)
        client = gspread.authorize(creds)

        # 開啟試算表
        sheet = client.open("big5 fb").sheet1
        
        # 寫入資料
        sheet.append_row(row_data)
        st.success("✅ 問卷回饋已成功同步至 Google 雲端試算表！")
        
    except Exception as e:
        st.error(f"雲端儲存失敗: {e}")
        # 備份到本機 CSV
        try:
            pd.DataFrame([row_data]).to_csv('backup_log.csv', mode='a', index=False, header=False)
        except: pass

# --- 3. 主程式介面 ---
def main():
    st.title("🗺️ 旅遊推薦系統")

    df = load_data()
    if df is None:
        st.error("❌ 找不到資料檔 `TAIWAN_FILTERED.csv`")
        return

    if 'step' not in st.session_state: st.session_state.step = 1
    if 'recs' not in st.session_state: st.session_state.recs = []
    if 'user_id' not in st.session_state:
        st.session_state.user_id = f"User_{str(uuid.uuid4())[:8]}"

    if st.session_state.step == 1:
        st.header("第一階段：個人化分析")
        st.info(f"🆔 使用者編號：**{st.session_state.user_id}**")

        selected_city = st.selectbox("您想去哪個縣市？", VALID_CITIES)
        cat_options = {"F1": "F1 - 腎上腺素活動", "F2": "F2 - 荒野自然活動", "F3": "F3 - 派對、音樂與夜生活", "F4": "F4 - 陽光、水與沙灘", "F5": "F5 - 博物館、船遊與觀景點", "F6": "F6 - 主題與動物公園", "F7": "F7 - 文化遺產", "F8": "F8 - 運動與競賽", "F9": "F9 - 美食活動", "F10": "F10 - 健康與福祉", "F11": "F11 - 自然現象"}
        manual_cat = st.selectbox("感興趣的類型：", list(cat_options.keys()), format_func=lambda x: cat_options[x])

        st.subheader("人格特質測驗")
        questions = [{'id': 'q1', 'text': '1. 趨向於安靜、少言。'}, {'id': 'q2', 'text': '2. 富有同情心、溫柔的人。'}, {'id': 'q3', 'text': '3. 傾向於雜亂無章。'}, {'id': 'q4', 'text': '4. 處事冷靜、能很好地處理壓力。'}, {'id': 'q5', 'text': '5. 對藝術、美學沒什麼興趣。'}, {'id': 'q6', 'text': '6. 很有活力。'}, {'id': 'q7', 'text': '7. 有時對人無理。'}, {'id': 'q8', 'text': '8. 能堅持到任務完成。'}, {'id': 'q9', 'text': '9. 常感到情緒低落、憂鬱。'}, {'id': 'q10', 'text': '10. 有豐富的想像力。'}, {'id': 'q11', 'text': '11. 害羞、內斂。'}, {'id': 'q12', 'text': '12. 待人禮貌、體貼。'}, {'id': 'q13', 'text': '13. 做事有效率、能完成計畫。'}, {'id': 'q14', 'text': '14. 容易感到焦慮。'}, {'id': 'q15', 'text': '15. 對事物有很多好奇心。'}]
        answers = {q['id']: st.slider(q['text'], 1, 5, 3, key=q['id']) for q in questions}

        if st.button("🚀 開始分析並推薦", type="primary", use_container_width=True):
            process_recommendation(df, st.session_state.user_id, answers, manual_cat, selected_city)
            st.session_state.step = 2
            st.rerun()

    elif st.session_state.step == 2:
        user = st.session_state.user_data
        st.header("第二階段：推薦結果")
        
        # --- 修改點：新增給受試者的提醒文字 ---
        st.info("💡 **本系統的 推薦結果 會優先推薦您 熱門程度較高(評論數) 的景點，代表其旅遊品質經過較多旅客的驗證，再以 Google星級 為次排序。**")
        
        st.write(f"**📍 地區：** {user['selected_city']} | **🎯 主題：** {user['manual_cat_label']}")
        p = user['personality']
        st.info(f"人格分析：E({p['E']:.1f}) A({p['A']:.1f}) C({p['C']:.1f}) N({p['N']:.1f}) O({p['O']:.1f})")

        if not st.session_state.recs:
            st.warning("⚠️ 找不到符合的景點。")
        else:
            st.dataframe(pd.DataFrame(st.session_state.recs), hide_index=True, use_container_width=True)

        st.divider()
        with st.form("feedback_form"):
            st.subheader("系統使用回饋")
            pu1 = st.slider("PU1. 系統能幫助我更精準地推薦景點", 1, 5, 3)
            pu2 = st.slider("PU2. 系統能節省我過濾資訊的時間", 1, 5, 3)
            pu3 = st.slider("PU3. 系統能提升我規劃旅遊的效率", 1, 5, 3)
            us1 = st.slider("US1. 我滿意系統推薦的景點準確度", 1, 5, 3)
            us2 = st.slider("US2. 我滿意系統的介面設計與操作流程", 1, 5, 3)
            us3 = st.slider("US3. 整體而言我對此系統感到滿意", 1, 5, 3)
            other_text = st.text_area("其他建議 (選填)：")

            if st.form_submit_button("送出回饋並結束", type="primary", use_container_width=True):
                scores = {"PU1": pu1, "PU2": pu2, "PU3": pu3, "US1": us1, "US2": us2, "US3": us3}
                save_feedback(scores, other_text)
                st.session_state.step = 3
                st.rerun()

    elif st.session_state.step == 3:
        st.balloons()
        st.success("✅ 感謝您的參與，資料已上傳雲端！")
        if st.button("🔄 重新開始"):
            for key in list(st.session_state.keys()): del st.session_state[key]
            st.rerun()

if __name__ == "__main__":
    main()