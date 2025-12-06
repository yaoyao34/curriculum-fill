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

# --- 2. 資料讀取 (核心修正：以 Curriculum 為主，History 為輔) ---
def load_data(dept, semester, grade):
    client = get_connection()
    if not client: return pd.DataFrame()
    try:
        sh = client.open(SPREADSHEET_NAME)
        # 讀取三個分頁
        ws_curr = sh.worksheet(SHEET_CURRICULUM)
        ws_hist = sh.worksheet(SHEET_HISTORY)
        ws_sub = sh.worksheet(SHEET_SUBMISSION) # 讀取提交紀錄
        
        # --- 修正讀取邏輯：處理重複標頭 ---
        # 直接使用 get_all_values() 讀取原始資料 (List of Lists)，避開 Pandas 對重複 header 的檢查
        # 然後手動處理第一列作為 header
        def get_df_from_worksheet(ws):
            data = ws.get_all_values()
            if not data: return pd.DataFrame()
            
            headers = data[0]
            rows = data[1:]
            
            # 手動重新命名重複的 header
            # 例如遇到第二個 '冊次' 改為 '冊次(2)'
            seen_counts = {}
            new_headers = []
            for col in headers:
                col = str(col).strip() # 去除空白
                if col in seen_counts:
                    seen_counts[col] += 1
                    # 如果原本是 "冊次"，第二次出現就變成 "冊次(2)"
                    # 如果您原本的 CSV 已經叫 "冊次(2)" 就不會進來這裡，這是為了防呆
                    new_name = f"{col}({seen_counts[col]})"
                    # 針對常見欄位做優化命名，對應我們程式碼的邏輯
                    if col == '教科書': new_name = f"教科書(優先{seen_counts[col]})" # 若原始資料只叫教科書
                    if col == '冊次': new_name = f"冊次({seen_counts[col]})"
                    if col == '出版社': new_name = f"出版社({seen_counts[col]})"
                    if col == '字號' or col == '審定字號': new_name = f"審定字號({seen_counts[col]})"
                    new_headers.append(new_name)
                else:
                    seen_counts[col] = 1
                    # 第一次出現，確保名稱對應程式碼
                    if col == '教科書': new_headers.append('教科書(優先1)')
                    elif col == '冊次': new_headers.append('冊次(1)')
                    elif col == '出版社': new_headers.append('出版社(1)')
                    elif col == '字號' or col == '審定字號': new_headers.append('審定字號(1)')
                    else: new_headers.append(col)
            
            return pd.DataFrame(rows, columns=new_headers)

        df_curr = get_df_from_worksheet(ws_curr)
        df_hist = get_df_from_worksheet(ws_hist)
        df_sub = get_df_from_worksheet(ws_sub)
        
        # 轉型避免錯誤
        for df in [df_curr, df_hist, df_sub]:
            if not df.empty:
                # 確保欄位存在再轉型，避免報錯
                if '年級' in df.columns: df['年級'] = df['年級'].astype(str)
                if '學期' in df.columns: df['學期'] = df['學期'].astype(str)
                # 確保所有需要的欄位都存在，若無則補空值
                for col in ['教科書(優先1)', '冊次(1)', '出版社(1)', '審定字號(1)', '教科書(優先2)', '冊次(2)', '出版社(2)', '審定字號(2)', '備註', '適用班級']:
                    if col not in df.columns: df[col] = ""

    except Exception as e:
        st.error(f"讀取錯誤: {e}")
        return pd.DataFrame()

    # 1. 篩選課綱 (Curriculum) - 這是基準，一定要有這些課
    # 使用字串比對，避免數字型別問題
    mask_curr = (df_curr['科別'] == dept) & (df_curr['學期'] == str(semester)) & (df_curr['年級'] == str(grade))
    target_courses = df_curr[mask_curr]

    if target_courses.empty:
        return pd.DataFrame()

    display_rows = []
    
    # 2. 針對每一門「課綱」中的課，去查找資料
    for _, row in target_courses.iterrows():
        c_name = row['課程名稱']
        c_type = row['課程類別']
        default_class = row.get('預設適用班級', '') # Curriculum 預設班級

        # 優先級 1: 檢查 Submission (本學期是否已填報過)
        # 邏輯：如果這學期已經有人送出過這門課的資料，就顯示最後一次送出的內容
        sub_matches = pd.DataFrame()
        if not df_sub.empty:
             mask_sub = (df_sub['科別'] == dept) & (df_sub['學期'] == str(semester)) & (df_sub['年級'] == str(grade)) & (df_sub['課程名稱'] == c_name)
             sub_matches = df_sub[mask_sub]

        if not sub_matches.empty:
            # 如果有提交紀錄，使用提交紀錄 (可能有多筆，全部列出)
            for _, s_row in sub_matches.iterrows():
                display_rows.append({
                    "科別": dept, "年級": grade, "學期": semester,
                    "課程類別": c_type, "課程名稱": c_name,
                    # Submission 的欄位名稱可能跟我們手動改的不一樣，這裡做相容處理
                    "教科書(優先1)": s_row.get('教科書(優先1)', '') or s_row.get('教科書(1)', ''), 
                    "冊次(1)": s_row.get('冊次(1)', ''), 
                    "出版社(1)": s_row.get('出版社(1)', ''), 
                    "審定字號(1)": s_row.get('審定字號(1)', '') or s_row.get('字號(1)', ''), # Submission 可能叫 字號(1)
                    "教科書(優先2)": s_row.get('教科書(優先2)', '') or s_row.get('教科書(2)', ''), 
                    "冊次(2)": s_row.get('冊次(2)', ''), 
                    "出版社(2)": s_row.get('出版社(2)', ''), 
                    "審定字號(2)": s_row.get('審定字號(2)', '') or s_row.get('字號(2)', ''),
                    "適用班級": s_row.get('適用班級', default_class), "備註": s_row.get('備註', '')
                })
        else:
            # 優先級 2: 檢查 History (是否有歷史資料)
            # 注意：History 通常只對應「課程名稱」，不一定對應年級/學期 (因為可能換年級開)
            hist_matches = df_hist[df_hist['課程名稱'] == c_name]

            if not hist_matches.empty:
                # 如果有歷史資料，全部列出 (例如測量實習有兩本)
                for _, h_row in hist_matches.iterrows():
                    display_rows.append({
                        "科別": dept, "年級": grade, "學期": semester,
                        "課程類別": c_type, "課程名稱": c_name,
                        "教科書(優先1)": h_row.get('教科書(優先1)', ''), "冊次(1)": h_row.get('冊次(1)', ''), "出版社(1)": h_row.get('出版社(1)', ''), "審定字號(1)": h_row.get('審定字號(1)', ''),
                        "教科書(優先2)": h_row.get('教科書(優先2)', ''), "冊次(2)": h_row.get('冊次(2)', ''), "出版社(2)": h_row.get('出版社(2)', ''), "審定字號(2)": h_row.get('審定字號(2)', ''),
                        "適用班級": h_row.get('適用班級', default_class), "備註": h_row.get('備註', '')
                    })
            else:
                # 優先級 3: 完全沒資料，顯示空白列 (這就是修正的關鍵！)
                display_rows.append({
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
        # 修正標題列，確保有兩個字號欄位，且名稱不重複 (這很重要，對應上面的 load_data)
        ws_sub.append_row(["填報時間", "科別", "年級", "學期", "課程名稱", "教科書(1)", "冊次(1)", "出版社(1)", "字號(1)", "教科書(2)", "冊次(2)", "出版社(2)", "字號(2)", "適用班級", "備註"])

    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    data_list = []
    
    # 確保 DataFrame 中有所有需要的欄位，避免 KeyError
    expected_cols = ["科別", "年級", "學期", "課程名稱", "教科書(優先1)", "冊次(1)", "出版社(1)", "審定字號(1)", "教科書(優先2)", "冊次(2)", "出版社(2)", "審定字號(2)", "適用班級", "備註"]
    for col in expected_cols:
        if col not in df_to_save.columns:
            df_to_save[col] = "" # 若缺欄位則補空值

    for _, row in df_to_save.iterrows():
        data_list.append([
            timestamp, 
            row['科別'], row['年級'], row['學期'], row['課程名稱'],
            row['教科書(優先1)'], row['冊次(1)'], row['出版社(1)'], row['審定字號(1)'],
            row['教科書(優先2)'], row['冊次(2)'], row['出版社(2)'], row['審定字號(2)'], # 這裡確保寫入 字號(2)
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
    prefix = {"1": "一", "2": "二", "3": "三"}.get(str(grade), "")
    if not prefix: return []
    suffixes = []
    if dept in DEPT_SPECIFIC_CONFIG:
        suffixes = DEPT_SPECIFIC_CONFIG[dept].get(sys_name, [])
    else:
        suffixes = ALL_SUFFIXES.get(sys_name, [])
    if str(grade) == "3" and sys_name == "建教班": return []
    return [f"{prefix}{s}" for s in suffixes]

# --- 6. Callbacks ---
def update_class_list_from_checkboxes():
    dept = st.session_state.get('dept_val')
    grade = st.session_state.get('grade_val')
    current_list = list(st.session_state['active_classes'])
    
    for sys_key, sys_name in [('cb_reg', '普通科'), ('cb_prac', '實用技能班'), ('cb_coop', '建教班')]:
        is_checked = st.session_state[sys_key]
        target_classes = get_target_classes_for_dept(dept, grade, sys_name)
        if is_checked:
            for c in target_classes:
                if c not in current_list: current_list.append(c)
        else:
            for c in target_classes:
                if c in current_list: current_list.remove(c)
    
    st.session_state['active_classes'] = sorted(list(set(current_list)))
    
    if st.session_state['cb_reg'] and st.session_state['cb_prac'] and st.session_state['cb_coop']:
        st.session_state['cb_all'] = True
    else:
        st.session_state['cb_all'] = False

def toggle_all_checkboxes():
    new_state = st.session_state['cb_all']
    st.session_state['cb_reg'] = new_state
    st.session_state['cb_prac'] = new_state
    st.session_state['cb_coop'] = new_state
    update_class_list_from_checkboxes()

def on_editor_change():
    """表格編輯/勾選變動時觸發"""
    edits = st.session_state["main_editor"]["edited_rows"]
    
    target_idx = None
    for idx, changes in edits.items():
        if "勾選" in changes and changes["勾選"] is True:
            target_idx = int(idx)
            break
            
    if target_idx is not None:
        st.session_state['data']["勾選"] = False
        st.session_state['data'].at[target_idx, "勾選"] = True
        st.session_state['edit_index'] = target_idx
        
        row_data = st.session_state['data'].iloc[target_idx]
        st.session_state['form_data'] = {
            'course': row_data["課程名稱"],
            'book1': row_data.get("教科書(優先1)", ""), 'vol1': row_data.get("冊次(1)", ""), 'pub1': row_data.get("出版社(1)", ""), 'code1': row_data.get("審定字號(1)", ""),
            'book2': row_data.get("教科書(優先2)", ""), 'vol2': row_data.get("冊次(2)", ""), 'pub2': row_data.get("出版社(2)", ""), 'code2': row_data.get("審定字號(2)", ""),
            'note': row_data.get("備註", "")
        }
        
        class_str = str(row_data.get("適用班級", ""))
        class_list = [c.strip() for c in class_str.replace("，", ",").split(",") if c.strip()]
        grade = st.session_state.get('grade_val')
        valid_classes = get_all_possible_classes(grade) if grade else []
        final_list = [c for c in class_list if c in valid_classes]
        
        st.session_state['active_classes'] = final_list
        st.session_state['cb_reg'] = False
        st.session_state['cb_prac'] = False
        st.session_state['cb_coop'] = False
        st.session_state['cb_all'] = False

def auto_load_data():
    dept = st.session_state.get('dept_val')
    sem = st.session_state.get('sem_val')
    grade = st.session_state.get('grade_val')
    
    if dept and sem and grade:
        df = load_data(dept, sem, grade)
        st.session_state['data'] = df
        st.session_state['loaded'] = True
        st.session_state['edit_index'] = None
        st.session_state['active_classes'] = []
        st.session_state['cb_reg'] = True
        st.session_state['cb_prac'] = False
        st.session_state['cb_coop'] = False
        st.session_state['cb_all'] = False
        update_class_list_from_checkboxes()

# --- 7. 主程式 ---
def main():
    st.set_page_config(page_title="教科書填報系統", layout="wide")
    st.title("📚 教科書填報系統")

    # --- CSS 注入：強制表格換行與增高，並放大字體 ---
    st.markdown("""
        <style>
        /* 全域文字放大 */
        html, body, [class*="css"] {
            font-family: 'Segoe UI', sans-serif;
        }
        
        /* 1. 表格主體 - 強制白色背景 */
        div[data-testid="stDataEditor"] {
            background-color: #ffffff !important;
        }
        
        /* 2. 資料儲存格 (td) - 強制樣式 */
        div[data-testid="stDataEditor"] table td {
            font-size: 18px !important;       /* 字體加大 */
            color: #000000 !important;        /* 強制純黑色字體 */
            background-color: #ffffff !important; /* 強制純白背景 */
            white-space: pre-wrap !important; /* 強制換行 */
            word-wrap: break-word !important; /* 長單字斷行 */
            vertical-align: top !important;   /* 內容置頂 */
            height: auto !important;          /* 高度自適應 */
            min-height: 60px !important;      /* 最小高度 */
            line-height: 1.6 !important;
            border-bottom: 1px solid #e0e0e0 !important;
            opacity: 1 !important;            /* 取消透明度 */
        }
        
        /* 3. 針對 disabled (唯讀) 欄位 */
        div[data-testid="stDataEditor"] table td[aria-disabled="true"],
        div[data-testid="stDataEditor"] table td[data-disabled="true"] {
            color: #000000 !important; 
            -webkit-text-fill-color: #000000 !important;
            background-color: #ffffff !important;
            opacity: 1 !important;
        }
        
        /* 4. 表頭 (th) */
        div[data-testid="stDataEditor"] table th {
            font-size: 18px !important;
            font-weight: bold !important;
            background-color: #333333 !important;
            color: #ffffff !important;
            border-bottom: 2px solid #000000 !important;
        }
        
        /* 5. 隱藏 index */
        thead tr th:first-child { display: none }
        tbody th { display: none }
        </style>
    """, unsafe_allow_html=True)

    if 'edit_index' not in st.session_state: st.session_state['edit_index'] = None
    if 'active_classes' not in st.session_state: st.session_state['active_classes'] = []
    if 'form_data' not in st.session_state:
        st.session_state['form_data'] = {
            'course': '', 'book1': '', 'vol1': '全', 'pub1': '', 'code1': '',
            'book2': '', 'vol2': '全', 'pub2': '', 'code2': '', 'note': ''
        }
    if 'cb_all' not in st.session_state: st.session_state['cb_all'] = False
    if 'cb_reg' not in st.session_state: st.session_state['cb_reg'] = False
    if 'cb_prac' not in st.session_state: st.session_state['cb_prac'] = False
    if 'cb_coop' not in st.session_state: st.session_state['cb_coop'] = False
    if 'last_selected_row' not in st.session_state: st.session_state['last_selected_row'] = None

    with st.sidebar:
        st.header("1. 填報設定")
        dept_options = [
            "建築科", "機械科", "電機科", "製圖科", "室設科", 
            "國文科", "英文科", "數學科", "自然科", "社會科", 
            "資訊科技", "體育科", "國防科", "藝能科", "健護科", "輔導科", "閩南語"
        ]
        
        dept = st.selectbox("科別", dept_options, key='dept_val', on_change=auto_load_data)
        col1, col2 = st.columns(2)
        with col1: sem = st.selectbox("學期", ["1", "2", "寒", "暑"], key='sem_val', on_change=auto_load_data)
        with col2: grade = st.selectbox("年級", ["1", "2", "3"], key='grade_val', on_change=auto_load_data)
        
        if st.button("🔄 手動重載", type="secondary", use_container_width=True):
            auto_load_data()

    if 'loaded' not in st.session_state and dept and sem and grade:
        auto_load_data()

    if st.session_state.get('loaded'):
        
        with st.sidebar:
            st.divider()
            is_edit_mode = st.session_state['edit_index'] is not None
            header_text = f"2. 修改第 {st.session_state['edit_index'] + 1} 列" if is_edit_mode else "2. 新增/插入課程"
            st.subheader(header_text)
            
            if is_edit_mode:
                if st.button("❌ 取消修改", type="secondary"):
                    st.session_state['edit_index'] = None
                    st.session_state['data']["勾選"] = False
                    st.rerun()

            current_form = st.session_state['form_data']

            course_list = get_course_list()
            course_index = 0
            if is_edit_mode and current_form['course'] in course_list:
                course_index = course_list.index(current_form['course'])
            
            if course_list:
                input_course = st.selectbox("選擇課程", course_list, index=course_index)
            else:
                input_course = st.text_input("課程名稱", value=current_form['course'])
            
            st.markdown("**第一優先**")
            input_book1 = st.text_input("書名", value=current_form['book1'])
            bc1, bc2 = st.columns([1, 2])
            vol_opts = ["全", "上", "下", "I", "II", "III", "IV", "V", "VI"]
            vol1_idx = vol_opts.index(current_form['vol1']) if current_form['vol1'] in vol_opts else 0
            with bc1: input_vol1 = st.selectbox("冊次", vol_opts, index=vol1_idx)
            with bc2: input_pub1 = st.text_input("出版社", value=current_form['pub1'])
            input_code1 = st.text_input("審定字號", value=current_form['code1']) 

            st.markdown("**第二優先**")
            input_book2 = st.text_input("備選書名", value=current_form['book2'])
            bc3, bc4 = st.columns([1, 2])
            vol2_idx = vol_opts.index(current_form['vol2']) if current_form['vol2'] in vol_opts else 0
            with bc3: input_vol2 = st.selectbox("冊次(2)", vol_opts, index=vol2_idx)
            with bc4: input_pub2 = st.text_input("出版社(2)", value=current_form['pub2'])
            input_code2 = st.text_input("審定字號(2)", value=current_form['code2']) 
            
            st.markdown("##### 適用班級")
            st.caption("👇 勾選學制 (勾'全部'選全校)")
            
            c_all, c1, c2, c3 = st.columns([1, 1, 1, 1])
            with c_all: st.checkbox("全部", key="cb_all", on_change=toggle_all_checkboxes)
            with c1: st.checkbox("普通", key="cb_reg", on_change=update_class_list_from_checkboxes)
            with c2: st.checkbox("實技", key="cb_prac", on_change=update_class_list_from_checkboxes)
            with c3: st.checkbox("建教", key="cb_coop", on_change=update_class_list_from_checkboxes)
            
            st.caption("👇 點選加入其他班級")
            all_possible = get_all_possible_classes(grade)
            
            selected_classes = st.multiselect(
                "最終班級列表:",
                options=all_possible,
                key="active_classes"
            )
            
            input_class_str = ",".join(selected_classes)
            input_note = st.text_input("備註", value=current_form['note'])

            if is_edit_mode:
                if st.button("🔄 更新表格", type="primary", use_container_width=True):
                    if not input_book1 or not input_pub1:
                        st.error("⚠️ 書名和出版社為必填！")
                    else:
                        idx = st.session_state['edit_index']
                        st.session_state['data'].at[idx, "課程名稱"] = input_course
                        st.session_state['data'].at[idx, "教科書(優先1)"] = input_book1
                        st.session_state['data'].at[idx, "冊次(1)"] = input_vol1
                        st.session_state['data'].at[idx, "出版社(1)"] = input_pub1
                        st.session_state['data'].at[idx, "審定字號(1)"] = input_code1
                        st.session_state['data'].at[idx, "教科書(優先2)"] = input_book2
                        st.session_state['data'].at[idx, "冊次(2)"] = input_vol2
                        st.session_state['data'].at[idx, "出版社(2)"] = input_pub2
                        st.session_state['data'].at[idx, "審定字號(2)"] = input_code2
                        st.session_state['data'].at[idx, "適用班級"] = input_class_str
                        st.session_state['data'].at[idx, "備註"] = input_note
                        
                        st.session_state['edit_index'] = None
                        st.session_state['last_selected_row'] = None 
                        st.success("更新成功！")
                        st.rerun()
            else:
                if st.button("➕ 加入表格", type="secondary", use_container_width=True):
                    if not input_book1 or not input_pub1:
                        st.error("⚠️ 書名和出版社為必填！")
                    else:
                        new_row = {
                            "勾選": False,
                            "科別": dept, "年級": grade, "學期": sem,
                            "課程類別": "部定必修", 
                            "課程名稱": input_course,
                            "教科書(優先1)": input_book1, "冊次(1)": input_vol1, "出版社(1)": input_pub1, "審定字號(1)": input_code1,
                            "教科書(優先2)": input_book2, "冊次(2)": input_vol2, "出版社(2)": input_pub2, "審定字號(2)": input_code2,
                            "適用班級": input_class_str,
                            "備註": input_note
                        }
                        st.session_state['data'] = pd.concat([st.session_state['data'], pd.DataFrame([new_row])], ignore_index=True)
                        st.success(f"已加入：{input_course}")
                        st.rerun()

        st.success(f"目前編輯：**{dept}** / **{grade}年級** / **第{sem}學期**")
        
        edited_df = st.data_editor(
            st.session_state['data'],
            num_rows="dynamic",
            use_container_width=True,
            height=600,
            key="main_editor",
            on_change=on_editor_change,
            column_config={
                "勾選": st.column_config.CheckboxColumn("勾選", width="small", disabled=False),
                "科別": None, 
                "年級": None, 
                "學期": None,
                "課程類別": st.column_config.TextColumn("類別", width="small", disabled=True),
                "課程名稱": st.column_config.TextColumn("課程名稱", width="medium", disabled=True),
                "教科書(優先1)": st.column_config.TextColumn("教科書(1)", width="medium", disabled=True), 
                "冊次(1)": st.column_config.TextColumn("冊次", width="small", disabled=True), 
                "出版社(1)": st.column_config.TextColumn("出版社(1)", width="small", disabled=True),
                "審定字號(1)": st.column_config.TextColumn("字號(1)", width="small", disabled=True),
                "教科書(優先2)": st.column_config.TextColumn("教科書(2)", width="medium", disabled=True),
                "冊次(2)": st.column_config.TextColumn("冊次(2)", width="small", disabled=True), 
                "出版社(2)": st.column_config.TextColumn("出版社(2)", width="small", disabled=True),
                "審定字號(2)": st.column_config.TextColumn("字號(2)", width="small", disabled=True),
                "適用班級": st.column_config.TextColumn("適用班級", width="medium", disabled=True), 
                "備註": st.column_config.TextColumn("備註", width="medium", disabled=True),
            }
        )

        col_submit, _ = st.columns([1, 4])
        with col_submit:
            if st.button("💾 確認提交 (寫入資料庫)", type="primary", use_container_width=True):
                # final_df = st.session_state['data'] # 不需要 drop 勾選了，因為根本沒有這個欄位
                if st.session_state['data'].empty:
                    st.error("表格是空的")
                else:
                    with st.spinner("寫入中..."):
                        if save_submission(st.session_state['data']):
                            st.success("✅ 資料已成功提交！")
                            st.balloons()

    else:
        st.info("👈 請先在左側選擇科別")

if __name__ == "__main__":
    main()
