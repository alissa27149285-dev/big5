import streamlit as st
import pandas as pd
import os
import datetime
import re
import uuid
import gspread
from google.oauth2.service_account import Credentials

# --- 設定頁面 ---
st.set_page_config(page_title="旅遊推薦系統", layout="centered")

# --- 定義標準 17 縣市清單 ---
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

        df = pd.read_csv(csv_file, encoding='utf-8-sig')
        df.columns = [c.strip() for c in df.columns]

        if '城市' in df.columns:
            df.rename(columns={'城市': '縣市'}, inplace=True)
        
        if '縣市' in df.columns:
            df['縣市'] = df['縣市'].astype(str).str.strip().str.replace('臺', '台')
            df = df[df['縣市'].isin(VALID_CITIES)]

        if '類別編號' in df.columns:
            df['類別編號'] = df['類別編號'].astype(str).str.strip()

        def clean_num(x):
            if pd.notnull(x):
                return int(re.sub(r'\D', '', str(x)) or 0)
            return 0

        if '評論數' in df.columns:
            df['評論數'] = df['評論數'].apply(clean_num)

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

    plan = [(manual_cat, 3, "自選主題"), (top_cats[0], 3, "人格適配"),
            (top_cats[1], 3, "人格適配"), (top_cats[2], 1, "人格適配")]

    for cid, count, lbl in plan:
        pool = work_df[(work_df['類別編號'] == cid) & (~work_df['景點名稱'].isin(seen))]
        if pool.empty: continue
        sorted_pool = pool.sort_values(by=['評論數', 'Star'], ascending=False).head(count)
        for _, r in sorted_pool.iterrows():
            recs.append({
                "推薦排名": rank, "來源標籤": lbl, "景點名稱": r['景點名稱'],
                "縣市": r['縣市'], "Google星級": f"⭐ {r['Star']}", "評論數量": r['評論數']
            })
            seen.add(r['景點名稱'])
            rank += 1

    st.session_state.user_data = {
        "name": user_id, "personality": {"E":E,"A":A,"C":C,"N":N,"O":O},
        "selected_city": selected_city, "manual_cat_label": manual_cat
    }
    st.session_state.recs = recs

# --- 3. 儲存回饋函式 ---
def save_feedback(scores, text):
    u = st.session_state.user_data
    p = u['personality']
    tw_time = (datetime.datetime.now() + datetime.timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")
    
    row_data = [
        tw_time, u['name'], u['selected_city'], u['manual_cat_label'],
        f"{p['E']:.2f}", f"{p['A']:.2f}", f"{p['C']:.2f}", f"{p['N']:.2f}", f"{p['O']:.2f}",
        scores["PU1"], scores["PU2"], scores["PU3"],
        scores["US1"], scores["US2"], scores["US3"],
        text, "|".join([r['景點名稱'] for r in st.session_state.recs])
    ]

    try:
        credentials_dict = dict(st.secrets["gcp_service_account"])
        scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
        creds = Credentials.from_service_account_info(credentials_dict, scopes=scopes)
        client = gspread.authorize(creds)
        # 請確認你的試算表名稱是 "big5 fb"
        sheet = client.open("big5 fb").sheet1
        sheet.append_row(row_data)
        st.success("✅ 回饋已成功同步至雲端！")
    except Exception as e:
        st.error(f"雲端儲存失敗: {e}")

# --- 4. 主程式介面 ---
def main():
    st.title("🗺️ 旅遊推薦系統")
    df = load_data()
    if df is None:
        st.error("❌ 找不到資料檔 `TAIWAN_FILTERED.csv`")
        return

    if 'step' not in st.session_state: st.session_state.step = 1
    if 'user_id' not in st.session_state: st.session_state.user_id = f"User_{str(uuid.uuid4())[:8]}"

    # --- 步驟 1：測驗與條件選擇 ---
    if st.session_state.step == 1:
        st.header("第一階段：填寫測驗與地區")
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

    # --- 步驟 2：顯示推薦清單 (最優先) ---
    elif st.session_state.step == 2:
        user = st.session_state.user_data
        
        # 1. 顯示推薦清單區塊 (這部分現在會在最上面)
        st.header("🏆 專屬您的推薦清單")
        st.info("💡 系統依據您的「人格特質」與「自選主題」，優先推薦熱門且高評價的景點。")
        
        st.write(f"📍 **旅遊地區：** {user['selected_city']} | 🎯 **自選主題：** {user['manual_cat_label']}")
        
        if not st.session_state.recs:
            st.warning("⚠️ 找不到符合條件的景點。")
        else:
            # 轉換成 DataFrame 並顯示
            display_df = pd.DataFrame(st.session_state.recs)
            st.dataframe(display_df, hide_index=True, use_container_width=True)

        st.markdown("---") # 視覺分隔線

        # 2. 顯示回饋問卷區塊 (在推薦清單下方)
        st.header("📋 系統使用回饋")
        with st.form("feedback_form"):
            pu1 = st.slider("PU1. 系統能幫助我更精準地推薦景點", 1, 5, 3)
            pu2 = st.slider("PU2. 系統能節省我過濾資訊的時間", 1, 5, 3)
            pu3 = st.slider("PU3. 系統能提升我規劃旅遊的效率", 1, 5, 3)
            us1 = st.slider("US1. 我滿意系統推薦的景點準確度", 1, 5, 3)
            us2 = st.slider("US2. 我滿意系統的介面設計與操作流程", 1, 5, 3)
            us3 = st.slider("US3. 整體而言我對此系統感到滿意", 1, 5, 3)
            other_text = st.text_area("其他建議 (選填)：")

            if st.form_submit_button("送出問卷並結束", type="primary", use_container_width=True):
                scores = {"PU1": pu1, "PU2": pu2, "PU3": pu3, "US1": us1, "US2": us2, "US3": us3}
                save_feedback(scores, other_text)
                st.session_state.step = 3
                st.rerun()

    # --- 步驟 3：結束 ---
    elif st.session_state.step == 3:
        st.balloons()
        st.success("✅ 感謝參與！資料已成功上傳。")
        if st.button("🔄 重新開始"):
            for key in list(st.session_state.keys()): del st.session_state[key]
            st.rerun()

if __name__ == "__main__":
    main()