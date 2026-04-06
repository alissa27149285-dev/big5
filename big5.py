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

# --- 1. 資料讀取 ---
@st.cache_data
def load_data():
    try:
        csv_file = 'TAIWAN_FILTERED.csv'
        if not os.path.exists(csv_file): return None
        df = pd.read_csv(csv_file, encoding='utf-8-sig')
        df.columns = [c.strip() for c in df.columns]
        if '城市' in df.columns: df.rename(columns={'城市': '縣市'}, inplace=True)
        if '縣市' in df.columns:
            df['縣市'] = df['縣市'].astype(str).str.strip().str.replace('臺', '台')
        def clean_num(x):
            if pd.notnull(x): return int(re.sub(r'\D', '', str(x)) or 0)
            return 0
        if '評論數' in df.columns: df['評論數'] = df['評論數'].apply(clean_num)
        star_col = 'Google 評分' if 'Google 評分' in df.columns else 'Google 星級'
        df['Star'] = pd.to_numeric(df[star_col], errors='coerce').fillna(0.0) if star_col in df.columns else 0.0
        return df
    except Exception as e:
        st.error(f"資料讀取錯誤: {e}")
        return None

# --- 2. 核心邏輯 ---
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
    recs, seen, rank = [], set(), 1
    plan = [(manual_cat, 3, "自選主題"), (top_cats[0], 3, "人格適配"), (top_cats[1], 3, "人格適配"), (top_cats[2], 1, "人格適配")]

    for cid, count, lbl in plan:
        pool = work_df[(work_df['類別編號'] == cid) & (~work_df['景點名稱'].isin(seen))]
        if pool.empty: continue
        sorted_pool = pool.sort_values(by=['評論數', 'Star'], ascending=False).head(count)
        for _, r in sorted_pool.iterrows():
            recs.append({"排名": rank, "依據": lbl, "景點名稱": r['景點名稱'], "評分": f"⭐ {r['Star']}", "評論數": r['評論數']})
            seen.add(r['景點名稱'])
            rank += 1
    st.session_state.user_data = {"name": user_id, "personality": {"E":E,"A":A,"C":C,"N":N,"O":O}, "selected_city": selected_city, "manual_cat_label": manual_cat}
    st.session_state.recs = recs

# --- 3. 儲存函式 ---
def save_feedback(scores, text):
    u = st.session_state.user_data
    p = u['personality']
    tw_time = (datetime.datetime.now() + datetime.timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")
    row_data = [tw_time, u['name'], u['selected_city'], u['manual_cat_label'], f"{p['E']:.2f}", f"{p['A']:.2f}", f"{p['C']:.2f}", f"{p['N']:.2f}", f"{p['O']:.2f}", scores["PU1"], scores["PU2"], scores["PU3"], scores["US1"], scores["US2"], scores["US3"], text, "|".join([r['景點名稱'] for r in st.session_state.recs])]
    try:
        credentials_dict = dict(st.secrets["gcp_service_account"])
        creds = Credentials.from_service_account_info(credentials_dict, scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"])
        client = gspread.authorize(creds)
        client.open("big5 fb").sheet1.append_row(row_data)
        st.success("✅ 問卷已成功上傳雲端！")
    except Exception as e: st.error(f"上傳失敗: {e}")

# --- 4. 主程式 ---
def main():
    st.title("🗺️ 旅遊推薦系統")
    df = load_data()
    if df is None: return

    if 'step' not in st.session_state: st.session_state.step = 1
    if 'user_id' not in st.session_state: st.session_state.user_id = f"User_{str(uuid.uuid4())[:8]}"

    # 步驟 1: 測驗頁面
    if st.session_state.step == 1:
        st.header("第一階段：填寫測驗")
        selected_city = st.selectbox("您想去哪個縣市？", ["台北市", "新北市", "桃園市", "台中市", "台南市", "高雄市", "基隆市", "宜蘭縣", "新竹縣", "苗栗縣", "彰化縣", "南投縣", "雲林縣", "嘉義縣", "屏東縣", "花蓮縣", "台東縣"])
        cat_options = {"F1": "F1 - 腎上腺素", "F2": "F2 - 自然", "F3": "F3 - 派對", "F4": "F4 - 沙灘", "F5": "F5 - 博物館", "F6": "F6 - 公園", "F7": "F7 - 文化", "F8": "F8 - 運動", "F9": "F9 - 美食", "F10": "F10 - 健康", "F11": "F11 - 現象"}
        manual_cat = st.selectbox("感興趣類型", list(cat_options.keys()), format_func=lambda x: cat_options[x])
        
        st.subheader("人格特質測驗")
        questions = [{'id': 'q1', 'text': '1. 趨向於安靜、少言。'}, {'id': 'q2', 'text': '2. 富有同情心、溫柔的人。'}, {'id': 'q3', 'text': '3. 傾向於雜亂無章。'}, {'id': 'q4', 'text': '4. 處事冷靜、能很好地處理壓力。'}, {'id': 'q5', 'text': '5. 對藝術、美學沒什麼興趣。'}, {'id': 'q6', 'text': '6. 很有活力。'}, {'id': 'q7', 'text': '7. 有時對人無理。'}, {'id': 'q8', 'text': '8. 能堅持到任務完成。'}, {'id': 'q9', 'text': '9. 常感到情緒低落、憂鬱。'}, {'id': 'q10', 'text': '10. 有豐富的想像力。'}, {'id': 'q11', 'text': '11. 害羞、內斂。'}, {'id': 'q12', 'text': '12. 待人禮貌、體貼。'}, {'id': 'q13', 'text': '13. 做事有效率、能完成計畫。'}, {'id': 'q14', 'text': '14. 容易感到焦慮。'}, {'id': 'q15', 'text': '15. 對事物有很多好奇心。'}]
        answers = {q['id']: st.slider(q['text'], 1, 5, 3, key=q['id']) for q in questions}
        
        if st.button("🚀 生成推薦清單", type="primary"):
            process_recommendation(df, st.session_state.user_id, answers, manual_cat, selected_city)
            st.session_state.step = 2
            st.rerun()

    # 步驟 2: 結果頁面 (改用 Tab 分頁)
    elif st.session_state.step == 2:
        # 使用 Tabs 確保視線第一時間在推薦結果上
        tab1, tab2 = st.tabs(["🏆 專屬推薦清單", "📋 填寫使用回饋"])

        with tab1:
            st.success("分析完成！以下是為您精選的景點：")
            user = st.session_state.user_data
            st.write(f"📍 **地區：** {user['selected_city']} | 🎯 **主題：** {user['manual_cat_label']}")
            st.dataframe(pd.DataFrame(st.session_state.recs), hide_index=True, use_container_width=True)
            
            st.warning("👉 **查看完景點後，請點擊上方分頁「填寫使用回饋」完成問卷。**")

        with tab2:
            st.subheader("系統使用回饋")
            with st.form("feedback_form"):
                pu1 = st.slider("PU1. 系統能精準推薦景點", 1, 5, 3)
                pu2 = st.slider("PU2. 節省過濾時間", 1, 5, 3)
                pu3 = st.slider("PU3. 提升規劃效率", 1, 5, 3)
                us1 = st.slider("US1. 推薦準確度滿意度", 1, 5, 3)
                us2 = st.slider("US2. 介面設計滿意度", 1, 5, 3)
                us3 = st.slider("US3. 整體滿意度", 1, 5, 3)
                other_text = st.text_area("建議回饋")
                if st.form_submit_button("送出並結束"):
                    save_feedback({"PU1":pu1,"PU2":pu2,"PU3":pu3,"US1":us1,"US2":us2,"US3":us3}, other_text)
                    st.session_state.step = 3
                    st.rerun()

    elif st.session_state.step == 3:
        st.balloons()
        st.success("✅ 感謝您的參與！")
        if st.button("重新開始"):
            for key in list(st.session_state.keys()): del st.session_state[key]
            st.rerun()

if __name__ == "__main__":
    main()