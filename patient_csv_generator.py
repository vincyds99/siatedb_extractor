#!/usr/bin/env python3
import os
import sys
import time
import re
import json
import psycopg2
import csv
import gc
import math
from datetime import datetime, date
from pathlib import Path
from psycopg2 import sql
from operator import itemgetter
from collections import defaultdict
from psycopg2.extras import execute_values

# Define the list of allowed features for the neural network (request of the professor)
ALLOWED_NN_PROPS = {
    # Raw properties (15)
    "Arter", "Azotemia Post", "Azotemia Pre", "BCM post", "BMI", 
    "FFM post", "FM post", "Peso Post", "Peso Pre", "QB Medio", 
    "QB Totale", "Score CVC", "Score FAV", "Tipo Accesso Vascolare", 
    "Vena", "Vitamina D",
    # Dependent properties (11)
    "A/V", "Mesi CVC", "Mesi FAV", "Minuti Dialisi", "Ore Dialisi", 
    "PA/QB", "PV/QB", "QB", "TSAT", "UF", "UF Tot"
}

# Vascular access features subset (12 properties)
VASCULAR_ACCESS_PROPS = {
    # Raw properties (6)
    "Arter", "Vena", "Score CVC", "Score FAV", "QB Medio", "QB Totale",
    # Dependent properties (6)
    "A/V", "Mesi CVC", "Mesi FAV", "PA/QB", "PV/QB", "QB"
}

# get environment variable with optional default and required flag
def get_env(name, default=None, required=False):
    v = os.environ.get(name, default)
    print(f"[python-runner] Env var {name} = {v}")
    if required and not v:
        print(f"Missing required env var: {name}")
        sys.exit(1)
    return v

# connect to Postgres DB
def connect(dbname, user, password, host, port, autocommit=False):
    conn = psycopg2.connect(dbname=dbname, user=user, password=password, host=host, port=port)
    conn.autocommit = autocommit
    return conn

# wait for DB to be ready
def wait_for_db(host, port, user, password, dbname, timeout=60, interval=2):
    start = time.time()
    last_exc = None
    while True:
        try:
            conn = psycopg2.connect(dbname=dbname, user=user, password=password, host=host, port=port)
            conn.close()
            return True
        except Exception as e:
            last_exc = e
            if time.time() - start >= timeout:
                print(f"[python-runner] Timeout waiting for DB ({timeout}s): {last_exc}")
                return False
            print("[python-runner] DB not ready, retrying in {0}s...".format(interval))
            time.sleep(interval)

# clean birthday values
def clean_birthday_rows(rows):
    out = []
    pattern = re.compile(r'"([^"]+)"')
    for r in rows:
        try:
            pid = r[0]
            raw = r[1]
        except Exception:
            continue

        if raw is None:
            ts = None
        elif isinstance(raw, str):
            m = pattern.search(raw)
            if m:
                ts = m.group(1)
            else:
                t = raw.strip()
                if t.startswith('(') and t.endswith(')'):
                    t = t[1:-1]
                ts = t.split(',', 1)[0].strip().strip('"').strip("'")
        else:
            ts = str(raw)

        out.append((pid, ts))
    return out

# process generality_list
def process_generality_list(generality_list):
    result = defaultdict(lambda: [None, None, None, None])
    prop_index = {
        'Birthday': 0, 'Data Nascita': 0,
        'Gender': 1, 'Sesso': 1,
        'Height': 2, 'Altezza': 2,
        'Etnicity': 3, 'Ethnicity': 3, 'Etnia': 3,
    }
    ts_pattern = re.compile(r'"([^\"]+)"')

    for row in generality_list:
        if not row or len(row) < 3:
            continue
        pid, prop, raw = row[0], row[1], row[2]
        if pid is None:
            continue

        idx = prop_index.get(prop)
        if idx is None:
            continue

        value_str = None
        if raw is None:
            value_str = None
        else:
            s = str(raw).strip()
            if idx == 0:
                m = ts_pattern.search(s)
                if m:
                    value_str = m.group(1)
                else:
                    if s.startswith('(') and s.endswith(')'):
                        inner = s[1:-1]
                    else:
                        inner = s
                    value_str = inner.split(',', 1)[0].strip().strip('"').strip("'")
            else:
                if s.startswith('(') and s.endswith(')'):
                    inner = s[1:-1]
                else:
                    inner = s
                value_str = inner.split(',', 1)[0].strip().strip('"').strip("'")

        result[pid][idx] = value_str

    return dict(result)

# clean death_list entries
def clean_death_list(rows):
    out = {}
    pattern = re.compile(r'^\s*(?P<patient_id>[^\[\s]+)\s*\[\s*(?P<first>[^,\]]+)')
    for r in rows:
        if not r:
            continue
        raw = r[0] if isinstance(r, (list, tuple)) else r
        if raw is None:
            continue
        s = str(raw).strip()
        m = pattern.search(s)
        if m:
            patient_id = m.group('patient_id').strip()
            ts = m.group('first').strip().strip('"').strip("'")
            out[patient_id] = ts
            continue

        if '[' in s:
            before, after = s.split('[', 1)
            patient_id = before.strip()
            first = after.split(',', 1)[0].strip().strip(']').strip().strip('"').strip("'")
            out[patient_id] = first
        else:
            out[s] = None
    return out

# return list of CSV files in a directory
def file_list(directory):
    if not os.path.isdir(directory):
        raise FileNotFoundError(f"Directory does not exist: {directory}")
    return [
        os.path.join(directory, nome)
        for nome in os.listdir(directory)
        if os.path.isfile(os.path.join(directory, nome)) and nome.endswith('.csv')
    ]

# normalize gender
def normalize_gender(raw_gender):
    if raw_gender is None:
        return None
    g = str(raw_gender).strip().lower()
    gender_map = {
        'm': 'M', 'maschio': 'M', 'male': 'M',
        'f': 'F', 'femmina': 'F', 'female': 'F',
        'unknown': 'Unknown', 'sconosciuto': 'Unknown',
    }
    return gender_map.get(g, 'Unknown')

# normalize ethnicity
def normalize_ethnicity(raw_ethnicity):
    if raw_ethnicity is None:
        return None
    e = str(raw_ethnicity).strip()
    ethnicity_map = {
        'caucasica': 'Caucasian', 'caucasian': 'Caucasian',
        'etiopica': 'Ethiopian', 'ethiopian': 'Ethiopian',
        'americana': 'American', 'american': 'American',
        'africana': 'African', 'african': 'African',
        'asiatica': 'Asian', 'asian': 'Asian',
        'ispanica': 'Hispanic', 'hispanic': 'Hispanic',
        'malese': 'Malaysian', 'malaysian': 'Malaysian',
        'mongolica': 'Mongolian', 'mongolian': 'Mongolian',
        'other': 'Other', 'altro': 'Other',
    }
    return ethnicity_map.get(e.lower(), 'Other')

# map raw domain type
def map_to_domain_type(domain_row):
    if not domain_row or not domain_row[0]:
        return None
    raw = domain_row[0].strip().lower()
    if raw in ('real', 'double precision', 'numeric', 'decimal'): return 'real'
    if raw in ('float'): return 'float'
    if raw in ('smallint', 'integer', 'bigint', 'int'): return 'discrete'
    if raw in ('timestamp', 'time', 'datetime', 'date'): return 'time'
    if raw in ('string', 'str', 'text', 'varchar', 'char'): return 'string'
    return 'string'

# create custom domains and tables
def create_domains_and_tables(cur):
    cur.execute("""CREATE TYPE GenderType AS ENUM ('M', 'F', 'Unknown'); 
                CREATE TYPE Ethnicity AS ENUM ('American', 'Caucasian', 'African', 'Asian', 'Hispanic', 'Ethiopian', 'Malaysian', 'Mongolian', 'Other'); 
                CREATE TYPE DomainType AS ENUM ('real', 'discrete', 'string', 'time', 'float'); CREATE TYPE WindowType AS ENUM ('3m', '6m', '1y'); 
                CREATE DOMAIN String4PatientId AS CHAR(24);CREATE DOMAIN String4VarName AS VARCHAR(67);CREATE DOMAIN String4EventType AS VARCHAR(100);
                CREATE TYPE EventType AS ENUM ('Dati Anemia', 'Dati CKD-MBD', 'Dati Nutrizione', 'Ricovero', 'Seduta di dialisi');""")
    
    cur.execute("""CREATE TABLE Patient (
                    patient_id String4PatientId PRIMARY KEY,     
                    date_of_birth timestamp NOT NULL,
                    date_of_death timestamp,                      
                    date_of_critical_event timestamp,
                    gender GenderType,
                    ethnicity Ethnicity,
                    height REAL
                );""")
    
    cur.execute("""CREATE TABLE Measurements (
            patient_ID String4PatientId NOT NULL,
            event_ID String4EventType,
            interval timestamp NOT NULL,
            event_type EventType,
            property VARCHAR(100) NOT NULL,
            value VARCHAR(100) NOT NULL,
            PRIMARY KEY (patient_ID, interval, property),
            CONSTRAINT patient_ref FOREIGN KEY(patient_ID) REFERENCES Patient(patient_id)
        );""")
    
    cur.execute("""CREATE TABLE DependentProperties (
            patient_ID String4PatientId NOT NULL,
            interval timestamp NOT NULL,
            property VARCHAR(100) NOT NULL,
            value VARCHAR(100) NOT NULL,
            PRIMARY KEY (patient_ID, interval, property),
            CONSTRAINT patient_ref FOREIGN KEY(patient_ID) REFERENCES Patient(patient_id)
        );""")
    
    cur.execute("""CREATE TABLE TimeVar (
        var_id SERIAL PRIMARY KEY,
        var_name String4VarName NOT NULL UNIQUE,
        var_domain DomainType NOT NULL,
        lower_bound REAL NOT NULL,
        upper_bound REAL NOT NULL,
        average DOUBLE PRECISION NOT NULL,
        std_dev DOUBLE PRECISION NOT NULL
    );""")
    
    cur.execute("""CREATE TABLE TimeSeries (
        time timestamp NOT NULL,
        pid String4PatientId NOT NULL,
        tid int NOT NULL,
        varvalue REAL NOT NULL,
    	PRIMARY KEY (time, pid, tid),
        CONSTRAINT pid_ref FOREIGN KEY(pid) REFERENCES Patient(patient_id),
        CONSTRAINT tid_ref FOREIGN KEY(tid) REFERENCES TimeVar(var_id)
    );
    CREATE INDEX IF NOT EXISTS idx_timeseries_pid ON TimeSeries (pid);""")
    
    cur.execute("""CREATE TABLE NN_Training_Dataset (
        timestamp timestamp NOT NULL,
        patient_id String4PatientId NOT NULL,
        history_days INTEGER NOT NULL,
        misure DOUBLE PRECISION[] NOT NULL,
        tte INTEGER NOT NULL,
        log_tte DOUBLE PRECISION NOT NULL,
        tte_uncapped INTEGER NOT NULL,
        log_tte_uncapped DOUBLE PRECISION NOT NULL,
        PRIMARY KEY (timestamp, patient_id),
        CONSTRAINT patient_ref FOREIGN KEY(patient_id) REFERENCES Patient(patient_id)
    );""")

# insert patients
def insert_patients(cur, birthday_rows, dest_conn, patients_list, death_dict, generality_dict, critical_event_dict):
    query = """
                INSERT INTO Patient (patient_id, date_of_birth)
                VALUES (%s, %s)
                ON CONFLICT (patient_id)
                DO UPDATE SET date_of_birth = EXCLUDED.date_of_birth;
            """
    cur.executemany(query, birthday_rows)
    dest_conn.commit()
    
    for patient_id, dob in birthday_rows:
        cur.execute("""
            UPDATE Patient
            SET date_of_birth = %s
            WHERE patient_id = %s;
        """, (dob, patient_id))
        dest_conn.commit()

    for patient_id in patients_list:
        clean_pid = patient_id.strip() if isinstance(patient_id, str) else patient_id
        if clean_pid in death_dict:
            death_date = death_dict[clean_pid]
            cur.execute("""
                UPDATE Patient
                SET date_of_death = %s
                WHERE patient_id = %s;
            """, (death_date, clean_pid))
            dest_conn.commit()
        if clean_pid in critical_event_dict:
            crit_date = critical_event_dict[clean_pid]
            cur.execute("""
                UPDATE Patient
                SET date_of_critical_event = %s
                WHERE patient_id = %s;
            """, (crit_date, clean_pid))
            dest_conn.commit()
        if clean_pid in generality_dict:
            gender = normalize_gender(generality_dict[clean_pid][1])
            height = generality_dict[clean_pid][2]
            ethnicity = normalize_ethnicity(generality_dict[clean_pid][3])

            cur.execute("""
                UPDATE Patient
                SET gender = %s, height = %s, ethnicity = %s
                WHERE patient_id = %s;
            """, (gender, height, ethnicity, clean_pid))
            dest_conn.commit()

def stream_and_insert_measures(db_params_src, dest_conn, birthday_by_patient):
    dbname, user, password, host, port = db_params_src
    read_conn = connect(dbname, user, password, host, port)
    batch = []
    batch_size = 5000 
    try:
        with read_conn.cursor(name='src_measures_reader') as read_cur, dest_conn.cursor() as write_cur:
            read_cur.itersize = batch_size
            read_cur.execute("""
                SELECT patient, interval, property, event, value 
                FROM patient.propertymeasure 
                WHERE property NOT IN ('Data Nascita', 'Sesso', 'Altezza', 'Etnia');
            """)
            
            for row in read_cur:
                patient_id, interval, property, event, value = row
                clean_pid = patient_id.strip() if isinstance(patient_id, str) else patient_id
                
                if clean_pid not in birthday_by_patient.keys():
                    continue
                
                if interval is not None and hasattr(interval, 'lower'):
                    interval_str = str(interval.lower) if interval.lower is not None else None
                else:
                    interval_str = str(interval) if interval else None
                    
                batch.append((clean_pid, interval_str, event, property, value))
                
                if len(batch) >= batch_size:
                    execute_values(write_cur, """
                        INSERT INTO Measurements (patient_ID, interval, event_ID, property, value)
                        VALUES %s;
                    """, batch)
                    dest_conn.commit()
                    batch.clear()
                    
            if batch:
                execute_values(write_cur, """
                    INSERT INTO Measurements (patient_ID, interval, event_ID, property, value)
                    VALUES %s;
                """, batch)
                dest_conn.commit()
    finally:
        read_conn.close()

def find_property_name(property_name):
    # Exact mapping based on filenames in the dependent_properties directory
    mapping = {
        "Alpha_EPODose_Weight": "Alpha EPODose Weight",
        "Alpha_ERI": "Alpha ERI",
        "FosfAlcIndex": "FosfAlcIndex",
        "Mesi_CVC": "Mesi CVC",
        "Mesi_FAV": "Mesi FAV",
        "Minuti_Dialisi": "Minuti Dialisi",
        "Ore_Dialisi": "Ore Dialisi",
        "PA_QB": "PA/QB",
        "PV_QB": "PV/QB",
        "TSAT": "TSAT",
        "UF": "UF",
        "UF_Tot": "UF Tot",
        "av": "A/V",
        "qb": "QB",
    }
    if property_name in mapping:
        return mapping[property_name]
    
    # Fallback to normalized comparison in case underscores or cases differ
    p = property_name.lower().replace("_", "")
    fallback_mapping = {
        "tsat": "TSAT",
        "qb": "QB",
        "fosfalcindex": "FosfAlcIndex",
        "mesifav": "Mesi FAV",
        "alphaepodoseweight": "Alpha EPODose Weight",
        "pvqb": "PV/QB",
        "paqb": "PA/QB",
        "oredialisi": "Ore Dialisi",
        "alphaeri": "Alpha ERI",
        "uftot": "UF Tot",
        "mesicvc": "Mesi CVC",
        "av": "A/V",
        "minutidialisi": "Minuti Dialisi",
        "uf": "UF",
    }
    return fallback_mapping.get(p, property_name)

def process_single_property_file(file, db_params, birthday_by_patient):
    basename = os.path.basename(file)
    if basename.endswith('.csv'):
        basename = basename[:-4]
    property_name = basename.removeprefix('derived_prop_')
    property_name = find_property_name(property_name)
    
    print(f"[python-runner] Executing: {file} - {property_name}")
    dbname, user, password, host, port = db_params
    conn = connect(dbname, user, password, host, port)
    try:
        with conn.cursor() as cur:
            with open(file, 'r', encoding='utf-8') as f:
                reader = csv.reader(f)
                chunk = []
                chunk_size = 5000
                
                for row in reader:
                    if len(row) < 3:
                        continue
                    patient_id, interval, value = row[0], row[1], row[2]
                    clean_pid = patient_id.strip() if isinstance(patient_id, str) else patient_id
                    
                    if clean_pid not in birthday_by_patient:
                        continue
                    chunk.append((clean_pid, interval, property_name, value))
                    
                    if len(chunk) >= chunk_size:
                        execute_values(cur, """
                            INSERT INTO DependentProperties (patient_ID, interval, property, value)
                            VALUES %s;
                        """, chunk)
                        conn.commit()
                        chunk.clear()
                        
                if chunk:
                    execute_values(cur, """
                        INSERT INTO DependentProperties (patient_ID, interval, property, value)
                        VALUES %s;
                    """, chunk)
                    conn.commit()
    finally:
        conn.close()

def insert_timevar_table(cur, dest_conn):
    properties=["Albuminemia", "Arter", "Azotemia Post", "Azotemia Pre", "BCM post", "BMI", "Bicarbonatemia", "Calcemia", "Circonferenza Braccio", "Colesterolemia", "DEI", "DPI", "Dosaggio Epoetina Alpha mensile", "FFM post", "FM post", "Ferritina", "Fosfatasi Alcalina", "Fosforemia", "Hb", "PTH", "Peso Post", "Peso Pre", "QB Medio", "QB Totale", "Score CVC", "Score FAV", "Sideremia", "Tipo Accesso Vascolare", "Transferrina", "Vena","Vitamina D"]
    dependent_properties=["A/V", "Alpha EPODose Weight", "Alpha ERI", "FosfAlcIndex", "Mesi CVC", "Mesi FAV","Minuti Dialisi", "Ore Dialisi", "PA/QB", "PV/QB", "QB", "TSAT", "UF", "UF Tot"]

    for property in properties:
        domain = """SELECT trim(split_part(trim(both '()' FROM value), ',', 2)) AS value_type
            FROM measurements WHERE property = %s LIMIT 1;"""
        cur.execute(domain, (property,))
        res = cur.fetchone()
        domain_result = map_to_domain_type(res) if res else None
        if domain_result in ('real','float', 'discrete'):
            q = """WITH prop_values AS (
                  SELECT CASE
                      WHEN split_part(trim(both '()' from value), ',', 1) ~ '^[+-]?([0-9]*[.])?[0-9]+([eE][+-]?[0-9]+)?$'
                      THEN split_part(trim(both '()' from value), ',', 1)::double precision
                    END AS v
                  FROM measurements WHERE property = %s
                )
                SELECT MIN(v), MAX(v), AVG(v), STDDEV_SAMP(v) FROM prop_values;"""
            cur.execute(q, (property,))
            min_val, max_val, average, dev_std = cur.fetchone()
            if average is not None:
                cur.execute("""INSERT INTO TimeVar (var_name, var_domain, lower_bound, upper_bound, average, std_dev)
                            VALUES (%s, %s, %s, %s, %s, %s);""", (property, domain_result, min_val, max_val, average, dev_std))
                dest_conn.commit()

    for property in dependent_properties:
        domain = """SELECT trim(split_part(trim(both '()' FROM value), ',', 2)) AS value_type
            FROM dependentproperties WHERE property = %s LIMIT 1;"""
        cur.execute(domain, (property,))
        res = cur.fetchone()
        domain_result = map_to_domain_type(res) if res else None
        if domain_result not in ('real','float', 'discrete'):
            continue
        
        q = """WITH prop_values AS (
              SELECT CASE
                  WHEN split_part(trim(both '()' from value), ',', 1) ~ '^[+-]?([0-9]*[.])?[0-9]+([eE][+-]?[0-9]+)?$'
                  THEN split_part(trim(both '()' from value), ',', 1)::double precision
                END AS v
              FROM dependentproperties WHERE property = %s
            )
            SELECT MIN(v), MAX(v), AVG(v), STDDEV_SAMP(v) FROM prop_values;"""
        cur.execute(q, (property,))
        min_val, max_val, average, dev_std = cur.fetchone()
        if average is not None:
            cur.execute("""INSERT INTO TimeVar (var_name, var_domain, lower_bound, upper_bound, average, std_dev)
                        VALUES (%s, %s, %s, %s, %s, %s);""", (property, domain_result, min_val, max_val, average, dev_std))
            dest_conn.commit()

def insert_timeseries_table(dest_conn, birthday_by_patient, property_tid, db_params):
    batch = []
    batch_size = 5000 
    print("[python-runner] Inserting Raw Measurements into TimeSeries...")
    dbname, user, password, host, port = db_params
    read_conn = connect(dbname, user, password, host, port)
    
    try:
        with read_conn.cursor(name='meas_reader') as read_cur, dest_conn.cursor() as write_cur:
            read_cur.itersize = batch_size
            read_cur.execute("SELECT patient_ID, interval, property, value FROM Measurements;")
            
            for patient_id, interval, property, value in read_cur:
                clean_pid = patient_id.strip() if isinstance(patient_id, str) else patient_id
                if clean_pid not in birthday_by_patient:
                    continue
                if property in property_tid:
                    tid = property_tid[property]
                    time_value = interval.lower if hasattr(interval, 'lower') else interval
                    if time_value is None:
                        continue
                    
                    if value is not None and isinstance(value, str) and value.strip() != '':
                        try:
                            numeric_value = float(value.strip().strip('()').split(',')[0])
                            batch.append((time_value, clean_pid, tid, numeric_value))
                        except ValueError:
                            continue

                if len(batch) >= batch_size:
                    execute_values(write_cur, """
                        INSERT INTO TimeSeries (time, pid, tid, varvalue)
                        VALUES %s
                        ON CONFLICT (time, pid, tid) DO UPDATE SET varvalue = EXCLUDED.varvalue;
                    """, batch)
                    dest_conn.commit()
                    batch.clear()

            if batch:
                execute_values(write_cur, """
                    INSERT INTO TimeSeries (time, pid, tid, varvalue)
                    VALUES %s
                    ON CONFLICT (time, pid, tid) DO UPDATE SET varvalue = EXCLUDED.varvalue;
                """, batch)
                dest_conn.commit()
                batch.clear()

        print("[python-runner] Inserting Dependent Properties into TimeSeries...")
        with read_conn.cursor(name='dep_props_reader') as read_cur, dest_conn.cursor() as write_cur:
            read_cur.itersize = batch_size
            read_cur.execute("SELECT patient_ID, interval, property, value FROM DependentProperties;")
            
            for patient_id, interval, property, value in read_cur:
                clean_pid = patient_id.strip() if isinstance(patient_id, str) else patient_id
                if clean_pid not in birthday_by_patient:
                    continue
                if property in property_tid:
                    tid = property_tid[property]
                    time_value = interval.lower if hasattr(interval, 'lower') else interval
                    if time_value is None:
                        continue
                    if value is not None and isinstance(value, str) and value.strip() != '':
                        try:
                            numeric_value = float(value.strip().strip('()').split(',')[0])
                            batch.append((time_value, clean_pid, tid, numeric_value))
                        except ValueError:
                            continue

                if len(batch) >= batch_size:
                    execute_values(write_cur, """
                        INSERT INTO TimeSeries (time, pid, tid, varvalue)
                        VALUES %s
                        ON CONFLICT (time, pid, tid) DO UPDATE SET varvalue = EXCLUDED.varvalue;
                    """, batch)
                    dest_conn.commit()
                    batch.clear()

            if batch:
                execute_values(write_cur, """
                    INSERT INTO TimeSeries (time, pid, tid, varvalue)
                    VALUES %s
                    ON CONFLICT (time, pid, tid) DO UPDATE SET varvalue = EXCLUDED.varvalue;
                """, batch)
                dest_conn.commit()
                batch.clear()
    finally:
        read_conn.close()

def process_patient_optimized(patient_id, pat_critical_events, var_ids, var_defaults, num_vars, max_session_time, db_params):
    dbname, user, password, host, port = db_params
    read_conn = connect(dbname, user, password, host, port)
    write_conn = connect(dbname, user, password, host, port)
    
    inserted_count = 0
    clean_pid = patient_id.strip() if isinstance(patient_id, str) else patient_id
    
    try:
        cursor_name = f'pat_read_{re.sub("[^a-zA-Z0-9_]", "_", clean_pid)}'
        with read_conn.cursor(name=cursor_name) as read_cur, write_conn.cursor() as write_cur:
            from datetime import datetime
            start_date = datetime(2023, 1, 1)

            # Query valid session timestamps from Measurements for this patient
            write_cur.execute("""
                SELECT DISTINCT interval
                FROM Measurements
                WHERE patient_ID = %s;
            """, (clean_pid,))
            valid_timestamps = {row[0] for row in write_cur.fetchall() if row[0] >= start_date}

            if not valid_timestamps:
                return 0

            patient_first_session = min(valid_timestamps)
            patient_last_session = max(valid_timestamps)

            read_cur.itersize = 2000
            
            read_cur.execute("""
                SELECT time, tid, varvalue
                FROM TimeSeries
                WHERE pid = %s
                ORDER BY time, tid;
            """, (clean_pid,))

            last_values = {vid: var_defaults[vid] for vid in var_ids}
            
            current_time = None
            current_session_vals = {}
            rows_to_insert = []
            chunk_size = 500

            def build_and_flush(time_stamp, vals):
                nonlocal inserted_count
                if time_stamp < start_date:
                    return

                for vid, v in vals.items():
                    last_values[vid] = v
                vector = [last_values[vid] for vid in var_ids]
                
                if time_stamp not in valid_timestamps:
                    return

                # Inclusion criterion: session must have at least W=30 days of history from patient's first session
                history_days = (time_stamp.date() - patient_first_session.date()).days
                if history_days < 30:
                    return

                # --- CRITICAL EVENT & CENSORING (Horizon = 360 days) ---
                # Find the first critical event strictly in the future of this session
                next_critical_event = next((ev for ev in pat_critical_events if ev.date() > time_stamp.date()), None)

                if next_critical_event is not None:
                    delta = (next_critical_event.date() - time_stamp.date()).days
                    tte_uncapped = delta
                    tte_capped = min(360, tte_uncapped)
                else:
                    # Patient has no critical event after this session
                    residual_days = (patient_last_session.date() - time_stamp.date()).days
                    # Censoring filter: discard session if no event occurs within 360 days and residual window is < 360 days
                    if residual_days < 360:
                        return
                    tte_capped = 360
                    tte_uncapped = max(360, (max_session_time.date() - time_stamp.date()).days + 1)

                log_tte_capped = round(math.log(tte_capped), 6)
                log_tte_uncapped = round(math.log(tte_uncapped), 6)

                rows_to_insert.append((time_stamp, clean_pid, history_days, vector, 
                                       tte_capped, log_tte_capped, tte_uncapped, log_tte_uncapped))

                if len(rows_to_insert) >= chunk_size:
                    execute_values(write_cur, """
                        INSERT INTO NN_Training_Dataset (timestamp, patient_id, history_days, misure, 
                                                         tte, log_tte, tte_uncapped, log_tte_uncapped)
                        VALUES %s
                        ON CONFLICT (timestamp, patient_id) DO NOTHING;
                    """, rows_to_insert)
                    write_conn.commit()
                    inserted_count += len(rows_to_insert)
                    rows_to_insert.clear()

            for time_val, tid, val in read_cur:
                if time_val != current_time:
                    if current_time is not None:
                        build_and_flush(current_time, current_session_vals)
                    current_time = time_val
                    current_session_vals = {}
                current_session_vals[tid] = val

            if current_time is not None:
                build_and_flush(current_time, current_session_vals)

            if rows_to_insert:
                execute_values(write_cur, """
                    INSERT INTO NN_Training_Dataset (timestamp, patient_id, history_days, misure, 
                                                     tte, log_tte, tte_uncapped, log_tte_uncapped)
                    VALUES %s
                    ON CONFLICT (timestamp, patient_id) DO NOTHING;
                """, rows_to_insert)
                write_conn.commit()
                inserted_count += len(rows_to_insert)

        return inserted_count
    
    except Exception as e:
        print(f"[python-runner] Failed patient {clean_pid}: {e}")
        write_conn.rollback()
        return 0
    finally:
        read_conn.close()
        write_conn.close()

def insert_nn_training_dataset_table(cur, dest_conn, db_params, critical_events_by_patient):
    cur.execute("""
        SELECT var_id, var_name, average 
        FROM TimeVar 
        ORDER BY var_id;
    """)
    
    variables = cur.fetchall()
    if not variables:
        print("[python-runner] No variables found in TimeVar. Skipping.")
        return

    var_ids = [v[0] for v in variables]
    var_defaults = {v[0]: v[2] for v in variables}
    num_vars = len(var_ids)

    cur.execute("SELECT MAX(time) FROM TimeSeries WHERE time <= '2026-03-01 00:00:00';")
    res = cur.fetchone()
    max_session_time = res[0] if res else None
    if max_session_time is None:
        print("[python-runner] No sessions found in TimeSeries. Skipping.")
        return

    cur.execute("SELECT patient_id FROM Patient;")
    patients = [r[0] for r in cur.fetchall()]

    print(f"[python-runner] Starting NN processing for {len(patients)} patients with {num_vars} features (sliding window >= 30, subsequent critical events, 360d censoring)...")

    total_inserted = 0
    for i, patient_id in enumerate(patients, 1):
        clean_pid = patient_id.strip() if isinstance(patient_id, str) else patient_id
        pat_events = critical_events_by_patient.get(clean_pid, [])
        inserted = process_patient_optimized(
            clean_pid, pat_events, var_ids, var_defaults, num_vars, max_session_time, db_params
        )
        total_inserted += inserted
        
        if i % 50 == 0 or i == len(patients):
            print(f"[python-runner] Progress: {i}/{len(patients)} patients completed. Inserted {total_inserted} rows...")
            gc.collect() 

    print(f"[python-runner] Completed NN_Training_Dataset. Total rows inserted: {total_inserted}")

def export_nn_training_datasets(cur, dest_conn):
    script_dir = os.path.dirname(os.path.abspath(__file__))
    nn_dir = os.path.join(script_dir, "NN_Dataset")
    os.makedirs(nn_dir, exist_ok=True)

    cur.execute("SELECT var_id, var_name FROM TimeVar ORDER BY var_id;")
    var_rows = cur.fetchall()
    var_names = [r[1] for r in var_rows]

    idx_44 = list(range(len(var_names)))
    idx_26 = [i for i, name in enumerate(var_names) if name in ALLOWED_NN_PROPS]
    idx_av = [i for i, name in enumerate(var_names) if name in VASCULAR_ACCESS_PROPS]

    print(f"[python-runner] Features mapping: {len(idx_44)} all clinical, {len(idx_26)} standard allowed (26), {len(idx_av)} vascular access only (12).")

    # Defined output files: 6 datasets (W=30 and W=60) + standard default aliases (W=30)
    files_info = [
        # W = 30 days
        ("NN_training_dataset_W30_26_uncapped.csv", idx_26, "uncapped", 30),
        ("NN_training_dataset_W30_26_capped360.csv", idx_26, "capped", 30),
        ("NN_training_dataset_W30_44_capped360.csv", idx_44, "capped", 30),
        # W = 60 days
        ("NN_training_dataset_W60_26_uncapped.csv", idx_26, "uncapped", 60),
        ("NN_training_dataset_W60_26_capped360.csv", idx_26, "capped", 60),
        ("NN_training_dataset_W60_44_capped360.csv", idx_44, "capped", 60),
        # Default aliases (W = 30)
        ("NN_training_dataset_26_uncapped.csv", idx_26, "uncapped", 30),
        ("NN_training_dataset_26_capped360.csv", idx_26, "capped", 30),
        ("NN_training_dataset_44_capped360.csv", idx_44, "capped", 30),
    ]

    open_files = []
    writers = []
    header = ["timestamp", "patient_id", "misure", "tte", "log_tte"]

    for filename, _, _, _ in files_info:
        file_path = os.path.join(nn_dir, filename)
        if os.path.exists(file_path):
            print(f"[python-runner] Removing existing CSV file: {file_path}")
            os.remove(file_path)
        f = open(file_path, "w", encoding="utf-8", newline="")
        writer = csv.writer(f)
        writer.writerow(header)
        open_files.append(f)
        writers.append(writer)

    print(f"[python-runner] Streaming and generating CSV datasets in {nn_dir}...")
    with dest_conn.cursor(name='export_stream_cursor') as stream_cur:
        stream_cur.itersize = 5000
        stream_cur.execute("""
            SELECT timestamp, patient_id, history_days, misure, 
                   tte, log_tte, tte_uncapped, log_tte_uncapped 
            FROM NN_Training_Dataset 
            ORDER BY patient_id, timestamp;
        """)

        count = 0
        file_counts = [0] * len(files_info)
        for row in stream_cur:
            ts, pid, h_days, m, tte_c, log_c, tte_u, log_u = row
            clean_pid = pid.strip() if isinstance(pid, str) else pid

            m_44 = "{" + ",".join(str(m[i]) for i in idx_44) + "}"
            m_26 = "{" + ",".join(str(m[i]) for i in idx_26) + "}"
            m_av = "{" + ",".join(str(m[i]) for i in idx_av) + "}"

            for idx_f, (fname, feat_indices, mode, min_w) in enumerate(files_info):
                if h_days < min_w:
                    continue

                w = writers[idx_f]
                if feat_indices is idx_26:
                    fm = m_26
                elif feat_indices is idx_44:
                    fm = m_44
                else:
                    fm = m_av

                if mode == "uncapped":
                    w.writerow([ts, clean_pid, fm, tte_u, log_u])
                else:
                    w.writerow([ts, clean_pid, fm, tte_c, log_c])

                file_counts[idx_f] += 1

            count += 1
            if count % 50000 == 0:
                print(f"[python-runner] Streamed {count} source rows...")

    for f in open_files:
        f.close()

    for idx_f, (fname, _, _, _) in enumerate(files_info):
        print(f"[python-runner] File '{fname}': {file_counts[idx_f]} rows written.")

    print(f"[python-runner] Successfully exported all {len(files_info)} dataset files in {nn_dir}!")

def compute_age(dob, dod=None, ref_date=None):
    if ref_date is None:
        ref_date = date.today()
    elif isinstance(ref_date, datetime):
        ref_date = ref_date.date()

    def to_date(val):
        if val is None:
            return None
        if isinstance(val, datetime):
            return val.date()
        if isinstance(val, date):
            return val
        if isinstance(val, str):
            s = val.strip().strip('"').strip("'")
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y-%m-%dT%H:%M:%S"):
                try:
                    return datetime.strptime(s.split('.')[0], fmt).date()
                except ValueError:
                    continue
            try:
                return datetime.fromisoformat(s).date()
            except Exception:
                return None
        return None

    dob_date = to_date(dob)
    if dob_date is None:
        return None

    dod_date = to_date(dod)
    target_date = dod_date if dod_date is not None else ref_date

    age = target_date.year - dob_date.year - ((target_date.month, target_date.day) < (dob_date.month, dob_date.day))
    return max(0, age)

def export_patient_age_json(cur, dest_conn):
    script_dir = os.path.dirname(os.path.abspath(__file__))
    nn_dir = os.path.join(script_dir, "NN_Dataset")
    os.makedirs(nn_dir, exist_ok=True)
    json_path = os.path.join(nn_dir, "patient_age.json")

    print(f"[python-runner] Exporting patient ages to {json_path}...")
    cur.execute("""
        SELECT patient_id, date_of_birth, date_of_death 
        FROM Patient 
        WHERE date_of_birth IS NOT NULL
        ORDER BY patient_id;
    """)
    rows = cur.fetchall()

    today = date.today()
    patient_ages = {}
    for pid, dob, dod in rows:
        clean_pid = pid.strip() if isinstance(pid, str) else str(pid)
        age = compute_age(dob, dod, ref_date=today)
        if age is not None:
            patient_ages[clean_pid] = age

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(patient_ages, f, indent=2)

    print(f"[python-runner] Successfully exported {len(patient_ages)} patient ages to {json_path}!")

def main():
    DB_HOST = get_env('DB_HOST', 'datalake_backend_db')
    DB_PORT = get_env('DB_PORT', '5432')
    DB_USER = get_env('DB_USER', required=True)
    DB_PASS = get_env('DB_PASS', required=True)
    DB_NAME = get_env('DB_NAME', required=True)
    NEW_DB_NAME = get_env('NEW_DB_NAME', 'datalake_export')

    db_params_src = (DB_NAME, DB_USER, DB_PASS, DB_HOST, DB_PORT)
    db_params_dest = (NEW_DB_NAME, DB_USER, DB_PASS, DB_HOST, DB_PORT)

    print(f"[python-runner] Connection to {DB_HOST}:{DB_PORT} as {DB_USER}. Source DB: {DB_NAME}. Dest DB: {NEW_DB_NAME}")

    ready = wait_for_db(DB_HOST, DB_PORT, DB_USER, DB_PASS, DB_NAME, timeout=60, interval=2)
    if not ready:
        sys.exit(1)

    try:
        src_conn = connect(DB_NAME, DB_USER, DB_PASS, DB_HOST, DB_PORT)
        with src_conn.cursor() as cur:
            cur.execute("SELECT * FROM patient.patient;")
            patients_list = list(map(itemgetter(0), cur.fetchall()))

            cur.execute("select patient.propertymeasure.patient, patient.propertymeasure.value from patient.propertymeasure where patient.propertymeasure.property='Data Nascita';")
            birthday_list = clean_birthday_rows(cur.fetchall())

            cur.execute("select patient.patientevent.patient || ' ' || patient.patientevent.interval from patient.patientevent where type = 'Decesso';")
            death_dict = clean_death_list(cur.fetchall())

            # Extract all critical events strictly subsequent to each patient's first dialysis session (>= 2023)
            cur.execute("""
                WITH first_session AS (
                    SELECT patient, min(lower(interval)) as first_sess
                    FROM patient.propertymeasure
                    WHERE lower(interval) >= '2023-01-01'
                    GROUP BY patient
                )
                SELECT e.patient, lower(e.interval)
                FROM patient.patientevent e
                JOIN first_session s ON e.patient = s.patient
                WHERE e.type != 'Seduta Dialisi'
                  AND lower(e.interval)::date > s.first_sess::date
                ORDER BY e.patient, lower(e.interval);
            """)
            critical_events_by_patient = defaultdict(list)
            for row in cur.fetchall():
                p_id = row[0].strip() if isinstance(row[0], str) else row[0]
                critical_events_by_patient[p_id].append(row[1])

            # For Patient table date_of_critical_event column, store the first critical event subsequent to the first session
            critical_event_dict = {
                pid: ev_list[0] for pid, ev_list in critical_events_by_patient.items() if ev_list
            }

            cur.execute("select patient.propertymeasure.patient, patient.propertymeasure.property, patient.propertymeasure.value from patient.propertymeasure where patient.propertymeasure.property='Sesso' or patient.propertymeasure.property='Altezza' or patient.propertymeasure.property='Data Nascita' or patient.propertymeasure.property='Etnia';")
            generality_dict = process_generality_list(cur.fetchall())
    except Exception as e:
        print(f"[python-runner] Source DB error: {e}")
        sys.exit(1)

    try:
        admin_conn = connect('postgres', DB_USER, DB_PASS, DB_HOST, DB_PORT, autocommit=True)
        with admin_conn.cursor() as cur:
            cur.execute(sql.SQL("SELECT 1 FROM pg_database WHERE datname = %s"), [NEW_DB_NAME])
            if cur.fetchone():
                print(f"[python-runner] Dropping existing DB '{NEW_DB_NAME}'.")
                cur.execute(sql.SQL("DROP DATABASE {};").format(sql.Identifier(NEW_DB_NAME)))
            print(f"[python-runner] Creating DB '{NEW_DB_NAME}'...")
            cur.execute(sql.SQL("CREATE DATABASE {};").format(sql.Identifier(NEW_DB_NAME)))
    except Exception as e:
        print(f"[python-runner] DB creation error: {e}")
        sys.exit(1)

    try:
        dest_conn = connect(NEW_DB_NAME, DB_USER, DB_PASS, DB_HOST, DB_PORT)
        with dest_conn.cursor() as cur:

            create_domains_and_tables(cur)
            dest_conn.commit()

            birthday_by_patient = {pid: dob for pid, dob in birthday_list if pid and dob}
            birthday_rows = list(birthday_by_patient.items())
            insert_patients(cur, birthday_rows, dest_conn, patients_list, death_dict, generality_dict, critical_event_dict)

            print("[python-runner] Streaming raw measures...")
            stream_and_insert_measures(db_params_src, dest_conn, birthday_by_patient)

            print("[python-runner] Processing Dependent Properties...")
            dependent_properties_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dependent_properties")
            files = file_list(dependent_properties_dir)
            
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                futures = [executor.submit(process_single_property_file, file, db_params_dest, birthday_by_patient) for file in files]
                for future in futures:
                    future.result()

            print("[python-runner] Inserting into TimeVar Table...")
            insert_timevar_table(cur, dest_conn)

            cur.execute("SELECT var_name, var_id FROM TimeVar;")
            property_tid = {row[0]: row[1] for row in cur.fetchall()}

            print("[python-runner] Inserting into TimeSeries Table...")
            insert_timeseries_table(dest_conn, birthday_by_patient, property_tid, db_params_dest)

            print("[python-runner] Starting NN_Training_Dataset population...")
            insert_nn_training_dataset_table(cur, dest_conn, db_params_dest, critical_events_by_patient)

            print("[python-runner] Exporting NN datasets to CSV files in NN_Dataset/...")
            export_nn_training_datasets(cur, dest_conn)

            print("[python-runner] Exporting patient ages to JSON in NN_Dataset/...")
            export_patient_age_json(cur, dest_conn)

    except Exception as e:
        print(f"[python-runner] Destination DB error: {e}")
        sys.exit(1)
    finally:
        try:
            src_conn.close()
            dest_conn.close()
            admin_conn.close()
        except Exception:
            pass

    print("[python-runner] Operation completed successfully!")

if __name__ == "__main__":
    main()