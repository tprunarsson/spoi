from xml.parsers.expat import model
import pandas as pd
import numpy as np
import re
from pyscipopt import Model, quicksum, SCIP_PARAMEMPHASIS, SCIP_PARAMSETTING
import os
import json
import datetime as datetime
import time
from collections import defaultdict

def round_time_to_nearest_5_minutes(t_str):
    """Round a time string (HH:MM) to the nearest 5 minutes."""
    try:
        h, m = map(int, t_str.split(':'))
        total = h * 60 + m
        rounded = int(round(total / 5.0) * 5)
        h_ = rounded // 60
        m_ = rounded % 60
        return f"{h_:02d}:{m_:02d}"
    except Exception:
        return t_str

def save_solution(result_df, edited_df=None, solution_dir="solutions"):
    os.makedirs(solution_dir, exist_ok=True)
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"solution_{timestamp}.json"
    path = os.path.join(solution_dir, filename)

    df_to_save = result_df.copy()
    for col in ['Byrjun', 'Endir']:
        if col in df_to_save.columns:
            df_to_save[col] = df_to_save[col].apply(round_time_to_nearest_5_minutes)

    full_data = {
        "conditions": edited_df.to_dict(orient="records") if edited_df is not None else [],
        "solution": df_to_save.to_dict(orient="records")
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(full_data, f, ensure_ascii=False, indent=2)

    return path

def list_solutions(solution_dir="solutions"):
    if not os.path.exists(solution_dir):
        return []
    files = [f for f in os.listdir(solution_dir) if f.endswith('.json')]
    files.sort(reverse=True)
    return files

def load_solution(filename, solution_dir="solutions"):
    path = os.path.join(solution_dir, filename)
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    result_df = pd.DataFrame(data.get("solution", []))
    conditions_df = pd.DataFrame(data.get("conditions", []))
    return result_df, conditions_df

def run_scip_optimization(df: pd.DataFrame, kill_callback=None, prev_soln=None) -> pd.DataFrame:
    """
    SCIP version of your optimizer (PySCIPOpt). Same inputs/outputs as your Gurobi function.
    """

    # ------------------- Data Preparation ---------------------
    # 1. Data Cleaning & Splitting Columns
    df = df.copy()
    df['Salur/svæði'] = df['Salur/svæði'].str.split('|').apply(lambda x: set(item.strip() for item in x))

    def split_if_string(val):
        if isinstance(val, str) and ',' in val:
            return [float(x.strip()) for x in val.split(',')]
        elif pd.isna(val) or (isinstance(val, str) and val.strip() == ""):
            return []
        else:
            return [float(val)]
        
    df['Lengd'] = df['Lengd'].apply(split_if_string)
    df['LengdHelgar'] = df['LengdHelgar'].apply(split_if_string)

    # 2. Extract Days and Area
    def extract_days(info):
        days_match = re.search(r'\(([^)]+)\)', info)
        if days_match:
            # Split by comma, slash, or whitespace, then strip
            days = re.split(r'[,/ ]+', days_match.group(1))
            days = [d.strip() for d in days if d.strip()]
        else:
            days = None
        area = re.sub(r'\s*\(.*\)', '', info).strip()
        return area, days

     # First, build area/day mapping per exercise
    svaedi_dagar_df = df[['Æfing','Salur/svæði']].explode('Salur/svæði').reset_index(drop=True)
    svaedi_dagar_df[['Area', 'Days']] = svaedi_dagar_df['Salur/svæði'].apply(lambda x: pd.Series(extract_days(x)))

    # Build a dict: (Æfing, Area) -> allowed days (list)
    svaedi_dagar = svaedi_dagar_df.dropna(subset=['Days']).set_index(['Æfing', 'Area'])['Days'].to_dict()
    svaedi = set(svaedi_dagar_df['Salur/svæði'].str.strip())
    print(f"Debug: Unique areas extracted: {svaedi}")
    aefing = set(svaedi_dagar_df['Æfing'].str.strip())

    # 3. Add Priority Dictionary
    priority_order = dict(zip(df['Æfing'], df.get('Priority', [1]*len(df))))

    # 4. Model Dictionaries
    svaedi_dagar_df = svaedi_dagar_df.dropna(subset=['Days'])
    svaedi_dagar = svaedi_dagar_df.set_index(['Æfing', 'Area'])['Days'].to_dict()
    svaedi_dagar = {key: [day.strip() for day in days] for key, days in svaedi_dagar.items()}

    undan_eftir_df = df[['Æfing','fyrir/undan']].reset_index(drop=True).dropna(subset=['fyrir/undan'])
    undan_eftir_df['sorted_tuple'] = undan_eftir_df.apply(lambda row: tuple(sorted([row['Æfing'], row['fyrir/undan']])), axis=1)
    undan_eftir_df = undan_eftir_df.drop_duplicates(subset=['sorted_tuple']).drop(columns=['sorted_tuple'])
    undan_eftir = dict(zip(undan_eftir_df['Æfing'], undan_eftir_df['fyrir/undan']))

    # 5. Time Utilities
    def time_to_minutes(time_str):
        hours, minutes = map(int, time_str.split(':'))
        return hours * 60 + minutes

    def extract_time_ranges(time_range):
        if '-' in time_range:
            start_time, end_time = time_range.split('-')
            return (time_to_minutes(start_time), time_to_minutes(end_time))
        else:
            start_time = time_to_minutes(time_range)
            return (start_time, start_time)

    class_schedule = {}
    for _, row in df.iterrows():
        time_dict = {}
        for day in ['sun', 'mán', 'þri', 'mið', 'fim', 'fös', 'lau']:
            if day in row and pd.notna(row[day]):
                time_range = row[day]
                time_dict[day] = extract_time_ranges(str(time_range))
        class_schedule[row['Æfing']] = time_dict

    # 6. Conflicts
    def split_conflicts(conflict_str):
        if pd.notna(conflict_str) and conflict_str.strip():
            return [c.strip() for c in conflict_str.split('|')]
        else:
            return []

    conflict_dict = {}
    if 'Árekstur' in df.columns:
        for _, row in df.iterrows():
            exercise = row['Æfing']
            conflicts = split_conflicts(row['Árekstur'])
            if conflicts:
                conflict_dict[exercise] = conflicts

    # 7. Exercises Info
    number_exercises = {
        row['Æfing']: [row['Lengd'], row['LengdHelgar'], row['Æfingarhópar']]
        for _, row in df.iterrows()
    }

    # 8. Sets and Mapping for MIP Model
    E = list(number_exercises.keys())
    D = ['sun', 'mán', 'þri', 'mið', 'fim', 'fös', 'lau']
    Dw = ['sun', 'lau']
    A = svaedi

    e_a = df[['Æfing', 'Salur/svæði']].explode('Salur/svæði').copy()
    e_a['Area'] = e_a['Salur/svæði'].apply(lambda s: extract_days(s)[0])
    e_a = e_a.groupby('Æfing')['Area'].apply(set).to_dict()
    #print(f"Debug: e_a mapping: {e_a}")

    EXsubset = {}
    DXsubset = {}
    for e in E:
        EXsubset[e] = [f"{e} - {i+1}" for i in range(len(number_exercises[e][0]))] + [f"{e} * {i+1}" for i in range(len(number_exercises[e][1]))]
        DXsubset[e] = [dx * number_exercises[e][2] for dx in number_exercises[e][0]] + [dx * number_exercises[e][2] for dx in number_exercises[e][1]]

    EX = [item for sublist in EXsubset.values() for item in sublist]
    DX_values = [value for sublist in DXsubset.values() for value in sublist]
    DX = dict(zip(EX, DX_values))

    # 9. Legal 3-Tuples and UB/LB/CX Construction
    EDA = []
    UB = {}
    LB = {}
    CX = {}

    # (optional but cleaner) keep a set for quick membership:
    areas_by_ex = df[['Æfing','Salur/svæði']].explode('Salur/svæði').assign(
        Area=lambda r: r['Salur/svæði'].apply(lambda s: extract_days(s)[0])
    ).groupby('Æfing')['Area'].apply(set).to_dict()

    # When building EDA / LB / UB:
    for e in E:
        for d in D:
            for a in A:
                allowed_days = svaedi_dagar.get((e, a))     # keys are (e, Area) already
                if allowed_days is None or d in allowed_days:
                    if d in class_schedule[e] and a in areas_by_ex.get(e, set()):
                        for ex in EXsubset[e]:
                            if (d in Dw and '*' in ex) or (d not in Dw and '*' not in ex):
                                EDA.append((ex, d, a))
                                UB[(ex, d, a)] = class_schedule[e][d][1]
                                LB[(ex, d, a)] = class_schedule[e][d][0]
                                if e in conflict_dict:
                                    tmp = [EXsubset[e_] for e_ in conflict_dict[e] if e_ in EXsubset]
                                    if tmp:
                                        CX[ex] = tmp[0]

    if False:
        for e in E:
            for d in D:
                for a in A:
                    allowed_days = svaedi_dagar.get((e, a))
                    # Only proceed if no restriction, or this day is allowed
                    if (allowed_days is None or d in allowed_days):
                        if d in class_schedule[e] and a in e_a[e]:
                            for ex in EXsubset[e]:
                                if d in Dw and '*' in ex:
                                    EDA.append((ex, d, a))
                                if d not in Dw and '*' not in ex:
                                    EDA.append((ex, d, a))
                                UB[(ex, d, a)] = class_schedule[e][d][1]
                                LB[(ex, d, a)] = class_schedule[e][d][0]
                                if e in conflict_dict:
                                    tmp = [EXsubset[e_] for e_ in conflict_dict[e] if e_ in EXsubset]
                                    if tmp:
                                        CX[ex] = tmp[0]

    ekki_deila_svaedi = {
        'A-sal': ['1/3 A-sal-1', '1/3 A-sal-2', '1/3 A-sal-3', '2/3 A-sal'],
        '2/3 A-sal': ['1/3 A-sal-1', '1/3 A-sal-2']
    }
    #print(f"Debug: EDA: {EDA}")
    # Build lookup dict for previous solution
    prev_assignment = {}
    mod_penalty = {}
    modifiedEDA = []
    if prev_soln is not None:
        for _, row in prev_soln.iterrows():
            e = row['Æfing']
            d = row['Dagur']
            a = row['Salur/svæði']
            t = row['Byrjun']
            start_minutes = time_to_minutes(str(t))
            modified = row.get('Modified', False)
            violated = row.get('ViolatedWindow', False)
            penalty = 0 if violated else (100 if modified else 1)
            if modified:
                print(f"Debug: Modified: {e} on {d} in {a} at {t} ({start_minutes} minutes)")
            if violated:
                print(f"Debug: Violated: {e} on {d} in {a} at {t} ({start_minutes} minutes)")
            # Match against all possible sub-EXs
            if not violated:
                for ex in EXsubset.get(e, []):
                    key = (ex, d)
                    if modified:
                        modifiedEDA.append(key)
                        prev_assignment[key] = start_minutes
                        mod_penalty[key] = penalty
        #print(modifiedEDA)
    # ------------------- MIP Model ---------------------

    print("EDA size:", len(EDA))
    for ex in EX:
        cnt = sum(1 for d in D for a in A if (ex, d, a) in EDA)
        if cnt == 0:
            print("NO OPTIONS FOR", ex)


    # ------------------- MIP Model (SCIP) ---------------------
    model = Model()
    tic = time.time()
    M = 24 * 60

    # Decision variables
    x = {(ex,d,a): model.addVar(lb=0.0, ub=UB[(ex,d,a)], vtype="C", name=f"x[{ex},{d},{a}]") for (ex,d,a) in EDA}
    z = {(ex,d,a): model.addVar(vtype="B", name=f"z[{ex},{d},{a}]") for (ex,d,a) in EDA}
    DEE = [(d,e1,undan_eftir[e1]) for d in D for e1 in undan_eftir]
    zu = {(d,e1,e2): model.addVar(lb=0.0, ub=1.0, vtype="C", name=f"zu[{d},{e1},{e2}]") for (d,e1,e2) in DEE}  # binary slack for equality-ish checks
    ExE = {(EX[i], EX[j]) for i in range(len(EX)) for j in range(len(EX)) if i != j}
    y = {(e1,e2): model.addVar(vtype="B", name=f"y[{e1},{e2}]") for (e1,e2) in ExE}
    q = {(e,i): model.addVar(lb=0.0, ub=1.0, vtype="C", name=f"q[{e},{i}]") for e in E for i in range(len(D))}
    dt = {(ex,d,a): model.addVar(lb=0.0, ub=60.0, vtype="C", name=f"dt[{ex},{d},{a}]") for (ex,d,a) in EDA}
    zt = {(ex,d,a): model.addVar(vtype="B", name=f"zt[{ex},{d},{a}]") for (ex,d,a) in EDA}
    delta_prev = {(ex,d): model.addVar(lb=0.0, vtype="C", name=f"delta_prev[{ex},{d}]") for (ex,d) in prev_assignment.keys()}

    model.hideOutput(False)  # set to True to silence
    toc = time.time()
    print(f"1. Execution time: {toc - tic:.2f} seconds")

    # --- 2. Time and Activation Constraints ---
    tic = time.time()
    for (ex,d,a) in EDA:
        model.addCons(z[(ex,d,a)] * LB[(ex,d,a)] - dt[(ex,d,a)] <= x[(ex,d,a)])
        model.addCons(x[(ex,d,a)] <= UB[(ex,d,a)] * z[(ex,d,a)] + dt[(ex,d,a)])
        model.addCons(dt[(ex,d,a)] <= M * zt[(ex,d,a)])

    # Each exercise instance exactly once
    for ex in EX:
        model.addCons(quicksum(z[(ex,d,a)] for d in D for a in A if (ex,d,a) in EDA) == 1)

    # Each exercise at most once per day
    for d in D:
        for e in E:
            model.addCons(quicksum(z[(ex,d,a)] for ex in EXsubset[e] for a in A if (ex,d,a) in EDA) <= 1)
    toc = time.time()
    print(f"2. Execution time: {toc - tic:.2f} seconds")
    # --- 3. Overlap Constraints (same location) ---
    tic = time.time()
    eda_by_ex = defaultdict(set)
    for ex,d,a in EDA:
        eda_by_ex[ex].add((d,a))
    EEDA = []
    for e1,e2 in ExE:
        shared = eda_by_ex[e1].intersection(eda_by_ex[e2])
        for d,a in shared:
            EEDA.append((e1,e2,d,a))
    
    for (e1,e2,d,a) in EEDA:
        model.addCons(x[(e1,d,a)] + DX[e1] <= x[(e2,d,a)] + M*(1 - z[(e1,d,a)]) + M*(1 - z[(e2,d,a)]) + M*y[(e1,e2)])
        model.addCons(x[(e2,d,a)] + DX[e2] <= x[(e1,d,a)] + M*(1 - z[(e1,d,a)]) + M*(1 - z[(e2,d,a)]) + M*(1 - y[(e1,e2)]))
    
    toc = time.time()
    print(f"3. Execution time: {toc - tic:.2f} seconds")
    
    # --- 4. Overlap for shared areas (ekki_deila_svaedi) ---
    
    tic = time.time()
    eda_by_ex_day = defaultdict(set)
    for ex,d,a in EDA:
        eda_by_ex_day[(ex,d)].add(a)
    EEDAA = []
    for (e1,e2) in ExE:
        for d in D:
            for a1 in [aa for aa in eda_by_ex_day[(e1,d)] if aa in ekki_deila_svaedi]:
                for a2 in (eda_by_ex_day[(e2,d)] & set(ekki_deila_svaedi[a1])):
                    EEDAA.append((e1,e2,d,a1,a2))
    for (e1,e2,d,a1,a2) in EEDAA:
        model.addCons(x[(e1,d,a1)] + DX[e1] <= x[(e2,d,a2)] + M*(1 - z[(e1,d,a1)]) + M*(1 - z[(e2,d,a2)]) + M*y[(e1,e2)])
        model.addCons(x[(e2,d,a2)] + DX[e2] <= x[(e1,d,a1)] + M*(1 - z[(e1,d,a1)]) + M*(1 - z[(e2,d,a2)]) + M*(1 - y[(e1,e2)]))
    toc = time.time()
    print(f"4. Execution time: {toc - tic:.2f} seconds")
    
    # --- 5. Conflict Constraints (Árekstur) ---
    """
    tic = time.time()
    for e1 in CX:
        for e2 in CX[e1]:
            for d in D:
                lhs1 = quicksum(x[(e1,d,a)] for a in A if (e1,d,a) in EDA) + DX[e1]
                rhs1 = quicksum(x[(e2,d,a)] for a in A if (e2,d,a) in EDA) \
                        + M*(1 - quicksum(z[(e1,d,a)] for a in A if (e1,d,a) in EDA)) \
                        + M*(1 - quicksum(z[(e2,d,a)] for a in A if (e2,d,a) in EDA)) \
                        + M*y[(e1,e2)]
                model.addCons(lhs1 <= rhs1)
                lhs2 = quicksum(x[(e2,d,a)] for a in A if (e2,d,a) in EDA) + DX[e2]
                rhs2 = quicksum(x[(e1,d,a)] for a in A if (e1,d,a) in EDA) \
                        + M*(1 - quicksum(z[(e1,d,a)] for a in A if (e1,d,a) in EDA)) \
                        + M*(1 - quicksum(z[(e2,d,a)] for a in A if (e2,d,a) in EDA)) \
                        + M*(1 - y[(e1,e2)])
                model.addCons(lhs2 <= rhs2)
    toc = time.time()
    print(f"5. Execution time: {toc - tic:.2f} seconds")
    """
    # --- 6. Legal Days for Area/Exercise (svaedi_dagar) ---
    """
    tic = time.time()
    for (e,a), days_allowed in svaedi_dagar.items():
        model.addCons(quicksum(z[(ex,d,a_)]
                               for d in D
                               for ex in EXsubset[e]
                               for a_ in [a]
                               if (ex,d,a_) in EDA and d not in days_allowed) == 0)
    
    toc = time.time()
    print(f"6. Execution time: {toc - tic:.2f} seconds")
    """
    # --- 7. Before/After Constraints (undan_eftir) ---
    """
    tic = time.time()
    for (d,e1,e2) in DEE:
        lhs = quicksum(x[(ex,d,a)] + DX[ex]*z[(ex,d,a)]
                       for ex in EXsubset[e1] for a in A if (ex,d,a) in EDA)
        rhs = quicksum(x[(ex,d,a)]
                       for ex in EXsubset[e2] for a in A if (ex,d,a) in EDA) \
              + M*(1 - quicksum(z[(ex,d,a)] for ex in EXsubset[e1] for a in A if (ex,d,a) in EDA)) \
              + M*(1 - quicksum(z[(ex,d,a)] for ex in EXsubset[e2] for a in A if (ex,d,a) in EDA))
        model.addCons(lhs <= rhs)

        lhs2 = quicksum(x[(ex,d,a)] + DX[ex]*z[(ex,d,a)]
                        for ex in EXsubset[e1] for a in A if (ex,d,a) in EDA)
        rhs2 = quicksum(x[(ex,d,a)]
                        for ex in EXsubset[e2] for a in A if (ex,d,a) in EDA) \
               - M*(1 - quicksum(z[(ex,d,a)] for ex in EXsubset[e1] for a in A if (ex,d,a) in EDA)) \
               - M*(1 - quicksum(z[(ex,d,a)] for ex in EXsubset[e2] for a in A if (ex,d,a) in EDA))
        model.addCons(lhs2 >= rhs2)

        # “How often same day?” relaxers using zu
        lhs3 = quicksum(z[(ex,d,a)] for a in A for ex in EXsubset[e1] if (ex,d,a) in EDA)
        rhs3 = quicksum(z[(ex,d,a)] for a in A for ex in EXsubset[e2] if (ex,d,a) in EDA) + M*zu[(d,e1,e2)]
        model.addCons(lhs3 <= rhs3)

        lhs4 = quicksum(z[(ex,d,a)] for a in A for ex in EXsubset[e1] if (ex,d,a) in EDA)
        rhs4 = quicksum(z[(ex,d,a)] for a in A for ex in EXsubset[e2] if (ex,d,a) in EDA) - M*zu[(d,e1,e2)]
        model.addCons(lhs4 >= rhs4)
    toc = time.time()
    print(f"7. Execution time: {toc - tic:.2f} seconds")
  
    """
    # --- 8. Two Consecutive Days (q variable) ---
    """
    tic = time.time()
    for e in E:
        for i in range(len(D)-1):
            model.addCons(
                quicksum(z[(ex, D[i], a)] + z[(ex, D[i+1], a)]
                         for ex in EXsubset[e] for a in A
                         if (ex, D[i], a) in EDA and (ex, D[i+1], a) in EDA) - 1 <= q[(e, i)]
            )
        model.addCons(
            quicksum(z[(ex, D[6], a)] + z[(ex, D[0], a)]
                     for ex in EXsubset[e] for a in A
                     if (ex, D[6], a) in EDA and (ex, D[0], a) in EDA) - 1 <= q[(e, 6)]
        )
    toc = time.time()
    print(f"8. Execution time: {toc - tic:.2f} seconds")
    """

    # --- 9. Bias for locations (same as original) ---
    bias = {a: 1.0 for a in A}
    bias['1/3 A-sal-1'] = 1.02
    bias['1/3 A-sal-2'] = 1.01

    # --- 10. Prev-solution “stay close” constraints (if any) ---
    def stayclose_constraint_fn():
        exprs = []
        for (ex,d) in prev_assignment:
            exprs.append(model.addCons(
                delta_prev[(ex,d)] >= quicksum(x[(ex,d,a)] for a in A if (ex,d,a) in EDA) - prev_assignment[(ex,d)]
            ))
            exprs.append(model.addCons(
                delta_prev[(ex,d)] >= -quicksum(x[(ex,d,a)] for a in A if (ex,d,a) in EDA) + prev_assignment[(ex,d)]
            ))
        # For modified items, forbid zt triggers
        if modifiedEDA:
            model.addCons(quicksum(zt[(ex,d,a)] for (ex,d,a) in EDA if (ex,d) in modifiedEDA) == 0)
        return exprs

    # --- Multi-objective phases (sequential solve with fixing) ---
    objectives = {}
    objectives["timeflex"] = {
        "expr": quicksum(priority_order.get(e, 1) * (dt[(ex,d,a)] + 100*zt[(ex,d,a)])
                         for e in E for ex in EXsubset[e]
                         for d in D for a in A if (ex,d,a) in EDA),
        "default_limit": 1000,
        "timelimit": 1200,
        "addcons": []
    }
    objectives["stayclose"] = {
        "expr": quicksum(mod_penalty.get((ex,d), 1) * delta_prev[(ex,d)] for (ex,d) in prev_assignment.keys()),
        "default_limit": 1000,
        "timelimit": 120,
        "addcons": [stayclose_constraint_fn] if prev_assignment else []
    }
    objectives["before_after"] = {
        "expr": quicksum(zu[(d,e1,e2)] for (d,e1,e2) in DEE),
        "default_limit": 1000,
        "timelimit": 120,
        "addcons": []
    }
    objectives["default"] = {
        "expr": 100 * quicksum(q[(e,i)] for e in E for i in range(len(D))) \
                + (1 / max(1, len(EX))) * quicksum(bias[a] * x[(ex,d,a)] for (ex,d,a) in EDA),
        "default_limit": 1000,
        "timelimit": 120,
        "addcons": []
    }

    if prev_soln is not None:
        objective_order = ["stayclose", "timeflex"]
    else:
        objective_order = ["before_after", "timeflex", "default"]
    objective_order = ["timeflex"]
    # --- Run phases (replace your current loop with this) ---
    final_sol = None  # keep the last phase's best solution

    for idx, phase in enumerate(objective_order):
        print(f"***Phase {idx+1} ({phase}): Starting optimization...")
        # add phase-specific constraints
        for fn in objectives[phase].get("addcons", []):
            fn()

        obj_expr  = objectives[phase]["expr"]
        timelimit = float(objectives[phase]["timelimit"])
        default_limit = objectives[phase].get("default_limit", 1000)

        model.setObjective(obj_expr, sense="minimize")
        model.setRealParam("limits/time", timelimit)
        model.setEmphasis(SCIP_PARAMEMPHASIS.FEASIBILITY)  # focus on finding a feasible sol’n
        #model.setHeuristics(SCIP_PARAMSETTING.AGGRESSIVE)  # push heuristics
        #model.setIntParam("limits/solutions", 1)           # stop at first feasible solution

        # Guard A: every sub-instance has at least one feasible (d,a)
        for ex in EX:
            cnt = sum(1 for d in D for a in A if (ex, d, a) in EDA)
            if cnt == 0:
                raise RuntimeError(f"No (d,a) options for {ex}")

        # Guard B: time windows are sane
        bad_windows = [(k, LB[k], UB[k]) for k in LB if LB[k] > UB[k]]
        if bad_windows:
            raise RuntimeError(f"{len(bad_windows)} windows have LB>UB (first: {bad_windows[0]})")


        model.optimize()
        print("SCIP Status:", model.getStatus(), "NSols:", model.getNSols())
        if model.getNSols() > 0:
            sol = model.getBestSol()
            picked = sum(1 for k in z if model.getSolVal(sol, z[k]) > 0.5)
            print("Selected sessions:", picked, "of", len(EX))

        status = model.getStatus()
        nsols  = model.getNSols()

        # if we have a solution, keep a handle to it (for the final read-out)
        if nsols > 0:
            final_sol = model.getBestSol()
            best_val  = model.getObjVal()
            print(f"***Phase {idx+1} ({phase}): feasible, best obj = {best_val:.6g}")
            # Only add a fixing constraint if there IS a next phase
            if idx < len(objective_order) - 1:
                model.addCons(obj_expr <= best_val + 1e-6)
        else:
            best_val = None
            print(f"***Phase {idx+1} ({phase}): no feasible incumbent. Using fallback limit {default_limit}.")
            if idx < len(objective_order) - 1:
                model.addCons(obj_expr <= default_limit)

        # Only free transform BETWEEN phases
        if idx < len(objective_order) - 1:
            model.freeTransform()


    # --------------- Build Output DataFrame ---------------
    ABREV = {
        '1/3 A-sal-1': 'A', '1/3 A-sal-2': 'A', '1/3 A-sal-3': 'A',
        '2/3 A-sal': 'A', 'A-sal': 'A', 'B-sal': 'B',
        'Gervi fjær': 'G', 'Gervi nær': 'G', 'Aðalvöllur': 'Aðalv',
        'Æfingavöllur': 'Æfingv', 'Gervigras': 'Gervi'
    }
    days = ['mán', 'þri', 'mið', 'fim', 'fös', 'lau', 'sun']

    # No feasible solution? return an empty frame
    if final_sol is None:
        return pd.DataFrame(columns=[
            'Æfing','Dagur','Salur/svæði','Byrjun','Endir',
            'ViolatedWindow','Hluti','Modified'
        ])

    records = []
    for e in E:
        for d in days:
            for a in A:
                for ex in EXsubset[e]:
                    key = (ex, d, a)
                    if key in EDA and model.getSolVal(final_sol, z[key]) > 0.5:
                        start = model.getSolVal(final_sol, x[key])
                        duration = DX[ex]
                        end = start + duration
                        start_total = int(round(start))  # total minutes from midnight
                        end_total   = int(round(end))    # total minutes from midnight

                        start_hour, start_min = divmod(start_total, 60)
                        end_hour,   end_min   = divmod(end_total,   60)

                        window_start = LB[key]; window_end = UB[key]
                        violated_window = not (window_start <= start_total <= window_end)
                        if violated_window:
                            print(f"Debug: Violated window for {e} on {d} in {a} at {start_hour:02d}:{start_min:02d} (window: {window_start}-{window_end})")
                        records.append({
                            'Æfing': e,
                            'Dagur': d,
                            'Salur/svæði': ABREV.get(a, a),
                            'Byrjun': f"{start_hour:02d}:{start_min:02d}",
                            'Endir':  f"{end_hour:02d}:{end_min:02d}",
                            'ViolatedWindow': violated_window,
                            'Hluti': a,
                            'Modified': False
                        })

    result_df = pd.DataFrame(records)
    for col in ['Dagur','Byrjun','Endir','Salur/svæði','Hluti','Æfing','Modified']:
        result_df[col] = result_df[col].astype(str).str.strip()

    def clean_time_str(t):
        s = str(t).strip()
        # Remove single or double quotes around the value
        if re.fullmatch(r"['\"].*['\"]", s):
            s = s[1:-1]
        # Normalize invalid times like HH:60 → (HH+1):00
        if re.fullmatch(r"\d{2}:\d{2}", s):
            hh, mm = map(int, s.split(":"))
            if mm == 60:
                hh = (hh + 1) % 24
                mm = 0
            s = f"{hh:02d}:{mm:02d}"
        return s

    for col in ["Byrjun", "Endir"]:
        result_df[col] = result_df[col].apply(clean_time_str)

    # Cleanly free SCIP to avoid segfaults on Streamlit reruns
    model.freeTransform()
    model.freeProb()

    return result_df
