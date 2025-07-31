import pandas as pd
import numpy as np
import re
import gurobipy as gp
from gurobipy import GRB
import os
import json
import datetime as datetime

def save_solution(result_df, edited_df=None, solution_dir="solutions"):
    os.makedirs(solution_dir, exist_ok=True)
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"solution_{timestamp}.json"
    path = os.path.join(solution_dir, filename)
    full_data = {
        "conditions": edited_df.to_dict(orient="records") if edited_df is not None else [],
        "solution": result_df.to_dict(orient="records")
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(full_data, f, ensure_ascii=False, indent=2)

    return path

def list_solutions(solution_dir="sports/solutions"):
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

def run_gurobi_optimization(df: pd.DataFrame, kill_callback=None, prev_soln=None) -> pd.DataFrame:
    """
    Full MIP optimizer: processes user-edited df, runs Gurobi, returns schedule DataFrame.
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
        days = days_match.group(1).split('/') if days_match else None
        area = re.sub(r'\s*\(.*\)', '', info).strip()
        return area, days

    svaedi_dagar_df = df[['Æfing','Salur/svæði']].explode('Salur/svæði').reset_index(drop=True)
    svaedi_dagar_df[['Salur/svæði', 'Dagar']] = svaedi_dagar_df['Salur/svæði'].apply(lambda x: pd.Series(extract_days(x)))
    svaedi = set(svaedi_dagar_df['Salur/svæði'].str.strip())
    print(f"Debug: Unique areas extracted: {svaedi}")
    aefing = set(svaedi_dagar_df['Æfing'].str.strip())

    # 3. Add Priority Dictionary
    priority_order = dict(zip(df['Æfing'], df.get('Priority', [1]*len(df))))

    # 4. Model Dictionaries
    svaedi_dagar_df = svaedi_dagar_df.dropna(subset=['Dagar'])
    svaedi_dagar = svaedi_dagar_df.set_index(['Æfing', 'Salur/svæði'])['Dagar'].to_dict()
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
    print(f"Debug: e_a mapping: {e_a}")

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
    for e in E:
        for d in D:
            for a in A:
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
                    if key in EDA:
                        prev_assignment[key] = start_minutes
                        mod_penalty[key] = penalty
        print(modifiedEDA)
    # ------------------- MIP Model ---------------------
    
    # --- 1. Model Setup ---
    model = gp.Model()

    # Decision variables
    x = model.addVars(EDA, ub=UB, name='x')
    z = model.addVars(EDA, vtype="B", name='z')
    M = 24 * 60
    ExE = {(EX[i], EX[j]) for i in range(len(EX)) for j in range(len(EX)) if i != j}
    y = model.addVars(ExE, vtype="B", name='y')
    q = model.addVars(E, range(len(D)), name='q')
    #c = model.addVars(EDA, vtype="B", name='c')
    dt = model.addVars(EDA, name='dt') # new time flex strategy 
    delta_prev = model.addVars(prev_assignment.keys(), name="delta_prev")  # deviation from previous start
    zt = model.addVars(EDA, vtype="B", name='zt') # new time flex strategy

    # --- 2. Time and Activation Constraints ---

    # If exercise ex is scheduled on day d in area a, it should start after LB
    model.addConstrs(z[ex, d, a] * LB[(ex, d, a)] - dt[(ex, d, a)] <= x[ex, d, a] for (ex, d, a) in EDA)

    # If not scheduled, force to zero
    model.addConstrs(x[ex, d, a] <= UB[(ex, d, a)] * z[ex, d, a] + dt[(ex, d, a)] for (ex, d, a) in EDA)

    # Indicator for time change:
    model.addConstrs(dt[(ex, d, a)] <= M * zt[(ex, d, a)] for (ex, d, a) in EDA)

    # Each exercise instance can be performed at most once
    model.addConstrs(gp.quicksum(z[ex, d, a] for d in D for a in A if (ex, d, a) in EDA) == 1 for ex in EX)

    # Each exercise (group) at most once per day
    model.addConstrs(
        gp.quicksum(z[ex, d, a] for ex in EXsubset[e] for a in A if (ex, d, a) in EDA) <= 1
        for d in D for e in E
    )

    # --- 3. Overlap Constraints (no overlap at same location) ---

    model.addConstrs(
        x[e1, d, a] + DX[e1] <= x[e2, d, a] + M * (1 - z[e1, d, a]) + M * (1 - z[e2, d, a]) + M * y[e1, e2]
        for (e1, e2) in ExE for d in D for a in A
        if (e1, d, a) in EDA and (e2, d, a) in EDA
    )
    model.addConstrs(
        x[e2, d, a] + DX[e2] <= x[e1, d, a] + M * (1 - z[e1, d, a]) + M * (1 - z[e2, d, a]) + M * (1 - y[e1, e2])
        for (e1, e2) in ExE for d in D for a in A
        if (e1, d, a) in EDA and (e2, d, a) in EDA
    )

    # --- 4. Overlap Constraints for Shared Areas (ekki_deila_svaedi) ---

    model.addConstrs(
        x[e1, d, a1] + DX[e1] <= x[e2, d, a2] + M * (1 - z[e1, d, a1]) + M * (1 - z[e2, d, a2]) + M * y[e1, e2]
        for (e1, e2) in ExE for d in D
        for a1 in ekki_deila_svaedi for a2 in ekki_deila_svaedi[a1]
        if (e1, d, a1) in EDA and (e2, d, a2) in EDA
    )
    model.addConstrs(
        x[e2, d, a2] + DX[e2] <= x[e1, d, a1] + M * (1 - z[e1, d, a1]) + M * (1 - z[e2, d, a2]) + M * (1 - y[e1, e2])
        for (e1, e2) in ExE for d in D
        for a1 in ekki_deila_svaedi for a2 in ekki_deila_svaedi[a1]
        if (e1, d, a1) in EDA and (e2, d, a2) in EDA
    )

    # --- 5. Conflict Constraints (Árekstur) ---

    model.addConstrs(
        gp.quicksum(x[e1, d, a] for a in A if (e1, d, a) in EDA) + DX[e1]
        <= gp.quicksum(x[e2, d, a] for a in A if (e2, d, a) in EDA)
        + M * (1 - gp.quicksum(z[e1, d, a] for a in A if (e1, d, a) in EDA))
        + M * (1 - gp.quicksum(z[e2, d, a] for a in A if (e2, d, a) in EDA))
        + M * y[e1, e2]
        for e1 in CX for e2 in CX[e1] for d in D if e1 in DX
    )
    model.addConstrs(
        gp.quicksum(x[e2, d, a] for a in A if (e2, d, a) in EDA) + DX[e2]
        <= gp.quicksum(x[e1, d, a] for a in A if (e1, d, a) in EDA)
        + M * (1 - gp.quicksum(z[e1, d, a] for a in A if (e1, d, a) in EDA))
        + M * (1 - gp.quicksum(z[e2, d, a] for a in A if (e2, d, a) in EDA))
        + M * (1 - y[e1, e2])
        for e1 in CX for e2 in CX[e1] for d in D if e2 in DX
    )

    # --- 6. Legal Days for Area/Exercise (svaedi_dagar) ---

    model.addConstrs(
        gp.quicksum(z[ex, d, a] for d in D for ex in EXsubset[e] if (ex, d, a) in EDA and d not in svaedi_dagar[(e, a)]) == 0
        for (e, a) in svaedi_dagar
    )

    # --- 7. Before/After Constraints (undan_eftir) ---

    model.addConstrs(
        gp.quicksum(x[ex, d, a] + DX[ex] * z[ex, d, a] for ex in EXsubset[e1] for a in A if (ex, d, a) in EDA)
        <= gp.quicksum(x[ex, d, a] for ex in EXsubset[e2] for a in A if (ex, d, a) in EDA)
        + M * (1 - gp.quicksum(z[ex, d, a] for ex in EXsubset[e1] for a in A if (ex, d, a) in EDA))
        + M * (1 - gp.quicksum(z[ex, d, a] for ex in EXsubset[e2] for a in A if (ex, d, a) in EDA))
        for d in D for e1 in undan_eftir for e2 in [undan_eftir[e1]]
    )
    model.addConstrs(
        gp.quicksum(x[ex, d, a] + DX[ex] * z[ex, d, a] for ex in EXsubset[e1] for a in A if (ex, d, a) in EDA)
        >= gp.quicksum(x[ex, d, a] for ex in EXsubset[e2] for a in A if (ex, d, a) in EDA)
        - M * (1 - gp.quicksum(z[ex, d, a] for ex in EXsubset[e1] for a in A if (ex, d, a) in EDA))
        - M * (1 - gp.quicksum(z[ex, d, a] for ex in EXsubset[e2] for a in A if (ex, d, a) in EDA))
        for d in D for e1 in undan_eftir for e2 in [undan_eftir[e1]]
    )

    # --- 8. Two Consecutive Days (q variable) ---

    model.addConstrs(
        gp.quicksum(z[ex, D[i], a] + z[ex, D[i+1], a]
                    for ex in EXsubset[e] for a in A
                    if (ex, D[i], a) in EDA and (ex, D[i+1], a) in EDA) - 1 <= q[e, i]
        for e in E for i in range(len(D)-1)
    )
    model.addConstrs(
        gp.quicksum(z[ex, D[6], a] + z[ex, D[0], a]
                    for ex in EXsubset[e] for a in A
                    if (ex, D[6], a) in EDA and (ex, D[0], a) in EDA) - 1 <= q[e, 6]
        for e in E
    )

    # --- 9. Biases for Locations (if needed) ---
    bias = {a: 1.0 for a in A}
    bias['1/3 A-sal-1'] = 1.02
    bias['1/3 A-sal-2'] = 1.01

    if prev_soln is not None:
        expr1 =  model.addConstrs(
                delta_prev[ex, d] >= gp.quicksum(x[ex, d, a] for a in A if (ex,d,a) in EDA) - prev_assignment[ex,d] for (ex,d) in prev_assignment
            )
        expr2 =  model.addConstrs(
                delta_prev[ex, d] >= -gp.quicksum(x[ex, d, a] for a in A if (ex,d,a) in EDA) + prev_assignment[ex,d] for (ex,d) in prev_assignment
            )
        stayclose_constraints = [expr1, expr2]
    else:
        stayclose_constraints = []

    # --- 10. Objectives Setup (Using 'e', not 'ex', for priority) ---
    def stayclose_constraint_fn(model):
        expr1 = model.addConstrs(
            (delta_prev[ex, d] >= gp.quicksum(x[ex, d, a] for a in A if (ex, d, a) in EDA) - prev_assignment[(ex, d)]
            for (ex, d) in prev_assignment),
            name="prev_diff_lower"
        )
        expr2 = model.addConstrs(
            (delta_prev[ex, d] >= -gp.quicksum(x[ex, d, a] for a in A if (ex, d, a) in EDA) + prev_assignment[(ex, d)]
            for (ex, d) in prev_assignment),
            name="prev_diff_upper"
        )
        expr3 = model.addConstr(
            (gp.quicksum(zt[ex, d, a] for (ex, d, a) in EDA if (ex, d) in modifiedEDA) == 0),
            name="no_modified_zt"
        )
        return [expr1, expr2, expr3]
    objectives = {
        "timeflex": {
            "expr": gp.quicksum(priority_order.get(e, 1) * (dt[ex, d, a] + 100*zt[ex, d, a])
                                for e in E for ex in EXsubset[e]
                                for d in D for a in A if (ex, d, a) in EDA),
            "timelimit": 100,
            "constraints": [
                # Require every session to be scheduled (for timeflex only)
                #lambda model: model.addConstrs(
                #    gp.quicksum(z[ex, d, a] for d in D for a in A if (ex, d, a) in EDA) == 1
                #    for ex in EX
                #)
            ],
            "sense": gp.GRB.MINIMIZE
        },
        "stayclose": {
            "expr": gp.quicksum(mod_penalty.get((ex, d), 1)*delta_prev[ex, d] for (ex, d) in prev_assignment.keys()),
            "timelimit": 60,
            "constraints": [stayclose_constraint_fn],
            "sense": gp.GRB.MINIMIZE
        },
        "default": {
            "expr": 100 * gp.quicksum(q[e, i] for e in E for i in range(len(D)))
                + (1 / (len(EX))) * gp.quicksum(bias[a] * x[ex, d, a] for (ex, d, a) in EDA),
            "timelimit": 100,
            "constraints": [],
            "sense": gp.GRB.MINIMIZE
        }
    }
    if prev_soln is not None:
        # Add previous solution as a constraint to the default objective
        #objective_order = ["stayclose", "timeflex",  "default"] # "feasibility",
        objective_order = ["stayclose", "timeflex"]
    else:
        objective_order = ["timeflex",  "default"] # "feasibility",

    added_constraints = []

    for i, obj_name in enumerate(objective_order):
        print(f"Solving objective: {obj_name}")

        obj = objectives[obj_name]

        # Add phase-specific constraints
        added_constraints.clear()
        for constr_fn in obj.get("constraints", []):
            constrs = constr_fn(model)
            added_constraints.append(constrs)
        model.update()

        obj_expr = obj["expr"]
        time_limit = obj["timelimit"]
        sense = obj.get("sense", gp.GRB.MINIMIZE)

        model.setObjective(obj_expr, sense=sense)
        model.setParam('TimeLimit', time_limit)
        if kill_callback is not None:
            model.optimize(kill_callback)
        else:
            model.optimize()
        
        if model.status in [gp.GRB.OPTIMAL, gp.GRB.TIME_LIMIT]:
            best_val = model.ObjVal
            model.addConstr(obj_expr <= best_val + 1e-6, name=f"fix_{obj_name}")
        else:
            print(f"Warning: {obj_name} not solved to optimality.")

        # Remove constraints ONLY if not on the last iteration
        #if i < len(objective_order) - 1:
        #    for constr_group in added_constraints:
        #        if hasattr(constr_group, "values"):
        #            for c in constr_group.values():
        #                model.remove(c)
        #        else:
        #            model.remove(constr_group)
        #    model.update()


    # --------------- Build Output DataFrame for Display/Calendar ---------------
    # Abbreviations for fields
    ABREV = {
        '1/3 A-sal-1': 'A', '1/3 A-sal-2': 'A', '1/3 A-sal-3': 'A',
        '2/3 A-sal': 'A', 'A-sal': 'A', 'B-sal': 'B',
        'Gervi fjær': 'G', 'Gervi nær': 'G', 'Aðalvöllur': 'Aðalv',
        'Æfingavöllur': 'Æfingv', 'Gervigras': 'Gervi'
    }
    days = ['mán', 'þri', 'mið', 'fim', 'fös', 'lau', 'sun']

    records = []
    for e in E:
        for d in days:
            for a in A:
                for ex in EXsubset[e]:
                    key = (ex, d, a)
                    if key in EDA and z[ex, d, a].X > 0.1:
                        start = x[ex, d, a].X
                        duration = DX[ex]
                        end = start + duration
                        start_hour = int(start // 60)
                        start_min = int(start % 60)
                        end_hour = int(end // 60)
                        end_min = int(end % 60)
                        window_start = LB[key]
                        window_end = UB[key]
                        violated_window = not (window_start <= start <= window_end)
                        record = {
                            'Æfing': e,
                            'Dagur': d,
                            'Salur/svæði': ABREV.get(a, a),
                            'Byrjun': f"{start_hour:02d}:{start_min:02d}",
                            'Endir': f"{end_hour:02d}:{end_min:02d}",
                            'ViolatedWindow': violated_window
                        }
                        records.append(record)
    result_df = pd.DataFrame(records)
    for col in ['Dagur', 'Byrjun', 'Endir', 'Salur/svæði', 'Æfing']:
        result_df[col] = result_df[col].astype(str).str.strip()
    return result_df
