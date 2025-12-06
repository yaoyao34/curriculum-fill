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
    # ... (下略，邏輯不變) ...
    mask_curr = (df_curr['科別'] == dept) & (df_curr['學期'] == semester) & (df_curr['年級'] == grade)
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
        sub_matches = pd.DataFrame()
        if not df_sub.empty:
             mask_sub = (df_sub['科別'] == dept) & (df_sub['學期'] == semester) & (df_sub['年級'] == grade) & (df_sub['課程名稱'] == c_name)
             sub_matches = df_sub[mask_sub]

        if not sub_matches.empty:
            for _, s_row in sub_matches.iterrows():
                display_rows.append({
                    "科別": dept, "年級": grade, "學期": semester,
                    "課程類別": c_type, "課程名稱": c_name,
                    "教科書(優先1)": s_row.get('教科書(優先1)', '') or s_row.get('教科書(1)', ''), 
                    "冊次(1)": s_row.get('冊次(1)', ''), 
                    "出版社(1)": s_row.get('出版社(1)', ''), 
                    "審定字號(1)": s_row.get('審定字號(1)', '') or s_row.get('字號(1)', ''),
                    "教科書(優先2)": s_row.get('教科書(優先2)', '') or s_row.get('教科書(2)', ''), 
                    "冊次(2)": s_row.get('冊次(2)', ''), 
                    "出版社(2)": s_row.get('出版社(2)', ''), 
                    "審定字號(2)": s_row.get('審定字號(2)', '') or s_row.get('字號(2)', ''),
                    "適用班級": s_row.get('適用班級', default_class), "備註": s_row.get('備註', '')
                })
        else:
            # 優先級 2: 檢查 History
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
                # 優先級 3: 空白列
                display_rows.append({
                    "科別": dept, "年級": grade, "學期": semester,
                    "課程類別": c_type, "課程名稱": c_name,
                    "教科書(優先1)": "", "冊次(1)": "", "出版社(1)": "", "審定字號(1)": "",
                    "教科書(優先2)": "", "冊次(2)": "", "出版社(2)": "", "審定字號(2)": "",
                    "適用班級": default_class, "備註": ""
                })

    return pd.DataFrame(display_rows)
