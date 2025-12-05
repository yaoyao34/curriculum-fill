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
                    "勾選": False, # 新增勾選欄位
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
def get_all_possible_classes(grade):
    prefix = {"1": "一", "2": "二", "3": "三"}.get(str(grade), "")
    if not prefix: return []
    classes = []
    for sys_name, suffixes in ALL_SUFFIXES.items():
        if str(grade) == "3" and sys_name == "建教班": continue
        for s in suffixes: classes.append(f"{prefix}{s}")
    return sorted(list(set(classes)))

def get_default_classes(dept, grade):
    prefix = {"1": "一", "2": "二", "3": "三"}.get(str(grade), "")
    defaults = []
    if dept in DEPT_SPECIFIC_CONFIG:
        config = DEPT_SPECIFIC_CONFIG[dept]
        for sys_name, suffixes in config.items():
            if str(grade) == "3" and sys_name == "建教班": continue
            for s in suffixes: defaults.append(f"{prefix}{s}")
    else:
        return get_all_possible_classes(grade)
    return sorted(list(set(defaults)))

# --- 6. 主程式 ---
def main():
    st.set_page_config(page_title="教科書填報系統", layout="wide")
    st.title("📚 教科書填報系統")

    # 初始化 Session State (確保欄位有預設值)
    keys_to_init = ['form_course', 'form_book1', 'form_vol1', 'form_pub1', 'form_book2', 'form_vol2', 'form_pub2', 'form_note', 'edit_index']
    for k in keys_to_init:
        if k not in st.session_state:
            st.session_state[k] = "" if k != 'edit_index' else None

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
                st.session_state['selected_classes'] = get_default_classes(dept, grade)
                st.session_state['edit_index'] = None # 重置編輯狀態

    if st.session_state.get('loaded'):
        
        # --- 側邊欄：編輯表單 ---
        with st.sidebar:
            st.divider()
            # 判斷是「新增」還是「修改」
            is_edit_mode = st.session_state['edit_index'] is not None
            header_text = f"2. 修改第 {st.session_state['edit_index'] + 1} 列資料" if is_edit_mode else "2. 新增/插入課程"
            st.subheader(header_text)
            
            if is_edit_mode:
                st.info("💡 修改完後請按「更新表格」")
                if st.button("❌ 取消修改 (切換回新增模式)", type="secondary"):
                    st.session_state['edit_index'] = None
                    st.rerun()

            # 課程選單
            course_list = get_course_list()
            # 使用 key 綁定 session_state，實現雙向綁定
            input_course = st.selectbox("選擇課程", course_list, key='form_course') if course_list else st.text_input("課程名稱", key='form_course')
            
            # 書籍資料 1
            st.markdown("**第一優先 (必填)**")
            input_book1 = st.text_input("書名", key='form_book1')
            bc1, bc2 = st.columns([1, 2])
            with bc1: input_vol1 = st.selectbox("冊次", ["全", "上", "下", "I", "II", "III", "IV", "V", "VI"], key='form_vol1')
            with bc2: input_pub1 = st.text_input("出版社", key='form_pub1')

            # 書籍資料 2 (補回功能)
            st.markdown("**第二優先 (選填)**")
            input_book2 = st.text_input("備選書名", key='form_book2')
            bc3, bc4 = st.columns([1, 2])
            with bc3: input_vol2 = st.selectbox("冊次(2)", ["全", "上", "下", "I", "II", "III", "IV", "V", "VI"], key='form_vol2')
            with bc4: input_pub2 = st.text_input("出版社(2)", key='form_pub2')
            
            # 班級選擇器 (Multiselect)
            st.markdown("##### 適用班級 (點選編修)")
            all_classes_opts = get_all_possible_classes(grade)
            
            # 若無 selected_classes 初始化，給預設值
            if 'selected_classes' not in st.session_state:
                st.session_state['selected_classes'] = get_default_classes(dept, grade)

            selected_classes = st.multiselect(
                "班級列表：",
                options=all_classes_opts,
                key="selected_classes" # 這裡綁定 session_state
            )
            input_class_str = ",".join(selected_classes)
            
            input_note = st.text_input("備註", key='form_note')

            # 按鈕：新增 或 更新
            if is_edit_mode:
                if st.button("🔄 更新表格", type="primary", use_container_width=True):
                    idx = st.session_state['edit_index']
                    st.session_state['data'].at[idx, "課程名稱"] = input_course
                    st.session_state['data'].at[idx, "教科書(優先1)"] = input_book1
                    st.session_state['data'].at[idx, "冊次(1)"] = input_vol1
                    st.session_state['data'].at[idx, "出版社(1)"] = input_pub1
                    st.session_state['data'].at[idx, "教科書(優先2)"] = input_book2
                    st.session_state['data'].at[idx, "冊次(2)"] = input_vol2
                    st.session_state['data'].at[idx, "出版社(2)"] = input_pub2
                    st.session_state['data'].at[idx, "適用班級"] = input_class_str
                    st.session_state['data'].at[idx, "備註"] = input_note
                    st.session_state['data'].at[idx, "勾選"] = False # 更新完取消勾選
                    st.session_state['edit_index'] = None # 退出編輯模式
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
                    st.success(f"已加入：{input_course}")
                    st.rerun()

        # --- 中央顯示區 ---
        st.success(f"目前編輯：**{dept}** / **{grade}年級** / **第{sem}學期**")
        
        # 資料編輯器
        edited_df = st.data_editor(
            st.session_state['data'],
            num_rows="dynamic",
            use_container_width=True,
            height=600,
            column_config={
                "勾選": st.column_config.CheckboxColumn("勾選", help="勾選後可載入左側編輯", width="small"),
                "課程類別": st.column_config.SelectboxColumn("類別", options=["部定必修", "校訂必修", "校訂選修", "實習科目", "一般科目"], width="small"),
                "適用班級": st.column_config.TextColumn("適用班級", width="medium"),
            }
        )

        # 邏輯：檢查是否有勾選動作
        # 找出哪一列被勾選了
        selected_rows = edited_df[edited_df["勾選"] == True]
        
        if not selected_rows.empty:
            # 取第一個被勾選的列
            target_idx = selected_rows.index[0]
            row_data = selected_rows.iloc[0]
            
            # 如果這個 index 跟目前編輯的不一樣，代表使用者剛勾選
            if st.session_state.get('edit_index') != target_idx:
                st.session_state['edit_index'] = target_idx
                
                # 將資料填入 Session State，側邊欄會自動抓取
                st.session_state['form_course'] = row_data["課程名稱"]
                st.session_state['form_book1'] = row_data["教科書(優先1)"]
                st.session_state['form_vol1'] = row_data["冊次(1)"]
                st.session_state['form_pub1'] = row_data["出版社(1)"]
                st.session_state['form_book2'] = row_data["教科書(優先2)"]
                st.session_state['form_vol2'] = row_data["冊次(2)"]
                st.session_state['form_pub2'] = row_data["出版社(2)"]
                st.session_state['form_note'] = row_data["備註"]
                
                # 處理班級字串轉列表
                class_str = str(row_data["適用班級"])
                if class_str:
                    # 分割字串並去除空白
                    class_list = [c.strip() for c in class_str.replace("，", ",").split(",") if c.strip()]
                    # 過濾掉不在選項內的奇怪班級，避免報錯，或者動態加入選項
                    valid_opts = get_all_possible_classes(grade)
                    final_list = [c for c in class_list if c in valid_opts]
                    st.session_state['selected_classes'] = final_list
                else:
                    st.session_state['selected_classes'] = []
                
                st.rerun()

        # 提交按鈕
        col_submit, _ = st.columns([1, 4])
        with col_submit:
            if st.button("💾 確認提交 (寫入資料庫)", type="primary", use_container_width=True):
                # 提交前過濾掉「勾選」欄位，以免寫入 Google Sheet 報錯
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
