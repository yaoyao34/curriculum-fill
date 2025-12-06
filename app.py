import streamlit as st
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
import datetime
import json
import base64

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
        ws_sub = sh.worksheet(SHEET_SUBMISSION)
        
        def get_df(ws):
            data = ws.get_all_values()
            if not data: return pd.DataFrame()
            headers = data[0]
            rows = data[1:]
            seen = {}
            new_headers = []
            for col in headers:
                c = str(col).strip()
                if c in seen:
                    seen[c] += 1
                    new_name = f"{c}({seen[c]})"
                    if c == '教科書': new_name = f"教科書(優先{seen[c]})"
                    if c == '冊次': new_name = f"冊次({seen[c]})"
                    if c == '出版社': new_name = f"出版社({seen[c]})"
                    if c == '字號' or c == '審定字號': new_name = f"審定字號({seen[c]})"
                    new_headers.append(new_name)
                else:
                    seen[c] = 1
                    if c == '教科書': new_headers.append('教科書(優先1)')
                    elif c == '冊次': new_headers.append('冊次(1)')
                    elif c == '出版社': new_headers.append('出版社(1)')
                    elif c == '字號' or c == '審定字號': new_headers.append('審定字號(1)')
                    else: new_headers.append(c)
            return pd.DataFrame(rows, columns=new_headers)

        df_curr = get_df(ws_curr)
        df_hist = get_df(ws_hist)
        df_sub = get_df(ws_sub)
        
        for df in [df_curr, df_hist, df_sub]:
            if not df.empty:
                if '年級' in df.columns: df['年級'] = df['年級'].astype(str)
                if '學期' in df.columns: df['學期'] = df['學期'].astype(str)
                
    except Exception as e:
        st.error(f"讀取錯誤: {e}")
        return pd.DataFrame()

    mask_curr = (df_curr['科別'] == dept) & (df_curr['學期'] == str(semester)) & (df_curr['年級'] == str(grade))
    target_courses = df_curr[mask_curr]

    if target_courses.empty:
        return pd.DataFrame()

    display_rows = []
    
    for _, row in target_courses.iterrows():
        c_name = row['課程名稱']
        c_type = row['課程類別']
        default_class = row.get('預設適用班級', '') 
        
        sub_matches = pd.DataFrame()
        if not df_sub.empty:
             mask_sub = (df_sub['科別'] == dept) & (df_sub['學期'] == str(semester)) & (df_sub['年級'] == str(grade)) & (df_sub['課程名稱'] == c_name)
             sub_matches = df_sub[mask_sub]

        if not sub_matches.empty:
            for _, s_row in sub_matches.iterrows():
                display_rows.append({
                    "勾選": False,
                    "科別": dept, "年級": grade, "學期": semester,
                    "課程類別": c_type, "課程名稱": c_name,
                    "適用班級": s_row.get('適用班級', default_class),
                    "教科書(優先1)": s_row.get('教科書(優先1)', '') or s_row.get('教科書(1)', ''), 
                    "冊次(1)": s_row.get('冊次(1)', ''), 
                    "出版社(1)": s_row.get('出版社(1)', ''), 
                    "審定字號(1)": s_row.get('審定字號(1)', '') or s_row.get('字號(1)', ''),
                    "教科書(優先2)": s_row.get('教科書(優先2)', '') or s_row.get('教科書(2)', ''), 
                    "冊次(2)": s_row.get('冊次(2)', ''), 
                    "出版社(2)": s_row.get('出版社(2)', ''), 
                    "審定字號(2)": s_row.get('審定字號(2)', '') or s_row.get('字號(2)', ''),
                    "備註": s_row.get('備註', '')
                })
        else:
            hist_matches = df_hist[df_hist['課程名稱'] == c_name]

            if not hist_matches.empty:
                # 嘗試找完全對應班級的
                exact_match = hist_matches[hist_matches['適用班級'] == default_class]
                
                if not exact_match.empty:
                    target_rows = exact_match
                else:
                    target_rows = hist_matches

                for _, h_row in target_rows.iterrows():
                    hist_class = h_row.get('適用班級', '')
                    final_class = hist_class if hist_class else default_class
                    
                    display_rows.append({
                        "勾選": False,
                        "科別": dept, "年級": grade, "學期": semester,
                        "課程類別": c_type, "課程名稱": c_name,
                        "適用班級": final_class,
                        "教科書(優先1)": h_row.get('教科書(優先1)', ''), "冊次(1)": h_row.get('冊次(1)', ''), "出版社(1)": h_row.get('出版社(1)', ''), "審定字號(1)": h_row.get('審定字號(1)', ''),
                        "教科書(優先2)": h_row.get('教科書(優先2)', ''), "冊次(2)": h_row.get('冊次(2)', ''), "出版社(2)": h_row.get('出版社(2)', ''), "審定字號(2)": h_row.get('審定字號(2)', ''),
                        "備註": h_row.get('備註', '')
                    })
            else:
                display_rows.append({
                    "勾選": False,
                    "科別": dept, "年級": grade, "學期": semester,
                    "課程類別": c_type, "課程名稱": c_name,
                    "適用班級": default_class,
                    "教科書(優先1)": "", "冊次(1)": "", "出版社(1)": "", "審定字號(1)": "",
                    "教科書(優先2)": "", "冊次(2)": "", "出版社(2)": "", "審定字號(2)": "",
                    "備註": ""
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
        ws_sub.append_row(["填報時間", "科別", "年級", "學期", "課程名稱", "教科書(1)", "冊次(1)", "出版社(1)", "字號(1)", "教科書(2)", "冊次(2)", "出版社(2)", "字號(2)", "適用班級", "備註"])

    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    data_list = []
    
    for col in ["教科書(優先1)", "冊次(1)", "出版社(1)", "審定字號(1)", "教科書(優先2)", "冊次(2)", "出版社(2)", "審定字號(2)", "適用班級", "備註"]:
        if col not in df_to_save.columns: df_to_save[col] = ""

    for _, row in df_to_save.iterrows():
        data_list.append([
            timestamp, 
            row['科別'], row['年級'], row['學期'], row['課程名稱'],
            row['教科書(優先1)'], row['冊次(1)'], row['出版社(1)'], row['審定字號(1)'],
            row['教科書(優先2)'], row['冊次(2)'], row['出版社(2)'], row['審定字號(2)'],
            row['適用班級'], row['備註']
        ])
    ws_sub.append_rows(data_list)
    return True

# --- 5. 產生 HTML 報表 ---
def create_html_report(df, dept, grade, semester):
    html = f"""
    <html>
    <head>
        <title>{dept} {grade}年級 第{semester}學期 教科書選用表</title>
        <style>
            body {{ font-family: 'Microsoft JhengHei', sans-serif; padding: 20px; }}
            h2 {{ text-align: center; }}
            table {{ width: 100%; border-collapse: collapse; margin-top: 20px; }}
            th, td {{ border: 1px solid black; padding: 8px; text-align: center; font-size: 12px; }}
            th {{ background-color: #f2f2f2; }}
            .footer {{ margin-top: 20px; text-align: right; }}
        </style>
    </head>
    <body>
        <h2>{dept} {grade}年級 第{semester}學期 教科書選用表</h2>
        <p>列印時間：{datetime.datetime.now().strftime("%Y-%m-%d %H:%M")}</p>
        <table>
            <thead>
                <tr>
                    <th>課程名稱</th><th>適用班級</th>
                    <th>教科書(1)</th><th>冊次</th><th>出版社</th><th>字號</th>
                    <th>教科書(2)</th><th>冊次</th><th>出版社</th><th>字號</th>
                    <th>備註</th>
                </tr>
            </thead>
            <tbody>
    """
    for _, row in df.iterrows():
        html += f"""
            <tr>
                <td>{row['課程名稱']}</td>
                <td>{row['適用班級']}</td>
                <td>{row.get('教科書(優先1)', '')}</td>
                <td>{row.get('冊次(1)', '')}</td>
                <td>{row.get('出版社(1)', '')}</td>
                <td>{row.get('審定字號(1)', '')}</td>
                <td>{row.get('教科書(優先2)', '')}</td>
                <td>{row.get('冊次(2)', '')}</td>
                <td>{row.get('出版社(2)', '')}</td>
                <td>{row.get('審定字號(2)', '')}</td>
                <td>{row.get('備註', '')}</td>
            </tr>
        """
    html += """
            </tbody>
        </table>
        <div class="footer">
            <p>填表人簽章：____________________</p>
            <p>科主任簽章：____________________</p>
        </div>
    </body>
    </html>
    """
    return html

# --- 6. 班級計算邏輯 ---
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

# --- 7. Callbacks ---
def update_class_list_from_checkboxes():
    dept = st.session_state.get('dept_val')
    grade = st.session_state.get('grade_val')
    # 這裡必須讀取 class_multiselect 來確保同步
    current_list = list(st.session_state.get('class_multiselect', []))
    
    for sys_key, sys_name in [('cb_reg', '普通科'), ('cb_prac', '實用技能班'), ('cb_coop', '建教班')]:
        is_checked = st.session_state[sys_key]
        target_classes = get_target_classes_for_dept(dept, grade, sys_name)
        if is_checked:
            for c in target_classes:
                if c not in current_list: current_list.append(c)
        else:
            for c in target_classes:
                if c in current_list: current_list.remove(c)
    
    # 關鍵修正：更新 active_classes 並直接更新 widget key
    new_list = sorted(list(set(current_list)))
    st.session_state['active_classes'] = new_list
    st.session_state['class_multiselect'] = new_list  # 強制同步 Widget
    
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

def on_multiselect_change():
    st.session_state['active_classes'] = st.session_state['class_multiselect']

def on_editor_change():
    """當表格勾選變動時觸發"""
    key = f"main_editor_{st.session_state['editor_key_counter']}"
    if key not in st.session_state: return

    edits = st.session_state[key]["edited_rows"]
    
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
        
        # 關鍵修正：將班級字串解析並正確填入
        class_str = str(row_data.get("適用班級", ""))
        class_list = [c.strip() for c in class_str.replace("，", ",").split(",") if c.strip()]
        
        grade = st.session_state.get('grade_val')
        valid_classes = get_all_possible_classes(grade) if grade else []
        final_list = [c for c in class_list if c in valid_classes]
        
        # 關鍵：同時更新變數與 Widget Key
        st.session_state['active_classes'] = final_list
        st.session_state['class_multiselect'] = final_list 
        
        st.session_state['cb_reg'] = False
        st.session_state['cb_prac'] = False
        st.session_state['cb_coop'] = False
        st.session_state['cb_all'] = False
    
    else:
        current_idx = st.session_state.get('edit_index')
        if current_idx is not None and str(current_idx) in edits:
             if edits[str(current_idx)].get("勾選") is False:
                 st.session_state['data'].at[current_idx, "勾選"] = False
                 st.session_state['edit_index'] = None

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
        
        # 預設：根據科別自動勾選學制
        # 判斷是否為專業科系
        if dept in DEPT_SPECIFIC_CONFIG:
            # 專業科系：預設勾選普通/實技/建教 (視該科有無)
            # 這裡簡化為預設全勾，讓 update_class_list 去過濾
            st.session_state['cb_reg'] = True
            st.session_state['cb_prac'] = True
            st.session_state['cb_coop'] = True
            st.session_state['cb_all'] = True
        else:
            # 共同科目：預設全勾 (全校)
            st.session_state['cb_reg'] = True
            st.session_state['cb_prac'] = True
            st.session_state['cb_coop'] = True
            st.session_state['cb_all'] = True
            
        update_class_list_from_checkboxes()
        st.session_state['editor_key_counter'] += 1

# --- 8. 主程式 ---
def main():
    st.set_page_config(page_title="教科書填報系統", layout="wide")
    st.title("📚 教科書填報系統")

    st.markdown("""
        <style>
        html, body, [class*="css"] { font-family: 'Segoe UI', sans-serif; }
        div[data-testid="stDataEditor"] { background-color: #ffffff !important; }
        div[data-testid="stDataEditor"] table td {
            font-size: 18px !important;
            color: #000000 !important;
            background-color: #ffffff !important;
            white-space: pre-wrap !important;
            word-wrap: break-word !important;
            vertical-align: top !important;
            height: auto !important;
            min-height: 60px !important;
            line-height: 1.6 !important;
            border-bottom: 1px solid #e0e0e0 !important;
            opacity: 1 !important;
        }
        div[data-testid="stDataEditor"] table td[aria-disabled="true"],
        div[data-testid="stDataEditor"] table td[data-disabled="true"] {
            color: #000000 !important; 
            -webkit-text-fill-color: #000000 !important;
            background-color: #ffffff !important;
            opacity: 1 !important;
        }
        div[data-testid="stDataEditor"] table th {
            font-size: 18px !important;
            font-weight: bold !important;
            background-color: #333333 !important;
            color: #ffffff !important;
            border-bottom: 2px solid #000000 !important;
        }
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
    
    if 'editor_key_counter' not in st.session_state: st.session_state['editor_key_counter'] = 0

    with st.sidebar:
        st.header("1. 填報設定")
        dept_options = [
            "建築科", "機械科", "電機科", "製圖科", "室設科", 
            "國文科", "英文科", "數學科", "自然科", "社會科", 
            "資訊科技", "體育科", "國防科", "藝術科", "健護科", "輔導科", "閩南語"
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
                    st.session_state['editor_key_counter'] += 1
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
            
            # 使用 key 和 default 來做雙向綁定
            selected_classes = st.multiselect(
                "最終班級列表:",
                options=all_possible,
                default=st.session_state['active_classes'],
                key="class_multiselect",
                on_change=on_multiselect_change
            )
            
            input_class_str = ",".join(selected_classes)
            input_note = st.text_input("備註", value=current_form['note'])

            if is_edit_mode:
                if st.button("🔄 更新表格", type="primary", use_container_width=True):
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
                    
                    st.session_state['form_data'] = {k: '' for k in st.session_state['form_data']}
                    st.session_state['active_classes'] = []
                    
                    st.session_state['data'].at[idx, "勾選"] = False 
                    st.session_state['edit_index'] = None
                    st.session_state['last_selected_row'] = None 
                    st.session_state['editor_key_counter'] += 1 
                    
                    st.success("更新成功！")
                    st.rerun()
            else:
                if st.button("➕ 加入表格", type="secondary", use_container_width=True):
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
                    st.session_state['editor_key_counter'] += 1
                    
                    st.session_state['form_data'] = {k: '' for k in st.session_state['form_data']}
                    st.session_state['active_classes'] = []
                    
                    st.success(f"已加入：{input_course}")
                    st.rerun()

        st.success(f"目前編輯：**{dept}** / **{grade}年級** / **第{sem}學期**")
        
        edited_df = st.data_editor(
            st.session_state['data'],
            num_rows="dynamic",
            use_container_width=True,
            height=600,
            key=f"main_editor_{st.session_state['editor_key_counter']}",
            on_change=on_editor_change,
            column_config={
                "勾選": st.column_config.CheckboxColumn("勾選", width="small", disabled=False),
                "科別": None, 
                "年級": None, 
                "學期": None,
                "課程類別": st.column_config.TextColumn("類別", width="small", disabled=True),
                "課程名稱": st.column_config.TextColumn("課程名稱", width="medium", disabled=True),
                "適用班級": st.column_config.TextColumn("適用班級", width="medium", disabled=True), 
                "教科書(優先1)": st.column_config.TextColumn("教科書(1)", width="medium", disabled=True), 
                "冊次(1)": st.column_config.TextColumn("冊次", width="small", disabled=True), 
                "出版社(1)": st.column_config.TextColumn("出版社(1)", width="small", disabled=True),
                "審定字號(1)": st.column_config.TextColumn("字號(1)", width="small", disabled=True),
                "教科書(優先2)": st.column_config.TextColumn("教科書(2)", width="medium", disabled=True),
                "冊次(2)": st.column_config.TextColumn("冊次(2)", width="small", disabled=True), 
                "出版社(2)": st.column_config.TextColumn("出版社(2)", width="small", disabled=True),
                "審定字號(2)": st.column_config.TextColumn("字號(2)", width="small", disabled=True),
                "備註": st.column_config.TextColumn("備註", width="medium", disabled=True),
            },
            column_order=[
                "勾選", "課程類別", "課程名稱", "適用班級",
                "教科書(優先1)", "冊次(1)", "出版社(1)", "審定字號(1)",
                "教科書(優先2)", "冊次(2)", "出版社(2)", "審定字號(2)",
                "備註"
            ]
        )

        col_submit, _ = st.columns([1, 4])
        with col_submit:
            if st.button("💾 存檔並轉 PDF (下載 HTML 報表)", type="primary", use_container_width=True):
                if st.session_state['data'].empty:
                    st.error("表格是空的")
                else:
                    with st.spinner("寫入資料庫並產生報表..."):
                        save_submission(st.session_state['data'])
                        html_report = create_html_report(st.session_state['data'], dept, grade, sem)
                        b64 = base64.b64encode(html_report.encode('utf-8')).decode()
                        href = f'<a href="data:text/html;base64,{b64}" download="{dept}_{grade}年級_第{sem}學期_教科書報表.html" style="text-decoration:none; color:white; background-color:#b31412; padding:10px 20px; border-radius:5px; font-weight:bold;">📄 點此下載報表 (請開啟後按 Ctrl+P 存為 PDF)</a>'
                        st.markdown(href, unsafe_allow_html=True)
                        st.success("✅ 資料已存檔！請點擊上方按鈕下載報表。")

    else:
        st.info("👈 請先在左側選擇科別")

if __name__ == "__main__":
    main()
