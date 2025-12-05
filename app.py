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

# --- 0. 班級資料庫 ---
ALL_SUFFIXES = {
    "普通科": ["機甲", "機乙", "電甲", "電乙", "建築", "室設", "製圖"],
    "建教班": ["機丙", "模丙"],
    "實用技能班": ["機加", "電修", "營造"]
}

DEPT_SPECIFIC_CONFIG = {
    "機械科": { "普通科": ["機甲", "機乙"], "建教班": ["機丙", "模丙"], "實用技能班": ["機加"] },
    "電機科": { "普通科": ["電甲", "電乙"], "建教班": [], "實用技能班": ["電修"] },
    "建築科": { "普通科": ["建築"], "建教班": [], "實用技能班": ["營造"] },
    "室設科": { "普通科": ["室設"], "建教班": [], "實用技能班": [] },
    "製圖科": { "普通科": ["製圖"], "建教班": [], "實用技能班": [] }
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
            st.error("找不到金鑰")
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
    for _, row in target_courses.iterrows():
        c_name = row['課程名稱']
        c_type = row['課程類別']
        default_class = row.get('預設適用班級', '')
        hist_matches = df_hist[df_hist['課程名稱'] == c_name]

        if not hist_matches.empty:
            for _, h_row in hist_matches.iterrows():
                display_rows.append({
                    "勾選": False,
                    "科別": dept, "年級": grade, "學期": semester,
                    "課程類別": c_type, "課程名稱": c_name,
                    "教科書(優先1)": h_row.get('教科書(優先1)', ''), "冊次(1)": h_row.get('冊次(1)', ''), "出版社(1)": h_row.get('出版社(1)', ''), "審定字號(1)": h_row.get('審定字號(1)', ''),
                    "教科書(優先2)": h_row.get('教科書(優先2)', ''), "冊次(2)": h_row.get('冊次(2)', ''), "出版社(2)": h_row.get('出版社(2)', ''), "審定字號(2)": h_row.get('審定字號(2)', ''),
                    "適用班級": h_row.get('適用班級', default_class), "備註": h_row.get('備註', '')
                })
        else:
            display_rows.append({
                "勾選": False,
                "科別": dept, "年級": grade, "學期": semester,
                "課程類別": c_type, "課程名稱": c_name,
                "教科書(優先1)": "", "冊次(1)": "", "出版社(1)": "", "審定字號(1)": "",
                "教科書(優先2)": "", "冊次(2)": "", "出版社(2)": "", "審定字號(2)": "",
                "適用班級": default_class, "備註": ""
            })
    return pd.DataFrame(display_rows)

# --- 3. 取得課程列表 ---
def get_course_list():
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

# --- 5. 班級計算與解析 ---
def generate_class_string(dept, grade, use_reg, use_prac, use_coop):
    if not dept or not grade: return ""
    prefix = {"1": "一", "2": "二", "3": "三"}.get(str(grade), "")
    
    # 判斷是否為專業科系
    if dept in DEPT_SPECIFIC_CONFIG:
        config = DEPT_SPECIFIC_CONFIG[dept]
        classes = []
        if use_reg: classes.extend([f"{prefix}{c}" for c in config.get("普通科", [])])
        if use_prac: classes.extend([f"{prefix}{c}" for c in config.get("實用技能班", [])])
        if use_coop and str(grade) != "3": classes.extend([f"{prefix}{c}" for c in config.get("建教班", [])])
        return ",".join(classes)
    else:
        # 共同科目：抓全校
        classes = []
        for sys_name, suffixes in ALL_SUFFIXES.items():
            if str(grade) == "3" and sys_name == "建教班": continue
            # 根據勾選決定是否加入該學制
            if (sys_name == "普通科" and use_reg) or \
               (sys_name == "實用技能班" and use_prac) or \
               (sys_name == "建教班" and use_coop):
                for s in suffixes: classes.append(f"{prefix}{s}")
        return ",".join(sorted(list(set(classes))))

# --- 6. 主程式 ---
def main():
    st.set_page_config(page_title="教科書填報系統", layout="wide")
    st.title("📚 教科書填報系統")

    # 初始化 Session State
    if 'edit_index' not in st.session_state: st.session_state['edit_index'] = None
    # 使用 dict 來暫存表單資料，而不是直接綁定 widget key，避免衝突
    if 'form_data' not in st.session_state:
        st.session_state['form_data'] = {
            'course': '', 'book1': '', 'vol1': '全', 'pub1': '', 
            'book2': '', 'vol2': '全', 'pub2': '', 'note': '', 'class_str': ''
        }

    # --- 側邊欄：設定 ---
    with st.sidebar:
        st.header("1. 填報設定")
        dept_options = [
            "建築科", "機械科", "電機科", "製圖科", "室設科", 
            "國文科", "英文科", "數學科", "自然科", "社會科", 
            "資訊科技", "體育科", "國防科", "藝能科", "健護科", "輔導科", "閩南語"
        ]
        dept = st.selectbox("科別", dept_options)
        col1, col2 = st.columns(2)
        with col1: sem = st.selectbox("學期", ["1", "2"])
        with col2: grade = st.selectbox("年級", ["1", "2", "3"])
        
        if st.button("📥 載入/重置 表格", type="primary", use_container_width=True):
            with st.spinner("讀取中..."):
                df = load_data(dept, sem, grade)
                st.session_state['data'] = df
                st.session_state['loaded'] = True
                st.session_state['edit_index'] = None
                # 重置表單
                st.session_state['form_data'] = {
                    'course': '', 'book1': '', 'vol1': '全', 'pub1': '', 
                    'book2': '', 'vol2': '全', 'pub2': '', 'note': '', 'class_str': ''
                }

    if st.session_state.get('loaded'):
        
        # --- 側邊欄：編輯表單 ---
        with st.sidebar:
            st.divider()
            is_edit_mode = st.session_state['edit_index'] is not None
            header_text = f"2. 修改第 {st.session_state['edit_index'] + 1} 列" if is_edit_mode else "2. 新增/插入課程"
            st.subheader(header_text)
            
            if is_edit_mode:
                if st.button("❌ 取消修改 (回新增模式)", type="secondary"):
                    st.session_state['edit_index'] = None
                    # 清空暫存
                    st.session_state['form_data'] = {k: '' for k in st.session_state['form_data']}
                    st.rerun()

            # 取得目前的暫存值 (可能是剛載入的，也可能是使用者剛輸入的)
            current_form = st.session_state['form_data']

            # 課程選單
            course_list = get_course_list()
            # 如果是編輯模式且課程在清單中，設定 index
            course_index = 0
            if is_edit_mode and current_form['course'] in course_list:
                course_index = course_list.index(current_form['course'])
            
            if course_list:
                input_course = st.selectbox("選擇課程", course_list, index=course_index)
            else:
                input_course = st.text_input("課程名稱", value=current_form['course'])
            
            # 書籍資料 1
            st.markdown("**第一優先**")
            input_book1 = st.text_input("書名", value=current_form['book1'])
            bc1, bc2 = st.columns([1, 2])
            vol_opts = ["全", "上", "下", "I", "II", "III", "IV", "V", "VI"]
            vol1_idx = vol_opts.index(current_form['vol1']) if current_form['vol1'] in vol_opts else 0
            with bc1: input_vol1 = st.selectbox("冊次", vol_opts, index=vol1_idx)
            with bc2: input_pub1 = st.text_input("出版社", value=current_form['pub1'])

            # 書籍資料 2
            st.markdown("**第二優先**")
            input_book2 = st.text_input("備選書名", value=current_form['book2'])
            bc3, bc4 = st.columns([1, 2])
            vol2_idx = vol_opts.index(current_form['vol2']) if current_form['vol2'] in vol_opts else 0
            with bc3: input_vol2 = st.selectbox("冊次(2)", vol_opts, index=vol2_idx)
            with bc4: input_pub2 = st.text_input("出版社(2)", value=current_form['pub2'])
            
            # --- 班級小幫手 (Checkbox 回歸！) ---
            st.markdown("##### 適用班級")
            st.caption("👇 勾選學制自動產生，也可手動修改下方文字框")
            
            # 這裡使用 checkbox 來動態生成字串
            # 注意：這裡不綁定 session_state，而是每次重新勾選就重新計算
            # 為了讓編輯模式下能預設勾選，我們需要一點邏輯，但為了避免複雜，
            # 這裡提供一個「重新產生」的按鈕邏輯比較簡單，或者直接讓使用者勾選後覆蓋
            
            c1, c2, c3 = st.columns(3)
            with c1: use_reg = st.checkbox("普通", value=True)
            with c2: use_prac = st.checkbox("實技")
            with c3: use_coop = st.checkbox("建教")
            
            # 計算新的字串
            new_class_str = generate_class_string(dept, grade, use_reg, use_prac, use_coop)
            
            # 決定顯示在 text_input 的值：
            # 如果是編輯模式剛載入(session有值)，優先顯示 session 的值 (原班級)
            # 但如果使用者動了 checkbox (導致 rerun)，我們希望看到新字串
            # 這裡做一個簡單的妥協：提供一個按鈕「套用勾選結果」
            
            # 或者：預設顯示 current_form['class_str']，如果為空則顯示 generated
            display_class_str = current_form['class_str'] if current_form['class_str'] else new_class_str
            
            # 讓使用者可以點擊按鈕來用 checkbox 的結果覆蓋手動輸入框
            if st.button("⬇️ 套用上方勾選結果"):
                display_class_str = new_class_str
            
            input_class_str = st.text_input("班級字串 (可手動修)", value=display_class_str)
            
            input_note = st.text_input("備註", value=current_form['note'])

            # 按鈕：新增 或 更新
            if is_edit_mode:
                if st.button("🔄 更新表格", type="primary", use_container_width=True):
                    idx = st.session_state['edit_index']
                    # 更新 DataFrame
                    st.session_state['data'].at[idx, "課程名稱"] = input_course
                    st.session_state['data'].at[idx, "教科書(優先1)"] = input_book1
                    st.session_state['data'].at[idx, "冊次(1)"] = input_vol1
                    st.session_state['data'].at[idx, "出版社(1)"] = input_pub1
                    st.session_state['data'].at[idx, "教科書(優先2)"] = input_book2
                    st.session_state['data'].at[idx, "冊次(2)"] = input_vol2
                    st.session_state['data'].at[idx, "出版社(2)"] = input_pub2
                    st.session_state['data'].at[idx, "適用班級"] = input_class_str
                    st.session_state['data'].at[idx, "備註"] = input_note
                    st.session_state['data'].at[idx, "勾選"] = False # 取消勾選
                    
                    # 清除狀態
                    st.session_state['edit_index'] = None
                    st.session_state['form_data'] = {k: '' for k in st.session_state['form_data']}
                    st.success("更新成功！")
                    st.rerun()
            else:
                if st.button("➕ 加入表格", type="secondary", use_container_width=True):
                    new_row = {
                        "勾選": False,
                        "科別": dept, "年級": grade, "學期": sem,
                        "課程類別": "部定必修", 
                        "課程名稱": input_course,
                        "教科書(優先1)": input_book1, "冊次(1)": input_vol1, "出版社(1)": input_pub1, "審定字號(1)": "",
                        "教科書(優先2)": input_book2, "冊次(2)": input_vol2, "出版社(2)": input_pub2, "審定字號(2)": "",
                        "適用班級": input_class_str,
                        "備註": input_note
                    }
                    st.session_state['data'] = pd.concat([st.session_state['data'], pd.DataFrame([new_row])], ignore_index=True)
                    st.session_state['form_data'] = {k: '' for k in st.session_state['form_data']} # 清空
                    st.success(f"已加入：{input_course}")
                    st.rerun()

        # --- 中央顯示區 ---
        st.success(f"目前編輯：**{dept}** / **{grade}年級** / **第{sem}學期**")
        
        edited_df = st.data_editor(
            st.session_state['data'],
            num_rows="dynamic",
            use_container_width=True,
            height=600,
            column_config={
                "勾選": st.column_config.CheckboxColumn("勾選", help="勾選後載入左側編輯", width="small"),
                "課程類別": st.column_config.SelectboxColumn("類別", options=["部定必修", "校訂必修", "校訂選修", "實習科目", "一般科目"], width="small"),
                "適用班級": st.column_config.TextColumn("適用班級", width="medium"),
            }
        )

        # 監聽勾選事件
        selected_rows = edited_df[edited_df["勾選"] == True]
        if not selected_rows.empty:
            target_idx = selected_rows.index[0]
            # 如果勾選了新的一行
            if st.session_state.get('edit_index') != target_idx:
                row_data = selected_rows.iloc[0]
                st.session_state['edit_index'] = target_idx
                # 將資料載入暫存 dict
                st.session_state['form_data'] = {
                    'course': row_data["課程名稱"],
                    'book1': row_data["教科書(優先1)"],
                    'vol1': row_data["冊次(1)"],
                    'pub1': row_data["出版社(1)"],
                    'book2': row_data["教科書(優先2)"],
                    'vol2': row_data["冊次(2)"],
                    'pub2': row_data["出版社(2)"],
                    'note': row_data["備註"],
                    'class_str': str(row_data["適用班級"])
                }
                st.rerun()

        col_submit, _ = st.columns([1, 4])
        with col_submit:
            if st.button("💾 確認提交 (寫入資料庫)", type="primary", use_container_width=True):
                final_df = edited_df.drop(columns=["勾選"])
                if final_df.empty:
                    st.error("表格是空的")
                else:
                    with st.spinner("寫入中..."):
                        if save_submission(final_df):
                            st.success("✅ 資料已成功提交！")
                            st.balloons()

    else:
        st.info("👈 請先在左側按「載入」")

if __name__ == "__main__":
    main()
