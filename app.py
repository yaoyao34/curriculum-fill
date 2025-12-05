import streamlit as st
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
import datetime
import json

# --- 全域設定 ---
SPREADSHEET_NAME = "教科書填報" 
SHEET_HISTORY = "DB_History"
SHEET_CURRICULUM = "DB_Curriculum"
SHEET_SUBMISSION = "Submission_Records"

# --- 0. 班級對照表 ---
DEPT_CLASS_MAP = {
    "機械科": { "普通科": ["機甲", "機乙"], "建教班": ["機丙", "模丙"], "實用技能班": ["機加"] },
    "電機科": { "普通科": ["電甲", "電乙"], "建教班": [], "實用技能班": ["電修"] },
    "建築科": { "普通科": ["建築"], "建教班": [], "實用技能班": ["營造"] },
    "室設科": { "普通科": ["室設"], "建教班": [], "實用技能班": [] },
    "製圖科": { "普通科": ["製圖"], "建教班": [], "實用技能班": [] },
    "default": { "普通科": [], "建教班": [], "實用技能班": [] }
}

# --- 1. 連線設定 ---
@st.cache_resource
def get_connection():
    scope = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
    if "GCP_CREDENTIALS" in st.secrets:
        try:
            creds_dict = json.loads(st.secrets["GCP_CREDENTIALS"])
            creds = Credentials.from_service_account_info(creds_dict, scopes=scope)
        except json.JSONDecodeError:
            st.error("Secrets 格式錯誤")
            return None
    else:
        try:
            creds = Credentials.from_service_account_file('credentials.json', scopes=scope)
        except Exception:
            st.error("找不到金鑰 (credentials.json 或 Secrets)")
            return None
    return gspread.authorize(creds)

# --- 2. 資料讀取 ---
def load_data(dept, semester, grade):
    client = get_connection()
    if not client: return pd.DataFrame()
    try:
        sh = client.open(SPREADSHEET_NAME)
        ws_curr = sh.worksheet(SHEET_CURRICULUM)
        ws_hist = sh.worksheet(SHEET_HISTORY)
        
        df_curr = pd.DataFrame(ws_curr.get_all_records())
        df_hist = pd.DataFrame(ws_hist.get_all_records())
        
        for df in [df_curr, df_hist]:
            if not df.empty:
                df['年級'] = df['年級'].astype(str)
                df['學期'] = df['學期'].astype(str)
    except Exception as e:
        st.error(f"讀取錯誤: {e}")
        return pd.DataFrame()

    mask_curr = (df_curr['科別'] == dept) & (df_curr['學期'] == semester) & (df_curr['年級'] == grade)
    target_courses = df_curr[mask_curr]

    if target_courses.empty:
        return pd.DataFrame()

    display_rows = []
    # 預設載入邏輯：比對歷史紀錄
    for _, row in target_courses.iterrows():
        c_name = row['課程名稱']
        c_type = row['課程類別']
        default_class = row.get('預設適用班級', '')
        hist_matches = df_hist[df_hist['課程名稱'] == c_name]

        if not hist_matches.empty:
            for _, h_row in hist_matches.iterrows():
                display_rows.append({
                    "科別": dept, "年級": grade, "學期": semester,
                    "課程類別": c_type, "課程名稱": c_name,
                    "教科書(優先1)": h_row.get('教科書(優先1)', ''), "冊次(1)": h_row.get('冊次(1)', ''), "出版社(1)": h_row.get('出版社(1)', ''), "審定字號(1)": h_row.get('審定字號(1)', ''),
                    "教科書(優先2)": h_row.get('教科書(優先2)', ''), "冊次(2)": h_row.get('冊次(2)', ''), "出版社(2)": h_row.get('出版社(2)', ''), "審定字號(2)": h_row.get('審定字號(2)', ''),
                    "適用班級": h_row.get('適用班級', default_class), "備註": h_row.get('備註', '')
                })
        else:
            display_rows.append({
                "科別": dept, "年級": grade, "學期": semester,
                "課程類別": c_type, "課程名稱": c_name,
                "教科書(優先1)": "", "冊次(1)": "", "出版社(1)": "", "審定字號(1)": "",
                "教科書(優先2)": "", "冊次(2)": "", "出版社(2)": "", "審定字號(2)": "",
                "適用班級": default_class, "備註": ""
            })
    return pd.DataFrame(display_rows)

# --- 3. 取得該科別的課程選單 (給側邊欄用) ---
def get_course_list(dept, semester, grade):
    # 簡單起見，直接讀取目前的 df (若已載入) 或重新篩選
    if 'data' in st.session_state and not st.session_state['data'].empty:
        return st.session_state['data']['課程名稱'].unique().tolist()
    return []

# --- 4. 存檔 ---
def save_submission(df_to_save):
    client = get_connection()
    sh = client.open(SPREADSHEET_NAME)
    try:
        ws_sub = sh.worksheet(SHEET_SUBMISSION)
    except:
        ws_sub = sh.add_worksheet(title=SHEET_SUBMISSION, rows=1000, cols=20)
        ws_sub.append_row(["填報時間", "科別", "年級", "學期", "課程名稱", "教科書(1)", "冊次", "出版社", "字號", "教科書(2)", "冊次", "出版社", "字號", "適用班級", "備註"])

    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    data_list = []
    for _, row in df_to_save.iterrows():
        data_list.append([
            timestamp, row['科別'], row['年級'], row['學期'], row['課程名稱'],
            row['教科書(優先1)'], row['冊次(1)'], row['出版社(1)'], row['審定字號(1)'],
            row['教科書(優先2)'], row['冊次(2)'], row['出版社(2)'], row['審定字號(2)'],
            row['適用班級'], row['備註']
        ])
    ws_sub.append_rows(data_list)
    return True

# --- 5. 班級字串產生器 ---
def generate_class_string(dept, grade, use_reg, use_prac, use_coop):
    if not dept or not grade: return ""
    prefix = {"1": "一", "2": "二", "3": "三"}.get(str(grade), "")
    config = DEPT_CLASS_MAP.get(dept, DEPT_CLASS_MAP["default"])
    classes = []
    if use_reg: classes.extend([f"{prefix}{c}" for c in config.get("普通科", [])])
    if use_prac: classes.extend([f"{prefix}{c}" for c in config.get("實用技能班", [])])
    if use_coop and str(grade) != "3": classes.extend([f"{prefix}{c}" for c in config.get("建教班", [])])
    return ",".join(classes)

# --- 6. 主程式 ---
def main():
    st.set_page_config(page_title="教科書填報系統", layout="wide")
    st.title("📚 教科書填報系統")

    with st.sidebar:
        st.header("1. 填報設定")
        dept = st.selectbox("科別", ["建築科", "機械科", "電機科", "製圖科", "室設科", "國文科", "英文科", "數學科", "自然科", "社會科"])
        col1, col2 = st.columns(2)
        with col1: sem = st.selectbox("學期", ["1", "2"])
        with col2: grade = st.selectbox("年級", ["1", "2", "3"])
        
        if st.button("📥 載入/重置 表格", type="primary", use_container_width=True):
            with st.spinner("讀取中..."):
                df = load_data(dept, sem, grade)
                st.session_state['data'] = df
                st.session_state['loaded'] = True

    # --- 顯示主畫面 ---
    if st.session_state.get('loaded'):
        
        # --- 側邊欄：新增課程表單 (在這裡操作！) ---
        with st.sidebar:
            st.divider()
            st.subheader("2. 新增/插入課程")
            st.info("👇 在這裡填寫，按按鈕直接加入右邊表格")
            
            # 課程選單 (從已載入的資料中抓取課程清單)
            course_list = get_course_list(dept, sem, grade)
            input_course = st.selectbox("選擇課程", course_list) if course_list else st.text_input("課程名稱")
            
            # 班級勾選 (自動產生)
            st.caption("勾選適用班級：")
            c1, c2, c3 = st.columns(3)
            with c1: u_reg = st.checkbox("普通", value=True)
            with c2: u_prac = st.checkbox("實技")
            with c3: u_coop = st.checkbox("建教")
            
            # 即時計算班級字串
            auto_class_str = generate_class_string(dept, grade, u_reg, u_prac, u_coop)
            input_class = st.text_input("適用班級 (可手動修)", value=auto_class_str)
            
            # 書籍資料
            input_book = st.text_input("教科書名")
            bc1, bc2 = st.columns([1, 2])
            with bc1: input_vol = st.selectbox("冊次", ["全", "上", "下", "I", "II"])
            with bc2: input_pub = st.text_input("出版社")
            input_note = st.text_input("備註")

            # 加入按鈕
            if st.button("➕ 加入表格", type="secondary", use_container_width=True):
                # 建立新的一列資料
                new_row = {
                    "科別": dept, "年級": grade, "學期": sem,
                    "課程類別": "部定必修", # 預設，可去右邊改
                    "課程名稱": input_course,
                    "教科書(優先1)": input_book, "冊次(1)": input_vol, "出版社(1)": input_pub, "審定字號(1)": "",
                    "教科書(優先2)": "", "冊次(2)": "", "出版社(2)": "", "審定字號(2)": "",
                    "適用班級": input_class,
                    "備註": input_note
                }
                # 加到 Session State 的 DataFrame
                st.session_state['data'] = pd.concat([st.session_state['data'], pd.DataFrame([new_row])], ignore_index=True)
                st.success(f"已加入：{input_course}")

        # --- 中央顯示區 ---
        st.success(f"目前編輯：**{dept}** / **{grade}年級** / **第{sem}學期**")
        
        # 顯示可編輯表格 (此處也能手動改)
        edited_df = st.data_editor(
            st.session_state['data'],
            num_rows="dynamic",
            use_container_width=True,
            height=600,
            column_config={
                "課程類別": st.column_config.SelectboxColumn("類別", options=["部定必修", "校訂必修", "校訂選修", "實習科目"], width="small"),
                "適用班級": st.column_config.TextColumn("適用班級", width="medium"),
            }
        )

        # 提交按鈕
        col_submit, _ = st.columns([1, 4])
        with col_submit:
            if st.button("💾 確認提交 (寫入資料庫)", type="primary", use_container_width=True):
                if edited_df.empty:
                    st.error("表格是空的")
                else:
                    with st.spinner("寫入中..."):
                        if save_submission(edited_df):
                            st.success("✅ 資料已成功提交！")
                            st.balloons()

    else:
        st.info("👈 請先在左側按「載入」")

if __name__ == "__main__":
    main()
