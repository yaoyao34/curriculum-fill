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

# --- 5. 班級計算邏輯 ---
def get_all_possible_classes(grade):
    prefix = {"1": "一", "2": "二", "3": "三"}.get(str(grade), "")
    if not prefix: return []
    classes = []
    for sys_name, suffixes in ALL_SUFFIXES.items():
        if str(grade) == "3" and sys_name == "建教班": continue
        for s in suffixes: classes.append(f"{prefix}{s}")
    return sorted(list(set(classes)))

def get_target_classes_for_dept(dept, grade, sys_name):
    """取得特定科別、特定學制的班級列表"""
    prefix = {"1": "一", "2": "二", "3": "三"}.get(str(grade), "")
    if not prefix: return []
    
    suffixes = []
    if dept in DEPT_SPECIFIC_CONFIG:
        # 專業科系：只抓該科
        suffixes = DEPT_SPECIFIC_CONFIG[dept].get(sys_name, [])
    else:
        # 共同科目：抓全校該學制
        suffixes = ALL_SUFFIXES.get(sys_name, [])
        
    # 三年級排除建教
    if str(grade) == "3" and sys_name == "建教班":
        return []
        
    return [f"{prefix}{s}" for s in suffixes]

# --- 6. 狀態管理 Callbacks ---
def update_class_list_from_checkboxes():
    """當 任意 Checkbox 變動時，更新選單內容"""
    dept = st.session_state.get('dept_val')
    grade = st.session_state.get('grade_val')
    
    current_list = list(st.session_state['active_classes'])
    
    # 檢查三個學制的勾選狀態
    for sys_key, sys_name in [('cb_reg', '普通科'), ('cb_prac', '實用技能班'), ('cb_coop', '建教班')]:
        is_checked = st.session_state[sys_key]
        target_classes = get_target_classes_for_dept(dept, grade, sys_name)
        
        if is_checked:
            # 加入
            for c in target_classes:
                if c not in current_list:
                    current_list.append(c)
        else:
            # 移除
            for c in target_classes:
                if c in current_list:
                    current_list.remove(c)
    
    st.session_state['active_classes'] = sorted(list(set(current_list)))
    
    # 連動 '全部' 勾選狀態 (若三個都勾，則勾選全部；否則取消)
    if st.session_state['cb_reg'] and st.session_state['cb_prac'] and st.session_state['cb_coop']:
        st.session_state['cb_all'] = True
    else:
        st.session_state['cb_all'] = False

def toggle_all_checkboxes():
    """當 '全部' Checkbox 變動時，連動其他三個"""
    new_state = st.session_state['cb_all']
    st.session_state['cb_reg'] = new_state
    st.session_state['cb_prac'] = new_state
    st.session_state['cb_coop'] = new_state
    # 強制執行一次更新
    update_class_list_from_checkboxes()

# --- 7. 主程式 ---
def main():
    st.set_page_config(page_title="教科書填報系統", layout="wide")
    st.title("📚 教科書填報系統")

    # 初始化 State
    if 'edit_index' not in st.session_state: st.session_state['edit_index'] = None
    if 'active_classes' not in st.session_state: st.session_state['active_classes'] = []
    if 'form_data' not in st.session_state:
        st.session_state['form_data'] = {
            'course': '', 'book1': '', 'vol1': '全', 'pub1': '', 
            'book2': '', 'vol2': '全', 'pub2': '', 'note': ''
        }
    
    # 初始化 Checkbox 狀態 (獨立變數)
    if 'cb_all' not in st.session_state: st.session_state['cb_all'] = False
    if 'cb_reg' not in st.session_state: st.session_state['cb_reg'] = False
    if 'cb_prac' not in st.session_state: st.session_state['cb_prac'] = False
    if 'cb_coop' not in st.session_state: st.session_state['cb_coop'] = False

    # --- 側邊欄：設定 ---
    with st.sidebar:
        st.header("1. 填報設定")
        dept_options = [
            "建築科", "機械科", "電機科", "製圖科", "室設科", 
            "國文科", "英文科", "數學科", "自然科", "社會科", 
            "資訊科技", "體育科", "國防科", "藝能科", "健護科", "輔導科", "閩南語"
        ]
        dept = st.selectbox("科別", dept_options, key='dept_val')
        col1, col2 = st.columns(2)
        with col1: sem = st.selectbox("學期", ["1", "2"], key='sem_val')
        with col2: grade = st.selectbox("年級", ["1", "2", "3"], key='grade_val')
        
        if st.button("📥 載入/重置 表格", type="primary", use_container_width=True):
            with st.spinner("讀取中..."):
                df = load_data(dept, sem, grade)
                st.session_state['data'] = df
                st.session_state['loaded'] = True
                st.session_state['edit_index'] = None
                st.session_state['active_classes'] = [] # 清空班級
                # 預設勾選普通科
                st.session_state['cb_all'] = False
                st.session_state['cb_reg'] = True
                st.session_state['cb_prac'] = False
                st.session_state['cb_coop'] = False
                update_class_list_from_checkboxes() # 執行一次初始化班級

    if st.session_state.get('loaded'):
        
        # --- 側邊欄：編輯表單 ---
        with st.sidebar:
            st.divider()
            is_edit_mode = st.session_state['edit_index'] is not None
            header_text = f"2. 修改第 {st.session_state['edit_index'] + 1} 列" if is_edit_mode else "2. 新增/插入課程"
            st.subheader(header_text)
            
            if is_edit_mode:
                if st.button("❌ 取消修改", type="secondary"):
                    st.session_state['edit_index'] = None
                    st.session_state['data']["勾選"] = False # 取消表格勾選
                    st.rerun()

            current_form = st.session_state['form_data']

            # 課程選單
            course_list = get_course_list()
            course_index = 0
            if is_edit_mode and current_form['course'] in course_list:
                course_index = course_list.index(current_form['course'])
            
            if course_list:
                input_course = st.selectbox("選擇課程", course_list, index=course_index)
            else:
                input_course = st.text_input("課程名稱", value=current_form['course'])
            
            # 書籍資料
            st.markdown("**第一優先**")
            input_book1 = st.text_input("書名", value=current_form['book1'])
            bc1, bc2 = st.columns([1, 2])
            vol_opts = ["全", "上", "下", "I", "II", "III", "IV", "V", "VI"]
            vol1_idx = vol_opts.index(current_form['vol1']) if current_form['vol1'] in vol_opts else 0
            with bc1: input_vol1 = st.selectbox("冊次", vol_opts, index=vol1_idx)
            with bc2: input_pub1 = st.text_input("出版社", value=current_form['pub1'])

            st.markdown("**第二優先**")
            input_book2 = st.text_input("備選書名", value=current_form['book2'])
            bc3, bc4 = st.columns([1, 2])
            vol2_idx = vol_opts.index(current_form['vol2']) if current_form['vol2'] in vol_opts else 0
            with bc3: input_vol2 = st.selectbox("冊次(2)", vol_opts, index=vol2_idx)
            with bc4: input_pub2 = st.text_input("出版社(2)", value=current_form['pub2'])
            
            # --- 班級設定 (新增 '全部' 選項) ---
            st.markdown("##### 適用班級")
            st.caption("👇 1. 勾選學制 (勾'全部'選全校)")
            
            # 版面調整：4欄位放 全部 / 普通 / 實技 / 建教
            c_all, c1, c2, c3 = st.columns([1, 1, 1, 1])
            with c_all: st.checkbox("全部", key="cb_all", on_change=toggle_all_checkboxes)
            with c1: st.checkbox("普通", key="cb_reg", on_change=update_class_list_from_checkboxes)
            with c2: st.checkbox("實技", key="cb_prac", on_change=update_class_list_from_checkboxes)
            with c3: st.checkbox("建教", key="cb_coop", on_change=update_class_list_from_checkboxes)
            
            st.caption("👇 2. 點選加入其他班級 (可直接在此增刪)")
            all_possible = get_all_possible_classes(grade)
            
            selected_classes = st.multiselect(
                "最終班級列表:",
                options=all_possible,
                key="active_classes"  # 雙向綁定
            )
            
            input_class_str = ",".join(selected_classes)
            input_note = st.text_input("備註", value=current_form['note'])

            # 按鈕
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
                    st.session_state['data'].at[idx, "勾選"] = False 
                    
                    st.session_state['edit_index'] = None
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
        
        # 資料編輯器 (冊次加寬為 large)
        edited_df = st.data_editor(
            st.session_state['data'],
            num_rows="dynamic",
            use_container_width=True,
            height=600,
            column_config={
                "勾選": st.column_config.CheckboxColumn("勾選", width="small"),
                "課程類別": st.column_config.SelectboxColumn("類別", options=["部定必修", "校訂必修", "校訂選修", "實習科目", "一般科目"], width="small"),
                "冊次(1)": st.column_config.SelectboxColumn("冊次", options=["全", "上", "下", "I", "II", "III", "IV", "V", "VI"], width="large"),
                "冊次(2)": st.column_config.SelectboxColumn("冊次(2)", options=["全", "上", "下", "I", "II", "III", "IV", "V", "VI"], width="large"),
                "適用班級": st.column_config.TextColumn("適用班級", width="medium"),
            }
        )

        # --- 邏輯：單選互斥 與 資料載入 ---
        current_checked = edited_df[edited_df["勾選"] == True].index.tolist()
        
        if len(current_checked) > 0:
            prev_idx = st.session_state.get('edit_index')
            target_idx = current_checked[0]
            if len(current_checked) > 1:
                new_ones = [i for i in current_checked if i != prev_idx]
                if new_ones: target_idx = new_ones[0]
                
            if target_idx != prev_idx:
                st.session_state['data']["勾選"] = False
                st.session_state['data'].at[target_idx, "勾選"] = True
                
                st.session_state['edit_index'] = target_idx
                
                row_data = st.session_state['data'].iloc[target_idx]
                st.session_state['form_data'] = {
                    'course': row_data["課程名稱"],
                    'book1': row_data["教科書(優先1)"], 'vol1': row_data["冊次(1)"], 'pub1': row_data["出版社(1)"],
                    'book2': row_data["教科書(優先2)"], 'vol2': row_data["冊次(2)"], 'pub2': row_data["出版社(2)"],
                    'note': row_data["備註"]
                }
                
                class_str = str(row_data["適用班級"])
                class_list = [c.strip() for c in class_str.replace("，", ",").split(",") if c.strip()]
                valid_classes = get_all_possible_classes(grade)
                final_list = [c for c in class_list if c in valid_classes]
                
                st.session_state['active_classes'] = final_list
                st.session_state['cb_all'] = False
                st.session_state['cb_reg'] = False
                st.session_state['cb_prac'] = False
                st.session_state['cb_coop'] = False
                
                st.rerun()
        
        else:
            if st.session_state.get('edit_index') is not None:
                st.session_state['edit_index'] = None
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
